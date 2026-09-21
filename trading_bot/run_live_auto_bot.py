"""
Back-compat entrypoint. Now delegates to run_vps.py (default: gold M5 only).
  python trading_bot\run_live_auto_bot.py                        -> gold_m5 only
  python trading_bot\run_live_auto_bot.py --bots gold_m5,nasdaq   (re-enable NASDAQ)
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.run_vps import main  # noqa: E402

if __name__ == "__main__":
    main()
