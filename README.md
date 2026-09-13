# 📈 NASDAQ-100 Order-Flow Scalper (USTECm / MetaTrader 5)

A local, semi-automated scalping bot for **NASDAQ-100 (USTECm)** on MetaTrader 5, mechanizing **Fabio Valentini's discretionary order-flow scalping system** (`docs/fabio-valentini-strategy-guide.md`) into rules an algorithm can actually execute.

**Read `STRATEGY_SPECIFICATION.md` first** — it maps every concept in the guide (Volume Profile, Big Trades filter, absorption, CVD, 0DTE sentiment) to what this bot actually computes from MT5 OHLCV data, and is explicit about where the mechanized version is an approximation of the real discretionary read.

Sibling project to the XAU/USD gold scalper (`../VWAP-EMA-BOT`) — same architecture, safety guardrails, and dashboard design, different market and strategy engine.

---

## Quick start

```bash
pip install -r requirements.txt
streamlit run trading_bot/streamlit_app.py
```

Opens at **http://localhost:8501**. MT5 auto-connects once the page loads. The auto-bot is a **▶ Start Bot** toggle at the top of the dashboard — off by default, no trades happen until you start it.

## What it does

1. Fetches M1 OHLCV bars from MT5 for `USTECm`.
2. Reconstructs **range bars** from them (a substitute for the guide's 40-range chart — MT5's API doesn't expose real tick data reliably enough to build these from ticks by default).
3. Computes a session **Volume Profile** (VAL/VAH/POC) and a **CVD proxy** on the range-bar series.
4. Evaluates 3 mechanized playbooks every time a new range bar closes:
   - **AAA** — Value Area Absorption Reversal
   - **SQUEEZE** — Momentum breakout of VAH/VAL
   - **FAILED_AUCTION** — fade a failed breakout attempt
5. Sizes positions by **risk-per-trade** (not fixed lot), executes via MT5 with the same DEMO-only + circuit-breaker guardrails as the gold bot.

## Directory structure

```
trading_bot/
  strategy.py         Range bars, Volume Profile, CVD, the 3 playbooks
  backtest.py          Causal zero-lookahead backtester + Monte Carlo noise gate
  circuit_breakers.py  Daily loss / consecutive-loss guardrails (generic, shared design with the gold bot)
  storage.py           SQLite: trades / settings / bot_logs
  mt5_bridge.py        MetaTrader5 connector
  data_feed.py         Synthetic NASDAQ-100 data generator (simulation fallback + backtest filler)
  live_engine.py       Background-thread live trading engine (Start/Stop from the dashboard)
  run_live_auto_bot.py Thin CLI wrapper around live_engine.py, for headless use
  streamlit_app.py     The dashboard
  tests/               Unit tests (run_tests.py)
STRATEGY_SPECIFICATION.md   Mechanized rules, explicit limitations
docs/fabio-valentini-strategy-guide.md   The original discretionary strategy guide
HANDOFF.md            Full project reference + session log
backups/              Dated SQLite snapshots (see backups/README.md)
```

## Run tests

```bash
python trading_bot/run_tests.py
```
