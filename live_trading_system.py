from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Literal

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# ==========================================================
# NIFTY LIVE TRADING SYSTEM
# ----------------------------------------------------------
# Expanded version with:
# - live signal engine
# - backtest engine
# - option strike selection logic
# - Dhan execution scaffold
#
# IMPORTANT:
# 1. Paper mode is usable as-is.
# 2. Dhan live execution requires you to wire real credentials and
#    actual broker methods in DhanBroker.
# 3. This is a disciplined execution framework, not a profit guarantee.
# ==========================================================

st.set_page_config(page_title="NIFTY Live Trading System", layout="wide")

TradeMode = Literal["PAPER", "LIVE"]
SignalSide = Literal["CE", "PE"]
Regime = Literal["TRENDING", "RANGE", "VOLATILE"]


@dataclass
class StrategyConfig:
    symbol: str = "^NSEI"
    vix_symbol: str = "^INDIAVIX"
    bar_interval: str = "5m"
    history_period: str = "10d"
    fast_ema: int = 20
    slow_ema: int = 50
    rsi_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    volume_lookback: int = 20
    body_strength_threshold: float = 0.55
    volume_spike_threshold: float = 1.35
    low_vix_threshold: float = 14.0
    high_vix_threshold: float = 18.0
    min_rr: float = 1.5
    max_rr: float = 2.0
    max_daily_loss_pct: float = 2.5
    risk_per_trade_pct: float = 1.0
    max_trades_per_day: int = 3
    confidence_threshold: float = 0.72
    allow_live_orders: bool = False
    lot_size: int = 75
    strike_step: int = 50
    option_moneyness: Literal["ATM", "ITM1", "OTM1"] = "ATM"
    use_regime_filter: bool = True
    use_volume_filter: bool = True
    use_bb_filter: bool = True


@dataclass
class Signal:
    timestamp: datetime
    side: SignalSide
    entry: float
    stop_loss: float
    target: float
    regime: Regime
    confidence: float
    reason: str


@dataclass
class Position:
    side: SignalSide
    entry: float
    stop_loss: float
    target: float
    qty: int
    opened_at: datetime
    mode: TradeMode
    reason: str
    option_symbol: str
    trailing_active: bool = False


# -----------------------------
# Broker abstraction
# -----------------------------
class BrokerInterface:
    def place_order(self, side: SignalSide, qty: int, option_symbol: str, mode: TradeMode) -> dict:
        raise NotImplementedError

    def exit_order(self, option_symbol: str, qty: int, mode: TradeMode) -> dict:
        raise NotImplementedError


class PaperBroker(BrokerInterface):
    def place_order(self, side: SignalSide, qty: int, option_symbol: str, mode: TradeMode) -> dict:
        return {
            "status": "paper_filled",
            "side": side,
            "qty": qty,
            "symbol": option_symbol,
            "mode": mode,
            "timestamp": datetime.now().isoformat(),
        }

    def exit_order(self, option_symbol: str, qty: int, mode: TradeMode) -> dict:
        return {
            "status": "paper_exit",
            "qty": qty,
            "symbol": option_symbol,
            "mode": mode,
            "timestamp": datetime.now().isoformat(),
        }


class DhanBroker(BrokerInterface):
    """
    LIVE WIRING GUIDE
    -----------------
    Replace placeholders with your real Dhan implementation.

    Expected live flow:
    1. Load DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN from st.secrets or env
    2. Resolve option_symbol into broker tradable security_id
    3. Send market/limit order
    4. Store returned order id
    5. Use broker LTP for real P&L and exits
    """

    def __init__(self, client_id: Optional[str] = None, access_token: Optional[str] = None):
        self.client_id = client_id
        self.access_token = access_token

    def place_order(self, side: SignalSide, qty: int, option_symbol: str, mode: TradeMode) -> dict:
        if mode != "LIVE":
            return {"status": "blocked", "reason": "LIVE mode not enabled"}
        raise NotImplementedError(
            "Wire your Dhan place_order here. Use client_id/access_token, resolve option symbol, then place the order."
        )

    def exit_order(self, option_symbol: str, qty: int, mode: TradeMode) -> dict:
        if mode != "LIVE":
            return {"status": "blocked", "reason": "LIVE mode not enabled"}
        raise NotImplementedError(
            "Wire your Dhan exit_order here. Resolve option symbol/security_id and exit the open position."
        )


