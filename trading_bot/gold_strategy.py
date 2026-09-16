"""
Gold (XAU/USD) Fibonacci Pivot + EMA 9 Scalper — Mechanized Strategy Engine.

Strategy Rules:
1. Daily Fibonacci Pivot Points calculated from previous day H/L/C:
   PP = (High + Low + Close) / 3
   Range = High - Low
   R1, R2, R3 = PP + (0.382, 0.618, 1.000) * Range
   S1, S2, S3 = PP - (0.382, 0.618, 1.000) * Range
2. EMA 9 (Exponential Moving Average 9-period on intraday candles, e.g. M5).
3. BUY Signal:
   - Candle closes above EMA 9 (close > ema9)
   - Candle crosses UP above any Pivot level (prev_close < Level and close > Level + Buffer)
4. SELL Signal:
   - Candle closes below EMA 9 (close < ema9)
   - Candle crosses DOWN below any Pivot level (prev_close > Level and close < Level - Buffer)
5. Whipsaw Cooldown:
   - 5-bar cooldown per level to prevent over-trading around choppy zones.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np


@dataclass
class GoldStrategyParameters:
    """Tunable parameters for Gold (XAUUSDm) Fib Pivot + EMA9 strategy."""
    symbol: str = "XAUUSDm"
    magic_number: int = 9212001
    timeframe_str: str = "M5"

    # Indicator parameters
    ema_period: int = 9
    buffer_pips: float = 2.0        # Buffer in pips beyond pivot level (1 pip = $0.10 in Gold)
    cooldown_bars: int = 5          # Cooldown bars before re-trading the same pivot level
    min_candle_range_pips: float = 5.0 # Minimum candle range (High - Low) in pips to filter micro-bars

    # Execution parameters
    sl_pips: float = 32.0           # Stop Loss in pips ($3.20)
    tp_pips: float = 60.0           # Take Profit in pips ($6.00)
    fixed_lot_size: float = 0.01    # Fixed lot size

    # Safety
    daily_loss_cap_usd: float = 10.0

    # Session Filter (London + NY: 07:00 UTC to 20:00 UTC / 12:00 PM to 01:00 AM PKT)
    enable_session_filter: bool = True
    session_start_utc_hour: int = 7
    session_end_utc_hour: int = 20


@dataclass
class DailyFibonacciPivots:
    """Daily Fibonacci Pivot Points container."""
    pp: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float

    def get_levels_dict(self) -> Dict[str, float]:
        return {
            'PP': self.pp,
            'R1': self.r1,
            'R2': self.r2,
            'R3': self.r3,
            'S1': self.s1,
            'S2': self.s2,
            'S3': self.s3,
        }


@dataclass
class GoldSignalResult:
    """Output of evaluating Gold Fib Pivot + EMA9 strategy on a bar."""
    bar_index: int
    time: str
    close_price: float
    signal_type: Optional[str] = None  # "BUY", "SELL", or None
    trigger_level: Optional[str] = None # "PP", "R1", etc.
    reason: str = ""
    suggested_entry: float = 0.0
    suggested_sl: float = 0.0
    suggested_tp: float = 0.0
    ema9_val: float = 0.0
    pivots: Optional[DailyFibonacciPivots] = None


def compute_fibonacci_pivots(high: float, low: float, close: float) -> DailyFibonacciPivots:
    """Calculate Fibonacci Pivot Points from previous session High, Low, Close."""
    pp = (high + low + close) / 3.0
    rng = high - low
    return DailyFibonacciPivots(
        pp=pp,
        r1=pp + (0.382 * rng),
        r2=pp + (0.618 * rng),
        r3=pp + (1.000 * rng),
        s1=pp - (0.382 * rng),
        s2=pp - (0.618 * rng),
        s3=pp - (1.000 * rng)
    )


def compute_ema(series: pd.Series, period: int = 9) -> pd.Series:
    """Compute Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def eval_gold_signal(
    df: pd.DataFrame,
    params: Optional[GoldStrategyParameters] = None,
    last_level_trade_bars: Optional[Dict[str, int]] = None
) -> Tuple[GoldSignalResult, Dict[str, int]]:
    """
    Evaluate Gold Fib Pivot + EMA 9 signal for the latest bar in df.

    df must contain columns: ['time', 'open', 'high', 'low', 'close']
    Also needs daily OHLC context to compute previous day's pivots.
    """
    if params is None:
        params = GoldStrategyParameters()
    if last_level_trade_bars is None:
        last_level_trade_bars = {}

    if len(df) < 10:
        return GoldSignalResult(
            bar_index=len(df) - 1,
            time=str(df['time'].iloc[-1]) if len(df) > 0 else "",
            close_price=float(df['close'].iloc[-1]) if len(df) > 0 else 0.0,
            reason="Insufficient bar history"
        ), last_level_trade_bars

    # Calculate EMA 9
    df = df.copy()
    df['ema9'] = compute_ema(df['close'], period=params.ema_period)

    # Convert time to datetime if needed
    if not pd.api.types.is_datetime64_any_dtype(df['time']):
        df['time_dt'] = pd.to_datetime(df['time'])
    else:
        df['time_dt'] = df['time']

    df['date'] = df['time_dt'].dt.date

    curr_idx = len(df) - 1
    curr_row = df.iloc[curr_idx]
    prev_row = df.iloc[curr_idx - 1]

    curr_date = curr_row['date']

    # Filter previous days for Pivot calculation
    prev_df = df[df['date'] < curr_date]
    if prev_df.empty:
        # Fallback to current available bars prior to today
        prev_df = df.iloc[:curr_idx]

    prev_day_date = prev_df['date'].max()
    prev_day_bars = prev_df[prev_df['date'] == prev_day_date]

    if prev_day_bars.empty:
        return GoldSignalResult(
            bar_index=curr_idx,
            time=str(curr_row['time']),
            close_price=float(curr_row['close']),
            reason="No previous day data for Fib Pivots"
        ), last_level_trade_bars

    prev_high = float(prev_day_bars['high'].max())
    prev_low = float(prev_day_bars['low'].min())
    prev_close = float(prev_day_bars['close'].iloc[-1])

    pivots = compute_fibonacci_pivots(prev_high, prev_low, prev_close)
    levels = pivots.get_levels_dict()

    c_curr = float(curr_row['close'])
    o_curr = float(curr_row['open'])
    h_curr = float(curr_row['high'])
    l_curr = float(curr_row['low'])
    c_prev = float(prev_row['close'])
    ema9_val = float(curr_row['ema9'])

    candle_range_pips = (h_curr - l_curr) / 0.10
    min_range_pips = params.min_candle_range_pips

    buffer_dist = params.buffer_pips * 0.10 # 1 pip in XAUUSD = $0.10
    sl_dist = params.sl_pips * 0.10
    tp_dist = params.tp_pips * 0.10

    # Session Filter Check (Asian session & late night blocked)
    if params.enable_session_filter:
        curr_hour = curr_row['time_dt'].hour
        if curr_hour < params.session_start_utc_hour or curr_hour >= params.session_end_utc_hour:
            return GoldSignalResult(
                bar_index=curr_idx,
                time=str(curr_row['time']),
                close_price=c_curr,
                reason=f"Outside trading session window ({curr_hour:02d}:00 UTC, active: {params.session_start_utc_hour:02d}:00-{params.session_end_utc_hour:02d}:00 UTC / Asian session blocked)",
                ema9_val=ema9_val,
                pivots=pivots
            ), last_level_trade_bars

    signal_type = None
    trigger_level = None
    reason = ""

    # Check crossovers across all 7 pivot levels
    for lvl_name, lvl_val in levels.items():
        # Check cooldown
        if lvl_name in last_level_trade_bars:
            if (curr_idx - last_level_trade_bars[lvl_name]) < params.cooldown_bars:
                continue

        # BUY: prev < lvl, curr > lvl + buffer, curr > ema9, green candle, range >= min_range
        if c_prev < lvl_val and c_curr > (lvl_val + buffer_dist) and c_curr > ema9_val and c_curr > o_curr and candle_range_pips >= min_range_pips:
            signal_type = "BUY"
            trigger_level = lvl_name
            reason = f"Bullish crossover above {lvl_name} ({lvl_val:.2f}) with Green candle & Close > EMA9 ({ema9_val:.2f})"
            last_level_trade_bars[lvl_name] = curr_idx
            break

        # SELL: prev > lvl, curr < lvl - buffer, curr < ema9, red candle, range >= min_range
        elif c_prev > lvl_val and c_curr < (lvl_val - buffer_dist) and c_curr < ema9_val and c_curr < o_curr and candle_range_pips >= min_range_pips:
            signal_type = "SELL"
            trigger_level = lvl_name
            reason = f"Bearish crossover below {lvl_name} ({lvl_val:.2f}) with Red candle & Close < EMA9 ({ema9_val:.2f})"
            last_level_trade_bars[lvl_name] = curr_idx
            break

    if signal_type == "BUY":
        entry = c_curr
        sl = entry - sl_dist
        tp = entry + tp_dist
    elif signal_type == "SELL":
        entry = c_curr
        sl = entry + sl_dist
        tp = entry - tp_dist
    else:
        entry = sl = tp = 0.0
        reason = "No crossover condition met"

    res = GoldSignalResult(
        bar_index=curr_idx,
        time=str(curr_row['time']),
        close_price=c_curr,
        signal_type=signal_type,
        trigger_level=trigger_level,
        reason=reason,
        suggested_entry=entry,
        suggested_sl=sl,
        suggested_tp=tp,
        ema9_val=ema9_val,
        pivots=pivots
    )

    return res, last_level_trade_bars
