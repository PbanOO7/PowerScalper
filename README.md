# PowerScalper

PowerScalper is a Streamlit app for selective index-options scalping, paper trading, backtesting, and staged Dhan live-trading integration.

The project currently runs from a single main file: [live_trading_system.py](/Users/prithwish/Documents/Trading%20Code/PowerScalper/live_trading_system.py).

## What The App Does

The app:

- loads index OHLCV data from Dhan historical APIs
- loads India VIX data from Dhan
- calculates technical indicators
- scores bullish and bearish setups
- generates `CE` or `PE` trade signals
- applies risk-managed position sizing
- supports paper execution inside the app
- runs a backtest using the same signal framework
- uses Dhan instrument master and option-chain APIs for market structure and contract mapping
- includes a Dhan execution scaffold for staged live deployment

Supported instruments in the current app:

- `NIFTY 50`
- `BANKNIFTY`
- `FINNIFTY`
- `SENSEX`

## Current Trading Model

This app is now tuned first for selective `NIFTY 50` scalping in paper mode before any live pilot.

The intended operating profile is:

- primary instrument: `NIFTY 50`
- target frequency: roughly `1` to `3` trades per day
- bar interval: `5m`
- hold time: roughly `5` to `15` minutes
- execution style: buy `ATM` or near-ATM option premium with strict risk caps

This app is designed around long option premium trades:

- buy `CE` when the bullish setup is strong
- buy `PE` when the bearish setup is strong
- use premium-based stop loss and target
- size quantity from account risk and lot size
- block trading when the daily kill switch is triggered

It is not selling options, and it is not a multi-leg options engine.

## Strategy Logic

Signals are built from a weighted score using:

- `EMA 20` and `EMA 50`
- `RSI`
- Bollinger Band expansion
- VWAP behavior
- candle classification
- breakout / breakdown confirmation
- volume spike filter
- India VIX filter and regime detection

The app generates a trade only when the combined confidence score is above the configured threshold.

## Risk Controls

The app includes the following controls:

- risk per trade capped to `1%` to `2%` of capital
- premium stop loss in the `20%` to `30%` range
- premium target in the `40%` to `60%` range
- max holding time, default `10` minutes
- max daily loss limit
- consecutive loss kill switch
- max trades per day
- capital allocation cap per trade
- VIX threshold to avoid low-volatility chop

### Kill Switch

New trades are blocked for the day when either of these is hit:

- the configured daily loss limit
- the configured consecutive loss limit

## Paper Mode vs Live Mode

`PAPER` mode is the default and is the safe mode for normal use.

`LIVE` mode is protected by:

- explicit mode toggle
- confirmation checkbox
- Dhan credential check

Even with those controls, live trading should still be treated as incomplete until you validate the full broker flow end to end.

## Data Sources

The app now uses Dhan for all market-data paths inside the Streamlit runtime:

- index historical candles from Dhan `historical` / `intraday` APIs
- India VIX history from Dhan
- option-chain expiries and strike-level chain data from Dhan
- instrument and contract resolution from Dhan's instrument master

There is no longer a runtime dependency on Yahoo Finance.

## Backtest Behavior

The backtest engine reuses the same signal framework and applies:

- premium-based entry and exit approximation
- slippage
- estimated brokerage / turnover costs
- capital tracking
- drawdown tracking
- risk-based sizing
- daily loss and consecutive-loss kill switches
- max holding time exits
- end-of-day exits

### Important Backtest Limitation

The backtest is more realistic than a raw spot approximation, but it still does not replay historical option-chain data tick by tick.

Current backtests use an internal option-premium proxy model derived from underlying movement. That is useful for screening and iteration, but it is still an approximation.

## Streamlit Layout

The app currently has five tabs:

### 1. `Live Signals`

Use this tab to:

- view the latest signal
- see the active rejection reason when no trade is taken
- inspect the `Why No Trade` diagnostics table for the current cycle
- inspect confidence, regime, stop, target, and suggested quantity
- place paper orders
- place live orders when enabled and confirmed
- monitor open positions
- manually exit positions
- review the session trade log
- review worker validation metrics and the paper-validation checklist

### 2. `Backtest`

Use this tab to:

- choose interval and lookback window
- set a backtest preset
- tune confidence, filters, slippage, and costs
- run a backtest
- inspect trades, PnL, drawdown, profit factor, and equity curve
- inspect signal counts, executed trades, rejection counts, average hold time, and trades/day
- review a lightweight signal-diagnostics table to see why candidates were rejected

