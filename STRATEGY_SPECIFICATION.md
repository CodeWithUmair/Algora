# NASDAQ Order-Flow Scalper — Strategy Specification

## 1. Source & honest scope

This bot mechanizes **Fabio Valentini's discretionary order-flow scalping system** (full guide: `docs/fabio-valentini-strategy-guide.md`), originally built for **NASDAQ futures (NQ) via Interactive Brokers**, real Level 2 order flow, footprint charts, and 0DTE options sentiment.

This implementation runs on **MetaTrader 5** against **`USTECm`** (NASDAQ-100 CFD, Exness) instead. MT5 does not expose real order-flow/DOM data through its API, so the original discretionary reads are replaced with **mechanized proxies** built from OHLCV bars. Be clear-eyed about what that means:

| Guide concept | Real requirement | This bot's proxy |
|---|---|---|
| 40-range chart | Tick-by-tick price feed | Range bars **reconstructed from M1 OHLC** using an Open→Low→High→Close (bullish) / Open→High→Low→Close (bearish) intrabar path assumption — a standard approximation, not real tick data |
| Volume Profile (VAL/VAH/POC) | Traded volume at each price | Session volume profile built by distributing each bar's `tick_volume` uniformly across its `[low, high]` range |
| Big Trades filter | Individual large order prints | Volume-spike detection: bar volume > N× its rolling average |
| Absorption | Aggressive orders failing to move price, seen on the tape | High-volume bar with a small body relative to its range, sitting at VAL/VAH |
| Cumulative Volume Delta | Buy-initiated vs sell-initiated trade classification | Proxy: `+volume` on bars that closed up, `-volume` on bars that closed down, cumulative |
| 0DTE options sentiment | Options flow data feed | **Not implemented** — no data source wired up. Documented gap, not faked. |

If real order-flow fidelity ever matters more than this, that's an Interactive Brokers + a footprint data vendor (e.g. Bookmap, Sierra Chart with a DOM feed) rewrite, not a tweak to this codebase.

## 2. Instrument & account

- **Symbol:** `USTECm` (confirmed available on the Exness demo account this was built against — verify on any other account/broker, symbol names for the NASDAQ-100 CFD vary: `USTEC`, `NAS100`, `US100`, etc.)
- Contract size 1.0, volume step 0.01, min lot 0.05 (per `symbol_info` on this account — re-check per broker).
- Same MT5 terminal/account pattern as the sibling gold bot: DEMO-only enforced at every order.

## 3. Mechanized building blocks (`trading_bot/strategy.py`)

