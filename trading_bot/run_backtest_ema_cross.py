"""
Real-data causal backtest of a new candidate strategy (not yet the live bot):

  15m 9EMA/21EMA cross DOWN  -> SHORT.  SL = high of the signal candle (real 15m bar). RR 1:2.
  15m 9EMA/21EMA cross UP    -> LONG.   SL = low of the signal candle (real 15m bar).  RR 1:2.

Default SL source is the M15 bar itself (--sl-tf m15, real M15 data, full ~13mo history on
this account). --sl-tf m1 uses the literal "1m candle" reading instead (real M1 data, capped
~3.3mo by this broker's maxbars).

Causality: signal confirmed on the CLOSED 15m bar i -> fill at bar i+1's open (+spread on
BUY). SL/TP are fixed prices computed from the signal bar's close and its SL, exactly what a
live engine would send as order SL/TP (matches this repo's other backtest scripts). If SL and
TP both fall inside the same 15m bar, SL is assumed hit first (conservative). One position at
a time. P&L convention: $1 per $1 move at 0.01 lot (rest of this repo).

Usage: python -m trading_bot.run_backtest_ema_cross [--data DIR] [--sl-tf m1|m15]
"""
import argparse
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from trading_bot.run_backtest_gold_fib import load_bars

EMA_FAST, EMA_SLOW, RR = 9, 21, 2.0
SPREAD = 0.25
LOT = 0.01


@dataclass
class Tr:
    dir: str
    entry_i: int
    exit_i: int
    entry: float
    sl: float
    tp: float
    pnl: float
    r: float
    reason: str
    day: object


