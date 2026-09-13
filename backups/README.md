# backups/

Dated snapshots of `nasdaq_trades.sqlite` (trade history + saved dashboard
config), so they survive moving between machines. Same convention as the
sibling gold bot's `backups/` folder.

`nasdaq_trades.sqlite` itself is git-ignored at the repo root (a working
file the app writes to constantly) - snapshot it here on purpose before
pushing/switching machines:

```bash
cp nasdaq_trades.sqlite "backups/nasdaq_trades_$(date +%Y-%m-%d).sqlite"
git add backups/ && git commit -m "Backup trade DB" && git push
```

On the other machine, after `git pull`:

```bash
cp backups/nasdaq_trades_<latest-date>.sqlite nasdaq_trades.sqlite
```
