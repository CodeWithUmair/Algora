"""
Pull real XAUUSDm M1/M5 history from a running/installable MT5 terminal into trading_bot/data_cache/.
Read-only (market data only, no orders). Usage:  python -m trading_bot.fetch_mt5_history [terminal64.exe path]
M1 depth is capped by the terminal's 'maxbars' (100000 = ~3.3 months); M5 goes back >1 year.
"""
import os, sys
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
import pandas as pd

SYMBOL = "XAUUSDm"  # use your broker's gold symbol if different (e.g. XAUUSD)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
    if not mt5.initialize(path=path):
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}")
    mt5.symbol_select(SYMBOL, True)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")
    os.makedirs(out, exist_ok=True)
    for tf, name, days, chunk in ((mt5.TIMEFRAME_M1, "M1", 200, 60), (mt5.TIMEFRAME_M5, "M5", 400, 200)):
        end = datetime.now(timezone.utc); start = end - timedelta(days=days); cur = end; frames = []
        while cur > start:
            cs = max(start, cur - timedelta(days=chunk))
            r = mt5.copy_rates_range(SYMBOL, tf, cs, cur)
            if r is not None and len(r): frames.append(pd.DataFrame(r))
            cur = cs
        df = pd.concat(frames).drop_duplicates("time").sort_values("time")
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"tick_volume": "volume"})[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
        df.to_parquet(os.path.join(out, f"{SYMBOL}_{name}.parquet"), index=False)
        print(name, len(df), df.time.iloc[0], "->", df.time.iloc[-1])
    mt5.shutdown()


if __name__ == "__main__":
    main()
