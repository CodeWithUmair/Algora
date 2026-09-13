"""
Data Feed & Realistic NASDAQ-100 Market Simulator for USTECm 1-minute data.

Same generator shape as the sibling gold bot's data_feed.py, scaled for
NASDAQ-100 price levels (~29,000+) and its much larger absolute point
volatility. Used as the MT5Bridge simulation fallback and for backtest
windows where real broker history isn't available.
"""

import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List


def generate_realistic_nasdaq_data(
    num_bars: int = 600,
    base_price: float = 29395.0,
    start_time: datetime = None,
    volatility: float = 8.0,
    seed: int = 42
) -> Dict[str, List]:
    """Generates authentic 1-minute NASDAQ-100 (USTECm) OHLCV bars."""
    if start_time is None:
        now = datetime.now(timezone.utc)
        start_time = datetime(now.year, now.month, now.day, 0, 0, tzinfo=timezone.utc) - timedelta(minutes=num_bars)

    rng = random.Random(seed)

    times, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    price = base_price
    trend = 0.6

    for i in range(num_bars):
        current_time = start_time + timedelta(minutes=i)
        times.append(current_time.isoformat())

        if i % 75 == 0:
            trend = rng.choice([1.5, -1.5, 1.0, -1.0, 0.0])

        hour = current_time.hour
        if 13 <= hour < 17:  # NY cash open - the guide's primary session
            session_mult = 2.4
            vol_base = 400
        elif 7 <= hour < 11:  # London morning
            session_mult = 1.5
            vol_base = 220
        elif 0 <= hour < 5:  # Asian
            session_mult = 0.7
            vol_base = 90
        else:
            session_mult = 1.0
            vol_base = 150

        o = price
        noise = rng.gauss(0, volatility * session_mult)
        delta = (trend * session_mult) + noise
        c = o + delta

        upper_wick = abs(rng.gauss(0, volatility * 0.5))
        lower_wick = abs(rng.gauss(0, volatility * 0.5))
        h = max(o, c) + upper_wick
        l = min(o, c) - lower_wick

        bar_vol = int(vol_base * rng.uniform(0.7, 1.4) + abs(delta) * 8)

        opens.append(round(o, 2))
        highs.append(round(h, 2))
        lows.append(round(l, 2))
        closes.append(round(c, 2))
        volumes.append(bar_vol)

        price = c

    return {"times": times, "opens": opens, "highs": highs, "lows": lows, "closes": closes, "volumes": volumes}
