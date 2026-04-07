from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from live_trading_system import (
    DhanBroker,
    PaperBroker,
    Position,
    StrategyConfig,
    apply_chain_filter_to_signal,
    append_worker_trade_log,
    build_signal,
    calculate_position_size,
    estimated_option_price,
    fetch_price_data,
    fetch_vix_data,
    get_dhan_broker,
    load_worker_config,
    load_worker_positions,
    load_worker_state,
    loss_limit_breached,
    make_option_symbol,
    now_ist,
    option_price_bounds,
    risk_checks,
    save_worker_positions,
    save_worker_state,
)


def current_worker_positions() -> list[tuple[str, Position]]:
    return [(payload["id"], Position(
        side=payload["side"],
        entry=float(payload["entry"]),
        stop_loss=float(payload["stop_loss"]),
        target=float(payload["target"]),
        qty=int(payload["qty"]),
        opened_at=now_ist() if not payload.get("opened_at") else datetime.fromisoformat(payload["opened_at"]),
        mode=payload["mode"],
        reason=payload["reason"],
        option_symbol=payload["option_symbol"],
        security_id=payload.get("security_id"),
        exchange_segment=payload.get("exchange_segment"),
        order_id=payload.get("order_id"),
        trailing_active=bool(payload.get("trailing_active", False)),
        entry_spot=payload.get("entry_spot"),
        stop_loss_spot=payload.get("stop_loss_spot"),
        target_spot=payload.get("target_spot"),
    )) for payload in load_worker_positions()]


def serialize_position(position_id: str, pos: Position) -> dict[str, Any]:
    return {
        "id": position_id,
        "side": pos.side,
        "entry": pos.entry,
        "stop_loss": pos.stop_loss,
        "target": pos.target,
        "qty": pos.qty,
        "opened_at": pos.opened_at.isoformat(),
        "mode": pos.mode,
        "reason": pos.reason,
        "option_symbol": pos.option_symbol,
        "security_id": pos.security_id,
        "exchange_segment": pos.exchange_segment,
        "order_id": pos.order_id,
        "trailing_active": pos.trailing_active,
        "entry_spot": pos.entry_spot,
        "stop_loss_spot": pos.stop_loss_spot,
        "target_spot": pos.target_spot,
    }


def is_market_hours(ts) -> bool:
    current_time = ts.timetz().replace(tzinfo=None)
    return current_time.strftime("%H:%M") >= "09:15" and current_time.strftime("%H:%M") <= "15:30"


