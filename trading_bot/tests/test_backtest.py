"""Smoke + sanity tests for the NASDAQ causal backtest engine."""

import unittest
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.strategy import StrategyParameters
from trading_bot.data_feed import generate_realistic_nasdaq_data
from trading_bot.backtest import run_causal_backtest, calculate_metrics_from_trades, Trade


class TestBacktestEngine(unittest.TestCase):
    def test_runs_end_to_end_without_error(self):
        data = generate_realistic_nasdaq_data(num_bars=3000, seed=1)
        params = StrategyParameters()
        params.enable_session_filter = False
        res = run_causal_backtest(
            data["opens"], data["highs"], data["lows"], data["closes"], data["times"], data["volumes"],
            params, initial_balance=100.0, num_noise_shuffles=10
        )
        self.assertGreater(res.num_range_bars, 0)
        self.assertIsNotNone(res.overall_metrics)

    def test_insufficient_bars_raises(self):
        params = StrategyParameters()
        with self.assertRaises(ValueError):
            run_causal_backtest([1, 2], [1, 2], [1, 2], [1, 2], ["t1", "t2"], [1, 1], params)

    def test_metrics_on_empty_trades(self):
        m = calculate_metrics_from_trades([], "OVERALL")
        self.assertEqual(m.total_trades, 0)
        self.assertEqual(m.noise_gate_passed, False)

    def test_win_rate_calculation(self):
        trades = [
            Trade(id=1, direction="BUY", model="AAA", signal_bar=0, signal_time="t", entry_bar=1,
                  entry_time="t", entry_price=100, stop_loss=95, take_profit=110, risk_points=5,
                  reward_points=10, net_pnl_usd=50.0, pnl_r_multiple=2.0, duration_bars=5),
            Trade(id=2, direction="SELL", model="SQUEEZE", signal_bar=10, signal_time="t", entry_bar=11,
                  entry_time="t", entry_price=100, stop_loss=105, take_profit=90, risk_points=5,
                  reward_points=10, net_pnl_usd=-25.0, pnl_r_multiple=-1.0, duration_bars=3),
        ]
        m = calculate_metrics_from_trades(trades, "OVERALL", initial_balance=100.0, num_shuffles=5)
        self.assertEqual(m.total_trades, 2)
        self.assertEqual(m.winning_trades, 1)
        self.assertEqual(m.win_rate_pct, 50.0)


if __name__ == "__main__":
    unittest.main()