def run(df15: pd.DataFrame, df1: Optional[pd.DataFrame], lot=LOT, spread=SPREAD, rr=RR,
        ema_fast=EMA_FAST, ema_slow=EMA_SLOW, m1_lookback=1) -> List[Tr]:
    """SL source:
    - df1 given: high/low of the last m1_lookback 1-minute candles ending when the 15m signal
      bar closes (1 = the literal "1m candle" reading). Real M1 history caps this to ~3.3 months.
    - df1=None: pure-M15 mode - SL is the signal bar's OWN high/low (the real 15m candle that
      confirmed the cross). Uses the full real M15 history, no M1 dependency."""
    o, h, l, c = (df15[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t15 = df15["time"]
    ema_f = df15["close"].ewm(span=ema_fast, adjust=False).mean().to_numpy()
    ema_s = df15["close"].ewm(span=ema_slow, adjust=False).mean().to_numpy()
    diff = ema_f - ema_s
    date = t15.dt.date.to_numpy()
    n = len(c)
    mult = lot * 100.0

    if df1 is None:
        def m1_extreme_at_close(bar_close_time, want_high: bool, i=None):
            return h[i] if want_high else l[i]
    else:
        m1_time = df1["time"]
        m1_h, m1_l = df1["high"].to_numpy(float), df1["low"].to_numpy(float)

        def m1_extreme_at_close(bar_close_time, want_high: bool, i=None):
            # high/low of the last m1_lookback 1m candles strictly before bar_close_time
            j = m1_time.searchsorted(bar_close_time, side="left") - 1
            if j < 0 or m1_time.iloc[j] < bar_close_time - pd.Timedelta(minutes=15):
                return None
            lo = max(0, j - m1_lookback + 1)
            return m1_h[lo:j + 1].max() if want_high else m1_l[lo:j + 1].min()

    trades: List[Tr] = []
    pos = None
    pending = None
    for i in range(n):
        if pending is not None and pos is None:
            d, sl, tp = pending
            fill = o[i] + spread if d == "BUY" else o[i]
            risk = (fill - sl) if d == "BUY" else (sl - fill)
            if risk > 0:
                pos = dict(d=d, sl=sl, tp=tp, entry=fill, ei=i, risk=risk)
            pending = None
        if pos is not None:
            d = pos["d"]
            if d == "BUY":
                sl_hit, tp_hit = l[i] <= pos["sl"], h[i] >= pos["tp"]
            else:
                sl_hit, tp_hit = h[i] + spread >= pos["sl"], l[i] + spread <= pos["tp"]
            hit = ("SL", pos["sl"]) if sl_hit else (("TP", pos["tp"]) if tp_hit else None)
            if hit:
                gross = ((hit[1] - pos["entry"]) if d == "BUY" else (pos["entry"] - hit[1])) * mult
                r = gross / (pos["risk"] * mult)
                trades.append(Tr(d, pos["ei"], i, pos["entry"], pos["sl"], pos["tp"], gross, r, hit[0], date[i]))
                pos = None
        if i == 0 or i >= n - 1:
            continue
        crossed_up = diff[i - 1] <= 0 and diff[i] > 0
        crossed_dn = diff[i - 1] >= 0 and diff[i] < 0
        if not (crossed_up or crossed_dn) or pos is not None or pending is not None:
            continue
        bar_close_time = t15.iloc[i] + pd.Timedelta(minutes=15)
        if crossed_up:
            sl = m1_extreme_at_close(bar_close_time, want_high=False, i=i)
            if sl is None or sl >= c[i]:
                continue
            risk = c[i] - sl
            pending = ("BUY", sl, c[i] + rr * risk)
        else:
            sl = m1_extreme_at_close(bar_close_time, want_high=True, i=i)
            if sl is None or sl <= c[i]:
                continue
            risk = sl - c[i]
            pending = ("SELL", sl, c[i] - rr * risk)
    return trades


def report(trades: List[Tr], label: str, days: int):
    n = len(trades)
    if n == 0:
        print(f"{label}: 0 trades")
        return
    wins = [t for t in trades if t.pnl > 0]
    gp = sum(t.pnl for t in wins)
    gl = -sum(t.pnl for t in trades if t.pnl <= 0)
    pf = gp / gl if gl > 0 else float("inf")
    net = sum(t.pnl for t in trades)
    traded_days = len(set(t.day for t in trades))
    print(f"{label:16s} n={n:4d}  win={100*len(wins)/n:4.1f}%  PF={pf:5.2f}  net=${net:+8.2f}  "
          f"trades/day={n/days:.2f}  trades/traded-day={n/traded_days:.2f}  traded {traded_days}/{days} days")


def pf_of(trades: List[Tr]) -> float:
    if not trades:
        return 0.0
    gp = sum(t.pnl for t in trades if t.pnl > 0)
    gl = -sum(t.pnl for t in trades if t.pnl <= 0)
    return gp / gl if gl > 0 else float("inf")


def sweep(df15, df1):
    """Grid-search RR and SL width (m1_lookback) over the full M1-covered window, split
    in-sample (first 65%) / out-of-sample (last 35%) so a combo only counts if it holds up
    on data it wasn't picked to fit - avoids just curve-fitting ~3 months of noise."""
    m1_start = df1.time.iloc[0]
    end = df15.time.iloc[-1]
    d15 = df15[df15.time >= m1_start - pd.Timedelta(hours=6)].reset_index(drop=True)
    d1 = df1.reset_index(drop=True)
    split = m1_start + (end - m1_start) * 0.65

    rows = []
    for rr in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        for lb in (1, 3, 5, 10, 15, 30):
            tr = [t for t in run(d15, d1, rr=rr, m1_lookback=lb) if d15.time[t.entry_i] >= m1_start]
            ins = [t for t in tr if d15.time[t.entry_i] < split]
            oos = [t for t in tr if d15.time[t.entry_i] >= split]
            if len(ins) < 20 or len(oos) < 10:
                continue
            rows.append(dict(rr=rr, lb=lb, n=len(tr), pf_in=pf_of(ins), pf_out=pf_of(oos),
                              net=sum(t.pnl for t in tr)))
    rows.sort(key=lambda r: r["pf_out"], reverse=True)
    print(f"\nsplit: in-sample < {split.date()}, out-of-sample >= {split.date()}")
    print(f"{'RR':>4} {'lookback(min)':>13} {'n':>5} {'PF in':>7} {'PF out':>7} {'net':>10}")
    for r in rows[:12]:
        print(f"{r['rr']:>4.1f} {r['lb']:>13d} {r['n']:>5d} {r['pf_in']:>7.2f} {r['pf_out']:>7.2f} ${r['net']:>+9.2f}")
    if not rows:
        print("(no combo reached the min sample size on the data available)")


TIMEFRAMES = ("M5", "M15", "M30", "H1", "H4", "D1")


def multi_tf(data_dir):
    """Same strategy (9/21 EMA cross, SL = signal candle's own high/low, RR 1:2), run
    independently on each timeframe's own real bars - SL/TP scale with that timeframe."""
    for tf in TIMEFRAMES:
        df = load_bars(tf, data_dir)
        end = df.time.iloc[-1]
        full = run(df, None)
        recent_start = end - pd.DateOffset(months=6)
        d = df[df.time >= recent_start].reset_index(drop=True)
        recent = [t for t in run(d, None) if d.time[t.entry_i] >= recent_start]
        span_full = (end.date() - df.time.iloc[0].date()).days + 1
        span_recent = (end.date() - recent_start.date()).days + 1

        def line(trades, days):
            n = len(trades)
            if n == 0:
                return "n=0"
            wins = [t for t in trades if t.pnl > 0]
            net = sum(t.pnl for t in trades)
            return f"n={n:4d} win={100*len(wins)/n:4.1f}% PF={pf_of(trades):5.2f} net=${net:+8.2f} tr/day={n/days:.2f}"

        print(f"{tf:>4} {len(df):7d} {str(df.time.iloc[0].date())}->{str(end.date())}")
        print(f"     full: {line(full, span_full)}")
        print(f"     6mo:  {line(recent, span_recent)}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None)
    ap.add_argument("--sweep", action="store_true", help="grid-search RR x SL-width instead of the fixed 1:2 baseline")
    ap.add_argument("--sl-tf", choices=("m1", "m15"), default="m15",
                     help="SL source: m1 = literal '1m candle' (real M1 data, capped ~3.3mo); "
                          "m15 = the signal bar's own high/low (real M15 data, full history). Default: m15")
    ap.add_argument("--multi-tf", action="store_true", help=f"run the strategy on each of {TIMEFRAMES} instead of just M15")
    a = ap.parse_args(argv)
    if a.multi_tf:
        multi_tf(a.data)
        return
    df15 = load_bars("M15", a.data)
    print(f"M15: {len(df15)} bars {df15.time.iloc[0]} -> {df15.time.iloc[-1]}")

    if a.sl_tf == "m1":
        df1 = load_bars("M1", a.data)
        print(f"M1:  {len(df1)} bars {df1.time.iloc[0]} -> {df1.time.iloc[-1]}  <- SL data; caps how far back this strategy can be tested")
        if a.sweep:
            sweep(df15, df1)
            return
        start_floor = df1.time.iloc[0]
    else:
        df1 = None
        print("SL source: the 15m signal candle's own high/low (real M15 bars only, no M1 dependency)")
        if a.sweep:
            raise SystemExit("--sweep only supports --sl-tf m1 for now")
        start_floor = df15.time.iloc[0]

    end = df15.time.iloc[-1]
    windows = (1, 2, 3, 6) if a.sl_tf == "m15" else (1, 2, 3)
    for months in windows:
        start = max(start_floor, end - pd.DateOffset(months=months))
        d15 = df15[(df15.time >= start - pd.Timedelta(hours=6)) & (df15.time <= end)].reset_index(drop=True)
        d1 = None if df1 is None else df1[(df1.time >= start - pd.Timedelta(hours=6)) & (df1.time <= end)].reset_index(drop=True)
        tr = [t for t in run(d15, d1) if d15.time[t.entry_i] >= start]
        span_days = (end.date() - start.date()).days + 1
        report(tr, f"{months} month(s)", span_days)
    if a.sl_tf == "m15" and start_floor < end - pd.DateOffset(months=6):
        tr = [t for t in run(df15, None) if df15.time[t.entry_i] >= start_floor]
        span_days = (end.date() - start_floor.date()).days + 1
        report(tr, "full history", span_days)


if __name__ == "__main__":
    main()