### 3. `Option Chain`

Use this tab to:

- load Dhan expiry lists
- inspect strike-wise `CE` / `PE` OI, volume, IV, bid, and ask
- view ATM context and PCR summaries
- review near-ATM positioning before execution

### 4. `Live Wiring Notes`

Use this tab as a quick in-app reminder for Dhan integration and secrets configuration.

### 5. `Help`

Use this tab for a short in-app operating guide and a link to the full repo documentation.

## Sidebar Controls

The main sidebar exposes the operating controls for the strategy:

- instrument, with `NIFTY 50` as the default
- trading mode: `PAPER` or `LIVE`
- capital
- `SL %`
- `Target %`
- risk per trade
- max holding time
- daily loss limit
- consecutive loss limit
- capital allocation cap
- max trades per day
- confidence threshold
- VIX threshold
- live bar interval
- history period
- strike selection
- expiry code
- live preset, with `Selective NIFTY Scalp` as the default

## How To Run

### Requirements

- Python `3.10+`
- dependencies from [requirements.txt](/Users/prithwish/Documents/Trading%20Code/PowerScalper/requirements.txt)

### Install

```bash
pip install -r requirements.txt
```

### Start The App

```bash
streamlit run live_trading_system.py
```

Streamlit will print a local URL in the terminal. Open that URL in your browser.

## How To Use The App

### Basic paper-trading workflow

1. Start the app.
2. Keep trading mode on `PAPER`.
3. Keep the instrument on `NIFTY 50` unless you are explicitly testing another index.
4. Use the `Selective NIFTY Scalp` live preset as the starting point.
5. Set capital and risk controls.
6. Review the live signal or the `Why No Trade` panel.
7. If a valid signal appears and the risk check is `OK`, execute the paper order.
8. Monitor open positions, worker metrics, and trade log.
9. Let the system hit stop, target, or time exit, or exit manually.

### Basic backtest workflow

1. Open the `Backtest` tab.
2. Select interval and period.
3. Choose a preset.
4. Adjust slippage and costs if needed.
5. Run the backtest.
6. Review trades, signal count, rejection counts, hold time, and trades/day before changing live settings.
7. Treat the backtest as a tuning tool, not proof of live scalp profitability.

## Paper Validation Checklist

Before trying a small live pilot, use paper mode to confirm:

- several sessions have completed without repeated data or execution failures
- paper expectancy is positive after estimated costs
- trade frequency is acceptable for the session style you want
- liquidity and spread rejections are not dominating the day
- stop, target, and time exits behave as expected

## Dhan Secrets Setup

The app reads Dhan credentials from Streamlit secrets or environment variables.

Recommended approach:

1. Create `.streamlit/secrets.toml`
2. Add:

```toml
[dhan]
client_id = "YOUR_CLIENT_ID"
access_token = "YOUR_ACCESS_TOKEN"
```

A template is included at [.streamlit/secrets.toml.example](/Users/prithwish/Documents/Trading%20Code/PowerScalper/.streamlit/secrets.toml.example).

Environment variable fallback is also supported:

```bash
export DHAN_CLIENT_ID="YOUR_CLIENT_ID"
export DHAN_ACCESS_TOKEN="YOUR_ACCESS_TOKEN"
```

## Live Trading Status

Live trading support is only partially implemented.

What is already present:

- Dhan credential loading
- Dhan profile/status check
- Dhan historical price and India VIX loading
- Dhan instrument master download
- Dhan option-chain expiry and strike data
- generated option symbol to Dhan contract resolution
- market order request scaffold
- LTP lookup for live mark prices

What still needs careful validation before production use:

- final order-state handling
- persistent broker-side position syncing
- websocket or postback-based fill tracking
- production-safe live execution testing
- better historical option pricing for backtests

## Project Structure

```text
PowerScalper/
├── .streamlit/
│   └── secrets.toml.example
├── live_trading_system.py
├── README.md
└── requirements.txt
```

## Practical Notes

- `PAPER` mode should be your default.
- Treat backtest returns with caution because the pricing model is still synthetic.
- Do not assume live and paper fills will match.
- Streamlit session state is used for current-session positions and logs.
- The app is still monolithic; strategy, UI, backtest, and execution scaffolding are all in one file.

## Disclaimer

This project is for development and educational use. Test thoroughly in paper mode before attempting any broker-connected execution with real capital.
