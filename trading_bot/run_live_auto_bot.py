"""
Standalone CLI launcher for the NASDAQ live auto-trading engine (no dashboard needed).

Drives `trading_bot.live_engine.LiveTradingEngine` - the same engine the
Streamlit dashboard's Start/Stop button controls. Prefer:

    streamlit run trading_bot/streamlit_app.py

...and use the toggle there, which runs engine + dashboard from ONE command.
Use this script only if you want the bot running with no UI at all.
"""

import sys
import os
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.live_engine import LiveTradingEngine


def run_live_auto_trading():
    engine = LiveTradingEngine(symbol="USTECm", db_path="nasdaq_trades.sqlite")
    ok, msg = engine.start()
    print(msg, flush=True)
    if not ok:
        return

    printed = 0
    try:
        while engine.is_running():
            time.sleep(1)
            lines = list(engine.log_lines)
            for line in lines[printed:]:
                try:
                    print(line, flush=True)
                except UnicodeEncodeError:
                    pass
            printed = len(lines)
        if engine.error:
            print(f"\n❌ Engine exited with error: {engine.error}", flush=True)
    except KeyboardInterrupt:
        print("\n🛑 Stop requested - shutting engine down...", flush=True)
        engine.stop()
        if engine._thread:
            engine._thread.join(timeout=15)
        print("🛑 Auto-trading engine stopped by user.", flush=True)


if __name__ == "__main__":
    run_live_auto_trading()
