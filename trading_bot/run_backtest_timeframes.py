"""
Multi-timeframe real-data backtest runner for the NASDAQ-100 (USTECm) order-flow scalper.

Feeds the causal backtester (backtest.run_causal_backtest) with OHLCV pulled at
each of several MT5 candle timeframes (M1, M3, M5, M15, H1, H4, D1) instead of
always M1, using the deepest history MT5 will give for that timeframe. Two passes
are run per timeframe:

  1. "as-configured" - StrategyParameters() unmodified, including the default
     30-minute UTC session filter (13:30-14:00, NY cash open).
  2. "no session filter" - same params with enable_session_filter=False.

The second pass exists because the session filter compares each RANGE BAR's
timestamp (inherited from the underlying candle it closed on) against a narrow
30-minute window. M1/M3/M5/M15 candles land inside that window regularly; H1/H4/D1
candle timestamps are fixed at hour/4-hour/day boundaries that mostly do NOT fall
inside a 13:30-14:00 window, so the as-configured pass can show zero or
near-zero trades on coarser timeframes for a config-alignment reason, not because
the underlying playbooks found nothing. Reporting both makes that distinction
visible instead of misreading "0 trades" as "no edge at all timeframes above M15".

Also note: range bar reconstruction (build_range_bars) approximates each input
candle's intrabar path as a straight Open->Low->High->Close or Open->High->Low->Close
walk. That approximation is reasonable for M1 candles (small moves) and gets
progressively cruder for H4/D1 candles, which can each span hundreds of points -
treat H4/D1 results as a rougher signal than M1-M15, not equally trustworthy.

Usage:
    python trading_bot/run_backtest_timeframes.py [TF ...]

    python trading_bot/run_backtest_timeframes.py                  # defaults to M1 M3 M5 M15 H1 H4 D1
    python trading_bot/run_backtest_timeframes.py M1 M15 H1         # only these
"""
import copy
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
MAX_BARS = 99999  # just under the terminal's maxbars=100000 cap, applies at every timeframe

TF_MAP = {
    "M1": None, "M3": None, "M5": None, "M15": None, "H1": None, "H4": None, "D1": None,
}


def _resolve_tf_map():
    return {
        "M1": mt5.TIMEFRAME_M1, "M3": mt5.TIMEFRAME_M3, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }


def fetch_history(symbol: str, tf_const):
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, MAX_BARS)
    if rates is None or len(rates) == 0:
        return None
    return rates


def run_one(tf_name: str, rates, params: StrategyParameters, label: str):
    times = [datetime.fromtimestamp(r["time"], tz=timezone.utc).isoformat() for r in rates]
    opens = [float(r["open"]) for r in rates]
    highs = [float(r["high"]) for r in rates]
    lows = [float(r["low"]) for r in rates]
    closes = [float(r["close"]) for r in rates]
    volumes = [float(r["tick_volume"]) for r in rates]

    try:
        result = run_causal_backtest(
            opens=opens, highs=highs, lows=lows, closes=closes,
            times=times, volumes=volumes, params=params, initial_balance=10000.0,
        )
    except ValueError as e:
        print(f"  [{label}] SKIPPED: {e}")
        return {"error": str(e)}

    ov, is_m, oos_m = result.overall_metrics, result.in_sample_metrics, result.out_of_sample_metrics
    model_counts: dict = {}
    for t in result.trades:
        model_counts[t.model] = model_counts.get(t.model, 0) + 1

    print(f"  [{label:20s}] range_bars={result.num_range_bars:6d} trades={ov.total_trades:4d} "
          f"win%={ov.win_rate_pct:6.2f} PF={ov.profit_factor:5.2f} exp_R={ov.expectancy_r:+.3f} "
          f"net$={ov.total_net_pnl_usd:+9.2f} maxDD%={ov.max_drawdown_pct:5.2f} "
          f"noise_gate={'PASS' if ov.noise_gate_passed else 'fail'} p={ov.noise_p_value} models={model_counts}")

    return {
        "range_bars": result.num_range_bars,
        "overall": {
            "trades": ov.total_trades, "win_rate_pct": ov.win_rate_pct, "profit_factor": ov.profit_factor,
            "expectancy_r": ov.expectancy_r, "expectancy_usd": ov.expectancy_usd,
            "total_net_pnl_usd": ov.total_net_pnl_usd, "max_drawdown_pct": ov.max_drawdown_pct,
            "max_drawdown_usd": ov.max_drawdown_usd, "sharpe_ratio": ov.sharpe_ratio,
            "noise_gate_passed": ov.noise_gate_passed, "noise_p_value": ov.noise_p_value,
            "z_score": ov.z_score, "max_consecutive_losses": ov.max_consecutive_losses,
            "payoff_ratio": ov.payoff_ratio,
        },
        "in_sample": {
            "trades": is_m.total_trades, "win_rate_pct": is_m.win_rate_pct, "profit_factor": is_m.profit_factor,
            "expectancy_r": is_m.expectancy_r, "total_net_pnl_usd": is_m.total_net_pnl_usd,
        },
        "out_of_sample": {
            "trades": oos_m.total_trades, "win_rate_pct": oos_m.win_rate_pct, "profit_factor": oos_m.profit_factor,
            "expectancy_r": oos_m.expectancy_r, "total_net_pnl_usd": oos_m.total_net_pnl_usd,
        },
        "model_mix": model_counts,
        "initial_balance": result.initial_balance, "final_balance": result.final_balance,
    }


def main():
    requested = [a.upper() for a in sys.argv[1:]] or ["M1", "M3", "M5", "M15", "H1", "H4", "D1"]

    ok = mt5.initialize()
    if not ok:
        print("MT5 initialize failed:", mt5.last_error())
        sys.exit(1)
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"Symbol {SYMBOL} not found: {mt5.last_error()}")
        sys.exit(1)
    if not info.visible:
        mt5.symbol_select(SYMBOL, True)

    tf_map = _resolve_tf_map()
    base_params = StrategyParameters()
    no_session_params = replace(base_params, enable_session_filter=False)

    results = {}
    for tf_name in requested:
        if tf_name not in tf_map:
            print(f"\nSkipping unknown timeframe: {tf_name}")
            continue
        rates = fetch_history(SYMBOL, tf_map[tf_name])
        if rates is None:
            print(f"\n=== {tf_name}: no data returned ({mt5.last_error()}) ===")
            results[tf_name] = {"error": "no data returned"}
            continue

        first = datetime.fromtimestamp(rates[0]["time"], tz=timezone.utc)
        last = datetime.fromtimestamp(rates[-1]["time"], tz=timezone.utc)
        days = (last - first).days
        print(f"\n=== {tf_name}: {len(rates)} candles, {first} -> {last} ({days} days, {days/30.44:.1f} months) ===")

        as_configured = run_one(tf_name, rates, base_params, "as-configured")
        no_filter = run_one(tf_name, rates, no_session_params, "no session filter")

        results[tf_name] = {
            "candles": len(rates), "window_start": first.isoformat(), "window_end": last.isoformat(),
            "days_available": days,
            "as_configured": as_configured,
            "no_session_filter": no_filter,
        }

    mt5.shutdown()

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtest_results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.abspath(os.path.join(out_dir, f"timeframes_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"))
    with open(out_path, "w") as f:
        json.dump({
            "symbol": SYMBOL, "data_fetched_at": datetime.now(timezone.utc).isoformat(),
            "strategy_params_as_configured": base_params.__dict__,
            "timeframes": results,
        }, f, indent=2)
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