def run_worker_cycle() -> int:
    config = load_worker_config()
    state = load_worker_state()
    state["last_heartbeat"] = now_ist().isoformat(timespec="seconds")

    if not config or not state.get("enabled"):
        state["status"] = "idle"
        save_worker_state(state)
        return 15

    capital = float(config.get("capital", 100000.0))
    expiry_code = str(config.get("expiry_code", ""))
    mode = config.get("mode", "PAPER")
    poll_interval_seconds = int(config.get("poll_interval_seconds", 60))
    cfg = StrategyConfig(**config.get("strategy", {}))
    state["mode"] = mode

    if state.get("trade_day") != str(now_ist().date()):
        state["trade_day"] = str(now_ist().date())
        state["daily_pnl"] = 0.0
        state["loss_streak"] = 0
        state["daily_trade_count"] = 0
        state["day_start_capital"] = capital

    broker = PaperBroker() if mode == "PAPER" else get_dhan_broker(
        config.get("dhan_client_id") or None,
        config.get("dhan_access_token") or None,
    )
    ready, reason = broker.status()
    if not ready and mode == "LIVE":
        state["status"] = "error"
        state["last_error"] = reason
        save_worker_state(state)
        return 15

    try:
        price_df = fetch_price_data(
            cfg.underlying_security_id,
            cfg.underlying_exchange_segment,
            cfg.bar_interval,
            cfg.history_period,
            broker=broker if isinstance(broker, DhanBroker) else None,
        )
        vix_df = fetch_vix_data(broker=broker if isinstance(broker, DhanBroker) else None)
    except Exception as exc:
        state["status"] = "error"
        state["last_error"] = f"Data fetch failed: {exc}"
        save_worker_state(state)
        return 15

    if price_df.empty:
        state["status"] = "waiting_data"
        state["last_error"] = "No price data returned from broker."
        save_worker_state(state)
        return 15

    latest_ts = price_df.index[-1]
    if not is_market_hours(latest_ts):
        state["status"] = "waiting_market"
        state["last_run_at"] = now_ist().isoformat(timespec="seconds")
        state["last_error"] = None
        save_worker_state(state)
        return max(poll_interval_seconds, 15)

    if vix_df.empty:
        vix_now = (cfg.low_vix_threshold + cfg.high_vix_threshold) / 2
        vix_prev = None
    else:
        vix_now = float(vix_df["Close"].iloc[-1])
        vix_prev = float(vix_df["Close"].iloc[-2]) if len(vix_df) > 1 else None

    positions = current_worker_positions()
    current_spot = float(price_df["Close"].iloc[-1])
    updated_positions: list[dict[str, Any]] = []

    for position_id, pos in positions:
        ref_spot = pos.entry_spot or current_spot
        mark_price = estimated_option_price(current_spot, ref_spot, pos.side, cfg)
        if (
            pos.mode == "LIVE"
            and isinstance(broker, DhanBroker)
            and pos.security_id
            and pos.exchange_segment
        ):
            try:
                mark_price = broker.get_ltp(pos.security_id, pos.exchange_segment)
            except Exception:
                pass

        held_minutes = (now_ist() - pos.opened_at).total_seconds() / 60
        exit_reason = None
        exit_mark = mark_price
        if mark_price <= pos.stop_loss:
            exit_reason = "SL"
            exit_mark = pos.stop_loss
        elif mark_price >= pos.target:
            exit_reason = "TARGET"
            exit_mark = pos.target
        elif held_minutes >= cfg.max_holding_minutes:
            exit_reason = "TIME"

        if exit_reason is None:
            updated_positions.append(serialize_position(position_id, pos))
            continue

        resp = broker.exit_order(pos.option_symbol, pos.qty, pos.mode)
        if resp.get("status") not in {"paper_exit", "exited", "success", "accepted"}:
            updated_positions.append(serialize_position(position_id, pos))
            append_worker_trade_log({
                **resp,
                "timestamp": now_ist().isoformat(timespec="seconds"),
                "event": "exit_failed",
                "position_id": position_id,
            })
            continue

        realized = (exit_mark - pos.entry) * pos.qty
        state["daily_pnl"] = float(state.get("daily_pnl", 0.0)) + realized
        state["loss_streak"] = int(state.get("loss_streak", 0)) + 1 if realized < 0 else 0
        append_worker_trade_log({
            **resp,
            "timestamp": now_ist().isoformat(timespec="seconds"),
            "position_id": position_id,
            "event": "exit",
            "side": pos.side,
            "qty": pos.qty,
            "symbol": pos.option_symbol,
            "entry_price": pos.entry,
            "exit_price": exit_mark,
            "exit_reason": exit_reason,
            "realized_pnl": realized,
            "entry_spot": pos.entry_spot,
        })

    save_worker_positions(updated_positions)

    signal = build_signal(price_df, vix_now, vix_prev, cfg)
    signal, _, no_trade_reason = apply_chain_filter_to_signal(
        signal,
        expiry_code,
        cfg,
        broker=broker if isinstance(broker, DhanBroker) else None,
    )

    trades_today = int(state.get("daily_trade_count", 0))
    if signal is not None:
        ok, reason = risk_checks(
            signal,
            float(state.get("day_start_capital", capital)),
            float(state.get("daily_pnl", 0.0)),
            trades_today,
            int(state.get("loss_streak", 0)),
            cfg,
        )
        qty = calculate_position_size(
            capital,
            cfg.risk_per_trade_pct,
            signal.option_entry,
            signal.option_stop_loss,
            cfg.lot_size,
            cfg.max_capital_allocation_pct,
            signal.option_entry,
        )
        signal_key = f"{signal.timestamp}_{signal.side}_{round(signal.entry, 2)}"
        if (
            ok
            and qty > 0
            and signal_key != state.get("last_signal_key")
            and not loss_limit_breached(float(state.get("day_start_capital", capital)), float(state.get("daily_pnl", 0.0)), cfg)
            and int(state.get("loss_streak", 0)) < cfg.max_consecutive_losses
            and trades_today < cfg.max_trades_per_day
        ):
            option_symbol = make_option_symbol(
                signal.entry,
                signal.side,
                expiry_code,
                cfg.option_moneyness,
                cfg.strike_step,
                cfg.option_prefix,
            )
            resp = broker.place_order(signal.side, qty, option_symbol, mode)
            append_worker_trade_log({
                **resp,
                "timestamp": now_ist().isoformat(timespec="seconds"),
                "event": "entry_attempt",
                "signal_reason": signal.reason,
            })
            if resp.get("status") in {"paper_filled", "filled", "success", "accepted"}:
                entry_price = signal.option_entry
                if (
                    mode == "LIVE"
                    and isinstance(broker, DhanBroker)
                    and resp.get("security_id")
                    and resp.get("exchange_segment")
                ):
                    try:
                        entry_price = broker.get_ltp(resp["security_id"], resp["exchange_segment"])
                    except Exception:
                        pass
                position = Position(
                    side=signal.side,
                    entry=entry_price,
                    stop_loss=signal.option_stop_loss,
                    target=signal.option_target,
                    qty=qty,
                    opened_at=now_ist(),
                    mode=mode,
                    reason=signal.reason,
                    option_symbol=option_symbol,
                    security_id=resp.get("security_id"),
                    exchange_segment=resp.get("exchange_segment"),
                    order_id=resp.get("order_id"),
                    entry_spot=signal.entry,
                    stop_loss_spot=signal.stop_loss,
                    target_spot=signal.target,
                )
                position_id = uuid.uuid4().hex
                payloads = load_worker_positions()
                payloads.append(serialize_position(position_id, position))
                save_worker_positions(payloads)
                state["last_signal_key"] = signal_key
                state["daily_trade_count"] = trades_today + 1
                state["last_action"] = f"Entered {option_symbol} x {qty}"
        else:
            state["last_action"] = f"No entry: {reason if signal is not None else no_trade_reason or 'No signal'}"
    else:
        state["last_action"] = no_trade_reason or "No signal"

    state["status"] = "active"
    state["last_error"] = None
    state["last_run_at"] = now_ist().isoformat(timespec="seconds")
    state["open_positions"] = len(load_worker_positions())
    save_worker_state(state)
    return max(poll_interval_seconds, 15)


def main() -> None:
    while True:
        try:
            sleep_for = run_worker_cycle()
        except Exception as exc:
            state = load_worker_state()
            state["status"] = "error"
            state["last_error"] = str(exc)
            state["last_heartbeat"] = now_ist().isoformat(timespec="seconds")
            save_worker_state(state)
            sleep_for = 15
        time.sleep(max(sleep_for, 5))


if __name__ == "__main__":
    main()
