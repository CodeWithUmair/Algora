# Project Handoff

**Purpose of this file:** a single, growing source of truth for this project that travels *with the repo* across machines and Claude Code sessions. Same convention as the sibling gold bot's `HANDOFF.md` (`../VWAP-EMA-BOT/HANDOFF.md`) — reference sections get edited in place as things change, §7 Session Log is append-only.

---

## 1. What this project is

A NASDAQ-100 scalping bot mechanizing **Fabio Valentini's discretionary order-flow strategy** (originally NQ futures + Interactive Brokers + real Level 2/footprint data) onto **MetaTrader 5** trading **`USTECm`**. Built 2026-09-13 as a sibling to the XAU/USD gold scalper (`../VWAP-EMA-BOT`), reusing its proven architecture (background-thread live engine + Streamlit dashboard + `st.fragment` real-time updates + circuit breakers + SQLite storage) with a completely different strategy engine.

**Read `STRATEGY_SPECIFICATION.md` before touching the strategy code** — it has the full mapping from the guide's discretionary concepts to what's actually mechanized, and is explicit about which parts are approximations (nearly all of them — MT5 doesn't give real order-flow data) vs. genuinely missing (0DTE options sentiment, not implemented at all).

## 2. Architecture / file map

Same shape as the gold bot, see `README.md` for the full tree. Reused **verbatim** from `../VWAP-EMA-BOT`: `circuit_breakers.py` (100% generic). Reused with **light adaptation** (symbol defaults, NASDAQ-specific fallback data): `storage.py` (added a `model` column), `mt5_bridge.py` (default symbol `USTECm`, added `fetch_ticks_range()` for optional future real-tick range bars), `data_feed.py` (NASDAQ-100 price levels ~29,000 instead of Gold's ~2,380). **Entirely new**: `strategy.py` (range bars, Volume Profile, CVD, the 3 playbooks), `backtest.py`'s simulation loop (same Trade/Metrics/noise-gate shapes as the gold bot, but walks range bars instead of time bars).

## 3. Instrument & account

- **Symbol:** `USTECm` — confirmed available on the same Exness demo account the gold bot uses (login 472544446, server Exness-MT5Trial16). Verify symbol name on any other account/broker — NASDAQ-100 CFD naming varies (`USTEC`, `NAS100`, `US100`).
- Contract size 1.0 (1 point move on 1.0 lot = $1.00), volume step 0.01, min lot 0.05.
- Same MT5 terminal as the gold bot (`C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe`) — both bots can run against the same terminal/account simultaneously (each uses its own magic number: gold `9212001`, this bot `9312001`).

## 4. Key design decisions (and why)

- **MT5 over Interactive Brokers** — matches the existing proven stack, one broker/terminal for both bots, at the cost of no real order-flow data. User's explicit choice when asked (2026-09-13).
- **Full auto-trader over decision-support dashboard** — the guide is discretionary, but user chose full mechanization, same as the gold bot's model. User's explicit choice (2026-09-13).
- **Range bars from M1 OHLC, not real ticks** — `mt5.copy_ticks_range()` exists and `fetch_ticks_range()` is wired up in `mt5_bridge.py`, but tick history depth on this broker/account is unreliable (same kind of retention limit that capped the gold bot's M1 history to ~2-3 months — see gold bot's `HANDOFF.md` §5 session log). M1-approximated range bars (documented intrabar-path heuristic in `strategy.py`) are the dependable default.
- **Risk-based position sizing instead of a fixed lot** — the guide's core edge claim is asymmetric dollar risk (tight losses, let winners run to 1:3-1:10+), which a fixed lot size doesn't express. `calculate_position_size()` sizes to a configurable `risk_per_trade_usd`.
- **0DTE options sentiment: not implemented.** No data source connected. Don't let a future session assume this filter exists just because the guide mentions it prominently.

## 5. What's NOT yet validated

This is a freshly-built v1. What's already been smoke-tested is in §7 (unit tests, a synthetic backtest, and one live-engine run against the real MT5 feed that computed VAL/VAH/POC/CVD correctly and stopped cleanly — but that was a ~10-second connect-and-observe run, **no order was ever placed**). Still open:

- [ ] Run the actual backtest suite against a real multi-month window once real M1 history is available (same caveat as the gold bot — check how far back this broker retains `USTECm` M1 data before assuming a window is real; the gold bot's equivalent check found only ~2-3 months of usable M1 history on this broker/account).
- [ ] Confirm the 3 playbooks actually fire at reasonable frequency on real (not synthetic) NASDAQ-100 data — the synthetic-data smoke test (2026-09-13) produced 28 trades / PF 2.09 / +0.74R expectancy over ~1.5 days of synthetic M1 data using FAILED_AUCTION and SQUEEZE, but AAA (absorption) never fired in that sample — worth checking whether its trigger conditions (`absorption_max_body_ratio`, `volume_spike_mult`) are tuned sensibly once real data is available.
- [ ] Tune `range_size_points` (default 15) against real NASDAQ-100 volatility — this is the single most impactful parameter and was set by judgment, not calibration.
- [ ] No live order has ever been placed by this bot (manual override or auto-engine) — first live/demo order is still ahead.
- [ ] Not yet pushed to GitHub — exists only as a local git repo (`43d2258`) on this machine.

## 6. How to run

See `README.md` → Quick start. One command (`streamlit run trading_bot/streamlit_app.py`), Start/Stop toggle in the dashboard, same pattern as the gold bot.

## 7. Session log

*(Newest first.)*

### 2026-09-13 (cont'd) — Handoff: user takes over from here
User is picking up direct work on this repo now (no longer solely Claude-driven). State at handoff:
- **Git:** local repo initialized and committed (`43d2258`, message "Initial commit: NASDAQ-100 order-flow scalper (USTECm)"). **Not pushed anywhere yet** — no remote configured. If this should end up on GitHub (own repo, or alongside the gold bot's `uzairshaikh346/VWAP-EMA-BOT`), that's a decision to make explicitly, not assume.
- **Tests:** full suite run via `python trading_bot/run_tests.py` → **20/20 passed** (not just the individual pieces mentioned in the entry below).
- **Live engine:** actually started against the real MT5 connection (not just planned) — connected to account 472544446 ($99.43 balance at the time), pulled real `USTECm` M1 data, built 272 range bars, computed VAL $29,335 / VAH $29,490 / POC $29,427.5 / CVD +44,767 / ATR $22.01, correctly detected it was outside the NY-open session window, then stopped cleanly on command. This was a short connect-and-observe run — **it never got far enough in time to evaluate a signal or place an order.**
- Whoever (human or Claude) continues from here should treat §5's checklist as the actual next steps, roughly in this order: (1) push somewhere if desired, (2) a longer live-engine run during the actual NY session window to see a real signal evaluate, (3) real-data backtest once/if enough M1 history exists, (4) tune `range_size_points` and the AAA absorption thresholds.

### 2026-09-13 — Repo created, v1 strategy engine + dashboard built
- User asked for a second bot repo (this one) to implement a NASDAQ strategy from a dropped guide (`fabio-valentini-strategy-guide.md`) — moved into `docs/` here, removed from the gold bot's repo root.
- Clarified two pivotal decisions with the user before building (see §4): MT5 over IBKR, full auto-trader over decision-support.
- Confirmed `USTECm` is the right symbol on the shared Exness demo account (`symbols_get()` scan).
- Wrote `STRATEGY_SPECIFICATION.md` mapping the guide's order-flow concepts to MT5-buildable proxies, explicit about the gaps (see §4).
- Built `trading_bot/strategy.py` from scratch: `build_range_bars()` (M1-OHLC-approximated range bars, unit-tested against a synthetic monotonic series), `calculate_volume_profile()` (causal, session-anchored, bucketed histogram), `calculate_cvd()`, volume-spike detection, and the 3 playbooks (`evaluate_signal_at_bar()`).
- Adapted `backtest.py`, `mt5_bridge.py`, `storage.py` from the gold bot's proven versions; copied `circuit_breakers.py` and `run_tests.py` verbatim.
- Built `live_engine.py` + `streamlit_app.py` following the exact background-thread-singleton + `st.fragment` real-time pattern already proven in the gold bot (avoids re-discovering the duplicate-render bug that pattern was built to fix there).
- Smoke-tested end-to-end on synthetic data: range-bar builder (unit tests pass), volume profile/CVD (unit tests pass), full backtest run (5000 M1 bars → 2126 range bars in 0.12s, 28 trades, 39.3% win rate, PF 2.09, +0.74R expectancy — directionally consistent with the guide's stated "moderate win rate, asymmetric R:R" profile, though this is simulated data, not evidence of a real edge).