# -----------------------------
# Indicator helpers
# -----------------------------
def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def enrich_price_data(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    x = df.copy()
    x["ema_fast"] = x["Close"].ewm(span=cfg.fast_ema, adjust=False).mean()
    x["ema_slow"] = x["Close"].ewm(span=cfg.slow_ema, adjust=False).mean()
    x["rsi"] = compute_rsi(x["Close"], cfg.rsi_period)
    x["vol_avg"] = x["Volume"].rolling(cfg.volume_lookback).mean()
    x["vol_ratio"] = x["Volume"] / x["vol_avg"]
    x["bb_mid"] = x["Close"].rolling(cfg.bb_period).mean()
    bb_std = x["Close"].rolling(cfg.bb_period).std()
    x["bb_upper"] = x["bb_mid"] + cfg.bb_std * bb_std
    x["bb_lower"] = x["bb_mid"] - cfg.bb_std * bb_std
    typical = (x["High"] + x["Low"] + x["Close"]) / 3
    x["session_date"] = pd.to_datetime(x.index).date
    x["pv"] = typical * x["Volume"]
    x["cum_pv"] = x.groupby("session_date")["pv"].cumsum()
    x["cum_vol"] = x.groupby("session_date")["Volume"].cumsum().replace(0, np.nan)
    x["vwap"] = x["cum_pv"] / x["cum_vol"]
    return x


# -----------------------------
# Candle intelligence
# -----------------------------
def candle_metrics(row: pd.Series) -> dict:
    o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
    full_range = max(h - l, 1e-9)
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return {
        "body": body,
        "range": full_range,
        "body_ratio": body / full_range,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "bullish": c > o,
        "bearish": c < o,
    }


def classify_candle(row: pd.Series, cfg: StrategyConfig) -> str:
    m = candle_metrics(row)
    if m["bullish"] and m["body_ratio"] >= cfg.body_strength_threshold and m["upper_wick"] < m["body"] * 0.4:
        return "strong_bullish"
    if m["bearish"] and m["body_ratio"] >= cfg.body_strength_threshold and m["lower_wick"] < m["body"] * 0.4:
        return "strong_bearish"
    if m["lower_wick"] > m["body"] * 2 and m["bullish"]:
        return "hammer"
    if m["upper_wick"] > m["body"] * 2 and m["bearish"]:
        return "shooting_star"
    return "neutral"


# -----------------------------
# Regime detection
# -----------------------------
def detect_regime(vix_value: float, vix_prev: Optional[float], cfg: StrategyConfig) -> Regime:
    if vix_value >= cfg.high_vix_threshold or (vix_prev is not None and vix_value > vix_prev and vix_value >= cfg.low_vix_threshold):
        return "VOLATILE"
    if vix_value <= cfg.low_vix_threshold:
        return "TRENDING"
    return "RANGE"


# -----------------------------
# Option mapping / strike selection
# -----------------------------
def round_to_strike(spot: float, step: int = 50) -> int:
    return int(round(spot / step) * step)


def choose_strike(spot: float, side: SignalSide, mode: str, step: int) -> int:
    atm = round_to_strike(spot, step)
    if mode == "ATM":
        return atm
    if mode == "ITM1":
        return atm - step if side == "CE" else atm + step
    if mode == "OTM1":
        return atm + step if side == "CE" else atm - step
    return atm


def make_option_symbol(spot: float, side: SignalSide, expiry_code: str, strike_mode: str, step: int) -> str:
    strike = choose_strike(spot, side, strike_mode, step)
    return f"NIFTY_{expiry_code}_{strike}_{side}"


def infer_nearest_weekly_expiry(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    days_ahead = (3 - now.weekday()) % 7
    expiry = now + timedelta(days=days_ahead)
    return expiry.strftime("%d%b%y").upper()


# -----------------------------
# Signal engine
# -----------------------------
def build_signal(df: pd.DataFrame, vix_now: float, vix_prev: Optional[float], cfg: StrategyConfig) -> Optional[Signal]:
    if df.empty or len(df) < max(cfg.slow_ema + 5, cfg.bb_period + 5):
        return None

    x = enrich_price_data(df, cfg)
    row = x.iloc[-1]
    prev = x.iloc[-2]
    candle_type = classify_candle(row, cfg)
    regime = detect_regime(vix_now, vix_prev, cfg)

    trend_up = row["ema_fast"] > row["ema_slow"] and row["Close"] > row["vwap"]
    trend_down = row["ema_fast"] < row["ema_slow"] and row["Close"] < row["vwap"]
    volume_ok = row["vol_ratio"] >= cfg.volume_spike_threshold
    breakout_up = row["Close"] > prev["High"]
    breakout_down = row["Close"] < prev["Low"]
    rsi_bullish = row["rsi"] >= 52
    rsi_bearish = row["rsi"] <= 48
    bb_expand_up = row["Close"] > row["bb_upper"]
    bb_expand_down = row["Close"] < row["bb_lower"]
    reject_vwap_for_put = row["High"] >= row["vwap"] and row["Close"] < row["vwap"]
    reject_vwap_for_call = row["Low"] <= row["vwap"] and row["Close"] > row["vwap"]

    bullish_score = 0.0
    bearish_score = 0.0
    bullish_reasons = []
    bearish_reasons = []

    if trend_up:
        bullish_score += 0.22
        bullish_reasons.append("Trend up")
    if trend_down:
        bearish_score += 0.22
        bearish_reasons.append("Trend down")

    if candle_type == "strong_bullish":
        bullish_score += 0.20
        bullish_reasons.append("Strong bullish candle")
    elif candle_type == "strong_bearish":
        bearish_score += 0.20
        bearish_reasons.append("Strong bearish candle")
    elif candle_type == "hammer":
        bullish_score += 0.10
        bullish_reasons.append("Hammer reversal")
    elif candle_type == "shooting_star":
        bearish_score += 0.10
        bearish_reasons.append("Shooting star reversal")

    if cfg.use_volume_filter and volume_ok:
        bullish_score += 0.14 if trend_up else 0.0
        bearish_score += 0.14 if trend_down else 0.0
        if trend_up:
            bullish_reasons.append("Volume spike")
        if trend_down:
            bearish_reasons.append("Volume spike")

    if breakout_up:
        bullish_score += 0.12
        bullish_reasons.append("Breakout above prior high")
    if breakout_down:
        bearish_score += 0.12
        bearish_reasons.append("Breakdown below prior low")

    if rsi_bullish:
        bullish_score += 0.10
        bullish_reasons.append("RSI bullish")
    if rsi_bearish:
        bearish_score += 0.10
        bearish_reasons.append("RSI bearish")

    if cfg.use_bb_filter and bb_expand_up:
        bullish_score += 0.08
        bullish_reasons.append("BB expansion up")
    if cfg.use_bb_filter and bb_expand_down:
        bearish_score += 0.08
        bearish_reasons.append("BB expansion down")

    if reject_vwap_for_call and regime in ("TRENDING", "RANGE"):
        bullish_score += 0.08
        bullish_reasons.append("VWAP support")
    if reject_vwap_for_put and regime in ("VOLATILE", "RANGE"):
        bearish_score += 0.08
        bearish_reasons.append("VWAP rejection")

    if cfg.use_regime_filter:
        if regime == "TRENDING":
            bullish_score += 0.06
            bullish_reasons.append("Low VIX regime")
        elif regime == "VOLATILE":
            bearish_score += 0.06
            bearish_reasons.append("High VIX regime")

    spot = float(row["Close"])
    ts = row.name.to_pydatetime() if hasattr(row.name, "to_pydatetime") else datetime.now()

    if bullish_score >= cfg.confidence_threshold and bullish_score > bearish_score:
        stop = min(float(row["Low"]), float(row["vwap"]))
        risk = max(spot - stop, 0.5)
        return Signal(ts, "CE", spot, stop, spot + risk * cfg.min_rr, regime, round(min(bullish_score, 0.99), 3), " | ".join(bullish_reasons))

    if bearish_score >= cfg.confidence_threshold and bearish_score > bullish_score:
        stop = max(float(row["High"]), float(row["vwap"]))
        risk = max(stop - spot, 0.5)
        return Signal(ts, "PE", spot, stop, spot - risk * cfg.min_rr, regime, round(min(bearish_score, 0.99), 3), " | ".join(bearish_reasons))

    return None


# -----------------------------
# Backtest engine
# -----------------------------
def derive_daily_vix_map(vix_df: pd.DataFrame) -> dict:
    if vix_df.empty:
        return {}
    temp = vix_df.copy()
    temp.index = pd.to_datetime(temp.index)
    return {d.date(): float(c) for d, c in zip(temp.index, temp["Close"])}


def backtest_strategy(price_df: pd.DataFrame, vix_df: pd.DataFrame, cfg: StrategyConfig, capital: float = 100000.0) -> tuple[pd.DataFrame, dict]:
    if price_df.empty:
        return pd.DataFrame(), {}

    vix_map = derive_daily_vix_map(vix_df)
    x = enrich_price_data(price_df, cfg)
    trades = []
    open_trade = None
    current_capital = capital
    peak_capital = capital
    max_drawdown = 0.0
    daily_trade_counts = {}
    daily_pnl = {}

    for i in range(max(cfg.slow_ema + 5, cfg.bb_period + 5), len(x)):
        now = x.index[i]
        day = pd.Timestamp(now).date()
        daily_trade_counts.setdefault(day, 0)
        daily_pnl.setdefault(day, 0.0)

        vix_now = vix_map.get(day, cfg.low_vix_threshold + 1)
        vix_prev = vix_map.get(day - timedelta(days=1), None)

        # manage open trade first
        if open_trade is not None:
            row = x.iloc[i]
            hit_exit = False
            exit_price = None
            reason = None

            if open_trade["side"] == "CE":
                if row["Low"] <= open_trade["stop_loss"]:
                    exit_price = open_trade["stop_loss"]
                    reason = "SL"
                    hit_exit = True
                elif row["High"] >= open_trade["target"]:
                    exit_price = open_trade["target"]
                    reason = "TARGET"
                    hit_exit = True
            else:
                if row["High"] >= open_trade["stop_loss"]:
                    exit_price = open_trade["stop_loss"]
                    reason = "SL"
                    hit_exit = True
                elif row["Low"] <= open_trade["target"]:
                    exit_price = open_trade["target"]
                    reason = "TARGET"
                    hit_exit = True

            end_of_day = i + 1 < len(x) and pd.Timestamp(x.index[i + 1]).date() != day
            if not hit_exit and end_of_day:
                exit_price = float(row["Close"])
                reason = "EOD"
                hit_exit = True

            if hit_exit:
                pnl_per_unit = (exit_price - open_trade["entry"]) if open_trade["side"] == "CE" else (open_trade["entry"] - exit_price)
                gross_pnl = pnl_per_unit * open_trade["qty"]
                current_capital += gross_pnl
                daily_pnl[day] += gross_pnl
                peak_capital = max(peak_capital, current_capital)
                dd = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown = max(max_drawdown, dd)
                trades.append({
                    "entry_time": open_trade["timestamp"],
                    "exit_time": now,
                    "side": open_trade["side"],
                    "regime": open_trade["regime"],
                    "entry": open_trade["entry"],
                    "exit": exit_price,
                    "qty": open_trade["qty"],
                    "gross_pnl": gross_pnl,
                    "reason": open_trade["reason"],
                    "exit_reason": reason,
                    "capital_after": current_capital,
                })
                open_trade = None

        # new entries
        if open_trade is None:
            if daily_trade_counts[day] >= cfg.max_trades_per_day:
                continue
            if daily_pnl[day] <= -(current_capital * cfg.max_daily_loss_pct / 100):
                continue

            sub_df = x.iloc[: i + 1][["Open", "High", "Low", "Close", "Volume"]].copy()
            sig = build_signal(sub_df, vix_now, vix_prev, cfg)
            if sig is None:
                continue

            risk_amt = current_capital * cfg.risk_per_trade_pct / 100
            per_unit_risk = max(abs(sig.entry - sig.stop_loss), 0.5)
            raw_qty = math.floor(risk_amt / per_unit_risk)
            lots = max(raw_qty // cfg.lot_size, 1)
            qty = lots * cfg.lot_size

            open_trade = {
                "timestamp": now,
                "side": sig.side,
                "entry": sig.entry,
                "stop_loss": sig.stop_loss,
                "target": sig.target,
                "qty": qty,
                "regime": sig.regime,
                "reason": sig.reason,
            }
            daily_trade_counts[day] += 1

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return trades_df, {
            "total_trades": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
        }

    wins = trades_df[trades_df["gross_pnl"] > 0]
    losses = trades_df[trades_df["gross_pnl"] <= 0]
    gross_profit = wins["gross_pnl"].sum()
    gross_loss = abs(losses["gross_pnl"].sum())
    stats = {
        "total_trades": int(len(trades_df)),
        "win_rate": float((trades_df["gross_pnl"] > 0).mean()),
        "net_pnl": float(trades_df["gross_pnl"].sum()),
        "return_pct": float((current_capital - capital) / capital),
        "max_drawdown_pct": float(max_drawdown),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
    }
    return trades_df, stats


# -----------------------------
# Risk management
# -----------------------------
def calculate_position_size(capital: float, risk_pct: float, entry: float, stop_loss: float, lot_size: int = 75) -> int:
    max_risk_rupees = capital * (risk_pct / 100)
    per_unit_risk = max(abs(entry - stop_loss), 0.5)
    raw_qty = math.floor(max_risk_rupees / per_unit_risk)
    lots = max(raw_qty // lot_size, 1)
    return lots * lot_size


def risk_checks(signal: Signal, capital: float, daily_realized_pnl: float, trades_today: int, cfg: StrategyConfig) -> tuple[bool, str]:
    if trades_today >= cfg.max_trades_per_day:
        return False, "Max trades reached"
    if daily_realized_pnl <= -(capital * cfg.max_daily_loss_pct / 100):
        return False, "Daily loss limit breached"
    if abs(signal.entry - signal.stop_loss) < 0.5:
        return False, "Stop distance too tight"
    return True, "OK"


# -----------------------------
# Data fetch
# -----------------------------
@st.cache_data(ttl=60)
def load_price_data(symbol: str, interval: str, period: str) -> pd.DataFrame:
    df = yf.download(symbol, interval=interval, period=period, auto_adjust=True, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df.dropna().copy()


@st.cache_data(ttl=900)
def load_vix_data(symbol: str = "^INDIAVIX", period: str = "1y") -> pd.DataFrame:
    df = yf.download(symbol, interval="1d", period=period, auto_adjust=True, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df.dropna().copy()


def supported_backtest_periods(interval: str) -> list[str]:
    # Yahoo Finance intraday intervals are limited to roughly the last 60 days.
    if interval in {"5m", "15m", "30m"}:
        return ["5d", "1mo"]
    return ["1mo", "3mo", "6mo"]


# -----------------------------
# Session state
# -----------------------------
def init_state() -> None:
    if "positions" not in st.session_state:
        st.session_state.positions = []
    if "trade_log" not in st.session_state:
        st.session_state.trade_log = []
    if "daily_pnl" not in st.session_state:
        st.session_state.daily_pnl = 0.0
    if "last_signal_key" not in st.session_state:
        st.session_state.last_signal_key = None


# -----------------------------
# UI
# -----------------------------
def main() -> None:
    init_state()

    st.title("NIFTY Live Trading System")
    st.caption("Live CE/PE signal engine + strike selection + backtest engine + Dhan execution scaffold.")

    with st.sidebar:
        st.header("Trading Controls")
        mode: TradeMode = st.radio("Trading Mode", ["PAPER", "LIVE"], index=0, horizontal=True)
        if mode == "LIVE":
            st.warning("⚠️ LIVE MODE ENABLED: Real trades will be executed. Start with small capital.")
        capital = st.number_input("Capital (₹)", min_value=10000.0, value=100000.0, step=10000.0)
        risk_pct = st.slider("Risk per trade (%)", 0.25, 3.0, 1.0, 0.25)
        max_trades = st.slider("Max trades per day", 1, 10, 3)
        confidence = st.slider("Minimum confidence", 0.50, 0.95, 0.72, 0.01)
        low_vix = st.slider("Low VIX threshold", 10.0, 18.0, 14.0, 0.5)
        high_vix = st.slider("High VIX threshold", 14.0, 25.0, 18.0, 0.5)
        interval = st.selectbox("Live bar interval", ["5m", "15m", "30m"], index=0)
        period = st.selectbox("Live history period", ["5d", "10d", "1mo"], index=1)
        strike_mode = st.selectbox("Strike selection", ["ATM", "ITM1", "OTM1"], index=0)
        expiry_code = st.text_input("Expiry code", value=infer_nearest_weekly_expiry())
        refresh = st.button("Refresh Signals", use_container_width=True)
        auto_refresh = st.checkbox("Auto-refresh every 60 sec", value=False)

    cfg = StrategyConfig(
        bar_interval=interval,
        history_period=period,
        risk_per_trade_pct=risk_pct,
        max_trades_per_day=max_trades,
        confidence_threshold=confidence,
        low_vix_threshold=low_vix,
        high_vix_threshold=high_vix,
        allow_live_orders=(mode == "LIVE"),
        option_moneyness=strike_mode,
    )

    broker: BrokerInterface = PaperBroker() if mode == "PAPER" else DhanBroker()

    live_tab, backtest_tab, notes_tab = st.tabs(["Live Signals", "Backtest", "Live Wiring Notes"])

    with live_tab:
        price_df = load_price_data(cfg.symbol, cfg.bar_interval, cfg.history_period)
        vix_df = load_vix_data(cfg.vix_symbol)

        if price_df.empty:
            st.error("Could not load NIFTY data.")
            return

        if vix_df.empty:
            st.warning("Could not load India VIX. Falling back to neutral assumptions.")
            vix_now = (cfg.low_vix_threshold + cfg.high_vix_threshold) / 2
            vix_prev = None
        else:
            vix_now = float(vix_df["Close"].iloc[-1])
            vix_prev = float(vix_df["Close"].iloc[-2]) if len(vix_df) > 1 else None

        signal = build_signal(price_df, vix_now, vix_prev, cfg)
        current_regime = detect_regime(vix_now, vix_prev, cfg)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Spot", f"{price_df['Close'].iloc[-1]:,.2f}")
        c2.metric("India VIX", f"{vix_now:.2f}")
        c3.metric("Regime", current_regime)
        c4.metric("Daily P&L", f"₹{st.session_state.daily_pnl:,.2f}")

        st.markdown("### Live Signal")
        if signal is None:
            st.info("No valid CE/PE setup right now. Stay flat.")
        else:
            option_symbol = make_option_symbol(signal.entry, signal.side, expiry_code, cfg.option_moneyness, cfg.strike_step)
            trades_today = sum(1 for t in st.session_state.trade_log if str(datetime.now().date()) in str(t.get("timestamp", "")))
            ok, reason = risk_checks(signal, capital, st.session_state.daily_pnl, trades_today, cfg)
            qty = calculate_position_size(capital, cfg.risk_per_trade_pct, signal.entry, signal.stop_loss, cfg.lot_size)

            with st.container(border=True):
                st.subheader(f"{signal.side} Signal")
                st.write(f"**Confidence:** {signal.confidence:.2f}")
                st.write(f"**Regime:** {signal.regime}")
                st.write(f"**Entry:** {signal.entry:.2f}")
                st.write(f"**Stop Loss:** {signal.stop_loss:.2f}")
                st.write(f"**Target:** {signal.target:.2f}")
                st.write(f"**Suggested Qty:** {qty}")
                st.write(f"**Option Symbol:** `{option_symbol}`")
                st.write(f"**Reason:** {signal.reason}")
                st.write(f"**Risk Check:** {reason}")

                signal_key = f"{signal.timestamp}_{signal.side}_{round(signal.entry, 2)}"
                can_fire = ok and signal_key != st.session_state.last_signal_key
                if st.button(f"Execute {mode} Order", disabled=not can_fire):
                    try:
                        resp = broker.place_order(signal.side, qty, option_symbol, mode)
                        st.session_state.last_signal_key = signal_key
                        st.session_state.positions.append(Position(
                            side=signal.side,
                            entry=signal.entry,
                            stop_loss=signal.stop_loss,
                            target=signal.target,
                            qty=qty,
                            opened_at=datetime.now(),
                            mode=mode,
                            reason=signal.reason,
                            option_symbol=option_symbol,
                        ))
                        st.session_state.trade_log.append(resp)
                        st.success(f"Order sent: {resp}")
                    except Exception as exc:
                        st.error(f"Execution failed: {exc}")

        st.markdown("### Open Positions")
        if not st.session_state.positions:
            st.write("No open positions.")
        else:
            current_spot = float(price_df["Close"].iloc[-1])
            rows = []
            for idx, pos in enumerate(st.session_state.positions):
                pnl = (current_spot - pos.entry) * pos.qty if pos.side == "CE" else (pos.entry - current_spot) * pos.qty
                rows.append({
                    "#": idx,
                    "Side": pos.side,
                    "Entry": pos.entry,
                    "SL": pos.stop_loss,
                    "Target": pos.target,
                    "Qty": pos.qty,
                    "Mode": pos.mode,
                    "Option": pos.option_symbol,
                    "Opened": pos.opened_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "Est. PnL": round(pnl, 2),
                    "Reason": pos.reason[:120],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

            exit_index = st.number_input("Exit position #", min_value=0, max_value=max(len(st.session_state.positions) - 1, 0), value=0, step=1)
            if st.button("Exit Selected Position"):
                if 0 <= exit_index < len(st.session_state.positions):
                    pos = st.session_state.positions.pop(exit_index)
                    try:
                        resp = broker.exit_order(pos.option_symbol, pos.qty, pos.mode)
                        current_spot = float(price_df["Close"].iloc[-1])
                        realized = (current_spot - pos.entry) * pos.qty if pos.side == "CE" else (pos.entry - current_spot) * pos.qty
                        st.session_state.daily_pnl += realized
                        st.session_state.trade_log.append({**resp, "realized_pnl": realized})
                        st.success(f"Exited. Realized P&L: ₹{realized:,.2f}")
                    except Exception as exc:
                        st.error(f"Exit failed: {exc}")

        st.markdown("### Trade Log")
        if st.session_state.trade_log:
            st.dataframe(pd.DataFrame(st.session_state.trade_log), use_container_width=True)
        else:
            st.write("No trades yet.")

        st.markdown("### Recent Price Data")
        st.line_chart(price_df[["Close"]].tail(100))

        if auto_refresh:
            st.caption("Auto-refresh enabled. Reloading in 60 seconds.")
            time.sleep(60)
            st.rerun()

    with backtest_tab:
        st.markdown("### Backtest Engine")
        bt_interval = st.selectbox("Backtest interval", ["5m", "15m", "30m"], index=0, key="bt_interval")
        bt_period_options = supported_backtest_periods(bt_interval)
        default_bt_period = "1mo" if "1mo" in bt_period_options else bt_period_options[0]
        bt_period = st.selectbox(
            "Backtest period",
            bt_period_options,
            index=bt_period_options.index(default_bt_period),
            key="bt_period",
        )
        run_bt = st.button("Run Backtest", use_container_width=True)

        if run_bt:
            bt_price = load_price_data(cfg.symbol, bt_interval, bt_period)
            bt_vix = load_vix_data(cfg.vix_symbol)
            bt_cfg = StrategyConfig(**{**cfg.__dict__, "bar_interval": bt_interval, "history_period": bt_period})
            if bt_price.empty:
                st.warning(
                    f"No backtest price data was returned for interval `{bt_interval}` and period `{bt_period}`. "
                    "Yahoo Finance only provides intraday data for a limited recent window."
                )
            else:
                with st.spinner("Running backtest..."):
                    trades_df, stats = backtest_strategy(bt_price, bt_vix, bt_cfg, capital)
                if not stats:
                    st.warning("No backtest output.")
                else:
                    k1, k2, k3, k4, k5 = st.columns(5)
                    k1.metric("Trades", stats["total_trades"])
                    k2.metric("Win Rate", f"{stats['win_rate'] * 100:.1f}%")
                    k3.metric("Net P&L", f"₹{stats['net_pnl']:,.2f}")
                    k4.metric("Return", f"{stats['return_pct'] * 100:.2f}%")
                    pf = stats['profit_factor'] if math.isfinite(stats['profit_factor']) else 999.0
                    k5.metric("Profit Factor", f"{pf:.2f}")
                    st.write(f"**Max Drawdown:** {stats['max_drawdown_pct'] * 100:.2f}%")
                    if not trades_df.empty:
                        st.dataframe(trades_df.tail(100), use_container_width=True)
                        equity = trades_df[["exit_time", "capital_after"]].copy()
                        equity = equity.set_index("exit_time")
                        st.line_chart(equity)
                    else:
                        st.info("No trades generated in the selected backtest window.")

    with notes_tab:
        st.markdown("### Dhan live execution wiring")
        st.code(
            """
1. Add your credentials to Streamlit secrets:
   [dhan]
   client_id = "YOUR_CLIENT_ID"
   access_token = "YOUR_ACCESS_TOKEN"

2. In DhanBroker.__init__ load them:
   self.client_id = st.secrets['dhan']['client_id']
   self.access_token = st.secrets['dhan']['access_token']

3. Resolve option_symbol to a broker tradable instrument/security id.

4. Implement:
   - place_order(side, qty, option_symbol, mode)
   - exit_order(option_symbol, qty, mode)

5. Keep PAPER mode until:
   - strike mapping is verified
   - order placement is verified
   - exit flow is verified
   - your risk limits are tested
            """.strip(),
            language="python",
        )


if __name__ == "__main__":
    main()
