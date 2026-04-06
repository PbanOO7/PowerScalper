from __future__ import annotations

import hmac
import math
import os
import time
import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

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
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN_TIME = "09:15"
MARKET_CLOSE_TIME = "15:30"
HELP_URL = "https://github.com/PbanOO7/PowerScalper#readme"

INSTRUMENTS = {
    "NIFTY 50": {
        "underlying_security_id": 26000,
        "option_prefix": "NIFTY",
        "underlying_symbol": "NIFTY",
        "order_exchange_segment": "NSE_FNO",
        "underlying_exchange_segment": "IDX_I",
        "lot_size": 65,
        "strike_step": 50,
        "expiry_weekday": 1,
    },
    "BANKNIFTY": {
        "underlying_security_id": 26009,
        "option_prefix": "BANKNIFTY",
        "underlying_symbol": "BANKNIFTY",
        "order_exchange_segment": "NSE_FNO",
        "underlying_exchange_segment": "IDX_I",
        "lot_size": 30,
        "strike_step": 100,
        "expiry_weekday": 1,
    },
    "FINNIFTY": {
        "underlying_security_id": 26037,
        "option_prefix": "FINNIFTY",
        "underlying_symbol": "FINNIFTY",
        "order_exchange_segment": "NSE_FNO",
        "underlying_exchange_segment": "IDX_I",
        "lot_size": 60,
        "strike_step": 50,
        "expiry_weekday": 1,
    },
    "SENSEX": {
        "underlying_security_id": 1,
        "option_prefix": "SENSEX",
        "underlying_symbol": "SENSEX",
        "order_exchange_segment": "BSE_FNO",
        "underlying_exchange_segment": "IDX_I",
        "lot_size": 20,
        "strike_step": 100,
        "expiry_weekday": 3,
    },
}

VIX_META = {
    "security_id": 21,
    "exchange_segment": "IDX_I",
    "instrument": "INDEX",
}


def now_ist() -> datetime:
    return datetime.now(IST)


def format_ist_timestamp(value: object, fmt: str = "%Y-%m-%d %H:%M:%S IST") -> str:
    if value is None or value == "":
        return ""

    ts: Optional[datetime] = None
    if isinstance(value, pd.Timestamp):
        ts = value.to_pydatetime()
    elif isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value)
        except ValueError:
            parsed = pd.to_datetime(value, errors="coerce")
            if pd.isna(parsed):
                return str(value)
            ts = parsed.to_pydatetime()

    if ts is None:
        return str(value)

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    else:
        ts = ts.astimezone(IST)
    return ts.strftime(fmt)


def period_to_dates(period: str) -> tuple[datetime, datetime]:
    now = now_ist()
    mapping = {
        "5d": timedelta(days=5),
        "10d": timedelta(days=10),
        "1mo": timedelta(days=30),
        "3mo": timedelta(days=90),
        "6mo": timedelta(days=180),
        "1y": timedelta(days=365),
    }
    delta = mapping.get(period, timedelta(days=30))
    return now - delta, now


def normalize_intraday_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    x = df.copy()
    idx = pd.to_datetime(x.index)
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize(IST)
    else:
        idx = idx.tz_convert(IST)

    x.index = idx
    x = x.sort_index()
    x = x[x.index.dayofweek < 5]
    x = x.between_time(MARKET_OPEN_TIME, MARKET_CLOSE_TIME)
    return x


def read_secret(section: str, key: str) -> Optional[str]:
    try:
        if section in st.secrets and key in st.secrets[section]:
            value = st.secrets[section][key]
            return str(value).strip() if value else None
    except Exception:
        pass
    return None


def auth_credentials() -> tuple[Optional[str], Optional[str]]:
    return read_secret("auth", "username"), read_secret("auth", "password")


