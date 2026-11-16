"""
One-command health check for the VPS. Read-only (no orders). Run from the repo folder:
    python -m trading_bot.check_vps
Prints PASS / WARN / FAIL per item. Paste the whole output to whoever is supporting the bots.
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
res = []


def rec(status, name, detail=""):
    res.append(status)
    print(f"[{status:<4}] {name}" + (f" - {detail}" if detail else ""))


def sh(*cmd):
    try:
        return subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:
        return f"error: {e}"


def last_line_age(path, needle=None):
    """(minutes since the last matching log line, that line) - log lines start with [YYYY-MM-DD HH:MM:SS]."""
    if not os.path.exists(path):
        return None, None
    lines = [ln for ln in open(path, encoding="utf-8", errors="replace").read().splitlines()
             if ln.strip() and (needle is None or needle in ln)]
    if not lines:
        return None, None
    try:
        ts = datetime.strptime(lines[-1][1:20], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 60.0, lines[-1]
    except Exception:
        return None, lines[-1]


def check_mt5(now):
    import MetaTrader5 as mt5
    if not mt5.initialize():
        rec("FAIL", "MT5 initialize", str(mt5.last_error()))
        return
    ti, ai = mt5.terminal_info(), mt5.account_info()
    rec("PASS" if ti.connected else "FAIL", "MT5 terminal connected to broker")
    rec("PASS" if ti.trade_allowed else "FAIL", "Algo Trading button ON (terminal trade_allowed)")
    rec("PASS" if ai.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "FAIL",
        "account is DEMO (bots refuse real accounts)", f"login {ai.login} {ai.server}")
    rec("PASS" if ai.margin_mode == mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING else "FAIL",
        "account is HEDGING (gold + nasdaq can hold positions side by side)", f"margin_mode={ai.margin_mode}")
    rec("PASS" if ai.balance > 0 else "FAIL", "balance",
        f"${ai.balance:.2f} equity ${ai.equity:.2f} free margin ${ai.margin_free:.2f} leverage 1:{ai.leverage}")
    rec("PASS" if (ai.trade_allowed and ai.trade_expert) else "FAIL", "account allows trading + expert advisors")

    for sym, lot in (("XAUUSDm", 0.01), ("USTECm", 0.1)):
        mt5.symbol_select(sym, True)
        si, tk = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        if si is None or tk is None:
            rec("FAIL", f"symbol {sym}", "not available on this account")
            continue
        age = (now - datetime.fromtimestamp(tk.time, timezone.utc)).total_seconds()
        rec("PASS" if si.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL else "WARN", f"{sym} tradable",
            f"trade_mode={si.trade_mode} spread ${tk.ask - tk.bid:.2f}")
        rec("PASS" if age < 300 else "WARN", f"{sym} price feed is live",
            f"last tick {age:.0f}s ago" + (" (market may just be closed)" if age >= 300 else ""))
        rec("PASS" if si.volume_min <= lot else "FAIL", f"{sym} broker min lot <= bot lot {lot}", f"min {si.volume_min}")
        m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, lot, tk.ask)
        if m is not None:
            rec("PASS" if m < ai.margin_free * 0.5 else "WARN", f"{sym} margin needed for {lot} lot",
                f"${m:.2f} of ${ai.margin_free:.2f} free")

    for tf, name, need in ((mt5.TIMEFRAME_M5, "M5", 1501), (mt5.TIMEFRAME_M15, "M15", 1000)):
        r = mt5.copy_rates_from_pos("XAUUSDm", tf, 0, need)
        n = 0 if r is None else len(r)
        rec("PASS" if n >= need else "FAIL", f"XAUUSDm {name} history (bot needs {need} bars)", f"got {n}")

    for magic, name in ((9212005, "gold M5"), (9312001, "nasdaq")):
        ps = [p for p in (mt5.positions_get() or []) if p.magic == magic]
        rec("INFO", f"open positions {name} (magic {magic})", str(len(ps)))
    mt5.shutdown()


def summary():
    print(f"\nSUMMARY: {res.count('PASS')} pass, {res.count('WARN')} warn, {res.count('FAIL')} FAIL")
    print("No failures." if res.count("FAIL") == 0 else "Fix the FAIL lines first, then re-run.")


def main():
    now = datetime.now(timezone.utc)
    print(f"VPS check @ {now:%Y-%m-%d %H:%M:%S} UTC | folder {BASE}\n")

    head, br, dirty = sh("git", "rev-parse", "--short", "HEAD"), sh("git", "rev-parse", "--abbrev-ref", "HEAD"), sh("git", "status", "--short")
    rec("PASS" if br == "GOLD" else "FAIL", "git branch", f"{br} @ {head}")
    if dirty:
        rec("WARN", "uncommitted local changes", dirty.replace("\n", "; ")[:200])
    rec("PASS" if os.path.exists(os.path.join(BASE, "trading_bot", "run_vps.py")) else "FAIL", "launcher present (run_vps.py)")

    rec("PASS", "python", sys.version.split()[0])
    for mod in ("MetaTrader5", "pandas", "numpy"):
        try:
            m = __import__(mod)
            rec("PASS", f"package {mod}", getattr(m, "__version__", ""))
        except Exception as e:
            rec("FAIL", f"package {mod}", str(e))

    try:
        check_mt5(now)
    except Exception as e:
        rec("FAIL", "MT5 checks crashed", repr(e))

    try:
        from trading_bot.news_filter import fetch_calendar
        ev = fetch_calendar()
        rec("PASS" if ev else "WARN", "news calendar", f"{len(ev)} events")
    except Exception as e:
        rec("WARN", "news calendar", str(e))

    logs = os.path.join(BASE, "logs")
    for bot in ("gold_m5",):
        age, _ = last_line_age(os.path.join(logs, f"{bot}.log"), "HEARTBEAT")
        if age is None:
            rec("WARN", f"{bot} heartbeat", "none yet - bot not started, or started <30 min ago")
        else:
            rec("PASS" if age < 40 else "FAIL", f"{bot} heartbeat", f"{age:.0f} min ago (must be <40 while running)")
    for bot in ("gold_m5", "nasdaq", "launcher"):
        age, line = last_line_age(os.path.join(logs, f"{bot}.log"))
        rec("INFO", f"{bot}.log last line", (f"{age:.0f} min ago: " if age is not None else "") + (line or "no log yet")[:110])
    errs = 0
    for bot in ("gold_m5",):
        p = os.path.join(logs, f"{bot}.log")
        if os.path.exists(p):
            errs += sum(1 for ln in open(p, encoding="utf-8", errors="replace")
                        if "ORDER FAILED" in ln or "Loop error" in ln or "FATAL" in ln)
    rec("PASS" if errs == 0 else "FAIL", "errors in gold logs (ORDER FAILED / Loop error / FATAL)", str(errs))

    for f in ("gold_m5_trades.sqlite", "nasdaq_trades.sqlite"):
        p = os.path.join(BASE, f)
        if not os.path.exists(p):
            rec("WARN", f"DB {f}", "not created yet (bot never started here)")
            continue
        try:
            n = sqlite3.connect(p).execute("select count(*) from trades").fetchone()[0]
            rec("PASS", f"DB {f}", f"{n} trades recorded")
        except Exception as e:
            rec("FAIL", f"DB {f}", str(e))
    summary()


if __name__ == "__main__":
    main()
