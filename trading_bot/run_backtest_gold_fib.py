"""
Real-data causal backtest of the GOLD strategy in gold_strategy.py
(Fibonacci daily pivots + EMA9, SL = N x signal-candle range, RR 1:2, EMA-overextension "shield").

Same rules as eval_gold_signal() but vectorised/incremental so it can walk ~100k M1 bars
(eval_gold_signal recomputes the EMA over the whole frame on every call). `--selfcheck`
proves the two agree on sampled bars.

Causality: signal on CLOSED bar i -> fill at bar i+1 open (+spread on BUY, bid on SELL).
SL/TP are the absolute prices computed from the signal close (exactly what the live engine
sends). Exits use bar high/low with the spread on the ask side for shorts; if SL and TP are
both inside one bar the SL is assumed hit first (conservative). One position at a time.
P&L convention (same as the rest of this repo / friend's screenshots): $1 per $1 move at 0.01 lot.

Real MT5 history only - no synthetic data.

Usage:
  python -m trading_bot.run_backtest_gold_fib [--data DIR] [--selfcheck]
"""

import argparse
import math
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from trading_bot.gold_strategy import GoldStrategyParameters, eval_gold_signal

PIP = 0.10
LEVELS = ["PP", "R1", "R2", "R3", "S1", "S2", "S3"]
DEFAULT_DATA_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache"),
    r"D:\mine\Bots\VWAP-EMA-BOT\trading_bot\data_cache",
]


def load_bars(timeframe: str, data_dir: Optional[str] = None) -> pd.DataFrame:
    dirs = [data_dir] if data_dir else DEFAULT_DATA_DIRS
    for d in dirs:
        p = os.path.join(d, f"XAUUSDm_{timeframe}.parquet")
        if os.path.exists(p):
            df = pd.read_parquet(p)
            df["time"] = pd.to_datetime(df["time"], utc=True)
            return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    raise FileNotFoundError(f"XAUUSDm_{timeframe}.parquet not found in {dirs}")


def prepare(df: pd.DataFrame, ema_period: int):
    t = df["time"]
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    ema = df["close"].ewm(span=ema_period, adjust=False).mean().to_numpy()
    date = t.dt.date.to_numpy()
    hour = t.dt.hour.to_numpy()
    day_stats: Dict = {}
    for i, d in enumerate(date):
        s = day_stats.get(d)
        if s is None:
            day_stats[d] = [h[i], l[i], c[i]]
        else:
            s[0] = max(s[0], h[i]); s[1] = min(s[1], l[i]); s[2] = c[i]
    days = sorted(day_stats)
    prev_of = {days[k]: days[k - 1] for k in range(1, len(days))}
    return o, h, l, c, ema, date, hour, day_stats, prev_of


def pivots_for(day, day_stats, prev_of):
    pd_ = prev_of.get(day)
    if pd_ is None:
        return None
    H, L, C = day_stats[pd_]
    pp = (H + L + C) / 3.0
    r = H - L
    return {"PP": pp, "R1": pp + .382 * r, "R2": pp + .618 * r, "R3": pp + r,
            "S1": pp - .382 * r, "S2": pp - .618 * r, "S3": pp - r}


def signal_at(i, o, h, l, c, ema, hour, date, day_stats, prev_of, p: GoldStrategyParameters,
              last_bar: Dict[str, int], session=None, use_cooldown=True, lv_fn=None):
    """Returns (type, level, sl_pips) or None. Mutates last_bar exactly like eval_gold_signal."""
    s0, s1 = session if session else (p.session_start_utc_hour, p.session_end_utc_hour)
    if p.enable_session_filter and (hour[i] < s0 or hour[i] >= s1):
        return None
    if p.max_ema_distance_pips > 0 and abs(c[i] - ema[i]) / PIP > p.max_ema_distance_pips:
        return None
    lv = lv_fn(i) if lv_fn else pivots_for(date[i], day_stats, prev_of)
    if lv is None or i < 10:
        return None
    rng_pips = (h[i] - l[i]) / PIP
    buf = p.buffer_pips * PIP
    for name in LEVELS:
        if use_cooldown and name in last_bar and (i - last_bar[name]) < p.cooldown_bars:
            continue
        v = lv[name]
        if c[i - 1] < v and c[i] > v + buf and c[i] > ema[i] and c[i] > o[i] and rng_pips >= p.min_candle_range_pips:
            last_bar[name] = i
            return "BUY", name, max(rng_pips * p.sl_candle_range_multiplier, p.min_sl_pips)
        if c[i - 1] > v and c[i] < v - buf and c[i] < ema[i] and c[i] < o[i] and rng_pips >= p.min_candle_range_pips:
            last_bar[name] = i
            return "SELL", name, max(rng_pips * p.sl_candle_range_multiplier, p.min_sl_pips)
    return None


@dataclass
class Tr:
    dir: str
    level: str
    entry_i: int
    exit_i: int
    entry: float
    sl: float
    tp: float
    sl_pips: float
    pnl: float
    r: float
    reason: str
    day: object


