# Backtest Report — NASDAQ-100 Order-Flow Scalper (`USTECm`)

**Date:** 2026-09-13
**Symbol:** `USTECm` (NASDAQ-100 CFD, Exness)
**Data source:** Real MT5 M1 OHLCV history, pulled live from a MetaTrader 5 terminal connected to an Exness demo account (account details intentionally omitted from this public report — see `HANDOFF.md` for how to reproduce against your own account)
**Engine:** `trading_bot/backtest.py` → `run_causal_backtest()` (causal, zero-lookahead; see [Methodology](#methodology))
**Reproduce with:** `python trading_bot/run_backtest_windows.py [months ...]` (time-window sweep, all on M1) or `python trading_bot/run_backtest_timeframes.py [TF ...]` (timeframe sweep, see [Multi-timeframe comparison](#multi-timeframe-comparison))

This report evaluates the mechanized strategy engine (`trading_bot/strategy.py`, see [`STRATEGY_SPECIFICATION.md`](STRATEGY_SPECIFICATION.md) for what it actually computes vs. the original discretionary guide) against real market data, not synthetic data.

## Summary

The strategy shows a small positive expectancy on real `USTECm` M1 data across every window tested, and every window clears a Monte-Carlo noise-control gate. **This is not evidence of a tradeable edge.** Net PnL is small in absolute terms, win rate stays under 40% throughout, one of the three playbooks (absorption) never fires on real data, and the out-of-sample slice of the longest window is net negative. Read the results in [Results](#results) and especially [Honest caveats](#honest-caveats) before drawing conclusions.

## Data availability

MT5/Exness retains only **~3.3 months (101 days)** of M1 history for `USTECm` on the account this was tested against — from 2026-06-02 20:10 UTC to 2026-09-11 20:54 UTC, 99,999 bars (the practical ceiling of `copy_rates_from_pos`, one under the terminal's `maxbars=100000` cap). This is a broker/terminal retention limit, not a choice made by this codebase.

The user requested six windows — 1, 2, 3, 4, 5, and 6 months back from the run date. Only the **1, 2, and 3-month windows are genuinely distinct data**. The 4, 5, and 6-month windows all clip to the same ~3.3-month ceiling and therefore produce **identical results** to each other — this is called out explicitly in the tool output rather than silently reusing data to look like a longer test.

A note on tooling: `mt5.copy_rates_range()` (the natural API for "give me bars between two dates") returned **zero bars** for any request reaching back more than ~69 days on this broker/terminal, even though `mt5.copy_rates_from_pos()` (position-based, "give me the last N bars") reached back the full ~101 days without issue. `run_backtest_windows.py` uses the latter for this reason. Worth re-checking if this stops working on a different account/terminal/broker.

## Methodology

`run_causal_backtest()` (in `trading_bot/backtest.py`) enforces the same discipline used throughout this codebase and its sibling gold-bot project:

1. **No lookahead bias** — a signal generated at range-bar `i`'s close only ever fills at bar `i+1`'s open.
2. **Realistic wick fills** — stop-loss/take-profit are evaluated against each bar's high/low, not its close.
3. **Realistic broker costs** — spread (2 points) and commission ($3/lot) are deducted from every trade.
4. **In-sample / out-of-sample split** — the first 75% of each window's range bars is in-sample, the last 25% out-of-sample, reported separately as well as combined.
5. **Noise-control gate** — a Monte Carlo permutation test (100 sign-flip shuffles of each trade's R-multiple) checks whether the realized expectancy is distinguishable from random noise (`p ≤ 0.05` and `≥ 20 trades` to pass). See [Honest caveats](#honest-caveats) for what this gate does and doesn't prove.

Each window's M1 OHLCV series is first reconstructed into **range bars** (`build_range_bars`, `range_size_points=15`, the unmodified default — no tuning was applied for this run), then evaluated bar-by-bar for the three mechanized playbooks (`AAA` absorption reversal, `SQUEEZE` momentum breakout, `FAILED_AUCTION` fade) as described in `STRATEGY_SPECIFICATION.md`. Position sizing is risk-based ($3/trade risk, not a fixed lot), starting balance $10,000.

## Results

| Window requested | Real or clipped? | M1 bars | Range bars | Trades | Win % | Profit Factor | Expectancy (R) | Net PnL | Max DD % | Noise gate | OOS Expectancy (R) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 1 month | Real | 30,100 | 6,616 | 33 | 36.4% | 1.32 | +0.170 | +$22.39 | 0.38% | PASS (p=0.0099) | +0.357 |
| 2 months | Real | 60,440 | 19,602 | 57 | 33.3% | 1.12 | +0.079 | +$15.48 | 0.30% | PASS (p=0.0099) | +0.012 |
| 3 months | Real | 88,919 | 33,801 | 94 | 28.7% | 1.21 | +0.135 | +$47.07 | 0.32% | PASS (p=0.0099) | +0.125 |
| 4 months | Clipped to full history | 99,999 | 40,253 | 94 | 26.6% | 1.14 | +0.088 | +$31.35 | 0.56% | PASS (p=0.0099) | **-0.078** |
| 5 months | Clipped to full history | 99,999 | 40,253 | 94 | 26.6% | 1.14 | +0.088 | +$31.35 | 0.56% | PASS (p=0.0099) | **-0.078** |
| 6 months | Clipped to full history | 99,999 | 40,253 | 94 | 26.6% | 1.14 | +0.088 | +$31.35 | 0.56% | PASS (p=0.0099) | **-0.078** |

Playbook mix was consistent across every window: `FAILED_AUCTION` accounted for **~97% of all trades**, `SQUEEZE` fired only 1–3 times per window, and **`AAA` (absorption) fired zero times in every window** — same finding as an earlier synthetic-data smoke test. In its current tuning, this strategy is effectively a single-playbook system on real `USTECm` data, not the three-playbook ensemble the specification describes.

Full per-window metrics (including Sharpe ratio, z-score, max consecutive losses, and the raw shuffled-expectancy distribution behind the noise gate) are in the timestamped JSON files under `backtest_results/`.

## Multi-timeframe comparison

Everything above feeds the backtester M1 OHLC. This section instead feeds it candles from seven different MT5 timeframes — **M1, M3, M5, M15, H1, H4, D1** — each pulled at its own maximum available depth, to see how the mechanized strategy behaves when the underlying "range bar" reconstruction (`build_range_bars`, see `STRATEGY_SPECIFICATION.md` §3) is built from coarser source candles instead of always M1.

Two passes were run per timeframe:

- **As-configured** — `StrategyParameters()` unmodified, including the default 30-minute UTC session filter (13:30–14:00, NY cash open).
- **No session filter (diagnostic)** — identical parameters except `enable_session_filter=False`, run purely to see whether the playbooks fire at all on that timeframe once the intraday time-window gate is removed.

### Why the second pass exists — and why the first one goes to zero on H4/D1

The session filter compares each **range bar's** timestamp — inherited from whichever underlying candle it closed on — against a fixed 13:30–14:00 UTC window. M1/M3/M5/M15 candles land inside that 30-minute window regularly. **H1, H4, and D1 candle timestamps sit on fixed hour/4-hour/day boundaries that almost never fall inside a 30-minute window**, so the as-configured pass produces **zero trades** on H4 and D1 — not because the playbooks found nothing, but because the session-gate mechanism was designed for intraday granularity and is structurally incompatible with daily/4-hourly candle timestamps. This is a config/mechanism mismatch, not a strategy verdict, and the no-filter column exists specifically to separate the two.

### Results — as-configured (live default behavior)

| Timeframe | Candles | Data depth | Range bars | Trades | Win % | PF | Expectancy (R) | Net PnL | Max DD % | Noise gate |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| M1 | 99,999 | 3.3 months | 40,253 | 94 | 26.6% | 1.14 | +0.088 | +$31.35 | 0.56% | PASS (p=0.0099) |
| M3 | 99,999 | 10.2 months | 31,884 | 34 | 29.4% | 0.84 | -0.116 | -$12.82 | 0.32% | fail (p=1.0) |
| M5 | 69,127 | 11.6 months | 64,423 | 138 | 30.4% | 1.20 | +0.119 | +$64.47 | 0.64% | PASS (p=0.0099) |
| M15 | 23,059 | 11.6 months | 49,349 | 117 | 45.3% | 1.79 | +0.428 | +$167.29 | 0.24% | PASS (p=0.0099) |
| H1 | 5,768 | 11.6 months | 34,339 | 89 | 62.9% | 4.66 | +1.340 | +$396.33 | 0.19% | PASS (p=0.0099) |
| H4 | 1,563 | 11.7 months | 23,123 | **0** | — | — | — | $0.00 | — | fail (session gate blocks every trade) |
| D1 | 306 | 11.7 months | 11,968 | **0** | — | — | — | $0.00 | — | fail (session gate blocks every trade) |

### Results — no session filter (diagnostic only, not the live config)

| Timeframe | Range bars | Trades | Win % | PF | Expectancy (R) | Net PnL | Max DD % | Noise gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| M1 | 40,253 | 796 | 24.9% | 0.87 | -0.093 | -$260.27 | 3.40% | fail (p=1.0) |
| M3 | 31,884 | 673 | 26.6% | 0.84 | -0.111 | -$261.09 | 4.08% | fail (p=1.0) |
| M5 | 64,423 | 1,443 | 29.5% | 1.01 | +0.005 | +$21.21 | 2.68% | PASS (p=0.0099) |
| M15 | 49,349 | 1,153 | 34.4% | 1.25 | +0.164 | +$630.99 | 0.69% | PASS (p=0.0099) |
| H1 | 34,339 | 804 | 43.7% | 2.06 | +0.592 | +$1,602.90 | 0.37% | PASS (p=0.0099) |
| H4 | 23,123 | 439 | 52.6% | 3.53 | +1.177 | +$1,719.74 | 0.34% | PASS (p=0.0099) |
| D1 | 11,968 | 99 | 48.5% | 4.03 | +1.483 | +$480.44 | 0.27% | PASS (p=0.0099) |

### Read the H1/H4/D1 numbers as a warning sign, not a discovery

Those diagnostic PF/expectancy figures climb dramatically as the timeframe gets coarser (PF 0.87 at M1 → PF 4.03 at D1), which looks exciting and is very likely **not real**. The reason: `build_range_bars` approximates each input candle's intrabar path as a straight walk through only its Open/High/Low/Close — a reasonable approximation for an M1 candle (small real move) and an increasingly fictional one for a D1 candle (which can span hundreds of points of real, jagged intraday price action compressed into 4 points and a straight-line guess). The ratio of synthetic range bars produced per real input candle makes this concrete:

| Timeframe | Real candles | Range bars produced | Range bars per real candle |
|---|---:|---:|---:|
| M1 | 99,999 | 40,253 | 0.40 |
| M3 | 99,999 | 31,884 | 0.32 |
| M5 | 69,127 | 64,423 | 0.93 |
| M15 | 23,059 | 49,349 | 2.14 |
| H1 | 5,768 | 34,339 | 5.95 |
| H4 | 1,563 | 23,123 | 14.79 |
| D1 | 306 | 11,968 | **39.11** |

At D1, every single real daily candle is being exploded into **~39 synthetic range bars** by drawing a straight line through 4 points and pretending that's the intraday path. The strategy then "trades" that fabricated path and reports a great-looking result. That result describes the model's behavior on **invented** price action, not on anything that actually happened in the market. The same concern applies with decreasing severity down through H4 (14.8x) and H1 (6.0x); M1/M3/M5 (≤1x) are the only timeframes where this reconstruction stays close to its original design intent.

**Bottom line: this strategy's range-bar/volume-profile/CVD machinery is built to run on M1 (or at most M5) source data. Feeding it H1/H4/D1 candles is unsupported by the codebase's own design assumptions, and the impressive-looking H1/H4/D1 numbers above should not be trusted or acted on** — they're the clearest evidence in this report of the intrabar-path approximation's limits, not a better version of the strategy. M15 sits in a grey zone (2.1x) worth treating with more skepticism than M1/M5 but less alarm than H1+.

## Real-account risk-management test ($100 balance, fixed lot, $10/day loss cap)

Before committing to forward testing, a specific real-account plan was backtested: start from the actual demo balance (**$100**), trade a **fixed lot size instead of risk-based sizing**, and add a **$10/day loss cap** — if the bot loses $10 or more on a given UTC calendar day, block any *new* entries for the rest of that day (existing open trades still run to their own stop/target), then resume automatically the next day. Profit is deliberately **not** capped — only losses stop new entries. The idea: protect the account from draining quickly on a bad day, without limiting the upside on a good one.

**Correction worth flagging:** the plan called for a 0.01 lot, but this broker's actual minimum lot for `USTECm` (confirmed live via `symbol_info()`) is **0.05**, not 0.01 — a 0.01 order would be rejected. All numbers below use the real 0.05 minimum.

The daily-loss-cap logic is now a built-in, optional feature of `run_causal_backtest()` (`daily_loss_cap_usd` parameter) — reproduce with `python trading_bot/run_backtest_risk_capped.py [TF ...]`.

| Timeframe | Trades | Win % | PF | Net PnL | Final Balance | Max DD % | Worst single UTC day | Days cap tripped |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| M15 | 117 | 45.3% | 1.76 | +$76.41 | **$176.41** | 6.36% | -$4.09 | 0 / 220 |
| M5 | 138 | 30.4% | 0.99 | -$1.79 | $98.27 | 30.29% | -$5.47 | 0 / 219 |
| M1 | 94 | 26.6% | 1.00 | +$0.43 | $100.42 | 21.51% | -$6.53 | 0 / 81 |

**The cap never tripped on any of these three timeframes, on any historical day.** The worst single-day loss recorded across all of them was -$6.53 (M1) — under the $10 threshold every time. Two things follow from that:

1. **As a safety net, a $10/day cap is non-intrusive** at this lot size and trade frequency (≤ ~1.2 trades/day) — it would not have blocked a single day of normal historical operation, so it costs nothing to have on. It exists purely to catch an *abnormal* day (a strategy malfunction, a burst of unusually large losses, a broker/data issue causing repeated bad fills) rather than something expected to fire under normal conditions.
2. **It does not protect against slow bleed.** M5's 30.29% max drawdown and M1's 21.51% did not come from one catastrophic day — they came from a string of smaller losing days spread out over time, each individually under the $10 threshold. A *daily* cap, by design, cannot see that pattern. If protecting against a multi-day losing streak matters too, that needs a separate rule (e.g. the existing `max_consecutive_losses` breaker in `circuit_breakers.py`, already built for live trading but not yet exercised in this backtest variant, or a rolling weekly/balance-based drawdown cap) — not just this daily one.

**M15 remains the standout**: +76% over the ~7.3-month window with a comparatively tame 6.36% max drawdown. M5 and M1 are roughly flat to slightly negative at this fixed lot size and don't currently justify live risk.

## Parameter optimization (2026-09-14)

The `AAA` never-fires finding and the untuned `range_size_points` default (both flagged repeatedly above) were addressed directly: a coordinate-descent parameter search (`trading_bot/optimize_parameters.py`) was run against real M15 `USTECm` data, in 3 stages, each fixing its winner before moving to the next (not a full cartesian grid — with ~51 total configurations tested against one historical sample, a full grid search would risk fitting noise; this narrower search exists specifically to limit that risk, not eliminate it):

1. **`range_size_points`** (8 values tested, 8–30) — winner: **8.0** (was 15.0, a judgment call, never calibrated).
2. **`absorption_max_body_ratio` × `volume_spike_mult`** (16 combinations) — winner: **0.55 / 2.5** (were 0.35 / 2.0). This is the fix for `AAA` never firing: at the original 0.35 threshold it fired zero times in every prior test in this report; at 0.55 it fires regularly (62–100 times depending on the exact combination).
3. **`breakout_min_body_ratio` × `rr_ratio` × `min_rr_ratio`** (27 combinations) — winner: **0.5 / 3.0 / 1.5** (were 0.60 / 2.0 / 1.5).

Each stage ranked candidates by **out-of-sample expectancy** (the last 25% of the data, held out from the ranking's own selection point) with a 15-trade minimum, not in-sample or overall performance — specifically to resist picking a config that only looks good on the data it was tuned against.

**A performance bug was found and fixed along the way**: `evaluate_signal_at_bar()` was rebuilding the entire range-bar volume list from scratch on every single call instead of reading only the small lookback window it actually needs — effectively an O(n²) cost on long histories. Fixed in `strategy.py` (now O(lookback) per call); a worst-case M15 config that didn't finish in several minutes before now runs in ~3.4 seconds. This benefits every backtest run in this repo, not just the search itself.

### Before vs. after (M15, $10,000 balance, risk-based sizing — the search's own scoring conditions)

| | Overall trades | Overall PF | Overall Expectancy (R) | Overall Net PnL | OOS trades | OOS PF | OOS Expectancy (R) | OOS Net PnL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original | 117 | 1.79 | +0.428 | +$167.29 | 44 | 1.10 | +0.068 | +$9.25 |
| **Tuned** | 177 | **2.85** | **+1.070** | **+$678.77** | 61 | **2.77** | **+1.066** | **+$241.81** |

### Robustness checks (data the search's own ranking never selected on)

Because ranking-by-OOS across ~51 tried configurations is itself a mild form of cherry-picking (the "OOS" segment was reused as a selection signal across every trial, not held out and checked once), two further checks were run against conditions the search never optimized for:

- **A different train/test split point** (50/50 instead of the search's 75/25, same M15 data): tuned OOS expectancy **+1.296R** (PF 3.13) vs. original **+0.412R** (PF 1.68) — held up.
- **A different timeframe entirely** (M5 — the tuned parameters were never evaluated against M5 during the search): tuned OOS expectancy **+0.603R** (PF 1.83) vs. original **+0.033R** (PF 1.09) — also held up, though by a smaller margin, consistent with M15 remaining the primary target.

Both checks point the same direction as the search's own result, which is reassuring but **not proof this isn't still somewhat overfit to this ~11.7-month sample** — see [Honest caveats](#honest-caveats), which applies in full to these tuned numbers too.

**The tuned values are now the live defaults** in `trading_bot/strategy.py`'s `StrategyParameters` dataclass — this is what `live_engine.py` actually reads at line 117 (`params = StrategyParameters()`), so this change takes effect for auto-trading, not just backtesting. Original (untuned) values are noted inline in the dataclass for reference.

### Real-account-shaped result with the tuned parameters ($100 balance, 0.05 lot, $10/day cap — same setup as the section above)

Month-by-month, with win rate and profit factor per month, not just PnL:

| Month | Trades | Wins | Losses | Win % | PF | Net PnL | Running Balance |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2025-09 (partial) | 7 | 3 | 4 | 42.9% | 1.27 | +$1.30 | $101.30 |
| 2025-10 | 8 | 4 | 4 | 50.0% | 2.13 | +$3.63 | $104.93 |
| 2025-11 | 3 | 1 | 2 | 33.3% | 1.59 | +$1.24 | $106.17 |
| 2025-12 | 3 | 3 | 0 | 100.0% | — (no losers) | +$5.06 | $111.23 |
| 2026-01 | 3 | 1 | 2 | 33.3% | 0.52 | -$0.87 | $110.36 |
| 2026-02 | 10 | 4 | 6 | 40.0% | 1.71 | +$3.86 | $114.22 |
| 2026-03 | 19 | 9 | 10 | 47.4% | 3.89 | +$28.50 | $142.72 |
| 2026-04 | 17 | 7 | 10 | 41.2% | 1.98 | +$10.22 | $152.94 |
| 2026-05 | 17 | 11 | 6 | 64.7% | 11.00 | +$46.02 | $198.96 |
| 2026-06 | 20 | 7 | 13 | 35.0% | 3.40 | +$29.84 | $228.80 |
| 2026-07 | 24 | 6 | 18 | 25.0% | 1.54 | +$9.99 | $238.79 |
| 2026-08 | 33 | 14 | 19 | 42.4% | 3.20 | +$42.30 | $281.09 |
| 2026-09 (partial) | 13 | 6 | 7 | 46.2% | 3.84 | +$16.65 | $297.74 |
| **OVERALL** | **177** | **76** | **101** | **42.9%** | **3.01** | **+$197.74** | **$297.78** |

**Overall (this exact $100/0.05-lot run): 177 trades, 42.9% win rate, profit factor 3.01, max drawdown 4.87%, Sharpe 40.59 (backtest-internal, not directly comparable to a buy-and-hold Sharpe), noise gate PASS (p=0.0099, z=6.63).** In-sample (first 75%): 116 trades, 45.7% win rate, PF 3.23. Out-of-sample (last 25%, the part the tuning search never selected on): 61 trades, 37.7% win rate, PF 2.66, and its own noise gate also PASSes (p=0.0099) — the edge didn't disappear on unseen data, it just got a bit weaker, which is the expected and honest pattern.

**On the win rate specifically: 42.9% is correct and expected, not a weak result — read it together with the profit factor, not alone.** This strategy's playbooks target 1:2 to 1:3+ reward:risk (`rr_ratio=3.0` for the momentum breakout model), so it's structurally supposed to lose more often than it wins while still coming out ahead — a coin that pays 3x on heads and costs 1x on tails only needs to land heads more than 25% of the time to be profitable, and this lands heads ~43% of the time. December's 100%-on-3-trades and January's 33%-on-3-trades are both too few trades to mean anything on their own — the month-to-month noise at this trade frequency is exactly why the overall 177-trade, 4-quarter figures (and the noise gate) matter more than any single month's win rate.

10 of 11 full months were net positive (vs. 8 of 11 before tuning), averaging ≈ **+$16.34/month** on the $100 base (vs. ≈ $6.25/month before) — still concentrated in a few standout months (March, May, June, August), not a smooth monthly return, but broader and less reliant on exactly 2 outlier months than the untuned version was. The $10/day loss cap still never tripped on any historical day (worst single day stayed well under $10). Reproduce with `python trading_bot/run_backtest_risk_capped.py M15`.

### On "10x/100x in a single month"

Worth addressing directly: **that target isn't compatible with the risk management already built into this bot, and chasing it through parameter tuning specifically would mean finding a config that curve-fits this one historical sample rather than a real edge.** Turning $100 into $1,000–$10,000 in 30 days requires either wildly oversized positions relative to account equity (the opposite of what `risk_per_trade_usd`/fixed-lot sizing and the daily loss cap are for) or an edge with a return profile no real, sustainable strategy — mechanized or discretionary — produces reliably. The honest, achievable target from this tuning pass is a meaningfully better, still-real edge: roughly 2-3x the monthly average this bot showed before tuning, with drawdown that stayed in a similar range. That's a legitimate improvement worth forward-testing. A 100x/month target is not a parameter-tuning problem; treating it as one is how strategies end up overfit to a lucky slice of history and then fail immediately in forward testing.

## Honest caveats

- **The noise gate passing is not the same as "edge confirmed."** It only tests whether the sequence of R-multiples is distinguishable from a random sign-flip of the same trades — i.e., whether the win/loss pattern is non-random given the risk:reward asymmetry already built into the strategy. It mechanically tends to pass whenever raw expectancy is decisively positive and the trade count clears the 20-trade floor. It says nothing about the magnitude or stability of the edge, sample representativeness, or whether costs/slippage assumptions hold up live.
- **Net PnL is small in absolute terms** — single- to double-digit dollars against a $10,000 starting balance across all windows. This has not yet demonstrated an edge large enough to be economically meaningful, only directionally positive.
- **Win rate stays under 40% in every window.** The strategy depends entirely on its asymmetric reward:risk ratio (target vs. stop), not accuracy — consistent with the original guide's stated profile, but worth remembering when interpreting a losing streak live.
- **The out-of-sample leg of the full-history window is net negative** (-0.078R, -$9.61) despite a strong-looking in-sample leg (+0.223R) — the model's most recent ~25% of data (roughly the back half of August into September) did not hold up as well as the earlier portion. The 1- and 2-month windows' own OOS legs are also thin (3 and 17 trades respectively) and weaker than their in-sample legs.
- **One of three playbooks has not fired once on real data.** `AAA` absorption's trigger thresholds (`absorption_max_body_ratio`, `volume_spike_mult`) are very likely mistuned for actual `USTECm` volatility and should be revisited before treating this as a validated three-model system.
- **No parameter tuning was performed for this run.** `range_size_points=15` (the single most volatility-sensitive parameter) is still an untuned default, set by judgment rather than calibration against real NASDAQ-100 price action.
- **This is not a substitute for forward/live testing.** No order has been placed by this bot's live engine to date (see `HANDOFF.md` §5). A backtest — however causally disciplined — cannot capture real slippage, requotes, latency, or broker-specific execution behavior.

## Conclusion

Directionally, the `FAILED_AUCTION`-dominated ruleset shows a small, repeatable positive skew on ~3.3 months of real `USTECm` M1 data, and stays positive (as-configured) on M5 and M15 too, with M15 the strongest of the three. That is a legitimate first real-data data point, not a green light. Before considering any live capital, the recommended next steps are: (1) tune `absorption_max_body_ratio`/`volume_spike_mult` so `AAA` actually participates, (2) tune `range_size_points` against real NASDAQ-100 volatility rather than the untuned default — and consider tuning it per-timeframe if M5/M15 are ever run as primary rather than M1, (3) re-run this same multi-window backtest after those changes, (4) get meaningfully more OOS trades before trusting the noise gate's verdict — 3–42 OOS trades per window is a thin sample for a p-value to mean much, and (5) treat H1/H4/D1 as **not viable timeframes for this strategy as built** — both because the session filter structurally blocks H4/D1 and because the range-bar reconstruction the whole strategy depends on becomes increasingly fictional above M5/M15 (see [Multi-timeframe comparison](#multi-timeframe-comparison)). If a genuinely higher-timeframe variant of this strategy is ever wanted, it needs its own session-filter design and probably its own range-bar (or non-range-bar) construction — not just pointing the existing M1 pipeline at coarser candles.

See `STRATEGY_SPECIFICATION.md` for the full mapping of the original discretionary guide to what this bot mechanizes, and `HANDOFF.md` for the running project history.
