"""
Real-account-shaped backtest: $100 starting balance, fixed 0.05 lot (this
broker's actual minimum for USTECm - confirmed live via symbol_info(), NOT
the 0.01 originally asked for, which this broker would reject), and a
$10/day loss cap (no profit cap) that halts new entries for the rest of the
UTC day once tripped, then resumes automatically the next day.

This mirrors a specific plan: run the bot 24/7 on a VPS, accept that some
sessions will be bad for it, but stop taking NEW risk for the day once losses
hit $10 rather than limiting how much it's allowed to make. Backtested here
(not live) to see what that rule would have done to these specific real
demo-account numbers before committing to forward testing.

Runs both WITH and WITHOUT the daily cap on each requested timeframe, so the
cap's actual effect (trades skipped, days tripped, final balance difference)
is visible rather than assumed.

Usage:
    python trading_bot/run_backtest_risk_capped.py [TF ...]

    python trading_bot/run_backtest_risk_capped.py            # defaults to M15 M5 M1
    python trading_bot/run_backtest_risk_capped.py M15         # only M15
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
from trading_bot.strategy import StrategyParameters
from trading_bot.backtest import run_causal_backtest

SYMBOL = "USTECm"
MAX_BARS = 99999
INITIAL_BALANCE = 100.0
FIXED_LOT_SIZE = 0.05
DAILY_LOSS_CAP_USD = 10.0

TF_CONST_NAMES = {
    "M1": "TIMEFRAME_M1", "M3": "TIMEFRAME_M3", "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1",
}


def fetch_history(symbol: str, tf_const):
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, MAX_BARS)
    if rates is None or len(rates) == 0:
        return None
    return rates


def run_variant(rates, params, label, daily_cap):
    times = [datetime.fromtimestamp(r["time"], tz=timezone.utc).isoformat() for r in rates]
    opens = [float(r["open"]) for r in rates]
    highs = [float(r["high"]) for r in rates]
    lows = [float(r["low"]) for r in rates]
    closes = [float(r["close"]) for r in rates]
    volumes = [float(r["tick_volume"]) for r in rates]

    result = run_causal_backtest(
        opens=opens, highs=highs, lows=lows, closes=closes, times=times, volumes=volumes,
        params=params, initial_balance=INITIAL_BALANCE,
        fixed_lot_size=FIXED_LOT_SIZE, daily_loss_cap_usd=daily_cap,
    )
    ov = result.overall_metrics
    tripped_days = [d for d in result.daily_pnl_log if d["cap_tripped"]]
    total_days = len(result.daily_pnl_log)
    print(f"  [{label:22s}] trades={ov.total_trades:4d} win%={ov.win_rate_pct:6.2f} PF={ov.profit_factor:5.2f} "
          f"exp_R={ov.expectancy_r:+.3f} net$={ov.total_net_pnl_usd:+8.2f} final_bal=${result.final_balance:8.2f} "
          f"maxDD%={ov.max_drawdown_pct:5.2f}"
          + (f" | cap tripped {len(tripped_days)}/{total_days} days" if daily_cap is not None else ""))

    return {
        "trades": ov.total_trades, "win_rate_pct": ov.win_rate_pct, "profit_factor": ov.profit_factor,
        "expectancy_r": ov.expectancy_r, "total_net_pnl_usd": ov.total_net_pnl_usd,
        "final_balance": result.final_balance, "max_drawdown_pct": ov.max_drawdown_pct,
        "max_drawdown_usd": ov.max_drawdown_usd, "max_consecutive_losses": ov.max_consecutive_losses,
        "noise_gate_passed": ov.noise_gate_passed, "noise_p_value": ov.noise_p_value,
        "days_total": total_days, "days_cap_tripped": len(tripped_days),
        "daily_pnl_log": result.daily_pnl_log,
    }


def main():
    requested = [a.upper() for a in sys.argv[1:]] or ["M15", "M5", "M1"]

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

    params = StrategyParameters()
    results = {}

    for tf_name in requested:
        const_name = TF_CONST_NAMES.get(tf_name)
        if const_name is None:
            print(f"\nSkipping unknown timeframe: {tf_name}")
            continue
        tf_const = getattr(mt5, const_name)
        rates = fetch_history(SYMBOL, tf_const)
        if rates is None:
            print(f"\n=== {tf_name}: no data ({mt5.last_error()}) ===")
            continue

        first = datetime.fromtimestamp(rates[0]["time"], tz=timezone.utc)
        last = datetime.fromtimestamp(rates[-1]["time"], tz=timezone.utc)
        print(f"\n=== {tf_name}: {len(rates)} candles, {first.date()} -> {last.date()} | "
              f"${INITIAL_BALANCE:.0f} start, {FIXED_LOT_SIZE} lot fixed ===")

        no_cap = run_variant(rates, params, "no daily cap", None)
        with_cap = run_variant(rates, params, f"${DAILY_LOSS_CAP_USD:.0f}/day cap", DAILY_LOSS_CAP_USD)

        results[tf_name] = {
            "candles": len(rates), "window_start": first.isoformat(), "window_end": last.isoformat(),
            "no_daily_cap": no_cap, "with_daily_cap": with_cap,
        }

    mt5.shutdown()

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtest_results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.abspath(os.path.join(out_dir, f"risk_capped_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"))
    with open(out_path, "w") as f:
        json.dump({
            "symbol": SYMBOL, "data_fetched_at": datetime.now(timezone.utc).isoformat(),
            "initial_balance": INITIAL_BALANCE, "fixed_lot_size": FIXED_LOT_SIZE,
            "daily_loss_cap_usd": DAILY_LOSS_CAP_USD, "strategy_params": params.__dict__,
            "timeframes": results,
        }, f, indent=2)
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