def run(df, p: GoldStrategyParameters, lot=0.01, spread=0.25, commission_per_lot=0.0,
        daily_loss_cap: Optional[float] = None, session=None, be=False, live_window: Optional[int] = None, tp_first: bool = False):
    o, h, l, c, ema, date, hour, day_stats, prev_of = prepare(df, p.ema_period)
    n = len(c)
    mult = lot * 100.0
    last_bar: Dict[str, int] = {}
    trades: List[Tr] = []
    pos = None
    pending = None
    day_pnl: Dict = {}
    lv_fn = None
    if live_window:
        # Emulates gold_live_engine.py: eval_gold_signal only sees the last `live_window` bars, so
        # 'previous day' = the latest earlier date INSIDE that window (partial day on M1).
        dord = np.array([d.toordinal() for d in date])
        def lv_fn(i):
            ws = max(0, i - live_window + 1)
            dw = dord[ws:i + 1]
            m = dw < dord[i]
            if m.any():
                pdate = dw[m].max(); sel = np.where(dw == pdate)[0] + ws
            else:
                if i - ws < 1: return None
                sub = dord[ws:i]; pdate = sub.max(); sel = np.where(sub == pdate)[0] + ws
            H = h[sel].max(); L = l[sel].min(); C = c[sel[-1]]
            pp = (H + L + C) / 3.0; r = H - L
            return {"PP": pp, "R1": pp + .382 * r, "R2": pp + .618 * r, "R3": pp + r,
                    "S1": pp - .382 * r, "S2": pp - .618 * r, "S3": pp - r}
    for i in range(n):
        if pending is not None and pos is None:
            d, lvl, sl, tp, sl_pips = pending
            fill = o[i] + spread if d == "BUY" else o[i]
            risk = (fill - sl) if d == "BUY" else (sl - fill)
            if risk > 0:
                pos = dict(d=d, lvl=lvl, sl=sl, tp=tp, entry=fill, ei=i, sl_pips=sl_pips, risk=risk)
            pending = None
        if pos is not None:
            d = pos["d"]
            hit = None
            if d == "BUY":
                sl_hit, tp_hit = l[i] <= pos["sl"], h[i] >= pos["tp"]
            else:
                sl_hit, tp_hit = h[i] + spread >= pos["sl"], l[i] + spread <= pos["tp"]
            if tp_first:  # optimistic: TP wins when both are inside one candle (NOT conservative)
                hit = ("TP", pos["tp"]) if tp_hit else (("SL", pos["sl"]) if sl_hit else None)
            else:
                hit = ("SL", pos["sl"]) if sl_hit else (("TP", pos["tp"]) if tp_hit else None)
            if hit:
                gross = ((hit[1] - pos["entry"]) if d == "BUY" else (pos["entry"] - hit[1])) * mult
                net = gross - commission_per_lot * lot
                r = net / (pos["risk"] * mult + commission_per_lot * lot)
                trades.append(Tr(d, pos["lvl"], pos["ei"], i, pos["entry"], pos["sl"], pos["tp"],
                                 pos["sl_pips"], net, r, hit[0], date[i]))
                day_pnl[date[i]] = day_pnl.get(date[i], 0.0) + net
                pos = None
        if i >= n - 1:
            break
        sig = signal_at(i, o, h, l, c, ema, hour, date, day_stats, prev_of, p, last_bar, session, lv_fn=lv_fn)
        if sig and pos is None and pending is None:
            if daily_loss_cap is not None and day_pnl.get(date[i], 0.0) <= -daily_loss_cap:
                continue
            d, lvl, sl_pips = sig
            e = c[i]
            sl = e - sl_pips * PIP if d == "BUY" else e + sl_pips * PIP
            tp = e + sl_pips * p.rr_ratio * PIP if d == "BUY" else e - sl_pips * p.rr_ratio * PIP
            pending = (d, lvl, sl, tp, sl_pips)
    return trades


def metrics(trades: List[Tr], initial=100.0, shuffles=2000, seed=7):
    n = len(trades)
    if n == 0:
        return dict(n=0)
    pnl = np.array([t.pnl for t in trades]); r = np.array([t.r for t in trades])
    wins = pnl[pnl > 0]; losses = pnl[pnl <= 0]
    gp, gl = wins.sum(), -losses.sum()
    eq = initial + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[initial], eq]))[1:]
    dd = float((peak - eq).max())
    # One-sided bootstrap: P(mean R <= 0) under resampling of the observed trades.
    # (The repo's run_noise_control_gate null is biased - it forces half the trades negative - so it is NOT used here.)
    rs = np.random.RandomState(seed)
    boots = rs.choice(r, size=(shuffles, n), replace=True).mean(axis=1)
    real = r.mean()
    p = float((boots <= 0).mean())
    se = r.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
    return dict(n=n, win=100 * len(wins) / n, pf=(gp / gl) if gl > 0 else float("inf"), net=float(pnl.sum()),
                exp_r=float(real), t=float(real / se) if se else float("nan"), p=p, dd=dd,
                avg_sl=float(np.mean([t.sl_pips for t in trades])))


