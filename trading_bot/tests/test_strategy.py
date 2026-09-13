"""Unit tests for the NASDAQ order-flow strategy engine."""

import unittest
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.strategy import (
    StrategyParameters, RangeBar, build_range_bars, calculate_volume_profile,
    calculate_cvd, calculate_atr, calculate_ema, is_volume_spike,
    evaluate_signal_at_bar, is_in_session_window, calculate_position_size,
)
from datetime import datetime, timezone


class TestRangeBars(unittest.TestCase):
    def test_monotonic_uptrend_produces_expected_bar_count(self):
        """10 bars each moving +10 points -> 100 points total -> 5 bars at range_size=20."""
        opens = [100 + i * 10 for i in range(10)]
        highs = [o + 10 for o in opens]
        lows = [o - 1 for o in opens]
        closes = [o + 9 for o in opens]
        times = [f"2026-01-01T00:{i:02d}:00+00:00" for i in range(10)]
        volumes = [100.0] * 10

        bars = build_range_bars(opens, highs, lows, closes, times, volumes, range_size=20.0)
        self.assertEqual(len(bars), 5)
        for b in bars:
            self.assertAlmostEqual(b.close - b.open, 20.0, places=6)

    def test_first_bar_opens_at_series_open(self):
        opens, highs, lows, closes = [100, 130], [140, 140], [90, 90], [130, 130]
        times = ["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"]
        bars = build_range_bars(opens, highs, lows, closes, times, [50.0, 50.0], range_size=15.0)
        self.assertEqual(bars[0].open, 100)

    def test_volume_conserved_within_tolerance(self):
        opens = [100 + i * 5 for i in range(20)]
        highs = [o + 3 for o in opens]
        lows = [o - 3 for o in opens]
        closes = [o + 2 for o in opens]
        times = [f"2026-01-01T00:{i:02d}:00+00:00" for i in range(20)]
        volumes = [50.0] * 20
        bars = build_range_bars(opens, highs, lows, closes, times, volumes, range_size=10.0)
        total_out = sum(b.volume for b in bars)
        # Some volume is left in the unclosed trailing bar - allow for that.
        self.assertLessEqual(total_out, sum(volumes) + 1e-6)
        self.assertGreater(total_out, sum(volumes) * 0.5)

    def test_rejects_non_positive_range_size(self):
        with self.assertRaises(ValueError):
            build_range_bars([1], [1], [1], [1], ["2026-01-01T00:00:00+00:00"], [1.0], range_size=0)


class TestVolumeProfile(unittest.TestCase):
    def test_val_vah_bracket_poc(self):
        highs = [105, 106, 104, 110, 108]
        lows = [100, 101, 99, 104, 102]
        volumes = [50, 60, 40, 80, 55]
        times = [f"2026-01-01T00:{i:02d}:00+00:00" for i in range(5)]
        val, vah, poc = calculate_volume_profile(highs, lows, volumes, times, bin_size=1.0)
        for i in range(5):
            self.assertLessEqual(val[i], poc[i])
            self.assertLessEqual(poc[i], vah[i])

    def test_session_reset_produces_independent_profiles(self):
        highs = [105, 105, 205, 205]
        lows = [95, 95, 195, 195]
        volumes = [10, 10, 10, 10]
        times = ["2026-01-01T01:00:00+00:00", "2026-01-01T02:00:00+00:00",
                 "2026-01-02T01:00:00+00:00", "2026-01-02T02:00:00+00:00"]
        val, vah, poc = calculate_volume_profile(highs, lows, volumes, times, bin_size=1.0, anchor_hour_utc=0)
        self.assertLess(vah[1], 150)   # day 1 profile stays near ~100
        self.assertGreater(val[3], 150)  # day 2 profile resets near ~200


class TestCVDAndVolumeSpike(unittest.TestCase):
    def test_cvd_accumulates_signed_volume(self):
        opens = [100, 103, 102]
        closes = [103, 102, 106]
        volumes = [10, 20, 30]
        cvd = calculate_cvd(opens, closes, volumes)
        self.assertEqual(cvd, [10, -10, 20])

    def test_volume_spike_detection(self):
        volumes = [10] * 20 + [50]
        self.assertTrue(is_volume_spike(volumes, 20, lookback=20, mult=2.0))
        self.assertFalse(is_volume_spike([10] * 21, 20, lookback=20, mult=2.0))


class TestSessionWindow(unittest.TestCase):
    def test_inside_and_outside_window(self):
        params = StrategyParameters(session_start_utc_minutes=13 * 60 + 30, session_end_utc_minutes=14 * 60)
        inside = datetime(2026, 1, 1, 13, 45, tzinfo=timezone.utc)
        outside = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(is_in_session_window(inside, params))
        self.assertFalse(is_in_session_window(outside, params))


class TestPositionSizing(unittest.TestCase):
    def test_risk_based_lot_sizing(self):
        # Risk $10, SL is 20 points away, $1/point/lot -> want 0.5 lot
        lot = calculate_position_size(10.0, 20.0, 1.0, volume_min=0.05, volume_max=500.0, volume_step=0.01)
        self.assertAlmostEqual(lot, 0.5, places=2)

    def test_clamped_to_volume_min(self):
        lot = calculate_position_size(0.01, 100.0, 1.0, volume_min=0.05, volume_max=500.0, volume_step=0.01)
        self.assertEqual(lot, 0.05)


class TestSignalEvaluation(unittest.TestCase):
    def test_no_signal_on_flat_quiet_market(self):
        params = StrategyParameters(range_size_points=10.0)
        bars = [
            RangeBar(time=f"t{i}", open=29000, high=29005, low=28995, close=29000, volume=50, source_bar_index=i)
            for i in range(30)
        ]
        val = [28990.0] * 30
        vah = [29010.0] * 30
        poc = [29000.0] * 30
        cvd = [0.0] * 30
        atr = [5.0] * 30
        sig = evaluate_signal_at_bar(bars, val, vah, poc, cvd, atr, 25, params)
        self.assertFalse(sig.all_passed)


if __name__ == "__main__":
    unittest.main()
