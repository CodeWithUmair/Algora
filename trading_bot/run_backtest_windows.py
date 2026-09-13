"""
Multi-window real-data backtest runner for the NASDAQ-100 (USTECm) order-flow scalper.

Connects to the live MT5 terminal, fetches the deepest available M1 history, then
runs the causal backtester (backtest.run_causal_backtest) on cumulative "last N
months" windows. Windows beyond the broker's actual retention depth are clipped to
the full available history and flagged as such, not faked.

Usage:
    python trading_bot/run_backtest_windows.py [months ...]

    python trading_bot/run_backtest_windows.py            # defaults to 1 2 3 4 5 6
    python trading_bot/run_backtest_windows.py 1 2 3       # only these windows

Note: uses mt5.copy_rates_from_pos(count=99999) rather than copy_rates_range() -
on this broker/terminal, copy_rates_range() was found to return 0 bars for any
window beyond ~69 days back even though pos-based queries reach further. A
broker/terminal quirk, not a documented MT5 API limitation - re-verify if this
stops working on a different account/terminal.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from trading_bot.strategy import StrategyParameters
from trading_bot.backtest import run_causal_backtest

SYMBOL = "USTECm"
MAX_M1_BARS = 99999  # just under the terminal's maxbars=100000 cap


def fetch_full_m1_history(symbol: str):
    ok = mt5.initialize()
    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol {symbol} not found: {mt5.last_error()}")
    if not info.visible:
        mt5.symbol_select(symbol, True)

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, MAX_M1_BARS)
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Failed to fetch M1 rates for {symbol}: {mt5.last_error()}")
    return rates


def main():
    months_list = [int(a) for a in sys.argv[1:]] or [1, 2, 3, 4, 5, 6]

    rates = fetch_full_m1_history(SYMBOL)
    times = [datetime.fromtimestamp(r["time"], tz=timezone.utc) for r in rates]
    opens = [float(r["open"]) for r in rates]
    highs = [float(r["high"]) for r in rates]
    lows = [float(r["low"]) for r in rates]
    closes = [float(r["close"]) for r in rates]
    volumes = [float(r["tick_volume"]) for r in rates]
    times_iso = [t.isoformat() for t in times]

    earliest, latest = times[0], times[-1]
    total_days = (latest - earliest).days
    print(f"Fetched {len(rates)} M1 bars for {SYMBOL}: {earliest} -> {latest} "
          f"({total_days} days, {total_days / 30.44:.1f} months)")

    params = StrategyParameters()  # defaults, unmodified
    results = []

    for months_back in months_list:
        cutoff = latest - timedelta(days=months_back * 30)
        clipped = cutoff < earliest
        effective_cutoff = max(cutoff, earliest)

        start_idx = 0
        for i, t in enumerate(times):
            if t >= effective_cutoff:
                start_idx = i
                break

        w_times = times_iso[start_idx:]
        print(f"\n=== Window: last {months_back} month(s) requested (from {cutoff.date()}) ===")
        if clipped:
            print(f"  NOTE: only {total_days / 30.44:.1f} months of M1 history exist on this broker/account; "
                  f"clipped to full available history starting {earliest.date()}")
        print(f"  M1 bars in window: {len(w_times)} ({w_times[0]} -> {w_times[-1]})")

        try:
            result = run_causal_backtest(
                opens=opens[start_idx:], highs=highs[start_idx:], lows=lows[start_idx:],
                closes=closes[start_idx:], times=w_times, volumes=volumes[start_idx:],
                params=params, initial_balance=10000.0,
            )
        except ValueError as e:
            print(f"  SKIPPED: {e}")
            results.append({"window_months_requested": months_back, "clipped_to_available": clipped, "error": str(e)})
            continue

        ov, is_m, oos_m = result.overall_metrics, result.in_sample_metrics, result.out_of_sample_metrics
        print(f"  Range bars: {result.num_range_bars}")
        print(f"  OVERALL : trades={ov.total_trades:3d} win%={ov.win_rate_pct:6.2f} PF={ov.profit_factor:5.2f} "
              f"exp_R={ov.expectancy_r:+.3f} net$={ov.total_net_pnl_usd:+9.2f} maxDD%={ov.max_drawdown_pct:5.2f} "
              f"noise_gate={'PASS' if ov.noise_gate_passed else 'fail'} p={ov.noise_p_value}")
        print(f"  IN-SAMP : trades={is_m.total_trades:3d} win%={is_m.win_rate_pct:6.2f} PF={is_m.profit_factor:5.2f} "
              f"exp_R={is_m.expectancy_r:+.3f} net$={is_m.total_net_pnl_usd:+9.2f}")
        print(f"  OUT-SAMP: trades={oos_m.total_trades:3d} win%={oos_m.win_rate_pct:6.2f} PF={oos_m.profit_factor:5.2f} "
              f"exp_R={oos_m.expectancy_r:+.3f} net$={oos_m.total_net_pnl_usd:+9.2f}")

        model_counts: dict = {}
        for t in result.trades:
            model_counts[t.model] = model_counts.get(t.model, 0) + 1
        print(f"  Model mix: {model_counts}")

        results.append({
            "window_months_requested": months_back, "clipped_to_available": clipped,
            "window_start": w_times[0], "window_end": w_times[-1],
            "m1_bars": len(w_times), "range_bars": result.num_range_bars,
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
        })

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtest_results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.abspath(os.path.join(out_dir, f"results_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"))
    with open(out_path, "w") as f:
        json.dump({
            "symbol": SYMBOL, "data_fetched_at": datetime.now(timezone.utc).isoformat(),
            "earliest_available_bar": earliest.isoformat(), "latest_available_bar": latest.isoformat(),
            "total_days_available": total_days, "strategy_params": params.__dict__,
            "windows": results,
        }, f, indent=2)
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