def fmt(label, m):
    if m["n"] == 0:
        return f"{label:<46} no trades"
    return (f"{label:<46} n={m['n']:<5} win={m['win']:5.1f}%  PF={m['pf']:5.2f}  net=${m['net']:+8.2f}  "
            f"expR={m['exp_r']:+.3f}  t={m['t']:+5.2f}  p={m['p']:.3f}  maxDD=${m['dd']:.1f}  avgSL={m['avg_sl']:.1f}pip")


def selfcheck(df: pd.DataFrame, p: GoldStrategyParameters, samples=400):
    """Compare incremental signal_at() with the real eval_gold_signal() on sampled bars (no cooldown)."""
    p2 = GoldStrategyParameters(**{**p.__dict__, "cooldown_bars": 0, "enable_session_filter": False})
    o, h, l, c, ema, date, hour, day_stats, prev_of = prepare(df, p2.ema_period)
    idx = np.random.RandomState(1).choice(np.arange(3000, len(df)), samples, replace=False)
    # bias sample towards bars that actually signal
    sig_idx = [i for i in range(3000, len(df)) if signal_at(i, o, h, l, c, ema, hour, date, day_stats, prev_of, p2, {}, use_cooldown=False)]
    idx = sorted(set(idx.tolist()) | set(sig_idx[:samples]))
    bad = 0; both_sig = 0
    for i in idx:
        mine = signal_at(i, o, h, l, c, ema, hour, date, day_stats, prev_of, p2, {}, use_cooldown=False)
        sub = df.iloc[max(0, i - 4000): i + 1].reset_index(drop=True)
        ref, _ = eval_gold_signal(sub, p2, {})
        a = (mine[0], mine[1]) if mine else (None, None)
        b = (ref.signal_type, ref.trigger_level)
        if mine and ref.signal_type:
            both_sig += 1
            sl_ref = abs(ref.suggested_entry - ref.suggested_sl) / PIP
            if abs(sl_ref - mine[2]) > 0.05:
                bad += 1; print("SL mismatch", i, sl_ref, mine[2])
        if a != b:
            bad += 1; print("MISMATCH", i, df["time"].iloc[i], a, b)
    print(f"selfcheck: {len(idx)} bars compared ({both_sig} with signals), mismatches={bad}")
    return bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None)
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    P = GoldStrategyParameters()
    print("Strategy defaults:", {k: getattr(P, k) for k in (
        "ema_period", "buffer_pips", "cooldown_bars", "min_candle_range_pips", "max_ema_distance_pips",
        "sl_candle_range_multiplier", "rr_ratio", "min_sl_pips", "fixed_lot_size",
        "session_start_utc_hour", "session_end_utc_hour")})
    for tf in ("M1", "M5"):
        df = load_bars(tf, a.data)
        print(f"\n===== {tf}: {len(df)} real bars {df['time'].iloc[0]} -> {df['time'].iloc[-1]} =====")
        if a.selfcheck:
            selfcheck(df.iloc[-40000:].reset_index(drop=True) if tf == "M1" else df.iloc[-20000:].reset_index(drop=True), P)
        for mult in (1.5, 2.0):
            PM = GoldStrategyParameters(**{**P.__dict__, "sl_candle_range_multiplier": mult})
            for name, sess in (("00-20 UTC (=05:00-01:00 PKT)", (0, 20)), ("07-20 UTC London+NY", (7, 20)), ("24h", (0, 24))):
                tr = run(df, PM, session=sess)
                print(fmt(f"{tf} SL={mult}x {name}", metrics(tr)))
        # live-config default, with the live $10/day loss cap, and with cost sensitivity
        tr = run(df, P, daily_loss_cap=10.0)
        print(fmt(f"{tf} LIVE default (2.0x, 00-20, $10 cap)", metrics(tr)))
        for sp in (0.0, 0.25, 0.40):
            print(fmt(f"{tf} LIVE default spread=${sp:.2f}", metrics(run(df, P, spread=sp))))

        if tf == "M5":
            last = df.iloc[-12000:].reset_index(drop=True)
            print(f"  -- last 12,000 M5 bars ({last['time'].iloc[0]} -> {last['time'].iloc[-1]}), the friend's stated data size")
            print(fmt(f"{tf} LIVE default, last 12k bars", metrics(run(last, P))))
        tr = run(df, P)
        by = {}
        for t in tr:
            by.setdefault(str(df['time'].iloc[t.entry_i])[:7], []).append(t)
        print("  -- LIVE default, per calendar month (stability check):")
        for k in sorted(by):
            m = metrics(by[k])
            print(f"     {k}: n={m['n']:<4} win={m['win']:5.1f}%  PF={m['pf']:5.2f}  net=${m['net']:+8.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
