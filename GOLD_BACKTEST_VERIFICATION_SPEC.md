# Gold Strategy Backtest Verification Spec (Fib Pivot + EMA9)

Run 2026-09-20 against branch `GOLD`, commit `83f4f03` (`gold_strategy.py`). Written so anyone (your friend, or their AI coding agent) can re-run the exact same test and compare numbers.

**Supersedes** `VWAP-EMA-BOT/BACKTEST_VERIFICATION_SPEC.md`, which tested the wrong strategy (an unrelated EMA/VWAP/OrderBlock bot) and should be ignored.

Reproduce: `python -m trading_bot.run_backtest_gold_fib --selfcheck` (script: `trading_bot/run_backtest_gold_fib.py`).

## 1. The strategy under test (exactly `gold_strategy.py`, all defaults, nothing tuned)

- Daily Fibonacci pivots from the previous UTC day's H/L/C: `PP=(H+L+C)/3`, `R1/R2/R3 = PP + 0.382/0.618/1.0 * (H-L)`, `S1/S2/S3 = PP - ...`.
- EMA 9 on closes (`ewm(span=9, adjust=False)`).
- BUY: previous close < level, close > level, close > EMA9, green candle. SELL: mirror image.
- Candle range >= 2 pips (1 pip = $0.10). Per-level cooldown 10 bars. Buffer 0 pips.
- "Shield": skip if |close - EMA9| > 15 pips.
- SL = max(2.0 x signal-candle range, 8 pips); TP = 2.0 x SL (RR 1:2). No break-even, no trailing.
- Session 00:00-20:00 UTC (= 05:00 AM-01:00 AM PKT). Fixed lot 0.01. Live daily loss cap $10.
- `timeframe_str` default in the code is **"M1"** (what the live engine trades).

## 2. Data

Real Exness `XAUUSDm` bars pulled from MT5 (parquet caches; **no synthetic data, no fixed seed**).

| TF | Bars | Range (UTC) |
|---|---|---|
| M1 | 102,699 | 2026-06-07 -> 2026-09-18 |
| M5 | 98,271 | 2025-05-01 -> 2026-09-18 |
| M5 "friend window" | last 12,000 | 2026-07-21 -> 2026-09-18 |

## 3. Simulation rules (causal)

- Signal on a CLOSED bar i; fill at bar i+1 open (+ $0.25 spread on BUY; SELL fills at bid). SL/TP are the absolute prices computed from the signal close (what the live engine sends).
- Exits from bar high/low; shorts pay the spread on the ask side; if SL and TP are both inside one bar, SL is assumed first. One position at a time.
- P&L: $1 per $1 move at 0.01 lot. Commission 0. Balance $100.
- Self-check: the fast backtest was compared with the real `eval_gold_signal()` on 1,395 sampled bars (incl. 607 signal bars): **0 mismatches** (signal, level and SL pips).
- Significance: bootstrap p = P(mean R <= 0) plus t-stat on R-multiples. NOTE: the repo's older `run_noise_control_gate` is **not** used - its null distribution forces half the trades negative, so it passes almost any positive result (earlier "PASS" results made with it are unreliable).

## 4. Results

**Friend's window (last 12,000 M5 bars), live defaults:** 69 trades, win 44.9%, PF 1.94, net +$418.80, expR +0.34, t=+1.89, bootstrap p=0.027, max DD $72.5, avg SL 127 pips.
Friend's claimed: 43-44% win, PF 1.6-1.7. **This window reproduces the claim** (different trade count / avg SL, so not identical).

**Same strategy, wider/other data (live defaults, spread $0.25):**

| Data | Trades | Win | PF | Net | t | p |
|---|---|---|---|---|---|---|
| M5 full 16 months, 00-20 UTC | 575 | 32.5% | 1.07 | +$282 | -0.66 | 0.75 |
| M5 full, $10/day cap | 465 | 34.4% | 1.17 | +$549 | +0.27 | 0.39 |
| M1 full 3.4 months, 00-20 UTC | 430 | 30.5% | 0.88 | -$204 | -1.67 | 0.96 |
| M1, $10/day cap | 256 | 30.9% | 0.90 | -$109 | -1.11 | 0.86 |
| M1, spread $0.00 | 419 | 32.0% | 0.95 | -$79 | -0.58 | 0.73 |

**M5 by month (live defaults):** May-Dec 2025 mostly losing (PF 0.45-0.92, one +month in Oct); Jan-Sep 2026 profitable every month (PF 1.08-2.95). **M1 lost money in every month** (Jun-Sep 2026, PF 0.81-0.98).

## 5. What this means

1. The friend's numbers are real **for M5 over the last ~2 months**, not fabricated.
2. That result is regime-dependent: the same rules were flat/losing across 2025 and are not statistically significant over the full 16 months. A 2-month window of 69 trades is thin evidence.
3. **The live bot trades M1** (`timeframe_str="M1"`), and M1 loses in every month tested. The friend's good result is M5.
4. Avg SL on M5 is ~127 pips = $12.7 loss per trade at 0.01 lot on a $100 account - larger than the $10 daily loss cap, so one loss can lock the day.

## 6. To match this on another machine

Same symbol (`XAUUSDm`, Exness), real MT5 history for the same windows, same defaults, same fill/cost model above. If the friend's tool reports different avg SL pips or trade counts on the same data, compare: timeframe used, whether the previous-day pivot uses the full UTC day, spread/commission assumptions, and whether SL/TP are from the signal close or the fill.

## 7. Is the backtest "AI"? Do results depend on the model?

No. It is plain deterministic Python (`gold_strategy.py` rules + `run_backtest_gold_fib.py` simulator). Same data + same code = the same numbers on any machine, with any AI model (or none). The AI only wrote/ran the code. The complete rule set is sections 1 and 3 above; nothing else is used.

## 8. Re-check on freshly pulled MT5 data (2026-09-20, terminal started, demo acct, last tick Fri 2026-09-18 20:57 UTC)

Reproduce: `python -m trading_bot.fetch_mt5_history` then `python -m trading_bot.run_backtest_gold_fib --selfcheck`.

- M5, last 12,000 bars: 69 trades, win 44.9%, PF 1.94, +$418.80 (identical on re-pull -> deterministic).
- M1 (100,000 bars = 3.3 months, terminal cap): 414 trades, win 30.4%, PF 0.88, -$190.

**Why the friend may see M1 as best: not reproducible here.** The friend's screenshot table says "M1", but their own description says M5 / 12,000 candles, and the M5 12k-bar window is the one that reproduces their numbers (44.9% vs 44.44%, PF 1.94 vs 1.71). M1 variants tried on real M1 bars, none profitable in a meaningful way (best PF ~1.06, not significant):
live-engine 1000-bar pivot window (PF 1.03), SL 1.0x/1.5x (0.87/0.90), zero spread (0.97), TP-first fills + zero spread (0.98), cooldown 30 (0.83), shield 8 pips (0.95), RR 1:1.5 (0.90), RR 1:3 (1.05), sessions 07-20 / 07-12 / 13-20 UTC (0.97 / 1.06 / 1.00).
Also the friend's M1 row shows avg SL 28.7 pips; real M1 gives ~55 pips with these rules, so the simulator/data differ.

**For the friend to settle it:** run the same two commands above on their laptop with the timeframe set to M1 and to M5, and send the trade count, win %, PF, avg SL pips and the first 5 trade timestamps. Any difference in avg SL pips or trade count on the same dates points to a different data feed or different rules.
