"""
Standalone CLI launcher for both NASDAQ (USTECm) and GOLD (XAUUSDm) live auto-trading engines.
Runs both engines on background daemon threads without requiring a browser UI.
"""

import sys
import os
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.live_engine import LiveTradingEngine
from trading_bot.gold_live_engine import GoldLiveTradingEngine


def run_live_auto_trading():
    db_path = "nasdaq_trades.sqlite"

    nasdaq_engine = LiveTradingEngine(symbol="USTECm", db_path=db_path)
    gold_engine = GoldLiveTradingEngine(symbol="XAUUSDm", db_path=db_path)

    print("⚡ [MULTI-BOT CLI] Starting NASDAQ-100 & GOLD Auto-Trading Engines...", flush=True)

    nasdaq_engine.start()
    gold_engine.start()

    printed_nasdaq = 0
    printed_gold = 0

    try:
        while nasdaq_engine.is_running() or gold_engine.is_running():
            time.sleep(1)

            # NASDAQ logs
            lines_n = list(nasdaq_engine.log_lines)
            for line in lines_n[printed_nasdaq:]:
                try:
                    print(line, flush=True)
                except UnicodeEncodeError:
                    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
                    print(line.encode(encoding, errors="replace").decode(encoding), flush=True)
            printed_nasdaq = len(lines_n)

            # GOLD logs
            lines_g = list(gold_engine.log_lines)
            for line in lines_g[printed_gold:]:
                try:
                    print(line, flush=True)
                except UnicodeEncodeError:
                    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
                    print(line.encode(encoding, errors="replace").decode(encoding), flush=True)
            printed_gold = len(lines_g)

    except KeyboardInterrupt:
        print("\n🛑 Stop requested - shutting down both engines...", flush=True)
        nasdaq_engine.stop()
        gold_engine.stop()

        if nasdaq_engine._thread:
            nasdaq_engine._thread.join(timeout=10)
        if gold_engine._thread:
            gold_engine._thread.join(timeout=10)

        print("🛑 Both NASDAQ & GOLD engines stopped cleanly by user.", flush=True)


if __name__ == "__main__":
    run_live_auto_trading()
