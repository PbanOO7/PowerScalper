# PowerScalper

PowerScalper is a Streamlit-based NIFTY options trading dashboard with:

- live CE/PE signal generation
- option strike selection
- paper-trading execution flow
- historical backtesting
- a scaffold for Dhan live broker integration

The current project is implemented in a single file: [live_trading_system.py](/Users/prithwish/Documents/Trading%20Code/PowerScalper/live_trading_system.py).

## What It Does

The app fetches NIFTY and India VIX market data using `yfinance`, computes technical indicators, classifies the market regime, and generates directional signals for call (`CE`) or put (`PE`) trades.

Core logic includes:

- EMA trend alignment
- RSI confirmation
- Bollinger Band expansion
- VWAP interaction
- candle pattern classification
- volume spike filtering
- VIX-based regime detection
- risk-based position sizing

The interface has three main sections:

- `Live Signals`: current signal, order execution controls, open positions, and trade log
- `Backtest`: historical simulation of the same signal logic
- `Live Wiring Notes`: instructions for connecting Dhan APIs

## Current Status

`PAPER` mode works as a simulation flow inside the Streamlit app.

`LIVE` mode is not production-ready yet. The `DhanBroker` class is only a placeholder and still needs:

- credential loading
- instrument resolution
- real order placement
- exit handling
- broker-side price and position tracking

## Requirements

- Python 3.10+
- `streamlit`
- `pandas`
- `numpy`
- `yfinance`

Install dependencies:

```bash
pip install streamlit pandas numpy yfinance
```

## Running The App

From the project root:

```bash
streamlit run live_trading_system.py
```

Then open the local Streamlit URL shown in the terminal.

## How The Strategy Works

At a high level, the strategy:

1. Downloads recent NIFTY OHLCV data and India VIX data.
2. Enriches price data with indicators such as EMA, RSI, Bollinger Bands, rolling volume, and VWAP.
3. Detects whether the market is `TRENDING`, `RANGE`, or `VOLATILE`.
4. Scores bullish and bearish setups using trend, breakout, candle structure, RSI, VWAP, volume, and Bollinger expansion.
5. Creates a `CE` or `PE` signal if the confidence threshold is met.
6. Sizes the trade using account capital, stop loss distance, and lot size.

## Backtesting

The backtest engine reuses the same signal-generation logic and simulates:

- one open trade at a time
- stop-loss exits
- target exits
- end-of-day exits
- daily trade limits
- max daily loss limits

Reported metrics include:

- total trades
- win rate
- net P&L
- return percentage
- max drawdown
- profit factor

## Important Limitations

- Open-position P&L is estimated from spot index movement, not actual option premium movement.
- `LIVE` mode will fail until `DhanBroker` is implemented.
- The app currently stores runtime state in Streamlit session state only.
- The project is monolithic right now; strategy logic, UI, backtesting, and execution live in one file.
- This is an execution framework, not a guarantee of profitability.

## Dhan Integration Notes

To make live execution usable, extend `DhanBroker` in `live_trading_system.py` to:

- load `client_id` and `access_token`
- map generated option symbols to tradable Dhan instruments
- place orders through the broker API
- exit open positions
- fetch real prices for accurate P&L and risk handling

The app already includes an in-app notes tab describing the intended integration flow.

## Project Structure

```text
PowerScalper/
├── live_trading_system.py
└── README.md
```

## Disclaimer

This project is for educational and development purposes. Test thoroughly in paper mode before connecting any live broker or risking capital.
