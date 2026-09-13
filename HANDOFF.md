# Project Handoff

**Purpose of this file:** a single, growing source of truth for this project that travels *with the repo* across machines and Claude Code sessions. Same convention as the sibling gold bot's `HANDOFF.md` (`../VWAP-EMA-BOT/HANDOFF.md`) — reference sections get edited in place as things change, §7 Session Log is append-only.

---

## 1. What this project is

A NASDAQ-100 scalping bot mechanizing **Fabio Valentini's discretionary order-flow strategy** (originally NQ futures + Interactive Brokers + real Level 2/footprint data) onto **MetaTrader 5** trading **`USTECm`**. Built 2026-09-13 as a sibling to the XAU/USD gold scalper (`../VWAP-EMA-BOT`), reusing its proven architecture (background-thread live engine + Streamlit dashboard + `st.fragment` real-time updates + circuit breakers + SQLite storage) with a completely different strategy engine.

**Read `STRATEGY_SPECIFICATION.md` before touching the strategy code** — it has the full mapping from the guide's discretionary concepts to what's actually mechanized, and is explicit about which parts are approximations (nearly all of them — MT5 doesn't give real order-flow data) vs. genuinely missing (0DTE options sentiment, not implemented at all).

## 2. Architecture / file map

Same shape as the gold bot, see `README.md` for the full tree. Reused **verbatim** from `../VWAP-EMA-BOT`: `circuit_breakers.py` (100% generic). Reused with **light adaptation** (symbol defaults, NASDAQ-specific fallback data): `storage.py` (added a `model` column), `mt5_bridge.py` (default symbol `USTECm`, added `fetch_ticks_range()` for optional future real-tick range bars), `data_feed.py` (NASDAQ-100 price levels ~29,000 instead of Gold's ~2,380). **Entirely new**: `strategy.py` (range bars, Volume Profile, CVD, the 3 playbooks), `backtest.py`'s simulation loop (same Trade/Metrics/noise-gate shapes as the gold bot, but walks range bars instead of time bars).

## 3. Instrument & account

- **Symbol:** `USTECm` — confirmed available on the same Exness demo account the gold bot uses (account details kept out of this public repo — see local/private notes). Verify symbol name on any other account/broker — NASDAQ-100 CFD naming varies (`USTEC`, `NAS100`, `US100`).
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

- [x] Run the actual backtest suite against a real multi-month window once real M1 history is available — done 2026-09-13, see §7. Actual depth found: ~3.3 months; 1/2/3-month windows were real, 4/5/6-month windows clipped to that same 3.3-month ceiling. Results are marginally positive but thin (see §7 for the full skeptical read) — **not yet evidence of a tradeable edge**, just a first real-data data point.
- [x] Confirm the 3 playbooks actually fire at reasonable frequency on real (not synthetic) NASDAQ-100 data — done 2026-09-13, see §7. Confirmed on real data too: AAA (absorption) fired **zero** times across all 6 windows, SQUEEZE fired 1-3 times per window, FAILED_AUCTION accounts for ~97% of all trades. This is now a repeated finding (synthetic + real), not a one-off — `absorption_max_body_ratio`/`volume_spike_mult` tuning is the next concrete step, not just a maybe.
- [ ] Tune `range_size_points` (default 15) against real NASDAQ-100 volatility — this is the single most impactful parameter and was set by judgment, not calibration.
- [ ] No live order has ever been placed by this bot (manual override or auto-engine) — first live/demo order is still ahead.
- [ ] Not yet pushed to GitHub — exists only as a local git repo (`43d2258`) on this machine.

## 6. How to run

See `README.md` → Quick start. One command (`streamlit run trading_bot/streamlit_app.py`), Start/Stop toggle in the dashboard, same pattern as the gold bot.

## 7. Session log

*(Newest first.)*

### 2026-09-13 (cont'd) — Real-data multi-window backtest run
Ran the actual backtest suite against real MT5 `USTECm` M1 history (item 1 of §5's checklist), connecting to the live terminal (a pre-authenticated demo account, details kept out of this public repo) and pulling history via `mt5.copy_rates_from_pos(..., count=99999)` — note **not** `copy_rates_range()`, which returned 0 bars for any window beyond ~69 days back on this broker/terminal even though pos-based queries reach further; a broker/terminal quirk worth remembering if a future session builds a real-tick or date-range fetch path (`fetch_ticks_range()` likely has the same issue, untested).

- **Actual M1 history depth found: ~3.3 months (101 days)**, 2026-06-02 20:10 UTC → 2026-09-11 20:54 UTC, 99,999 bars (hit against the terminal's `maxbars=100000` cap, so this is that cap, not necessarily the broker's true retention limit — worth re-checking with a fresh terminal/cache if more history is ever needed). Comparable to the gold bot's ~2-3 month finding referenced in §4.
- Requested windows were 1/2/3/4/5/6 months back from now-ish (2026-09-13). **1, 2, and 3-month windows were real, distinct data.** 4, 5, and 6-month windows all silently clipped to the same full ~3.3-month dataset (identical results across all three) since no more history exists — flagged explicitly in the run rather than faked.
- Backtester used unmodified default `StrategyParameters()` (`range_size_points=15`, etc. — none of this run tuned anything, see still-open item in §5).
- Results (`OVERALL` segment, all trades in the window):

  | Window | M1 bars | Range bars | Trades | Win% | PF | Expectancy (R) | Net PnL | Max DD% | OOS Expectancy (R) |
  |---|---|---|---|---|---|---|---|---|---|
  | 1 month | 30,100 | 6,616 | 33 | 36.4% | 1.32 | +0.170 | +$22.39 | 0.38% | +0.357 |
  | 2 months | 60,440 | 19,602 | 57 | 33.3% | 1.12 | +0.079 | +$15.48 | 0.30% | +0.012 |
  | 3 months | 88,919 | 33,801 | 94 | 28.7% | 1.21 | +0.135 | +$47.07 | 0.32% | +0.125 |
  | 4/5/6 months (= full 3.3mo history) | 99,999 | 40,253 | 94 | 26.6% | 1.14 | +0.088 | +$31.35 | 0.56% | **-0.078** |

- Every window's Monte Carlo noise gate reported `PASS` (p=0.0099, the floor value at 100 shuffles) — but this needs a skeptical read, not a "strategy validated" one: (1) the noise gate only sign-flip-shuffles R-multiples, so it mechanically passes whenever raw expectancy is decisively positive and trade count clears the 20-trade floor — it says the R:R asymmetry isn't pure noise, it does **not** say the edge is large or stable; (2) net PnL is tiny in absolute terms (single/double-digit dollars on a $10k initial balance) and win rate is well under 40% throughout; (3) the full-history run's out-of-sample slice (last ~25% of the 3.3mo window, i.e. roughly the back half of August into September) is **net negative** (-0.078R, -$9.61) despite the in-sample slice looking good (+0.223R) — the 1/2/3-month windows' own OOS legs are also thin (3, 17, 34 trades) and trend weaker than in-sample. Read as: directionally the FAILED_AUCTION-dominated ruleset has *some* positive skew on real data, not as evidence it's ready to trade.
- **AAA (absorption) fired in zero trades across every window** (same finding as the earlier synthetic-data smoke test in the entry below) — `SQUEEZE` fired only 1-3 times per window — the strategy is currently ~97% `FAILED_AUCTION` in practice. Worth revisiting `absorption_max_body_ratio`/`volume_spike_mult` per the still-open item in §5, since one of the three playbooks is effectively dead weight on real data.
- The runner is now a real part of the repo: `trading_bot/run_backtest_windows.py` (connects to MT5, pulls the deepest available M1 history via `copy_rates_from_pos`, runs `run_causal_backtest()` per window, prints the table above, writes full per-window metrics — IS/OOS breakdown, model mix, drawdown, Sharpe, z-score — to a timestamped JSON in `backtest_results/`, a new top-level dir, separate from `backups/` which is sqlite-snapshot-only per its own README). Re-run any time with `python trading_bot/run_backtest_windows.py` (optionally pass specific month counts, e.g. `... 1 2 3`). Latest output: `backtest_results/results_20260913_163010.json`.

### 2026-09-13 (cont'd) — Handoff: user takes over from here
User is picking up direct work on this repo now (no longer solely Claude-driven). State at handoff:
- **Git:** local repo initialized and committed (`43d2258`, message "Initial commit: NASDAQ-100 order-flow scalper (USTECm)"). **Not pushed anywhere yet** — no remote configured. If this should end up on GitHub (own repo, or alongside the gold bot's `uzairshaikh346/VWAP-EMA-BOT`), that's a decision to make explicitly, not assume.
- **Tests:** full suite run via `python trading_bot/run_tests.py` → **20/20 passed** (not just the individual pieces mentioned in the entry below).
- **Live engine:** actually started against the real MT5 connection (not just planned) — connected to the demo account (balance under $100 at the time; account details kept out of this public repo), pulled real `USTECm` M1 data, built 272 range bars, computed VAL $29,335 / VAH $29,490 / POC $29,427.5 / CVD +44,767 / ATR $22.01, correctly detected it was outside the NY-open session window, then stopped cleanly on command. This was a short connect-and-observe run — **it never got far enough in time to evaluate a signal or place an order.**
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