def render_login_shell() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(202, 138, 4, 0.16), transparent 28%),
                radial-gradient(circle at 85% 15%, rgba(15, 118, 110, 0.18), transparent 26%),
                linear-gradient(135deg, #f7f1e3 0%, #efe7d4 45%, #e5dcc8 100%);
        }
        [data-testid="stHeader"] {
            background: transparent;
        }
        [data-testid="stSidebar"] {
            display: none;
        }
        .login-stage {
            padding: 2.5rem 0 1rem 0;
            animation: fade-slide 0.8s ease-out;
        }
        .login-brand {
            padding: 2rem 2.2rem;
            border-radius: 28px;
            min-height: 540px;
            color: #1f2937;
            background:
                linear-gradient(160deg, rgba(255,255,255,0.72), rgba(255,248,235,0.84)),
                linear-gradient(135deg, #f4ede1, #f0e4cb);
            border: 1px solid rgba(120, 113, 108, 0.18);
            box-shadow: 0 28px 70px rgba(68, 64, 60, 0.12);
            position: relative;
            overflow: hidden;
        }
        .login-brand::after {
            content: "";
            position: absolute;
            width: 240px;
            height: 240px;
            right: -40px;
            bottom: -40px;
            border-radius: 999px;
            background: radial-gradient(circle, rgba(245, 158, 11, 0.20), rgba(245, 158, 11, 0));
            animation: float-orb 6s ease-in-out infinite;
        }
        .login-kicker {
            letter-spacing: 0.18em;
            text-transform: uppercase;
            font-size: 0.78rem;
            font-weight: 700;
            color: #b45309;
            margin-bottom: 1rem;
        }
        .login-title {
            font-family: Georgia, "Times New Roman", serif;
            font-size: 3.1rem;
            line-height: 1.02;
            font-weight: 700;
            color: #111827;
            margin-bottom: 1rem;
            max-width: 10ch;
        }
        .login-copy {
            font-size: 1rem;
            line-height: 1.75;
            color: #4b5563;
            max-width: 42ch;
            margin-bottom: 1.6rem;
        }
        .login-pill-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            margin-top: 1.25rem;
        }
        .login-pill {
            padding: 0.7rem 0.95rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(120, 113, 108, 0.14);
            color: #374151;
            font-size: 0.92rem;
            box-shadow: 0 10px 22px rgba(148, 163, 184, 0.08);
        }
        .login-card {
            padding: 2rem 1.8rem;
            border-radius: 28px;
            background: rgba(255, 255, 255, 0.92);
            border: 1px solid rgba(148, 163, 184, 0.18);
            box-shadow: 0 28px 70px rgba(51, 65, 85, 0.14);
            backdrop-filter: blur(12px);
            animation: fade-slide 0.9s ease-out;
        }
        .login-card h3 {
            margin: 0 0 0.35rem 0;
            font-size: 1.45rem;
            color: #111827;
        }
        .login-card p {
            margin: 0 0 1.25rem 0;
            color: #6b7280;
            line-height: 1.6;
        }
        .login-footer {
            margin-top: 1rem;
            color: #6b7280;
            font-size: 0.88rem;
        }
        @keyframes fade-slide {
            from {
                opacity: 0;
                transform: translateY(18px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        @keyframes float-orb {
            0%, 100% {
                transform: translateY(0px);
            }
            50% {
                transform: translateY(-14px);
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="login-stage">', unsafe_allow_html=True)
    left_col, right_col = st.columns([1.2, 0.9], gap="large")
    with left_col:
        st.markdown(
            """
            <div class="login-brand">
                <div class="login-kicker">Professional Access</div>
                <div class="login-title">PowerScalper Control Panel</div>
                <div class="login-copy">
                    Secure access to the strategy console, backtest engine, paper execution flow,
                    and live broker controls. Use your authorized credentials to continue.
                </div>
                <div class="login-pill-row">
                    <div class="login-pill">Signal Engine</div>
                    <div class="login-pill">Risk Controls</div>
                    <div class="login-pill">Paper and Live Modes</div>
                    <div class="login-pill">Broker Connectivity</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right_col:
        st.markdown(
            """
            <div class="login-card">
                <h3>Sign In</h3>
                <p>Only authorized users should have access to this trading environment.</p>
            """,
            unsafe_allow_html=True,
        )


def ensure_login() -> None:
    auth_user, auth_password = auth_credentials()
    if not auth_user or not auth_password:
        st.title("PowerScalper Login")
        st.error("App login is not configured. Add [auth].username and [auth].password to Streamlit secrets.")
        st.stop()

    if st.session_state.get("authenticated"):
        return

    render_login_shell()
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username", placeholder="Enter your user ID")
        password = st.text_input("Password", type="password", placeholder="Enter your password")
        submitted = st.form_submit_button("Login", use_container_width=True)

        if submitted:
            valid = hmac.compare_digest(username.strip(), auth_user) and hmac.compare_digest(password, auth_password)
            if valid:
                st.session_state.authenticated = True
                st.session_state.auth_username = auth_user
                st.rerun()
            st.error("Invalid username or password.")

    st.markdown(
        """
            <div class="login-footer">
                Protected deployment. Contact the administrator if you need access.
            </div>
        </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.stop()


@dataclass
class StrategyConfig:
    underlying_security_id: int = 26000
    instrument_name: str = "NIFTY 50"
    option_prefix: str = "NIFTY"
    underlying_symbol: str = "NIFTY"
    order_exchange_segment: str = "NSE_FNO"
    underlying_exchange_segment: str = "IDX_I"
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
    stop_loss_pct: float = 0.6
    target_pct: float = 1.2
    max_holding_minutes: int = 30
    max_daily_loss_pct: float = 5.0
    risk_per_trade_pct: float = 1.0
    max_capital_allocation_pct: float = 25.0
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 3
    confidence_threshold: float = 0.72
    min_vix_trade_threshold: float = 13.0
    allow_live_orders: bool = False
    lot_size: int = 75
    strike_step: int = 50
    option_moneyness: Literal["ATM", "ITM1", "OTM1"] = "ATM"
    use_regime_filter: bool = True
    use_volume_filter: bool = True
    use_bb_filter: bool = True
    backtest_slippage_pct: float = 0.0015
    backtest_cost_pct: float = 0.0010
    backtest_fixed_cost_per_order: float = 20.0
    option_price_factor_pct: float = 0.8


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
    option_entry: float
    option_stop_loss: float
    option_target: float


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
    security_id: Optional[str] = None
    exchange_segment: Optional[str] = None
    order_id: Optional[str] = None
    trailing_active: bool = False
    entry_spot: Optional[float] = None
    stop_loss_spot: Optional[float] = None
    target_spot: Optional[float] = None


# -----------------------------
# Broker abstraction
# -----------------------------
class BrokerInterface:
    def status(self) -> tuple[bool, str]:
        raise NotImplementedError

    def place_order(self, side: SignalSide, qty: int, option_symbol: str, mode: TradeMode) -> dict:
        raise NotImplementedError

    def exit_order(self, option_symbol: str, qty: int, mode: TradeMode) -> dict:
        raise NotImplementedError


class PaperBroker(BrokerInterface):
    def status(self) -> tuple[bool, str]:
        return True, "Paper execution ready."

    def place_order(self, side: SignalSide, qty: int, option_symbol: str, mode: TradeMode) -> dict:
        return {
            "status": "paper_filled",
            "side": side,
            "qty": qty,
            "symbol": option_symbol,
            "mode": mode,
            "timestamp": now_ist().isoformat(timespec="seconds"),
        }

    def exit_order(self, option_symbol: str, qty: int, mode: TradeMode) -> dict:
        return {
            "status": "paper_exit",
            "qty": qty,
            "symbol": option_symbol,
            "mode": mode,
            "timestamp": now_ist().isoformat(timespec="seconds"),
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

    base_url = "https://api.dhan.co/v2"

    def __init__(self, client_id: Optional[str] = None, access_token: Optional[str] = None):
        self.client_id = client_id or self._read_secret("dhan", "client_id") or os.getenv("DHAN_CLIENT_ID")
        self.access_token = access_token or self._read_secret("dhan", "access_token") or os.getenv("DHAN_ACCESS_TOKEN")
        self._profile_cache: Optional[dict] = None

    @staticmethod
    def _read_secret(section: str, key: str) -> Optional[str]:
        try:
            if section in st.secrets and key in st.secrets[section]:
                value = st.secrets[section][key]
                return str(value).strip() if value else None
        except Exception:
            pass
        return None

    def _headers(self, include_client_id: bool = False) -> dict:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": self.access_token or "",
        }
        if include_client_id:
            headers["client-id"] = self.client_id or ""
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[dict] = None,
        include_client_id: bool = False,
    ) -> dict:
        resp = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(include_client_id=include_client_id),
            json=payload,
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        if not resp.ok:
            detail = data.get("message") or data.get("remarks") or data.get("errorCode") or resp.text
            raise RuntimeError(f"Dhan API error {resp.status_code}: {detail}")
        return data

    @staticmethod
    def _parse_option_symbol(option_symbol: str) -> tuple[str, datetime.date, int, str]:
        try:
            prefix, expiry_code, strike, side = option_symbol.split("_")
            expiry_date = datetime.strptime(expiry_code.upper(), "%d%b%y").date()
            return prefix.upper(), expiry_date, int(strike), side.upper()
        except ValueError as exc:
            raise RuntimeError(f"Unsupported option symbol format: {option_symbol}") from exc

    def _instrument_meta_from_prefix(self, option_prefix: str) -> dict:
        for instrument in INSTRUMENTS.values():
            if instrument["option_prefix"].upper() == option_prefix.upper():
                return instrument
        raise RuntimeError(f"No Dhan instrument metadata configured for option prefix `{option_prefix}`.")

    def resolve_underlying_security_id(self, underlying_symbol: str) -> int:
        instruments = load_dhan_instrument_master()
        matches = instruments[instruments["UNDERLYING_SYMBOL"] == underlying_symbol.upper()].copy()
        if matches.empty:
            raise RuntimeError(f"No Dhan underlying security id found for `{underlying_symbol}`.")
        matches["UNDERLYING_SECURITY_ID"] = pd.to_numeric(matches["UNDERLYING_SECURITY_ID"], errors="coerce")
        matches = matches.dropna(subset=["UNDERLYING_SECURITY_ID"])
        if matches.empty:
            raise RuntimeError(f"Dhan instrument master does not contain a valid underlying security id for `{underlying_symbol}`.")
        return int(matches["UNDERLYING_SECURITY_ID"].iloc[0])

    def resolve_option_contract(self, option_symbol: str) -> dict:
        option_prefix, expiry_date, strike, side = self._parse_option_symbol(option_symbol)
        meta = self._instrument_meta_from_prefix(option_prefix)
        instruments = load_dhan_instrument_master()
        match = instruments[
            (instruments["UNDERLYING_SYMBOL"] == meta["underlying_symbol"])
            & (instruments["SM_EXPIRY_DATE"] == expiry_date)
            & (instruments["STRIKE_PRICE"] == float(strike))
            & (instruments["OPTION_TYPE"] == side)
        ]
        if match.empty:
            raise RuntimeError(
                f"No Dhan contract found for {option_symbol}. "
                "Check the expiry code, strike step, and whether the contract exists in Dhan's instrument master."
            )
        row = match.iloc[0]
        return {
            "security_id": str(row["SECURITY_ID"]),
            "exchange_segment": meta["order_exchange_segment"],
            "underlying_symbol": meta["underlying_symbol"],
            "expiry_date": str(expiry_date),
            "strike_price": strike,
            "option_type": side,
            "lot_size": int(float(row["LOT_SIZE"])),
        }

    def get_ltp(self, security_id: str, exchange_segment: str) -> float:
        data = self._request(
            "POST",
            "/marketfeed/ltp",
            payload={exchange_segment: [int(security_id)]},
            include_client_id=True,
        )
        segment_data = data.get("data", {}).get(exchange_segment, {})
        quote = segment_data.get(str(security_id), {})
        price = quote.get("last_price")
        if price is None:
            raise RuntimeError(f"No LTP returned for security_id {security_id} in segment {exchange_segment}.")
        return float(price)

    def get_option_chain_expiries(self, underlying_symbol: str, underlying_exchange_segment: str) -> list[str]:
        security_id = self.resolve_underlying_security_id(underlying_symbol)
        data = self._request(
            "POST",
            "/optionchain/expirylist",
            payload={
                "UnderlyingScrip": security_id,
                "UnderlyingSeg": underlying_exchange_segment,
            },
            include_client_id=True,
        )
        expiries = data.get("data", [])
        return [str(expiry) for expiry in expiries]

    def get_option_chain(self, underlying_symbol: str, underlying_exchange_segment: str, expiry: str) -> dict:
        security_id = self.resolve_underlying_security_id(underlying_symbol)
        return self._request(
            "POST",
            "/optionchain",
            payload={
                "UnderlyingScrip": security_id,
                "UnderlyingSeg": underlying_exchange_segment,
                "Expiry": expiry,
            },
            include_client_id=True,
        )

    def get_historical_data(
        self,
        security_id: int,
        exchange_segment: str,
        instrument: str,
        *,
        interval: Optional[str] = None,
        from_dt: datetime,
        to_dt: datetime,
        oi: bool = False,
    ) -> pd.DataFrame:
        path = "/charts/intraday" if interval else "/charts/historical"
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "oi": oi,
        }
        if interval:
            payload["interval"] = interval
            payload["fromDate"] = from_dt.strftime("%Y-%m-%d %H:%M:%S")
            payload["toDate"] = to_dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            payload["expiryCode"] = 0
            payload["fromDate"] = from_dt.strftime("%Y-%m-%d")
            payload["toDate"] = to_dt.strftime("%Y-%m-%d")

        data = self._request("POST", path, payload=payload)
        frame = pd.DataFrame({
            "Open": data.get("open", []),
            "High": data.get("high", []),
            "Low": data.get("low", []),
            "Close": data.get("close", []),
            "Volume": data.get("volume", []),
            "OpenInterest": data.get("open_interest", []),
        })
        timestamps = data.get("timestamp", [])
        if frame.empty or not timestamps:
            return pd.DataFrame()
        frame.index = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(IST)
        return frame

    def status(self) -> tuple[bool, str]:
        if not self.client_id or not self.access_token:
            return False, (
                "Missing Dhan credentials. Add [dhan].client_id and [dhan].access_token "
                "to Streamlit secrets or set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN."
            )
        try:
            if self._profile_cache is None:
                self._profile_cache = self._request("GET", "/profile")
            client = self._profile_cache.get("dhanClientId", self.client_id)
            return True, (
                f"Authenticated with Dhan client {client}. "
                "Order APIs may still require a Dhan-whitelisted static IP at runtime."
            )
        except Exception as exc:
            return False, f"Dhan authentication failed: {exc}"

    def preview_order_payload(self, side: SignalSide, qty: int, option_symbol: str) -> dict:
        contract = self.resolve_option_contract(option_symbol)
        return {
            "broker": "dhan",
            "transactionType": "BUY",
            "qty": qty,
            "option_symbol": option_symbol,
            "securityId": contract["security_id"],
            "exchangeSegment": contract["exchange_segment"],
            "order_type": "MARKET",
            "product_type": "INTRADAY",
        }

    def place_order(self, side: SignalSide, qty: int, option_symbol: str, mode: TradeMode) -> dict:
        if mode != "LIVE":
            return {"status": "blocked", "reason": "LIVE mode not enabled"}
        ready, reason = self.status()
        if not ready:
            raise RuntimeError(reason)
        contract = self.resolve_option_contract(option_symbol)
        payload = {
            "dhanClientId": self.client_id,
            "correlationId": f"{contract['underlying_symbol'][:8]}-{uuid.uuid4().hex[:20]}",
            "transactionType": "BUY",
            "exchangeSegment": contract["exchange_segment"],
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": contract["security_id"],
            "quantity": int(qty),
            "disclosedQuantity": 0,
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
            "amoTime": "OPEN",
        }
        data = self._request("POST", "/orders", payload=payload)
        return {
            "status": "accepted",
            "broker_status": data.get("orderStatus"),
            "order_id": data.get("orderId"),
            "transactionType": "BUY",
            "qty": qty,
            "symbol": option_symbol,
            "security_id": contract["security_id"],
            "exchange_segment": contract["exchange_segment"],
            "timestamp": now_ist().isoformat(timespec="seconds"),
            "raw": data,
        }

    def exit_order(self, option_symbol: str, qty: int, mode: TradeMode) -> dict:
        if mode != "LIVE":
            return {"status": "blocked", "reason": "LIVE mode not enabled"}
        ready, reason = self.status()
        if not ready:
            raise RuntimeError(reason)
        contract = self.resolve_option_contract(option_symbol)
        payload = {
            "dhanClientId": self.client_id,
            "correlationId": f"EXIT-{uuid.uuid4().hex[:20]}",
            "transactionType": "SELL",
            "exchangeSegment": contract["exchange_segment"],
            "productType": "INTRADAY",
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": contract["security_id"],
            "quantity": int(qty),
            "disclosedQuantity": 0,
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
            "amoTime": "OPEN",
        }
        data = self._request("POST", "/orders", payload=payload)
        return {
            "status": "accepted",
            "broker_status": data.get("orderStatus"),
            "order_id": data.get("orderId"),
            "transactionType": "SELL",
            "qty": qty,
            "symbol": option_symbol,
            "security_id": contract["security_id"],
            "exchange_segment": contract["exchange_segment"],
            "timestamp": now_ist().isoformat(timespec="seconds"),
            "raw": data,
        }


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


def make_option_symbol(
    spot: float,
    side: SignalSide,
    expiry_code: str,
    strike_mode: str,
    step: int,
    option_prefix: str,
) -> str:
    strike = choose_strike(spot, side, strike_mode, step)
    return f"{option_prefix}_{expiry_code}_{strike}_{side}"


def infer_nearest_weekly_expiry(now: Optional[datetime] = None, expiry_weekday: int = 3) -> str:
    now = now or now_ist()
    days_ahead = (expiry_weekday - now.weekday()) % 7
    expiry = now + timedelta(days=days_ahead)
    return expiry.strftime("%d%b%y").upper()


# -----------------------------
# Signal engine
# -----------------------------
def build_signal(df: pd.DataFrame, vix_now: float, vix_prev: Optional[float], cfg: StrategyConfig) -> Optional[Signal]:
    if df.empty or len(df) < max(cfg.slow_ema + 5, cfg.bb_period + 5):
        return None
    if vix_now < cfg.min_vix_trade_threshold:
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
    ts = row.name.to_pydatetime() if hasattr(row.name, "to_pydatetime") else now_ist()
    option_entry = estimated_option_price(spot, spot, "CE", cfg)
    option_stop, option_target = premium_stop_target(option_entry, cfg)

    if bullish_score >= cfg.confidence_threshold and bullish_score > bearish_score:
        bullish_reasons.append(f"Premium SL {cfg.stop_loss_pct:.1f}% / target {cfg.target_pct:.1f}%")
        return Signal(
            ts,
            "CE",
            spot,
            float(row["Low"]),
            float(row["High"]),
            regime,
            round(min(bullish_score, 0.99), 3),
            " | ".join(bullish_reasons),
            option_entry,
            option_stop,
            option_target,
        )

    if bearish_score >= cfg.confidence_threshold and bearish_score > bullish_score:
        option_entry = estimated_option_price(spot, spot, "PE", cfg)
        option_stop, option_target = premium_stop_target(option_entry, cfg)
        bearish_reasons.append(f"Premium SL {cfg.stop_loss_pct:.1f}% / target {cfg.target_pct:.1f}%")
        return Signal(
            ts,
            "PE",
            spot,
            float(row["High"]),
            float(row["Low"]),
            regime,
            round(min(bearish_score, 0.99), 3),
            " | ".join(bearish_reasons),
            option_entry,
            option_stop,
            option_target,
        )

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


def apply_backtest_slippage(price: float, side: SignalSide, is_entry: bool, slippage_pct: float) -> float:
    slip = max(slippage_pct, 0.0)
    if side == "CE":
        return price * (1 + slip) if is_entry else price * (1 - slip)
    return price * (1 - slip) if is_entry else price * (1 + slip)


def estimate_backtest_costs(entry_price: float, exit_price: float, qty: int, cfg: StrategyConfig) -> float:
    turnover = (abs(entry_price) + abs(exit_price)) * max(qty, 0)
    variable_cost = turnover * max(cfg.backtest_cost_pct, 0.0)
    fixed_cost = max(cfg.backtest_fixed_cost_per_order, 0.0) * 2
    return variable_cost + fixed_cost


def option_chain_to_dataframe(chain_response: dict) -> pd.DataFrame:
    oc = chain_response.get("data", {}).get("oc", {})
    rows = []
    for strike, node in oc.items():
        ce = node.get("ce", {}) or {}
        pe = node.get("pe", {}) or {}
        rows.append({
            "strike": float(strike),
            "ce_ltp": ce.get("last_price"),
            "ce_oi": ce.get("oi"),
            "ce_volume": ce.get("volume"),
            "ce_iv": ce.get("implied_volatility"),
            "ce_bid": ce.get("top_bid_price"),
            "ce_ask": ce.get("top_ask_price"),
            "ce_security_id": ce.get("security_id"),
            "ce_delta": (ce.get("greeks") or {}).get("delta"),
            "pe_ltp": pe.get("last_price"),
            "pe_oi": pe.get("oi"),
            "pe_volume": pe.get("volume"),
            "pe_iv": pe.get("implied_volatility"),
            "pe_bid": pe.get("top_bid_price"),
            "pe_ask": pe.get("top_ask_price"),
            "pe_security_id": pe.get("security_id"),
            "pe_delta": (pe.get("greeks") or {}).get("delta"),
        })
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
    numeric_cols = [col for col in frame.columns if col != "ce_security_id" and col != "pe_security_id"]
    frame[numeric_cols] = frame[numeric_cols].apply(pd.to_numeric, errors="coerce")
    return frame


def option_chain_summary(chain_response: dict, chain_df: pd.DataFrame) -> dict:
    last_price = float(chain_response.get("data", {}).get("last_price", 0.0))
    if chain_df.empty:
        return {
            "last_price": last_price,
            "atm_strike": None,
            "pcr_oi": 0.0,
            "pcr_volume": 0.0,
            "max_call_oi_strike": None,
            "max_put_oi_strike": None,
        }
    ce_oi_total = float(chain_df["ce_oi"].fillna(0).sum())
    pe_oi_total = float(chain_df["pe_oi"].fillna(0).sum())
    ce_vol_total = float(chain_df["ce_volume"].fillna(0).sum())
    pe_vol_total = float(chain_df["pe_volume"].fillna(0).sum())
    atm_idx = (chain_df["strike"] - last_price).abs().idxmin()
    max_call_idx = chain_df["ce_oi"].fillna(-1).idxmax()
    max_put_idx = chain_df["pe_oi"].fillna(-1).idxmax()
    return {
        "last_price": last_price,
        "atm_strike": float(chain_df.loc[atm_idx, "strike"]),
        "pcr_oi": pe_oi_total / ce_oi_total if ce_oi_total > 0 else 0.0,
        "pcr_volume": pe_vol_total / ce_vol_total if ce_vol_total > 0 else 0.0,
        "max_call_oi_strike": float(chain_df.loc[max_call_idx, "strike"]) if max_call_idx in chain_df.index else None,
        "max_put_oi_strike": float(chain_df.loc[max_put_idx, "strike"]) if max_put_idx in chain_df.index else None,
    }


def estimated_option_price(spot_now: float, spot_entry: float, side: SignalSide, cfg: StrategyConfig) -> float:
    base_premium = max(spot_entry * (cfg.option_price_factor_pct / 100), 20.0)
    intrinsic = max(spot_now - spot_entry, 0.0) if side == "CE" else max(spot_entry - spot_now, 0.0)
    directional_move = (spot_now - spot_entry) if side == "CE" else (spot_entry - spot_now)
    premium = base_premium + (directional_move * 0.5) + (intrinsic * 0.15)
    return max(premium, 1.0)


def option_price_bounds(high_spot: float, low_spot: float, spot_entry: float, side: SignalSide, cfg: StrategyConfig) -> tuple[float, float]:
    price_at_high = estimated_option_price(high_spot, spot_entry, side, cfg)
    price_at_low = estimated_option_price(low_spot, spot_entry, side, cfg)
    return min(price_at_high, price_at_low), max(price_at_high, price_at_low)


def premium_stop_target(entry_premium: float, cfg: StrategyConfig) -> tuple[float, float]:
    stop_loss = entry_premium * (1 - cfg.stop_loss_pct / 100)
    target = entry_premium * (1 + cfg.target_pct / 100)
    return max(stop_loss, 1.0), max(target, 1.0)


def calculate_position_size(
    capital: float,
    risk_pct: float,
    entry_premium: float,
    stop_loss_premium: float,
    lot_size: int = 75,
    max_allocation_pct: float = 25.0,
    premium_per_unit: Optional[float] = None,
) -> int:
    bounded_risk_pct = min(max(risk_pct, 1.0), 2.0)
    max_risk_rupees = capital * (bounded_risk_pct / 100)
    per_unit_risk = max(abs(entry_premium - stop_loss_premium), 0.5)
    risk_limited_qty = math.floor(max_risk_rupees / per_unit_risk)

    if premium_per_unit is None:
        premium_per_unit = max(entry_premium, 1.0)
    alloc_budget = capital * max(max_allocation_pct, 0.0) / 100
    alloc_limited_qty = math.floor(alloc_budget / max(premium_per_unit, 1.0))

    raw_qty = min(risk_limited_qty, alloc_limited_qty)
    if raw_qty < lot_size:
        return 0
    lots = raw_qty // lot_size
    return lots * lot_size


def reset_daily_state_if_needed(capital: float) -> None:
    current_day = str(now_ist().date())
    if st.session_state.trade_day != current_day:
        st.session_state.trade_day = current_day
        st.session_state.daily_pnl = 0.0
        st.session_state.loss_streak = 0
        st.session_state.day_start_capital = capital


def loss_limit_breached(day_start_capital: float, daily_realized_pnl: float, cfg: StrategyConfig) -> bool:
    return daily_realized_pnl <= -(day_start_capital * cfg.max_daily_loss_pct / 100)


def close_position_and_record(
    pos: Position,
    exit_mark: float,
    exit_reason: str,
    broker_response: dict,
    *,
    costs: float = 0.0,
) -> float:
    realized = (exit_mark - pos.entry) * pos.qty
    realized -= costs
    st.session_state.daily_pnl += realized
    st.session_state.loss_streak = st.session_state.loss_streak + 1 if realized < 0 else 0
    st.session_state.trade_log.append({
        **broker_response,
        "side": pos.side,
        "qty": pos.qty,
        "symbol": pos.option_symbol,
        "entry_price": pos.entry,
        "exit_price": exit_mark,
        "exit_reason": exit_reason,
        "realized_pnl": realized,
        "costs": costs,
        "entry_spot": pos.entry_spot,
        "exit_time": now_ist().isoformat(timespec="seconds"),
    })
    return realized


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
    daily_loss_streak = {}
    day_start_capital = {}

    for i in range(max(cfg.slow_ema + 5, cfg.bb_period + 5), len(x)):
        now = x.index[i]
        day = pd.Timestamp(now).date()
        daily_trade_counts.setdefault(day, 0)
        daily_pnl.setdefault(day, 0.0)
        daily_loss_streak.setdefault(day, 0)
        day_start_capital.setdefault(day, current_capital)

        vix_now = vix_map.get(day, cfg.low_vix_threshold + 1)
        vix_prev = vix_map.get(day - timedelta(days=1), None)

        # manage open trade first
        if open_trade is not None:
            row = x.iloc[i]
            hit_exit = False
            exit_price = None
            reason = None

            if open_trade["side"] == "CE":
                option_low, option_high = option_price_bounds(float(row["High"]), float(row["Low"]), open_trade["entry_spot"], "CE", cfg)
                if option_low <= open_trade["stop_loss"]:
                    exit_price = open_trade["stop_loss"]
                    reason = "SL"
                    hit_exit = True
                elif option_high >= open_trade["target"]:
                    exit_price = open_trade["target"]
                    reason = "TARGET"
                    hit_exit = True
            else:
                option_low, option_high = option_price_bounds(float(row["High"]), float(row["Low"]), open_trade["entry_spot"], "PE", cfg)
                if option_low <= open_trade["stop_loss"]:
                    exit_price = open_trade["stop_loss"]
                    reason = "SL"
                    hit_exit = True
                elif option_high >= open_trade["target"]:
                    exit_price = open_trade["target"]
                    reason = "TARGET"
                    hit_exit = True

            end_of_day = i + 1 < len(x) and pd.Timestamp(x.index[i + 1]).date() != day
            held_minutes = (now - open_trade["timestamp"]).total_seconds() / 60
            timed_out = held_minutes >= cfg.max_holding_minutes
            if not hit_exit and end_of_day:
                exit_price = estimated_option_price(float(row["Close"]), open_trade["entry_spot"], open_trade["side"], cfg)
                reason = "EOD"
                hit_exit = True
            elif not hit_exit and timed_out:
                exit_price = estimated_option_price(float(row["Close"]), open_trade["entry_spot"], open_trade["side"], cfg)
                reason = "TIME"
                hit_exit = True

            if hit_exit:
                side = open_trade["side"]
                entry_exec = apply_backtest_slippage(open_trade["entry"], side, True, cfg.backtest_slippage_pct)
                exit_exec = apply_backtest_slippage(float(exit_price), side, False, cfg.backtest_slippage_pct)
                pnl_per_unit = exit_exec - entry_exec
                gross_pnl = pnl_per_unit * open_trade["qty"]
                total_cost = estimate_backtest_costs(entry_exec, exit_exec, open_trade["qty"], cfg)
                net_pnl = gross_pnl - total_cost
                current_capital += net_pnl
                daily_pnl[day] += net_pnl
                daily_loss_streak[day] = daily_loss_streak[day] + 1 if net_pnl < 0 else 0
                peak_capital = max(peak_capital, current_capital)
                dd = (peak_capital - current_capital) / peak_capital if peak_capital > 0 else 0
                max_drawdown = max(max_drawdown, dd)
                trades.append({
                    "entry_time": open_trade["timestamp"],
                    "exit_time": now,
                    "side": side,
                    "regime": open_trade["regime"],
                    "entry": open_trade["entry"],
                    "exit": float(exit_price),
                    "entry_exec": entry_exec,
                    "exit_exec": exit_exec,
                    "qty": open_trade["qty"],
                    "gross_pnl": gross_pnl,
                    "costs": total_cost,
                    "net_pnl": net_pnl,
                    "entry_spot": open_trade["entry_spot"],
                    "exit_spot": float(row["Close"]),
                    "reason": open_trade["reason"],
                    "exit_reason": reason,
                    "capital_after": current_capital,
                })
                open_trade = None

        # new entries
        if open_trade is None:
            if daily_trade_counts[day] >= cfg.max_trades_per_day:
                continue
            if loss_limit_breached(day_start_capital[day], daily_pnl[day], cfg):
                continue
            if daily_loss_streak[day] >= cfg.max_consecutive_losses:
                continue

            sub_df = x.iloc[: i + 1][["Open", "High", "Low", "Close", "Volume"]].copy()
            sig = build_signal(sub_df, vix_now, vix_prev, cfg)
            if sig is None:
                continue

            qty = calculate_position_size(
                current_capital,
                cfg.risk_per_trade_pct,
                sig.option_entry,
                sig.option_stop_loss,
                cfg.lot_size,
                cfg.max_capital_allocation_pct,
                sig.option_entry,
            )
            if qty <= 0:
                continue

            open_trade = {
                "timestamp": now,
                "side": sig.side,
                "entry": sig.option_entry,
                "stop_loss": sig.option_stop_loss,
                "target": sig.option_target,
                "qty": qty,
                "regime": sig.regime,
                "reason": sig.reason,
                "entry_spot": sig.entry,
            }
            daily_trade_counts[day] += 1

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return trades_df, {
            "total_trades": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "total_costs": 0.0,
            "net_pnl": 0.0,
            "return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
        }

    wins = trades_df[trades_df["net_pnl"] > 0]
    losses = trades_df[trades_df["net_pnl"] <= 0]
    gross_profit = wins["net_pnl"].sum()
    gross_loss = abs(losses["net_pnl"].sum())
    stats = {
        "total_trades": int(len(trades_df)),
        "win_rate": float((trades_df["net_pnl"] > 0).mean()),
        "gross_pnl": float(trades_df["gross_pnl"].sum()),
        "total_costs": float(trades_df["costs"].sum()),
        "net_pnl": float(trades_df["net_pnl"].sum()),
        "return_pct": float((current_capital - capital) / capital),
        "max_drawdown_pct": float(max_drawdown),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
    }
    return trades_df, stats


# -----------------------------
# Risk management
# -----------------------------
def risk_checks(
    signal: Signal,
    day_start_capital: float,
    daily_realized_pnl: float,
    trades_today: int,
    loss_streak: int,
    cfg: StrategyConfig,
) -> tuple[bool, str]:
    if not 1.0 <= cfg.risk_per_trade_pct <= 2.0:
        return False, "Risk per trade must stay between 1% and 2%"
    if trades_today >= cfg.max_trades_per_day:
        return False, "Max trades reached"
    if loss_limit_breached(day_start_capital, daily_realized_pnl, cfg):
        return False, "Daily loss limit breached"
    if loss_streak >= cfg.max_consecutive_losses:
        return False, "Consecutive loss kill switch active"
    if signal.confidence < cfg.confidence_threshold:
        return False, "Signal confidence below threshold"
    if abs(signal.option_entry - signal.option_stop_loss) < 0.5:
        return False, "Stop distance too tight"
    return True, "OK"


# -----------------------------
# Data fetch
# -----------------------------
@st.cache_data(ttl=60)
def load_price_data(
    security_id: int,
    exchange_segment: str,
    interval: str,
    period: str,
    instrument: str = "INDEX",
) -> pd.DataFrame:
    broker = DhanBroker()
    ready, _ = broker.status()
    if not ready:
        return pd.DataFrame()
    from_dt, to_dt = period_to_dates(period)
    requested_interval = interval
    fetch_interval = interval
    if interval == "30m":
        fetch_interval = "5"
    elif interval == "5m":
        fetch_interval = "5"
    elif interval == "15m":
        fetch_interval = "15"
    else:
        fetch_interval = interval.replace("m", "")

    df = broker.get_historical_data(
        security_id,
        exchange_segment,
        instrument,
        interval=fetch_interval,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    if df.empty:
        return df
    df = normalize_intraday_data(df)
    if requested_interval == "30m":
        df = (
            df.resample("30min", origin="start_day")
            .agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
                "OpenInterest": "last",
            })
            .dropna(subset=["Open", "High", "Low", "Close"])
        )
    return df


@st.cache_data(ttl=900)
def load_vix_data(period: str = "1y") -> pd.DataFrame:
    broker = DhanBroker()
    ready, _ = broker.status()
    if not ready:
        return pd.DataFrame()
    from_dt, to_dt = period_to_dates(period)
    df = broker.get_historical_data(
        VIX_META["security_id"],
        VIX_META["exchange_segment"],
        VIX_META["instrument"],
        from_dt=from_dt,
        to_dt=to_dt,
    )
    return df.dropna().copy()


@st.cache_data(ttl=3600)
def load_dhan_instrument_master() -> pd.DataFrame:
    url = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    cols = [
        "EXCH_ID",
        "SEGMENT",
        "SECURITY_ID",
        "UNDERLYING_SECURITY_ID",
        "UNDERLYING_SYMBOL",
        "DISPLAY_NAME",
        "LOT_SIZE",
        "SM_EXPIRY_DATE",
        "STRIKE_PRICE",
        "OPTION_TYPE",
        "EXPIRY_FLAG",
    ]
    df = pd.read_csv(io.StringIO(resp.text), usecols=cols, low_memory=False)
    df["UNDERLYING_SYMBOL"] = df["UNDERLYING_SYMBOL"].astype(str).str.upper()
    df["OPTION_TYPE"] = df["OPTION_TYPE"].astype(str).str.upper()
    df["SM_EXPIRY_DATE"] = pd.to_datetime(df["SM_EXPIRY_DATE"], errors="coerce").dt.date
    df["STRIKE_PRICE"] = pd.to_numeric(df["STRIKE_PRICE"], errors="coerce")
    df["UNDERLYING_SECURITY_ID"] = pd.to_numeric(df["UNDERLYING_SECURITY_ID"], errors="coerce")
    df["SECURITY_ID"] = df["SECURITY_ID"].astype("Int64").astype(str)
    return df


def supported_backtest_periods(interval: str) -> list[str]:
    # Dhan intraday history is fetched in minute bars and resampled locally when needed.
    if interval in {"5m", "15m", "30m"}:
        return ["5d", "1mo"]
    return ["1mo", "3mo", "6mo"]


# -----------------------------
# Session state
# -----------------------------
def init_state() -> None:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "positions" not in st.session_state:
        st.session_state.positions = []
    if "trade_log" not in st.session_state:
        st.session_state.trade_log = []
    if "daily_pnl" not in st.session_state:
        st.session_state.daily_pnl = 0.0
    if "last_signal_key" not in st.session_state:
        st.session_state.last_signal_key = None
    if "loss_streak" not in st.session_state:
        st.session_state.loss_streak = 0
    if "trade_day" not in st.session_state:
        st.session_state.trade_day = str(now_ist().date())
    if "day_start_capital" not in st.session_state:
        st.session_state.day_start_capital = 100000.0
    if "option_chain_payload" not in st.session_state:
        st.session_state.option_chain_payload = None
    if "option_chain_expiries" not in st.session_state:
        st.session_state.option_chain_expiries = []
    if "option_chain_instrument" not in st.session_state:
        st.session_state.option_chain_instrument = None
    if "option_chain_loaded_expiry" not in st.session_state:
        st.session_state.option_chain_loaded_expiry = None


# -----------------------------
# UI
# -----------------------------
def main() -> None:
    init_state()
    ensure_login()

    st.title("Index Live Trading System")
    st.caption("Live CE/PE signal engine + strike selection + backtest engine + Dhan execution scaffold.")

    with st.sidebar:
        st.header("Trading Controls")
        st.caption(f"Logged in as `{st.session_state.get('auth_username', 'user')}`")
        if st.button("Logout", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.auth_username = None
            st.rerun()
        instrument_name = st.selectbox("Instrument", list(INSTRUMENTS.keys()), index=0)
        instrument = INSTRUMENTS[instrument_name]
        inferred_expiry = infer_nearest_weekly_expiry(expiry_weekday=instrument["expiry_weekday"])
        if st.session_state.get("expiry_instrument") != instrument_name:
            st.session_state.expiry_code_input = inferred_expiry
            st.session_state.expiry_instrument = instrument_name
        mode: TradeMode = st.radio("Trading Mode", ["PAPER", "LIVE"], index=0, horizontal=True)
        live_confirmed = False
        if mode == "LIVE":
            st.warning("LIVE mode can place real broker orders.")
            live_confirmed = st.checkbox("I confirm live trading is intentional", value=False)
        capital = st.number_input("Capital (₹)", min_value=10000.0, value=100000.0, step=10000.0)
        sl_pct = st.slider("SL (%)", 20.0, 30.0, 25.0, 1.0)
        target_pct = st.slider("Target (%)", 40.0, 60.0, 50.0, 1.0)
        risk_pct = st.slider("Risk per trade (%)", 1.0, 2.0, 1.0, 0.25)
        max_hold_mins = st.slider("Max holding time (min)", 5, 180, 30, 5)
        daily_loss_limit = st.slider("Daily loss limit (%)", 1.0, 10.0, 5.0, 0.5)
        max_loss_streak = st.slider("Consecutive losses limit", 1, 5, 3, 1)
        max_alloc_pct = st.slider("Capital allocation cap (%)", 5.0, 100.0, 25.0, 5.0)
        max_trades = st.slider("Max trades per day", 1, 10, 3)
        confidence = st.slider("Minimum confidence", 0.50, 0.95, 0.72, 0.01)
        min_vix_trade = st.slider("VIX threshold", 10.0, 20.0, 13.0, 0.5)
        low_vix = st.slider("Low VIX regime threshold", 10.0, 18.0, 14.0, 0.5)
        high_vix = st.slider("High VIX threshold", 14.0, 25.0, 18.0, 0.5)
        interval = st.selectbox("Live bar interval", ["5m", "15m", "30m"], index=0)
        period = st.selectbox("Live history period", ["5d", "10d", "1mo"], index=1)
        strike_mode = st.selectbox("Strike selection", ["ATM", "ITM1", "OTM1"], index=0)
        expiry_label = "Tuesday" if instrument["expiry_weekday"] == 1 else "Thursday"
        expiry_code = st.text_input(
            "Expiry code",
            key="expiry_code_input",
            help=f"Default weekly expiry is based on {instrument_name}'s usual {expiry_label} expiry cycle.",
        )
        st.caption(
            f"Lot size: {instrument['lot_size']} | Strike step: {instrument['strike_step']} | "
            f"Dhan underlying security ID: `{instrument['underlying_security_id']}`"
        )
        st.markdown(f"[Help: How to use this app]({HELP_URL})")
        refresh = st.button("Refresh Signals", use_container_width=True)
        auto_refresh = st.checkbox("Auto-refresh every 60 sec", value=False)

    cfg = StrategyConfig(
        underlying_security_id=instrument["underlying_security_id"],
        instrument_name=instrument_name,
        option_prefix=instrument["option_prefix"],
        underlying_symbol=instrument["underlying_symbol"],
        order_exchange_segment=instrument["order_exchange_segment"],
        underlying_exchange_segment=instrument["underlying_exchange_segment"],
        lot_size=instrument["lot_size"],
        strike_step=instrument["strike_step"],
        bar_interval=interval,
        history_period=period,
        stop_loss_pct=sl_pct,
        target_pct=target_pct,
        max_holding_minutes=max_hold_mins,
        max_daily_loss_pct=daily_loss_limit,
        risk_per_trade_pct=risk_pct,
        max_capital_allocation_pct=max_alloc_pct,
        max_consecutive_losses=max_loss_streak,
        max_trades_per_day=max_trades,
        confidence_threshold=confidence,
        min_vix_trade_threshold=min_vix_trade,
        low_vix_threshold=low_vix,
        high_vix_threshold=high_vix,
        allow_live_orders=(mode == "LIVE" and live_confirmed),
        option_moneyness=strike_mode,
    )

    reset_daily_state_if_needed(capital)
    if st.session_state.day_start_capital <= 0:
        st.session_state.day_start_capital = capital

    broker: BrokerInterface = PaperBroker() if mode == "PAPER" else DhanBroker()
    live_ready, live_reason = broker.status()

    if st.session_state.option_chain_instrument != instrument_name:
        st.session_state.option_chain_instrument = instrument_name
        st.session_state.option_chain_payload = None
        st.session_state.option_chain_expiries = []
        st.session_state.option_chain_loaded_expiry = None

    live_tab, backtest_tab, chain_tab, notes_tab, help_tab = st.tabs(
        ["Live Signals", "Backtest", "Option Chain", "Live Wiring Notes", "Help"]
    )

    with live_tab:
        if mode == "LIVE":
            if live_ready:
                st.info(f"LIVE broker status: {live_reason}")
            else:
                st.error(f"LIVE broker status: {live_reason}")

        price_df = load_price_data(
            cfg.underlying_security_id,
            cfg.underlying_exchange_segment,
            cfg.bar_interval,
            cfg.history_period,
        )
        vix_df = load_vix_data()

        if price_df.empty:
            st.error(f"Could not load {cfg.instrument_name} price data from Dhan.")
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
        kill_switch_active = (
            loss_limit_breached(st.session_state.day_start_capital, st.session_state.daily_pnl, cfg)
            or st.session_state.loss_streak >= cfg.max_consecutive_losses
        )
        st.caption(
            f"Loss streak: {st.session_state.loss_streak}/{cfg.max_consecutive_losses} | "
            f"Daily limit: {cfg.max_daily_loss_pct:.1f}% | "
            f"Max hold: {cfg.max_holding_minutes} min"
        )
        if kill_switch_active:
            st.error("Daily kill switch is active. New entries are blocked for the session.")

        st.markdown("### Live Signal")
        if signal is None:
            if vix_now < cfg.min_vix_trade_threshold:
                st.info(f"No trade: India VIX {vix_now:.2f} is below the trade threshold of {cfg.min_vix_trade_threshold:.2f}.")
            else:
                st.info("No valid CE/PE setup right now. Stay flat.")
        else:
            option_symbol = make_option_symbol(
                signal.entry,
                signal.side,
                expiry_code,
                cfg.option_moneyness,
                cfg.strike_step,
                cfg.option_prefix,
            )
            trades_today = sum(1 for t in st.session_state.trade_log if str(now_ist().date()) in str(t.get("timestamp", "")))
            estimated_premium = signal.option_entry
            ok, reason = risk_checks(
                signal,
                st.session_state.day_start_capital,
                st.session_state.daily_pnl,
                trades_today,
                st.session_state.loss_streak,
                cfg,
            )
            qty = calculate_position_size(
                capital,
                cfg.risk_per_trade_pct,
                signal.option_entry,
                signal.option_stop_loss,
                cfg.lot_size,
                cfg.max_capital_allocation_pct,
                estimated_premium,
            )
            if qty <= 0:
                ok = False
                reason = "Capital allocation cap too small for one lot"

            with st.container(border=True):
                st.subheader(f"{signal.side} Signal")
                st.write(f"**Confidence:** {signal.confidence:.2f}")
                st.write(f"**Regime:** {signal.regime}")
                st.write(f"**Spot Entry:** {signal.entry:.2f}")
                st.write(f"**Option Entry:** ₹{signal.option_entry:.2f}")
                st.write(f"**Option Stop Loss:** ₹{signal.option_stop_loss:.2f}")
                st.write(f"**Option Target:** ₹{signal.option_target:.2f}")
                st.write(f"**Suggested Qty:** {qty}")
                st.write(f"**Option Symbol:** `{option_symbol}`")
                st.write(f"**Reason:** {signal.reason}")
                st.write(f"**Risk Check:** {reason}")

                signal_key = f"{signal.timestamp}_{signal.side}_{round(signal.entry, 2)}"
                can_fire = (
                    ok
                    and not kill_switch_active
                    and signal_key != st.session_state.last_signal_key
                    and (mode == "PAPER" or (live_ready and live_confirmed))
                )
                if st.button(f"Execute {mode} Order", disabled=not can_fire):
                    try:
                        resp = broker.place_order(signal.side, qty, option_symbol, mode)
                        st.session_state.trade_log.append(resp)
                        if resp.get("status") in {"paper_filled", "filled", "success", "accepted"}:
                            st.session_state.last_signal_key = signal_key
                            entry_price = estimated_premium
                            if (
                                mode == "LIVE"
                                and isinstance(broker, DhanBroker)
                                and resp.get("security_id")
                                and resp.get("exchange_segment")
                            ):
                                try:
                                    entry_price = broker.get_ltp(resp["security_id"], resp["exchange_segment"])
                                except Exception:
                                    entry_price = estimated_premium
                            st.session_state.positions.append(Position(
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
                            ))
                            st.success(f"Order sent: {resp}")
                        else:
                            st.warning(f"Order was not placed: {resp}")
                    except Exception as exc:
                        st.error(f"Execution failed: {exc}")

        st.markdown("### Open Positions")
        if not st.session_state.positions:
            st.write("No open positions.")
        else:
            current_spot = float(price_df["Close"].iloc[-1])
            auto_close_indexes = []
            rows = []
            for idx, pos in enumerate(st.session_state.positions):
                ref_spot = pos.entry_spot or current_spot
                mark_price = estimated_option_price(current_spot, ref_spot, pos.side, cfg)
                price_label = "Option Proxy"
                if (
                    pos.mode == "LIVE"
                    and isinstance(broker, DhanBroker)
                    and live_ready
                    and pos.security_id
                    and pos.exchange_segment
                ):
                    try:
                        mark_price = broker.get_ltp(pos.security_id, pos.exchange_segment)
                        price_label = "Option LTP"
                    except Exception:
                        mark_price = estimated_option_price(current_spot, ref_spot, pos.side, cfg)
                pnl = (mark_price - pos.entry) * pos.qty
                held_minutes = (now_ist() - pos.opened_at).total_seconds() / 60
                timed_out = held_minutes >= cfg.max_holding_minutes
                stop_hit = mark_price <= pos.stop_loss
                target_hit = mark_price >= pos.target
                if timed_out:
                    auto_close_indexes.append(idx)
                elif stop_hit or target_hit:
                    auto_close_indexes.append(idx)
                rows.append({
                    "#": idx,
                    "Side": pos.side,
                    "Entry": pos.entry,
                    "SL": round(pos.stop_loss, 2),
                    "Target": round(pos.target, 2),
                    "Qty": pos.qty,
                    "Mode": pos.mode,
                    "Option": pos.option_symbol,
                    "Security ID": pos.security_id,
                    "Opened": format_ist_timestamp(pos.opened_at),
                    "Held (min)": round(held_minutes, 1),
                    "Mark Source": price_label,
                    "Est. PnL": round(pnl, 2),
                    "Exit Watch": "TIME" if timed_out else ("SL/TARGET" if stop_hit or target_hit else ""),
                    "Reason": pos.reason[:120],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

            for exit_index in sorted(auto_close_indexes, reverse=True):
                pos = st.session_state.positions[exit_index]
                try:
                    resp = broker.exit_order(pos.option_symbol, pos.qty, pos.mode)
                    if resp.get("status") in {"paper_exit", "exited", "success", "accepted"}:
                        st.session_state.positions.pop(exit_index)
                        exit_mark = estimated_option_price(current_spot, pos.entry_spot or current_spot, pos.side, cfg)
                        exit_reason = "TIME"
                        if exit_mark <= pos.stop_loss:
                            exit_mark = pos.stop_loss
                            exit_reason = "SL"
                        elif exit_mark >= pos.target:
                            exit_mark = pos.target
                            exit_reason = "TARGET"
                        if (
                            pos.mode == "LIVE"
                            and isinstance(broker, DhanBroker)
                            and live_ready
                            and pos.security_id
                            and pos.exchange_segment
                            ):
                            try:
                                exit_mark = broker.get_ltp(pos.security_id, pos.exchange_segment)
                            except Exception:
                                pass
                        realized = close_position_and_record(pos, exit_mark, exit_reason, resp)
                        st.warning(f"{exit_reason} exit executed for {pos.option_symbol}. Realized P&L: ₹{realized:,.2f}")
                except Exception as exc:
                    st.error(f"Auto-exit failed for {pos.option_symbol}: {exc}")

            exit_index = st.number_input("Exit position #", min_value=0, max_value=max(len(st.session_state.positions) - 1, 0), value=0, step=1)
            if st.button("Exit Selected Position"):
                if 0 <= exit_index < len(st.session_state.positions):
                    pos = st.session_state.positions[exit_index]
                    try:
                        resp = broker.exit_order(pos.option_symbol, pos.qty, pos.mode)
                        if resp.get("status") in {"paper_exit", "exited", "success", "accepted"}:
                            st.session_state.positions.pop(exit_index)
                            exit_mark = estimated_option_price(current_spot, pos.entry_spot or current_spot, pos.side, cfg)
                            if (
                                pos.mode == "LIVE"
                                and isinstance(broker, DhanBroker)
                                and live_ready
                                and pos.security_id
                                and pos.exchange_segment
                            ):
                                try:
                                    exit_mark = broker.get_ltp(pos.security_id, pos.exchange_segment)
                                except Exception:
                                    exit_mark = estimated_option_price(current_spot, pos.entry_spot or current_spot, pos.side, cfg)
                            realized = close_position_and_record(pos, exit_mark, "MANUAL", resp)
                            st.success(f"Exited. Realized P&L: ₹{realized:,.2f}")
                        else:
                            st.session_state.trade_log.append(resp)
                            st.warning(f"Exit was not placed: {resp}")
                    except Exception as exc:
                        st.error(f"Exit failed: {exc}")

        st.markdown("### Trade Log")
        if st.session_state.trade_log:
            trade_log_df = pd.DataFrame(st.session_state.trade_log).copy()
            for col in ("timestamp", "entry_time", "exit_time", "opened_at"):
                if col in trade_log_df.columns:
                    trade_log_df[col] = trade_log_df[col].apply(format_ist_timestamp)
            st.dataframe(trade_log_df, use_container_width=True)
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
        bt_preset = st.selectbox("Backtest preset", ["Balanced", "Aggressive", "Strict"], index=0, key="bt_preset")

        preset_defaults = {
            "Balanced": {
                "confidence_threshold": 0.55,
                "volume_spike_threshold": 1.10,
                "use_volume_filter": True,
                "use_bb_filter": True,
                "use_regime_filter": True,
            },
            "Aggressive": {
                "confidence_threshold": 0.48,
                "volume_spike_threshold": 1.00,
                "use_volume_filter": False,
                "use_bb_filter": False,
                "use_regime_filter": True,
            },
            "Strict": {
                "confidence_threshold": cfg.confidence_threshold,
                "volume_spike_threshold": cfg.volume_spike_threshold,
                "use_volume_filter": cfg.use_volume_filter,
                "use_bb_filter": cfg.use_bb_filter,
                "use_regime_filter": cfg.use_regime_filter,
            },
        }
        preset = preset_defaults[bt_preset]

        with st.expander("Backtest tuning", expanded=True):
            bt_confidence = st.slider(
                "Backtest confidence threshold",
                0.40,
                0.90,
                float(preset["confidence_threshold"]),
                0.01,
                key=f"bt_confidence_{bt_preset}",
            )
            bt_volume_spike = st.slider(
                "Backtest volume spike threshold",
                1.00,
                2.00,
                float(preset["volume_spike_threshold"]),
                0.05,
                key=f"bt_volume_spike_{bt_preset}",
            )
            bt_use_volume = st.checkbox(
                "Use volume filter",
                value=bool(preset["use_volume_filter"]),
                key=f"bt_use_volume_{bt_preset}",
            )
            bt_use_bb = st.checkbox(
                "Use Bollinger filter",
                value=bool(preset["use_bb_filter"]),
                key=f"bt_use_bb_{bt_preset}",
            )
            bt_use_regime = st.checkbox(
                "Use regime filter",
                value=bool(preset["use_regime_filter"]),
                key=f"bt_use_regime_{bt_preset}",
            )
            bt_slippage_bps = st.slider(
                "Slippage per side (bps)",
                0.0,
                100.0,
                float(cfg.backtest_slippage_pct * 10000),
                1.0,
                key=f"bt_slippage_bps_{bt_preset}",
                help="Applied on both entry and exit. 10 bps = 0.10%.",
            )
            bt_cost_bps = st.slider(
                "Variable costs round-trip (bps)",
                0.0,
                100.0,
                float(cfg.backtest_cost_pct * 10000),
                1.0,
                key=f"bt_cost_bps_{bt_preset}",
                help="Approximate brokerage, taxes, and fees as a percentage of turnover.",
            )
            bt_fixed_cost = st.number_input(
                "Fixed cost per order (₹)",
                min_value=0.0,
                value=float(cfg.backtest_fixed_cost_per_order),
                step=5.0,
                key=f"bt_fixed_cost_{bt_preset}",
                help="Applied once on entry and once on exit.",
            )
        run_bt = st.button("Run Backtest", use_container_width=True)

        if run_bt:
            bt_price = load_price_data(
                cfg.underlying_security_id,
                cfg.underlying_exchange_segment,
                bt_interval,
                bt_period,
            )
            bt_vix = load_vix_data()
            bt_cfg = StrategyConfig(
                **{
                    **cfg.__dict__,
                    "bar_interval": bt_interval,
                    "history_period": bt_period,
                    "confidence_threshold": bt_confidence,
                    "volume_spike_threshold": bt_volume_spike,
                    "use_volume_filter": bt_use_volume,
                    "use_bb_filter": bt_use_bb,
                    "use_regime_filter": bt_use_regime,
                    "backtest_slippage_pct": bt_slippage_bps / 10000,
                    "backtest_cost_pct": bt_cost_bps / 10000,
                    "backtest_fixed_cost_per_order": bt_fixed_cost,
                }
            )
            if bt_price.empty:
                st.warning(
                    f"No backtest price data was returned for interval `{bt_interval}` and period `{bt_period}`. "
                    "Dhan historical data did not return candles for the selected range."
                )
            else:
                st.caption(
                    f"Running {bt_preset.lower()} backtest with confidence >= {bt_confidence:.2f}, "
                    f"volume spike >= {bt_volume_spike:.2f}, volume filter={bt_use_volume}, "
                    f"BB filter={bt_use_bb}, regime filter={bt_use_regime}, "
                    f"slippage={bt_slippage_bps:.0f} bps/side, variable costs={bt_cost_bps:.0f} bps, "
                    f"fixed cost=₹{bt_fixed_cost:.0f}/order."
                )
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
                    st.write(f"**Gross P&L:** ₹{stats['gross_pnl']:,.2f}")
                    st.write(f"**Estimated Costs:** ₹{stats['total_costs']:,.2f}")
                    st.write(f"**Max Drawdown:** {stats['max_drawdown_pct'] * 100:.2f}%")
                    if not trades_df.empty:
                        display_trades_df = trades_df.tail(100).copy()
                        for col in ("entry_time", "exit_time"):
                            if col in display_trades_df.columns:
                                display_trades_df[col] = display_trades_df[col].apply(format_ist_timestamp)
                        st.dataframe(display_trades_df, use_container_width=True)
                        equity = trades_df[["exit_time", "capital_after"]].copy()
                        equity = equity.set_index("exit_time")
                        st.line_chart(equity)
                    else:
                        st.info("No trades generated in the selected backtest window.")

    with chain_tab:
        st.markdown("### Dhan Option Chain")
        chain_broker = DhanBroker()
        chain_ready, chain_reason = chain_broker.status()
        if chain_ready:
            st.info(f"Dhan data status: {chain_reason}")
        else:
            st.error(f"Dhan data status: {chain_reason}")

        expiry_left, expiry_right = st.columns([2, 1])
        with expiry_right:
            refresh_chain = st.button("Refresh Chain", use_container_width=True, disabled=not chain_ready)

        if chain_ready and (refresh_chain or not st.session_state.option_chain_expiries):
            try:
                st.session_state.option_chain_expiries = chain_broker.get_option_chain_expiries(
                    instrument["underlying_symbol"],
                    instrument["underlying_exchange_segment"],
                )
            except Exception as exc:
                st.error(f"Could not load Dhan expiry list: {exc}")

        expiries = st.session_state.option_chain_expiries
        if not expiries:
            st.warning("No option-chain expiries available yet. Check Dhan credentials or refresh again.")
        else:
            with expiry_left:
                selected_chain_expiry = st.selectbox("Dhan option-chain expiry", expiries, index=0, key="selected_chain_expiry")

            if chain_ready and (
                refresh_chain
                or st.session_state.option_chain_payload is None
                or st.session_state.option_chain_loaded_expiry != selected_chain_expiry
            ):
                try:
                    st.session_state.option_chain_payload = chain_broker.get_option_chain(
                        instrument["underlying_symbol"],
                        instrument["underlying_exchange_segment"],
                        selected_chain_expiry,
                    )
                    st.session_state.option_chain_loaded_expiry = selected_chain_expiry
                except Exception as exc:
                    st.error(f"Could not load Dhan option chain: {exc}")

            chain_payload = st.session_state.option_chain_payload
            chain_df = option_chain_to_dataframe(chain_payload or {})
            chain_stats = option_chain_summary(chain_payload or {}, chain_df)

            top_cols = st.columns(5)
            top_cols[0].metric("Underlying", f"{chain_stats['last_price']:,.2f}")
            top_cols[1].metric("ATM Strike", "-" if chain_stats["atm_strike"] is None else f"{chain_stats['atm_strike']:,.0f}")
            top_cols[2].metric("PCR (OI)", f"{chain_stats['pcr_oi']:.2f}")
            top_cols[3].metric("Max Call OI", "-" if chain_stats["max_call_oi_strike"] is None else f"{chain_stats['max_call_oi_strike']:,.0f}")
            top_cols[4].metric("Max Put OI", "-" if chain_stats["max_put_oi_strike"] is None else f"{chain_stats['max_put_oi_strike']:,.0f}")

            if chain_df.empty:
                st.info("No option-chain rows were returned by Dhan for this expiry.")
            else:
                atm_strike = chain_stats["atm_strike"] or float(chain_df["strike"].iloc[0])
                atm_slice = chain_df.iloc[(chain_df["strike"] - atm_strike).abs().argsort()[:11]].sort_values("strike")
                atm_row = chain_df.loc[(chain_df["strike"] - atm_strike).abs().idxmin()]

                snap_left, snap_right = st.columns(2)
                with snap_left:
                    st.markdown("#### ATM Snapshot")
                    st.write(f"**Call LTP:** ₹{(atm_row.get('ce_ltp') or 0):.2f}")
                    st.write(f"**Call OI:** {int(atm_row.get('ce_oi') or 0):,}")
                    st.write(f"**Call Volume:** {int(atm_row.get('ce_volume') or 0):,}")
                    st.write(f"**Call IV:** {float(atm_row.get('ce_iv') or 0):.2f}")
                with snap_right:
                    st.markdown("#### Put Snapshot")
                    st.write(f"**Put LTP:** ₹{(atm_row.get('pe_ltp') or 0):.2f}")
                    st.write(f"**Put OI:** {int(atm_row.get('pe_oi') or 0):,}")
                    st.write(f"**Put Volume:** {int(atm_row.get('pe_volume') or 0):,}")
                    st.write(f"**Put IV:** {float(atm_row.get('pe_iv') or 0):.2f}")

                oi_chart = atm_slice[["strike", "ce_oi", "pe_oi"]].set_index("strike").rename(
                    columns={"ce_oi": "Call OI", "pe_oi": "Put OI"}
                )
                vol_chart = atm_slice[["strike", "ce_volume", "pe_volume"]].set_index("strike").rename(
                    columns={"ce_volume": "Call Volume", "pe_volume": "Put Volume"}
                )
                st.markdown("#### Near-ATM OI")
                st.bar_chart(oi_chart)
                st.markdown("#### Near-ATM Volume")
                st.bar_chart(vol_chart)

                display_chain = chain_df.rename(
                    columns={
                        "strike": "Strike",
                        "ce_ltp": "CE LTP",
                        "ce_oi": "CE OI",
                        "ce_volume": "CE Volume",
                        "ce_iv": "CE IV",
                        "ce_bid": "CE Bid",
                        "ce_ask": "CE Ask",
                        "pe_ltp": "PE LTP",
                        "pe_oi": "PE OI",
                        "pe_volume": "PE Volume",
                        "pe_iv": "PE IV",
                        "pe_bid": "PE Bid",
                        "pe_ask": "PE Ask",
                    }
                )
                keep_cols = [
                    "Strike",
                    "CE LTP",
                    "CE OI",
                    "CE Volume",
                    "CE IV",
                    "CE Bid",
                    "CE Ask",
                    "PE Bid",
                    "PE Ask",
                    "PE IV",
                    "PE Volume",
                    "PE OI",
                    "PE LTP",
                ]
                st.markdown("#### Full Strike Table")
                st.dataframe(display_chain[keep_cols], use_container_width=True)

    with notes_tab:
        st.markdown("### Dhan live execution wiring")
        st.code(
            """
1. Add your credentials to `.streamlit/secrets.toml`:
   [dhan]
   client_id = "YOUR_CLIENT_ID"
   access_token = "YOUR_ACCESS_TOKEN"

   or

   DHAN_CLIENT_ID=YOUR_CLIENT_ID
   DHAN_ACCESS_TOKEN=YOUR_ACCESS_TOKEN

2. Resolve option_symbol to a broker tradable instrument/security id.
   - This app now uses Dhan's official instrument master CSV for that mapping.

3. Implement:
   - monitor postbacks or poll order book for final trade status
   - persist live positions/order ids beyond Streamlit session state

4. Optional next step:
   - fetch order book / positions from Dhan instead of relying on local session state
   - use postbacks or websocket/live order updates for fills
   - add static IP whitelisting if your Dhan account requires it

5. Keep PAPER mode until:
   - strike mapping is verified
   - order placement is verified
   - exit flow is verified
   - your risk limits are tested
            """.strip(),
            language="python",
        )

    with help_tab:
        st.markdown("### How to use PowerScalper")
        st.markdown(
            """
1. Start in `PAPER` mode.
2. Pick the instrument and confirm the expiry code.
3. Set capital, stop loss, target, and risk controls.
4. Wait for a valid `CE` or `PE` signal with risk check `OK`.
5. Execute the order and monitor open positions.
6. Let the system exit on `SL`, `TARGET`, `TIME`, or exit manually.
7. Use the `Backtest` tab before changing live settings.
            """.strip()
        )
        st.markdown("### What each tab does")
        st.markdown(
            """
- `Live Signals`: current signal, execution, positions, and trade log
- `Backtest`: historical simulation with slippage, costs, and equity curve
- `Live Wiring Notes`: Dhan integration notes and secrets setup
- `Help`: quick operating guide and documentation link
            """.strip()
        )
        st.markdown(f"[Open full setup and usage guide]({HELP_URL})")


if __name__ == "__main__":
    main()
