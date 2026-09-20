"""
Replay test for GoldLiveTradingEngine: feeds REAL cached XAUUSDm bars through a fake broker and checks the
engine sends exactly the orders the validated backtest signal logic produces (closed bars only, right SL/TP,
own magic/DB per timeframe). Skips if no cached data (run trading_bot/fetch_mt5_history.py first).
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from trading_bot.gold_live_engine import GoldLiveTradingEngine, TF_FETCH_BARS, TF_SECONDS, GOLD_MAGIC
from trading_bot.gold_strategy import GoldStrategyParameters
from trading_bot.run_backtest_gold_fib import prepare, signal_at, PIP

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache")


class _Acc:
    login, trade_mode, is_demo, balance = 1, "DEMO", True, 100.0


class FakeBridge:
    """Replays a real bars dataframe; position list is always empty (isolates signal/order logic)."""
    df = None
    cursor = 0
    orders = []

    def __init__(self, magic_number=None, symbol=None):
        self.magic = magic_number

    def connect(self): return True, "fake"
    def get_account_info(self): return _Acc()
    def is_algo_trading_enabled(self): return True
    def get_open_positions(self, symbol=None, magic_number=None): return []

    def fetch_recent_dataframe(self, symbol=None, count=500, timeframe_str="M5"):
        c = FakeBridge.cursor
        d = FakeBridge.df.iloc[max(0, c - count + 1): c + 1].copy()   # last row = forming bar
        d["time"] = d["time"].map(lambda t: t.isoformat())
        return d.reset_index(drop=True)

    def send_order(self, direction, volume, stop_loss=None, take_profit=None, symbol=None,
                   magic_number=None, comment="", **kw):
        FakeBridge.orders.append(dict(direction=direction, volume=volume, sl=stop_loss, tp=take_profit,
                                      magic=magic_number, comment=comment, cursor=FakeBridge.cursor))
        return True, 1000 + len(FakeBridge.orders), "fake fill"


@unittest.skipUnless(os.path.exists(os.path.join(CACHE, "XAUUSDm_M5.parquet")), "no cached XAUUSDm data")
class TestGoldLiveEngineReplay(unittest.TestCase):
    def test_m5_replay_matches_backtest_signals(self):
        df = pd.read_parquet(os.path.join(CACHE, "XAUUSDm_M5.parquet")).tail(3400).reset_index(drop=True)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        FakeBridge.df, FakeBridge.orders = df, []
        with tempfile.TemporaryDirectory() as td:
            eng = GoldLiveTradingEngine(timeframe="M5", db_path=os.path.join(td, "m5.sqlite"),
                                        use_news_filter=False, bridge_factory=FakeBridge)
            eng._quiet = True
            self.assertTrue(eng._connect())
            start = TF_FETCH_BARS["M5"] + 50
            for cur in range(start, len(df)):
                FakeBridge.cursor = cur
                # 'now' = 5s after bar cur-1 closed (bar cur is the forming bar)
                closed_at = df["time"].iloc[cur - 1] + timedelta(seconds=TF_SECONDS["M5"] + 5)
                eng.tick(now_utc=closed_at.to_pydatetime())
            orders = list(FakeBridge.orders)

            # reference: validated backtest signal generator, same bars, same cooldown rules
            P = GoldStrategyParameters()
            o, h, l, c, ema, date, hour, ds, po = prepare(df, P.ema_period)
            last, ref = {}, []
            for i in range(start - 1, len(df) - 1):     # closed bars: cursor-1 for cursor in [start, len)
                s = signal_at(i, o, h, l, c, ema, hour, date, ds, po, P, last)
                if s:
                    ref.append((i, s[0], s[1], s[2]))
            # engine-side view
            got = [(o_["cursor"] - 1, o_["direction"], o_["comment"].split("_")[-1]) for o_ in orders]
            want = [(i, d, lvl) for i, d, lvl, _ in ref]
            self.assertGreater(len(want), 5, "replay window produced too few signals to be meaningful")
            self.assertEqual(got, want)
            for od, (i, d, lvl, sl_pips) in zip(orders, ref):
                self.assertEqual(od["magic"], GOLD_MAGIC["M5"])
                self.assertEqual(od["volume"], 0.01)
                self.assertAlmostEqual(abs(od["sl"] - c[i]) / PIP, sl_pips, places=3)
                self.assertAlmostEqual(abs(od["tp"] - c[i]) / PIP, sl_pips * 2.0, places=3)

    def test_two_timeframes_are_isolated(self):
        m1 = GoldLiveTradingEngine(timeframe="M1"); m5 = GoldLiveTradingEngine(timeframe="M5")
        self.assertNotEqual(m1.params.magic_number, m5.params.magic_number)
        self.assertNotEqual(m1.db_path, m5.db_path)
        self.assertEqual((m1.params.timeframe_str, m5.params.timeframe_str), ("M1", "M5"))
        with self.assertRaises(ValueError):
            GoldLiveTradingEngine(timeframe="H1")


if __name__ == "__main__":
    unittest.main()
