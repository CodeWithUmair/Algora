"""
Single-command VPS launcher: runs independent live bots as threads in ONE process, one MT5 account.

  python -m trading_bot.run_vps                      # gold M1 + gold M5 + nasdaq   (default)
  python -m trading_bot.run_vps --bots gold_m1       # only the M1 gold bot
  python -m trading_bot.run_vps --bots gold_m5       # only the M5 gold bot
  python -m trading_bot.run_vps --bots gold_m1,gold_m5          # gold only (nasdaq off)

Each bot has its OWN magic number, SQLite trade DB, daily-loss breaker and log file:
  gold_m1 : magic 9212001  gold_m1_trades.sqlite  logs/gold_m1.log
  gold_m5 : magic 9212005  gold_m5_trades.sqlite  logs/gold_m5.log
  nasdaq  : magic 9312001  nasdaq_trades.sqlite   logs/nasdaq.log
A bot that crashes is restarted after a short delay (the others keep running). Ctrl+C stops all.
All bots are DEMO-only (enforced again on every order).
"""

import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.gold_live_engine import GoldLiveTradingEngine  # noqa: E402

LOG_DIR = os.path.join(BASE_DIR, "logs")
KNOWN = ("gold_m1", "gold_m5", "nasdaq")


def build_engine(name: str, daily_loss_cap: float, news: bool):
    log_file = os.path.join(LOG_DIR, f"{name}.log")
    if name in ("gold_m1", "gold_m5"):
        tf = name.split("_")[1].upper()
        return GoldLiveTradingEngine(symbol="XAUUSDm", timeframe=tf, daily_loss_cap_usd=daily_loss_cap,
                                     use_news_filter=news, log_file=log_file,
                                     db_path=os.path.join(BASE_DIR, f"{name}_trades.sqlite"))
    if name == "nasdaq":
        from trading_bot.live_engine import LiveTradingEngine
        eng = LiveTradingEngine(symbol="USTECm", db_path=os.path.join(BASE_DIR, "nasdaq_trades.sqlite"))
        eng.log_file = log_file
        return eng
    raise ValueError(name)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run independent live bots on one MT5 account")
    ap.add_argument("--bots", default="gold_m1,gold_m5,nasdaq", help=f"comma list from {KNOWN} (default: all three)")
    ap.add_argument("--daily-loss-cap", type=float, default=10.0, help="per-gold-bot realised daily loss cap in USD (default 10)")
    ap.add_argument("--no-news-filter", action="store_true", help="disable the (unbacktested) high-impact news pause")
    ap.add_argument("--restart-delay", type=float, default=30.0, help="seconds before restarting a crashed bot")
    a = ap.parse_args(argv)

    names = [b.strip().lower() for b in a.bots.split(",") if b.strip()]
    bad = [n for n in names if n not in KNOWN]
    if bad or not names:
        raise SystemExit(f"Unknown bot(s) {bad}. Choose from {KNOWN}")
    os.makedirs(LOG_DIR, exist_ok=True)

    engines = {n: build_engine(n, a.daily_loss_cap, not a.no_news_filter) for n in names}
    print(f"[LAUNCHER] starting: {', '.join(names)} (single MT5 account, separate magic/DB/log per bot)", flush=True)
    for e in engines.values():
        e.start()
        time.sleep(1.5)   # stagger MT5 attach

    printed = {n: 0 for n in names}   # only used for engines that don't print themselves (nasdaq)
    try:
        while True:
            time.sleep(1)
            for n, e in engines.items():
                if n == "nasdaq":
                    lines = list(e.log_lines)
                    for line in lines[printed[n]:]:
                        try:
                            with open(os.path.join(LOG_DIR, "nasdaq.log"), "a", encoding="utf-8") as f:
                                f.write(line + chr(10))
                        except Exception:
                            pass
                    printed[n] = len(lines)
                if not e.is_running():
                    print(f"[LAUNCHER] {n} is not running (error: {getattr(e, 'error', None)}). Restarting in {a.restart_delay:.0f}s...", flush=True)
                    time.sleep(a.restart_delay)
                    e.start()
    except KeyboardInterrupt:
        print("\n[LAUNCHER] Ctrl+C - stopping all bots...", flush=True)
        for e in engines.values():
            e.stop()
        for e in engines.values():
            if getattr(e, "_thread", None):
                e._thread.join(timeout=10)
        print("[LAUNCHER] all bots stopped.", flush=True)


if __name__ == "__main__":
    main()
