"""
Coordinate-descent-style parameter search for the NASDAQ-100 order-flow scalper,
run against real M15 USTECm data (the timeframe BACKTEST_REPORT.md identified as
the only trustworthy timeframe with a real edge so far).

WHY NOT A FULL GRID: a full cartesian sweep over every tunable parameter is
thousands of combinations on ~11.7 months of data with only ~100-150 trades per
run - testing that many configurations against one fixed dataset and picking the
"best" one is a classic multiple-comparisons overfitting trap; the "winner" would
likely just be the config that best matches noise in this specific historical
sample, not a real improvement. Instead this runs 3 short, targeted stages,
fixing each stage's winner before moving to the next, and ranks candidates by
OUT-OF-SAMPLE expectancy (not overall/in-sample) with a minimum OOS trade count,
specifically to resist overfitting - not to guarantee it can't happen. ANY
"winning" config from this script still needs real forward testing before being
trusted, exactly as BACKTEST_REPORT.md already recommends.

Stage 1: range_size_points (flagged in HANDOFF.md as the single most impactful,
         previously-untuned parameter).
Stage 2: absorption_max_body_ratio x volume_spike_mult (AAA never fired once in
         any prior real-data test - these two gate whether it fires at all).
Stage 3: breakout_min_body_ratio x rr_ratio x min_rr_ratio (Squeeze/Failed
         Auction's body/reward-ratio gates).

Sizing/session config matches live_engine.py's actual live defaults (risk-based
$3/trade, session filter ON) rather than the fixed-lot real-account test from
run_backtest_risk_capped.py, so this optimizes what the bot would actually run.

Usage: python trading_bot/optimize_parameters.py
"""
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from trading_bot.strategy import StrategyParameters
from trading_bot.backtest import run_causal_backtest

SYMBOL = "USTECm"
MAX_BARS = 99999
MIN_OOS_TRADES = 15


def fetch_m15():
    ok = mt5.initialize()
    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        raise RuntimeError(f"Symbol {SYMBOL} not found: {mt5.last_error()}")
    if not info.visible:
        mt5.symbol_select(SYMBOL, True)
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, MAX_BARS)
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Failed to fetch M15 rates: {mt5.last_error()}")
    return rates


def to_arrays(rates):
    times = [datetime.fromtimestamp(r["time"], tz=timezone.utc).isoformat() for r in rates]
    opens = [float(r["open"]) for r in rates]
    highs = [float(r["high"]) for r in rates]
    lows = [float(r["low"]) for r in rates]
    closes = [float(r["close"]) for r in rates]
    volumes = [float(r["tick_volume"]) for r in rates]
    return opens, highs, lows, closes, times, volumes


def evaluate(arrays, params):
    opens, highs, lows, closes, times, volumes = arrays
    try:
        result = run_causal_backtest(
            opens=opens, highs=highs, lows=lows, closes=closes, times=times, volumes=volumes,
            params=params, initial_balance=10000.0,
        )
    except ValueError:
        return None
    ov, oos = result.overall_metrics, result.out_of_sample_metrics
    model_counts = {}
    for t in result.trades:
        model_counts[t.model] = model_counts.get(t.model, 0) + 1
    return {
        "overall_trades": ov.total_trades, "overall_win_pct": ov.win_rate_pct, "overall_pf": ov.profit_factor,
        "overall_expectancy_r": ov.expectancy_r, "overall_net_pnl": ov.total_net_pnl_usd,
        "overall_max_dd_pct": ov.max_drawdown_pct, "overall_noise_gate": ov.noise_gate_passed,
        "oos_trades": oos.total_trades, "oos_win_pct": oos.win_rate_pct, "oos_pf": oos.profit_factor,
        "oos_expectancy_r": oos.expectancy_r, "oos_net_pnl": oos.total_net_pnl_usd,
        "model_mix": model_counts,
    }


def rank_key(metrics):
    """Primary: OOS expectancy (with a minimum trade count to avoid tiny-sample noise). Secondary: overall net PnL."""
    if metrics is None or metrics["oos_trades"] < MIN_OOS_TRADES:
        return (-999.0, -999999.0)
    return (metrics["oos_expectancy_r"], metrics["overall_net_pnl"])


