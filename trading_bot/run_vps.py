"""
Single-command VPS launcher: runs independent live bots as threads in ONE process, one MT5 account.

  python -m trading_bot.run_vps                         # gold M5 only   (default; NASDAQ is OFF)
  python -m trading_bot.run_vps --bots gold_m5,nasdaq   # re-enable NASDAQ

Each bot has its OWN magic number, SQLite trade DB, daily-loss breaker and log file:
  gold_m5 : magic 9212005  gold_m5_trades.sqlite  logs/gold_m5.log
  nasdaq  : magic 9312001  nasdaq_trades.sqlite   logs/nasdaq.log
A bot that crashes is restarted after a short delay (the others keep running). Ctrl+C stops all.
All bots are DEMO-only (enforced again on every order).

Second MT5 account on the SAME VPS (e.g. running a different strategy variant alongside the
first): run this script AGAIN as a SEPARATE process (a second PowerShell window), pointed at a
second, separately-installed MT5 terminal logged into the second account, with --tag so its
DB/log files don't collide with the first process's:

  python -m trading_bot.run_vps --mt5-path "C:\\Program Files\\MetaTrader 5 EXNESS 2\\terminal64.exe" --max-sl-pips 180 --tag capped

One MT5 Python connection can only ever talk to ONE terminal at a time (that's process-global,
not per-thread) - that's why this needs a second OS process, not just another bot in this list.
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
KNOWN = ("gold_m5", "nasdaq")


def disable_console_quickedit():
    """Windows console 'QuickEdit/Select' mode PAUSES every program that prints while text is selected
    (a single click in the window is enough; title bar shows 'Select ...'). All bot threads print, so a stray
    click would silently freeze all trading. Turn that mode off for this console."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)                      # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, (mode.value | 0x0080) & ~0x0040)   # set EXTENDED_FLAGS, clear QUICK_EDIT
    except Exception:
        pass


def llog(msg: str):
    """Launcher-level event log (start / crash / restart / stop) -> console AND logs/launcher.log."""
    from datetime import datetime, timezone
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] [LAUNCHER] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(LOG_DIR, "launcher.log"), "a", encoding="utf-8") as f:
            f.write(line + chr(10))
    except Exception:
        pass


def build_engine(name: str, daily_loss_cap: float, news: bool, tag: str = "", mt5_path=None, max_sl_pips=0.0):
    file_stem = f"{name}_{tag}" if tag else name   # keeps a second process's files from colliding with the first's
    log_file = os.path.join(LOG_DIR, f"{file_stem}.log")
    if name == "gold_m5":
        tf = name.split("_")[1].upper()
        return GoldLiveTradingEngine(symbol="XAUUSDm", timeframe=tf, daily_loss_cap_usd=daily_loss_cap,
                                     use_news_filter=news, log_file=log_file,
                                     db_path=os.path.join(BASE_DIR, f"{file_stem}_trades.sqlite"),
                                     mt5_path=mt5_path, max_sl_pips=max_sl_pips)
    if name == "nasdaq":
        from trading_bot.live_engine import LiveTradingEngine
        eng = LiveTradingEngine(symbol="USTECm", db_path=os.path.join(BASE_DIR, f"{file_stem}_trades.sqlite"))
        eng.log_file = log_file
        return eng
    raise ValueError(name)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run independent live bots on one MT5 account")
    ap.add_argument("--bots", default="gold_m5", help=f"comma list from {KNOWN} (default: gold_m5 only; nasdaq is off)")
    ap.add_argument("--daily-loss-cap", type=float, default=10.0, help="per-gold-bot realised daily loss cap in USD (default 10)")
    ap.add_argument("--no-news-filter", action="store_true", help="disable the (unbacktested) high-impact news pause")
    ap.add_argument("--restart-delay", type=float, default=30.0, help="seconds before restarting a crashed bot")
    ap.add_argument("--mt5-path", default=None,
                     help="terminal64.exe of a SPECIFIC MT5 install/account (gold_m5 only). "
                          "Default: whatever terminal is already open. Use this to run a second "
                          "account's bot as a separate process alongside a first one.")
    ap.add_argument("--max-sl-pips", type=float, default=0.0,
                     help="gold_m5: cap the dynamic SL at this many pips (0 = uncapped, current default behavior)")
    ap.add_argument("--tag", default="", help="suffix for this process's DB/log filenames, so a second "
                                                "process on the same VPS doesn't overwrite the first's files")
    a = ap.parse_args(argv)

    names = [b.strip().lower() for b in a.bots.split(",") if b.strip()]
    bad = [n for n in names if n not in KNOWN]
    if bad or not names:
        raise SystemExit(f"Unknown bot(s) {bad}. Choose from {KNOWN}")
    os.makedirs(LOG_DIR, exist_ok=True)
    disable_console_quickedit()

    engines = {n: build_engine(n, a.daily_loss_cap, not a.no_news_filter, tag=a.tag,
                                mt5_path=a.mt5_path, max_sl_pips=a.max_sl_pips) for n in names}
    tag_note = f" tag={a.tag!r}" if a.tag else ""
    llog(f"starting: {', '.join(names)}{tag_note} (separate magic/DB/log per bot)")
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
                            with open(os.path.join(LOG_DIR, f"{n}_{a.tag}.log" if a.tag else f"{n}.log"), "a", encoding="utf-8") as f:
                                f.write(line + chr(10))
                        except Exception:
                            pass
                    printed[n] = len(lines)
                if not e.is_running():
                    llog(f"{n} is NOT running (error: {getattr(e, 'error', None)}). Restarting in {a.restart_delay:.0f}s...")
                    time.sleep(a.restart_delay)
                    e.start()
                    llog(f"{n} restarted")
    except KeyboardInterrupt:
        print("\n[LAUNCHER] Ctrl+C - stopping all bots...", flush=True)
        for e in engines.values():
            e.stop()
        for e in engines.values():
            if getattr(e, "_thread", None):
                e._thread.join(timeout=10)
        llog("all bots stopped.")


if __name__ == "__main__":
    main()
