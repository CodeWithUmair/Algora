"""
Back-compat entrypoint. Now delegates to run_vps.py (default: gold M1 + gold M5 + NASDAQ).
  python trading_bot\run_live_auto_bot.py                        -> gold_m1 + gold_m5 + nasdaq
  python trading_bot\run_live_auto_bot.py --bots gold_m1,gold_m5   (gold only)
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.run_vps import main  # noqa: E402

if __name__ == "__main__":
    main()
