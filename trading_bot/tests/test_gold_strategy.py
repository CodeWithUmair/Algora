"""
Unit tests for Gold Fibonacci Pivot + EMA 9 Strategy Engine.
"""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from trading_bot.gold_strategy import (
    GoldStrategyParameters,
    compute_fibonacci_pivots,
    compute_ema,
    eval_gold_signal,
    DailyFibonacciPivots
)


class TestGoldStrategy(unittest.TestCase):

    def test_compute_fibonacci_pivots(self):
        # High=2700, Low=2600, Close=2650
        # PP = 2650, Range = 100
        # R1 = 2650 + 38.2 = 2688.2
        # S1 = 2650 - 38.2 = 2611.8
        pivots = compute_fibonacci_pivots(2700.0, 2600.0, 2650.0)
        self.assertAlmostEqual(pivots.pp, 2650.0)
        self.assertAlmostEqual(pivots.r1, 2688.2)
        self.assertAlmostEqual(pivots.s1, 2611.8)
        self.assertAlmostEqual(pivots.r2, 2711.8)
        self.assertAlmostEqual(pivots.s2, 2588.2)

    def test_buy_signal_crossover(self):
        # Create dummy 2-day M5 dataframe
        base_time = datetime(2026, 9, 14, 0, 0)
        times = [base_time + timedelta(minutes=5*i) for i in range(50)]
        closes = [2650.0] * 50
        closes[10] = 2700.0 # High
        closes[20] = 2600.0 # Low
        closes[49] = 2650.0 # Close

        day2_time = datetime(2026, 9, 15, 0, 0)
        times_day2 = [day2_time + timedelta(minutes=5*i) for i in range(10)]
        closes_day2 = [2640.0] * 8 + [2648.0, 2655.0]

        all_times = times + times_day2
        all_closes = closes + closes_day2
        opens = [c - 2.0 for c in all_closes] # Green candles for BUY
        highs = [c + 3.0 for c in all_closes]
        lows = [o - 3.0 for o in opens]
        highs[10] = 2700.0
        lows[20] = 2600.0

        df = pd.DataFrame({
            'time': all_times,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': all_closes
        })

        params = GoldStrategyParameters(buffer_pips=2.0, sl_pips=32.0, tp_pips=60.0, enable_session_filter=False)
        res, _ = eval_gold_signal(df, params)

        self.assertEqual(res.signal_type, "BUY")
        self.assertEqual(res.trigger_level, "PP")
        self.assertAlmostEqual(res.suggested_sl, 2655.0 - 3.20) # 32 pips = $3.20
        self.assertAlmostEqual(res.suggested_tp, 2655.0 + 6.0) # 60 pips = $6.00

    def test_sell_signal_crossover(self):
        base_time = datetime(2026, 9, 14, 0, 0)
        times = [base_time + timedelta(minutes=5*i) for i in range(50)]
        closes = [2650.0] * 50
        closes[10] = 2700.0
        closes[20] = 2600.0

        day2_time = datetime(2026, 9, 15, 0, 0)
        times_day2 = [day2_time + timedelta(minutes=5*i) for i in range(10)]
        closes_day2 = [2660.0] * 8 + [2652.0, 2644.0]

        all_times = times + times_day2
        all_closes = closes + closes_day2
        opens = [c + 2.0 for c in all_closes] # Red candles for SELL
        highs = [o + 3.0 for o in opens]
        lows = [c - 3.0 for c in all_closes]
        highs[10] = 2700.0
        lows[20] = 2600.0

        df = pd.DataFrame({
            'time': all_times,
            'open': opens,
            'high': highs,
            'low': lows,
            'close': all_closes
        })

        params = GoldStrategyParameters(buffer_pips=2.0, sl_pips=32.0, tp_pips=60.0, enable_session_filter=False)
        res, _ = eval_gold_signal(df, params)

        self.assertEqual(res.signal_type, "SELL")
        self.assertEqual(res.trigger_level, "PP")
        self.assertAlmostEqual(res.suggested_sl, 2644.0 + 3.20) # 32 pips = $3.20
        self.assertAlmostEqual(res.suggested_tp, 2644.0 - 6.0)


if __name__ == "__main__":
    unittest.main()
