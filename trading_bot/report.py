"""
Per-bot results report from the trade databases (one DB per bot, so nothing is mixed).

  python -m trading_bot.report                 # all bots, all time
  python -m trading_bot.report --since 2026-09-21
  python -m trading_bot.report --bot gold_m5

Shows: trades opened / closed / still open, win rate, net P&L, profit factor, avg win / avg loss, best / worst,
plus the last trades. Numbers are the REALISED P&L recorded from MT5 deal history (incl. commission/swap for gold).
"""
import argparse
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOTS = {
    "gold_m5": ("GOLD M5 (XAUUSDm)", "gold_m5_trades.sqlite"),
    "nasdaq": ("NASDAQ (USTECm)", "nasdaq_trades.sqlite"),
}


def load(db, since):
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    q = "SELECT * FROM trades" + (" WHERE entry_time >= ?" if since else "") + " ORDER BY entry_time"
    rows = [dict(r) for r in con.execute(q, (since,) if since else ())]
    con.close()
    return rows


def summarize(name, rows):
    print("=" * 78); print(name)
    if rows is None:
        print("  no database yet (bot never started on this machine)"); return
    closed = [r for r in rows if r.get("exit_time") or r.get("exit_price")]
    open_ = [r for r in rows if r not in closed]
    pnl = [float(r["net_pnl_usd"] or 0) for r in closed]
    wins = [p for p in pnl if p > 0]; losses = [p for p in pnl if p <= 0]
    print(f"  trades opened: {len(rows)} | closed: {len(closed)} | still open / unreconciled: {len(open_)}")
    if closed:
        gl = -sum(losses)
        print(f"  win rate: {100*len(wins)/len(closed):.1f}% ({len(wins)}W / {len(losses)}L) | net P&L: ${sum(pnl):+.2f} | "
              f"profit factor: {(sum(wins)/gl) if gl > 0 else float('inf'):.2f}")
        print(f"  avg win: ${(sum(wins)/len(wins)) if wins else 0:.2f} | avg loss: ${(sum(losses)/len(losses)) if losses else 0:.2f} | "
              f"best: ${max(pnl):+.2f} | worst: ${min(pnl):+.2f}")
    for r in rows[-8:]:
        pn = r["net_pnl_usd"]
        print(f"    {str(r['entry_time'])[:19]}  {r['direction']:<4} {str(r.get('model') or ''):<14} lot {r['lot_size']}  "
              f"entry {r['entry_price']}  ->  {('%+.2f' % pn) if r.get('exit_time') or r.get('exit_price') else 'OPEN'}  {r.get('exit_reason') or ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", choices=list(BOTS)); ap.add_argument("--since", help="YYYY-MM-DD")
    a = ap.parse_args()
    for key, (name, dbf) in BOTS.items():
        if a.bot and key != a.bot: continue
        summarize(name, load(os.path.join(BASE_DIR, dbf), a.since))
    print("=" * 78)


if __name__ == "__main__":
    sys.exit(main())
