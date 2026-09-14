"""
Locks in strategy_presets.py's frozen baseline - if these assertions ever
fail, someone edited the "validated" preset's values without updating this
test (or without meaning to touch it at all). See strategy_presets.py's
module docstring for what this snapshot represents and why it must not drift
silently.
"""

import unittest
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.strategy_presets import (
    VALIDATED_M15_BASELINE_2026_09_14,
    VALIDATED_TIMEFRAME,
    VALIDATED_FIXED_LOT_SIZE,
    VALIDATED_DAILY_LOSS_CAP_USD,
    VALIDATED_ENABLE_DAILY_PROFIT_LOCK,
    VALIDATED_METRICS_SUMMARY,
    get_validated_baseline,
)


class TestValidatedBaselinePreset(unittest.TestCase):
    def test_strategy_parameter_values_match_the_validated_search_result(self):
        p = VALIDATED_M15_BASELINE_2026_09_14
        self.assertEqual(p.range_size_points, 8.0)
        self.assertEqual(p.absorption_max_body_ratio, 0.55)
        self.assertEqual(p.volume_spike_mult, 2.5)
        self.assertEqual(p.breakout_min_body_ratio, 0.5)
        self.assertEqual(p.rr_ratio, 3.0)
        self.assertEqual(p.min_rr_ratio, 1.5)
        self.assertTrue(p.enable_htf_filter)
        self.assertTrue(p.enable_session_filter)

    def test_execution_settings_match_the_validated_run(self):
        self.assertEqual(VALIDATED_TIMEFRAME, "M15")
        self.assertEqual(VALIDATED_FIXED_LOT_SIZE, 0.1)
        self.assertEqual(VALIDATED_DAILY_LOSS_CAP_USD, 10.0)
        self.assertFalse(VALIDATED_ENABLE_DAILY_PROFIT_LOCK)

    def test_metrics_summary_matches_backtest_report(self):
        m = VALIDATED_METRICS_SUMMARY
        self.assertEqual(m["overall_trades"], 177)
        self.assertAlmostEqual(m["overall_win_rate_pct"], 42.9, places=1)
        self.assertAlmostEqual(m["overall_profit_factor"], 3.01, places=2)
        self.assertAlmostEqual(m["net_pnl_usd"], 395.49, places=2)
        self.assertAlmostEqual(m["max_drawdown_pct"], 8.44, places=2)
        self.assertFalse(m["daily_cap_ever_tripped"])

    def test_get_validated_baseline_returns_an_independent_mutable_copy(self):
        a = get_validated_baseline()
        b = get_validated_baseline()
        a.range_size_points = 999.0
        self.assertEqual(b.range_size_points, 8.0)  # mutating one copy must not affect another
        self.assertEqual(VALIDATED_M15_BASELINE_2026_09_14.range_size_points, 8.0)  # or the frozen original


if __name__ == "__main__":
    unittest.main()
