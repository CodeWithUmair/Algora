"""
Frozen snapshot of the best-validated strategy configuration found so far.

WHY THIS FILE EXISTS: `StrategyParameters()`'s dataclass defaults in strategy.py
are meant to be tuned further over time (that's exactly what optimize_parameters.py
does) - which means they WILL drift from whatever produced any specific backtest
result. This file is a deliberately un-drifting reference point: the exact
parameter values, lot size, and daily loss cap that produced the numbers below,
hardcoded rather than read from the (mutable) dataclass defaults, so a future
tuning pass that makes things worse always has a known-good baseline to compare
against or roll back to.

THIS IS NOT AUTOMATICALLY USED BY live_engine.py OR streamlit_app.py. Both of
those read StrategyParameters() (the live, current, possibly-since-tuned
defaults). This module is a reference/rollback point, not a live code path -
if a future session wants the bot to actually run this exact frozen config
again, it has to explicitly import and wire up VALIDATED_M15_BASELINE_2026_09_14
below, not assume it's already active just because this file exists.

VALIDATION SUMMARY (real MT5 USTECm M15 data, ~7.3 months, 2025-09 to 2026-09):
  - 177 trades, 42.9% win rate, profit factor 3.01, expectancy +1.00R
  - Noise gate PASSED independently on both segments (not just overall):
      in-sample:     116 trades, 45.7% win rate, PF 3.23, p=0.0099
      out-of-sample:  61 trades, 37.7% win rate, PF 2.66, p=0.0099
  - At $100 balance / 0.1 fixed lot / $10 daily loss cap / profit uncapped:
      net +$395.49, final balance $495.56, max drawdown 8.44% ($14.61)
      worst single UTC day: -$6.30 (cap never tripped on any historical day)
  - Cross-checked two ways the tuning search never selected on, both held up:
      a different train/test split (50/50 instead of 75/25) and a different
      timeframe entirely (M5, never touched during the search)
  - Full methodology, search log, and every caveat that still applies:
    see BACKTEST_REPORT.md's "Parameter optimization" and "Lot size
    comparison" sections. Reproduce with:
      python trading_bot/optimize_parameters.py
      python trading_bot/run_backtest_risk_capped.py M15

WHAT THIS IS NOT: proof of a long-term tradeable edge, a guarantee against
losing months (several of the underlying months were net negative), or
evidence this holds on any timeframe/broker/account this hasn't specifically
been tested on. It is the best real-data result produced so far by this
search process, on one broker, one demo account, ~11.7 months of history.
Forward-testing (live/demo trading, not backtesting) is what actually tests
whether it holds - see HANDOFF.md's session log for that status.
"""
from dataclasses import replace

from trading_bot.strategy import StrategyParameters

VALIDATED_M15_BASELINE_2026_09_14 = StrategyParameters(
    # 1. Range bar construction
    range_size_points=8.0,
    htf_range_size_points=60.0,

    # 2. Volume Profile
    profile_bin_size_points=5.0,
    value_area_pct=0.68,
    session_anchor_hour_utc=0,

    # 3. Volume-spike ("Big Trade") detection
    volume_spike_lookback=20,
    volume_spike_mult=2.5,

    # 4. Model A - AAA Value Area Absorption Reversal
    absorption_buffer_atr=0.5,
    absorption_max_body_ratio=0.55,

    # 5. Model B - Momentum / Squeeze Breakout
    breakout_min_body_ratio=0.5,
    rr_ratio=3.0,
    be_trigger_ratio=0.70,

    # 6. Model C - Failed Auction Fade
    fade_pierce_min_points=3.0,

    # 7. Shared
    atr_period=14,
    min_rr_ratio=1.5,
    min_sl_distance_points=8.0,
    max_sl_distance_points=60.0,

    # 8. HTF & session filters
    enable_htf_filter=True,
    htf_ema_period=50,
    enable_session_filter=True,
    session_start_utc_minutes=13 * 60 + 30,
    session_end_utc_minutes=14 * 60,
)

# Execution/risk settings validated alongside the parameters above - not part
# of StrategyParameters itself, but required to reproduce the exact backtest.
VALIDATED_TIMEFRAME = "M15"
VALIDATED_FIXED_LOT_SIZE = 0.1
VALIDATED_DAILY_LOSS_CAP_USD = 10.0
VALIDATED_ENABLE_DAILY_PROFIT_LOCK = False

VALIDATED_METRICS_SUMMARY = {
    "data_window": "2025-09-21 to 2026-09-14 (~7.3 months of trades within an ~11.7-month M15 history)",
    "overall_trades": 177,
    "overall_win_rate_pct": 42.9,
    "overall_profit_factor": 3.01,
    "overall_expectancy_r": 1.00,
    "in_sample_trades": 116,
    "in_sample_win_rate_pct": 45.7,
    "in_sample_profit_factor": 3.23,
    "in_sample_noise_gate_p": 0.0099,
    "out_of_sample_trades": 61,
    "out_of_sample_win_rate_pct": 37.7,
    "out_of_sample_profit_factor": 2.66,
    "out_of_sample_noise_gate_p": 0.0099,
    "account_initial_balance_usd": 100.0,
    "fixed_lot_size": VALIDATED_FIXED_LOT_SIZE,
    "net_pnl_usd": 395.49,
    "final_balance_usd": 495.56,
    "max_drawdown_pct": 8.44,
    "max_drawdown_usd": 14.61,
    "worst_single_day_usd": -6.30,
    "daily_loss_cap_usd": VALIDATED_DAILY_LOSS_CAP_USD,
    "daily_cap_ever_tripped": False,
}


def get_validated_baseline() -> StrategyParameters:
    """Returns a fresh copy of the frozen validated config (safe to mutate the result)."""
    return replace(VALIDATED_M15_BASELINE_2026_09_14)