1. **Range bars** — `build_range_bars(opens, highs, lows, closes, times, volumes, range_size)`. A new bar closes every time price moves `range_size` points from that bar's open; volume is split proportionally across the synthetic intrabar path. Default `range_size` is configurable (`StrategyParameters.range_size_points`, tune per instrument volatility — NASDAQ-100 moves in much bigger absolute point terms than Gold).
2. **Session Volume Profile** — `calculate_volume_profile(...)`: histogram of volume-at-price over a session window, returns `(val, vah, poc)`. Session anchor is configurable (default: UTC daily reset, same convention as the gold bot's VWAP).
3. **CVD proxy** — `calculate_cvd(opens, closes, volumes)`: causal cumulative up/down-volume delta line.
4. **Volume spike / "Big Trade" flag** — bar volume vs. rolling average.
5. **HTF trend filter** — M15 EMA(50) alignment, same pattern as the gold bot (`evaluate_htf_trend`), applied on the *range-bar* series' higher timeframe equivalent (a coarser range size) rather than a time-based HTF.

## 4. The three playbooks, mechanized

### Model A — "AAA" Value Area Absorption Reversal
- **Setup:** price (range bar) touches within `absorption_buffer_atr × ATR` of VAL (long) or VAH (short).
- **Trigger:** that bar is a volume spike (≥ `volume_spike_mult`× rolling avg volume) **and** has a small body (`body/range ≤ absorption_max_body_ratio`) — i.e. high effort, low result = absorption.
- **Direction:** reversal — buy at VAL absorption, sell at VAH absorption.
- **Stop:** beyond the absorption bar's extreme + buffer.
- **Target:** POC, else opposite Value Area boundary if that gives ≥ `min_rr_ratio`.

### Model B — Momentum / Squeeze Breakout
- **Setup:** range bar closes beyond VAH (long) or VAL (short), or beyond the current session's high/low.
- **Trigger:** volume spike **and** large body (`body/range ≥ breakout_min_body_ratio`) — i.e. a real expansion bar, not a wick.
- **Direction:** with the breakout.
- **Stop:** behind the breakout bar's opposite extreme.
- **Target:** fixed `rr_ratio` (default 1:2), stop moved to break-even once price has covered `be_trigger_ratio` (default 70%) of the distance to target — same break-even-shield mechanic as the gold bot.

### Model C — Failed Auction Fade
- **Setup:** a bar's high pierces VAH (or low pierces VAL) — an auction attempt.
- **Trigger:** the *next* bar closes back inside the value area (failed breakout, trapped traders).
- **Direction:** fade — opposite the failed breakout.
- **Stop:** beyond the failed breakout's extreme.
- **Target:** POC, else opposite Value Area boundary.

Only one model may be "armed" at a time per direction to avoid duplicate signals; a fresh range bar close is required between signals (no same-bar re-fire).

## 5. Risk management

Translating the guide's futures-contract dollar risk ($1,500–$2,500/trade, $10,000 daily) into CFD/lot terms, scaled to whatever account size is actually connected:

- **Risk-based position sizing:** `risk_per_trade_usd` (config) ÷ (SL distance in points × `$/point per 1.0 lot` from `symbol_info`) → lot size, rounded to `volume_step`, clamped to `[volume_min, volume_max]`. Not a fixed lot — this is the one deliberate departure from the gold bot's fixed-0.01-lot approach, because the guide's core edge claim is asymmetric $-risk, not fixed size.
- **Daily loss circuit breaker & consecutive-loss breaker** — reused verbatim from the gold bot's `circuit_breakers.py` (generic, not gold-specific).
- **Session window** — guide specifies a tight 10–20 minute execution window at NY/London open; mechanized as `enable_session_filter` + a configurable UTC window (default: 13:30–14:00 UTC, NY cash open) rather than trading all day.
- **Three-loss rule** — `max_consecutive_losses` circuit breaker, same as gold bot.
- **Daily profit lock** — same pattern as gold bot (`daily_profit_target_usd`, auto-pause for the day once hit).

## 6. What's reused as-is from the gold bot (`VWAP-EMA-BOT`)

`circuit_breakers.py` (100% generic), `storage.py` (100% generic, same `trades`/`settings`/`bot_logs` schema), `mt5_bridge.py` (same shape, symbol defaulted to `USTECm`, simulation fallback prices changed to NASDAQ-100 levels), the `live_engine.py` background-thread-singleton pattern, the Streamlit dark/gold design system, and the `st.fragment`-based real-time dashboard pattern (all proven working in the sibling repo — no reason to reinvent them).

## 7. Known limitations (be upfront, don't paper over these)

- Range bars, volume profile, and CVD are **all built from 1-minute OHLC bars, not real tick/DOM data**. They're reasonable, commonly-used approximations, not the real thing the guide describes.
- 0DTE options sentiment filter: not implemented, no data source connected.
- Backtesting depth is capped by whatever M1 history this MT5/broker retains (confirmed elsewhere to be roughly the last 2–3 months on this Exness demo account) — same caveat as the gold bot.
- The guide is written for a **multi-million-dollar futures account** trading fixed contract counts; this mechanization risk-scales to whatever account size is actually connected. Absolute dollar figures in the guide (e.g. "$10,000 daily drawdown") do not apply directly — see `risk_per_trade_usd`/`max_daily_loss_usd` config instead.
- **High-impact-news avoidance (added 2026-09-14, `trading_bot/news_filter.py`)** blocks new entries within 15 minutes of a High-impact USD calendar event (NFP, CPI, FOMC, etc.), pulled live from an unofficial ForexFactory calendar mirror. This is a **live-only, unvalidated-by-backtest** addition — that feed only ever exposes the current week, never a historical archive, so unlike every other parameter in this repo, its actual effect on returns has not been (and cannot currently be) backtested. Treat it as a reasonable extra safety layer, not a proven part of the edge.
