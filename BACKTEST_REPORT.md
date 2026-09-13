# Backtest Report — NASDAQ-100 Order-Flow Scalper (`USTECm`)

**Date:** 2026-09-13
**Symbol:** `USTECm` (NASDAQ-100 CFD, Exness)
**Data source:** Real MT5 M1 OHLCV history, pulled live from a MetaTrader 5 terminal connected to an Exness demo account (account details intentionally omitted from this public report — see `HANDOFF.md` for how to reproduce against your own account)
**Engine:** `trading_bot/backtest.py` → `run_causal_backtest()` (causal, zero-lookahead; see [Methodology](#methodology))
**Reproduce with:** `python trading_bot/run_backtest_windows.py [months ...]`

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

## Honest caveats

- **The noise gate passing is not the same as "edge confirmed."** It only tests whether the sequence of R-multiples is distinguishable from a random sign-flip of the same trades — i.e., whether the win/loss pattern is non-random given the risk:reward asymmetry already built into the strategy. It mechanically tends to pass whenever raw expectancy is decisively positive and the trade count clears the 20-trade floor. It says nothing about the magnitude or stability of the edge, sample representativeness, or whether costs/slippage assumptions hold up live.
- **Net PnL is small in absolute terms** — single- to double-digit dollars against a $10,000 starting balance across all windows. This has not yet demonstrated an edge large enough to be economically meaningful, only directionally positive.
- **Win rate stays under 40% in every window.** The strategy depends entirely on its asymmetric reward:risk ratio (target vs. stop), not accuracy — consistent with the original guide's stated profile, but worth remembering when interpreting a losing streak live.
- **The out-of-sample leg of the full-history window is net negative** (-0.078R, -$9.61) despite a strong-looking in-sample leg (+0.223R) — the model's most recent ~25% of data (roughly the back half of August into September) did not hold up as well as the earlier portion. The 1- and 2-month windows' own OOS legs are also thin (3 and 17 trades respectively) and weaker than their in-sample legs.
- **One of three playbooks has not fired once on real data.** `AAA` absorption's trigger thresholds (`absorption_max_body_ratio`, `volume_spike_mult`) are very likely mistuned for actual `USTECm` volatility and should be revisited before treating this as a validated three-model system.
- **No parameter tuning was performed for this run.** `range_size_points=15` (the single most volatility-sensitive parameter) is still an untuned default, set by judgment rather than calibration against real NASDAQ-100 price action.
- **This is not a substitute for forward/live testing.** No order has been placed by this bot's live engine to date (see `HANDOFF.md` §5). A backtest — however causally disciplined — cannot capture real slippage, requotes, latency, or broker-specific execution behavior.

## Conclusion

Directionally, the `FAILED_AUCTION`-dominated ruleset shows a small, repeatable positive skew on ~3.3 months of real `USTECm` data. That is a legitimate first real-data data point, not a green light. Before considering any live capital, the recommended next steps are: (1) tune `absorption_max_body_ratio`/`volume_spike_mult` so `AAA` actually participates, (2) tune `range_size_points` against real NASDAQ-100 volatility rather than the untuned default, (3) re-run this same multi-window backtest after those changes, and (4) get meaningfully more OOS trades before trusting the noise gate's verdict — 3–42 OOS trades per window is a thin sample for a p-value to mean much.

See `STRATEGY_SPECIFICATION.md` for the full mapping of the original discretionary guide to what this bot mechanizes, and `HANDOFF.md` for the running project history.