def main():
    print(f"Fetching M15 {SYMBOL} history...")
    rates = fetch_m15()
    arrays = to_arrays(rates)
    print(f"Loaded {len(rates)} M15 candles.\n")

    base = StrategyParameters()
    log = []

    # ---- Stage 1: range_size_points ----
    print("=== Stage 1: range_size_points ===")
    best_range = base.range_size_points
    best_metrics = evaluate(arrays, base)
    best_key = rank_key(best_metrics)
    for v in [8.0, 10.0, 12.0, 15.0, 18.0, 22.0, 26.0, 30.0]:
        p = replace(base, range_size_points=v)
        m = evaluate(arrays, p)
        k = rank_key(m)
        tag = " <- best so far" if m and k > best_key else ""
        if m:
            print(f"  range_size_points={v:5.1f}: overall trades={m['overall_trades']:4d} PF={m['overall_pf']:5.2f} "
                  f"exp_R={m['overall_expectancy_r']:+.3f} | OOS trades={m['oos_trades']:3d} exp_R={m['oos_expectancy_r']:+.3f} "
                  f"net$={m['oos_net_pnl']:+8.2f}{tag}")
        log.append({"stage": 1, "params": {"range_size_points": v}, "metrics": m})
        if m and k > best_key:
            best_key, best_range, best_metrics = k, v, m
    print(f"  WINNER: range_size_points={best_range}\n")
    base = replace(base, range_size_points=best_range)

    # ---- Stage 2: absorption_max_body_ratio x volume_spike_mult ----
    print("=== Stage 2: absorption_max_body_ratio x volume_spike_mult (AAA gating) ===")
    best_absorb, best_spike = base.absorption_max_body_ratio, base.volume_spike_mult
    best_metrics2 = evaluate(arrays, base)
    best_key2 = rank_key(best_metrics2)
    for abr in [0.25, 0.35, 0.45, 0.55]:
        for spike in [1.3, 1.6, 2.0, 2.5]:
            p = replace(base, absorption_max_body_ratio=abr, volume_spike_mult=spike)
            m = evaluate(arrays, p)
            k = rank_key(m)
            aaa_fires = m["model_mix"].get("AAA", 0) if m else 0
            tag = " <- best so far" if m and k > best_key2 else ""
            if m:
                print(f"  abr={abr:.2f} spike={spike:.1f}: overall trades={m['overall_trades']:4d} "
                      f"PF={m['overall_pf']:5.2f} AAA_fires={aaa_fires:3d} | OOS trades={m['oos_trades']:3d} "
                      f"exp_R={m['oos_expectancy_r']:+.3f}{tag}")
            log.append({"stage": 2, "params": {"absorption_max_body_ratio": abr, "volume_spike_mult": spike}, "metrics": m})
            if m and k > best_key2:
                best_key2, best_absorb, best_spike, best_metrics2 = k, abr, spike, m
    print(f"  WINNER: absorption_max_body_ratio={best_absorb}, volume_spike_mult={best_spike}\n")
    base = replace(base, absorption_max_body_ratio=best_absorb, volume_spike_mult=best_spike)

    # ---- Stage 3: breakout_min_body_ratio x rr_ratio x min_rr_ratio ----
    print("=== Stage 3: breakout_min_body_ratio x rr_ratio x min_rr_ratio ===")
    best_bmbr, best_rr, best_minrr = base.breakout_min_body_ratio, base.rr_ratio, base.min_rr_ratio
    best_metrics3 = evaluate(arrays, base)
    best_key3 = rank_key(best_metrics3)
    for bmbr in [0.5, 0.6, 0.7]:
        for rr in [1.5, 2.0, 3.0]:
            for minrr in [1.2, 1.5, 2.0]:
                p = replace(base, breakout_min_body_ratio=bmbr, rr_ratio=rr, min_rr_ratio=minrr)
                m = evaluate(arrays, p)
                k = rank_key(m)
                tag = " <- best so far" if m and k > best_key3 else ""
                if m:
                    print(f"  bmbr={bmbr:.1f} rr={rr:.1f} minrr={minrr:.1f}: overall trades={m['overall_trades']:4d} "
                          f"PF={m['overall_pf']:5.2f} | OOS trades={m['oos_trades']:3d} exp_R={m['oos_expectancy_r']:+.3f}{tag}")
                log.append({"stage": 3, "params": {"breakout_min_body_ratio": bmbr, "rr_ratio": rr, "min_rr_ratio": minrr}, "metrics": m})
                if m and k > best_key3:
                    best_key3, best_bmbr, best_rr, best_minrr, best_metrics3 = k, bmbr, rr, minrr, m
    print(f"  WINNER: breakout_min_body_ratio={best_bmbr}, rr_ratio={best_rr}, min_rr_ratio={best_minrr}\n")
    final_params = replace(base, breakout_min_body_ratio=best_bmbr, rr_ratio=best_rr, min_rr_ratio=best_minrr)

    print("=== FINAL candidate params vs original defaults ===")
    original = StrategyParameters()
    orig_metrics = evaluate(arrays, original)
    final_metrics = evaluate(arrays, final_params)
    print("ORIGINAL:", json.dumps(orig_metrics, indent=2))
    print("TUNED   :", json.dumps(final_metrics, indent=2))

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtest_results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.abspath(os.path.join(out_dir, f"optimization_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"))
    with open(out_path, "w") as f:
        json.dump({
            "symbol": SYMBOL, "timeframe": "M15", "data_fetched_at": datetime.now(timezone.utc).isoformat(),
            "min_oos_trades_filter": MIN_OOS_TRADES,
            "original_params": original.__dict__, "original_metrics": orig_metrics,
            "final_params": final_params.__dict__, "final_metrics": final_metrics,
            "search_log": log,
        }, f, indent=2)
    print(f"\nFull search log written to {out_path}")


if __name__ == "__main__":
    main()
