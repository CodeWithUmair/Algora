"""
NASDAQ-100 Order-Flow Scalper — mechanized strategy engine.

Mechanizes Fabio Valentini's discretionary order-flow playbooks (see
STRATEGY_SPECIFICATION.md for the full mapping and honest limitations)
using range bars + volume profile + CVD proxies built from MT5 OHLCV data,
since MT5 doesn't expose real tick/DOM order-flow data.

Zero-lookahead discipline: every function here only ever looks at bar i and
earlier when producing a value for bar i, same causal guarantee as the
sibling gold bot's strategy.py.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple


@dataclass
class StrategyParameters:
    """All tunable parameters, explicit names, sensible defaults."""

    # 1. Range bar construction
    range_size_points: float = 15.0       # Points of movement per synthetic range bar (NASDAQ-100)
    htf_range_size_points: float = 60.0   # Coarser range size used for the HTF trend filter

    # 2. Volume Profile
    profile_bin_size_points: float = 5.0  # Histogram bucket width
    value_area_pct: float = 0.68          # Fraction of volume inside VAL-VAH
    session_anchor_hour_utc: int = 0      # Daily reset hour for the rolling profile

    # 3. Volume-spike ("Big Trade") detection
    volume_spike_lookback: int = 20
    volume_spike_mult: float = 2.0

    # 4. Model A - AAA Value Area Absorption Reversal
    absorption_buffer_atr: float = 0.5    # How close to VAL/VAH counts as "testing" it
    absorption_max_body_ratio: float = 0.35  # Body/range must be <= this (small body = absorption)

    # 5. Model B - Momentum / Squeeze Breakout
    breakout_min_body_ratio: float = 0.60    # Body/range must be >= this (large body = real expansion)
    rr_ratio: float = 2.0
    be_trigger_ratio: float = 0.70           # Move SL to break-even at this fraction of the way to TP

    # 6. Model C - Failed Auction Fade
    fade_pierce_min_points: float = 3.0      # Minimum piercing beyond VAL/VAH to count as an "attempt"

    # 7. Shared
    atr_period: int = 14
    min_rr_ratio: float = 1.5                # Model A/C trades need at least this R:R to POC/opposite VA
    min_sl_distance_points: float = 8.0
    max_sl_distance_points: float = 60.0

    # 8. HTF & session filters
    enable_htf_filter: bool = True
    htf_ema_period: int = 50
    enable_session_filter: bool = True
    session_start_utc_minutes: int = 13 * 60 + 30   # 13:30 UTC = NY cash open
    session_end_utc_minutes: int = 14 * 60           # 14:00 UTC - tight 30 min execution window


@dataclass
class RangeBar:
    """One synthetic range bar, reconstructed from underlying M1 OHLC."""
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_bar_index: int   # index of the underlying M1 bar this range bar closed on


@dataclass
class SignalResult:
    """Output of evaluating the 3 playbooks at a given range bar."""
    bar_index: int
    time: str
    close_price: float
    model: Optional[str] = None       # "AAA", "SQUEEZE", "FAILED_AUCTION", or None
    direction: Optional[str] = None   # "BUY", "SELL", or None
    reason: str = ""
    suggested_entry: float = 0.0
    suggested_sl: float = 0.0
    suggested_tp: float = 0.0
    risk_points: float = 0.0
    reward_points: float = 0.0
    val: float = 0.0
    vah: float = 0.0
    poc: float = 0.0
    cvd: float = 0.0

    @property
    def all_passed(self) -> bool:
        return self.model is not None and self.direction is not None


# ============================================================================
# 1. RANGE BAR CONSTRUCTION
# ============================================================================

def build_range_bars(
    opens: List[float], highs: List[float], lows: List[float], closes: List[float],
    times: List[str], volumes: List[float], range_size: float
) -> List[RangeBar]:
    """
    Reconstructs range bars from M1 OHLCV using an intrabar path assumption:
    bullish M1 bars (close >= open) are assumed to travel Open -> Low -> High -> Close,
    bearish bars Open -> High -> Low -> Close. This is a standard approximation used
    when only OHLC (not tick/DOM) data is available - see STRATEGY_SPECIFICATION.md.

    Volume is split across the synthetic path proportional to the price distance
    each leg covers.
    """
    if range_size <= 0:
        raise ValueError("range_size must be positive")

    bars: List[RangeBar] = []
    n = len(closes)
    if n == 0:
        return bars

    cur_open = opens[0]
    cur_high = cur_open
    cur_low = cur_open
    cur_vol = 0.0

    for i in range(n):
        o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
        path = [o, l, h, c] if c >= o else [o, h, l, c]
        total_dist = sum(abs(path[k + 1] - path[k]) for k in range(len(path) - 1)) or 1.0

        for k in range(len(path) - 1):
            a, b = path[k], path[k + 1]
            leg_dist = abs(b - a)
            if leg_dist < 1e-9:
                continue
            leg_vol = v * (leg_dist / total_dist)
            direction = 1.0 if b > a else -1.0

            price = a
            remaining = b - a  # signed distance left to travel on this leg

            while abs(remaining) > 1e-9:
                boundary = cur_open + range_size if direction > 0 else cur_open - range_size
                move_to_boundary = boundary - price  # signed, same sign as `direction`

                if abs(remaining) < abs(move_to_boundary) - 1e-9:
                    # Doesn't reach the range-bar boundary - consume the rest of this leg here
                    price += remaining
                    cur_high = max(cur_high, price)
                    cur_low = min(cur_low, price)
                    cur_vol += leg_vol * (abs(remaining) / leg_dist)
                    remaining = 0.0
                else:
                    # Reaches (or passes) the boundary - close this range bar exactly at the boundary
                    price = boundary
                    cur_high = max(cur_high, price)
                    cur_low = min(cur_low, price)
                    cur_vol += leg_vol * (abs(move_to_boundary) / leg_dist)
                    bars.append(RangeBar(
                        time=times[i], open=cur_open, high=cur_high, low=cur_low,
                        close=price, volume=cur_vol, source_bar_index=i
                    ))
                    remaining -= move_to_boundary
                    cur_open = price
                    cur_high = price
                    cur_low = price
                    cur_vol = 0.0

    return bars


# ============================================================================
# 2. INDICATORS (EMA / ATR - same causal math as the gold bot)
# ============================================================================

def calculate_ema(prices: List[float], period: int) -> List[float]:
    if not prices or period <= 0:
        return []
    n = len(prices)
    ema = [0.0] * n
    if n < period:
        avg = sum(prices) / n
        return [avg] * n
    sma = sum(prices[:period]) / period
    for i in range(period):
        ema[i] = sma
    mult = 2.0 / (period + 1.0)
    for i in range(period, n):
        ema[i] = (prices[i] - ema[i - 1]) * mult + ema[i - 1]
    return ema


def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    n = len(highs)
    if n == 0:
        return []
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = [0.0] * n
    if n < period:
        avg = sum(tr) / n if n else 1.0
        return [avg] * n
    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    for i in range(period - 1):
        atr[i] = atr[period - 1]
    return atr


def evaluate_htf_trend(htf_closes: List[float], period: int = 50) -> Tuple[str, float, str]:
    """Simple macro trend read: last close vs EMA(period) on a higher-timeframe series."""
    if len(htf_closes) < period:
        return "NEUTRAL", 0.0, "Insufficient HTF bars"
    ema = calculate_ema(htf_closes, period)
    last_close, last_ema = htf_closes[-1], ema[-1]
    if last_close > last_ema:
        return "BULLISH", last_ema, f"HTF Price (${last_close:.2f}) > EMA{period} (${last_ema:.2f})"
    elif last_close < last_ema:
        return "BEARISH", last_ema, f"HTF Price (${last_close:.2f}) < EMA{period} (${last_ema:.2f})"
    return "NEUTRAL", last_ema, "HTF Price == EMA"


# ============================================================================
# 3. SESSION VOLUME PROFILE (VAL / VAH / POC) - causal, bucketed histogram
# ============================================================================

def _session_key(time_str: str, anchor_hour_utc: int) -> str:
    try:
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except Exception:
        return "0"
    shifted = dt.timestamp() - anchor_hour_utc * 3600
    day = datetime.fromtimestamp(shifted, tz=timezone.utc)
    return day.strftime("%Y-%m-%d")


def calculate_volume_profile(
    highs: List[float], lows: List[float], volumes: List[float], times: List[str],
    bin_size: float = 5.0, value_area_pct: float = 0.68, anchor_hour_utc: int = 0
) -> Tuple[List[float], List[float], List[float]]:
    """
    Returns (val_series, vah_series, poc_series) - one value per bar, computed
    causally from only bars in the same session up to and including bar i.
    Volume is distributed uniformly across each bar's [low, high] range into
    fixed-width price buckets.
    """
    n = len(highs)
    val_s, vah_s, poc_s = [0.0] * n, [0.0] * n, [0.0] * n
    if n == 0:
        return val_s, vah_s, poc_s

    histogram: Dict[int, float] = {}
    last_session = None

    for i in range(n):
        session = _session_key(times[i], anchor_hour_utc)
        if session != last_session:
            histogram = {}
            last_session = session

        lo, hi, vol = lows[i], highs[i], max(float(volumes[i]) if volumes[i] else 1.0, 1.0)
        lo_bin = int(math.floor(lo / bin_size))
        hi_bin = int(math.floor(hi / bin_size))
        num_bins = max(1, hi_bin - lo_bin + 1)
        vol_per_bin = vol / num_bins
        for b in range(lo_bin, hi_bin + 1):
            histogram[b] = histogram.get(b, 0.0) + vol_per_bin

        if not histogram:
            val_s[i] = vah_s[i] = poc_s[i] = (lo + hi) / 2.0
            continue

        poc_bin = max(histogram, key=histogram.get)
        total_vol = sum(histogram.values())
        target = total_vol * value_area_pct

        included = {poc_bin}
        acc = histogram[poc_bin]
        lo_b, hi_b = poc_bin, poc_bin
        while acc < target and (lo_b - 1 in histogram or hi_b + 1 in histogram):
            below = histogram.get(lo_b - 1, -1.0)
            above = histogram.get(hi_b + 1, -1.0)
            if above >= below:
                hi_b += 1
                acc += histogram.get(hi_b, 0.0)
                included.add(hi_b)
            else:
                lo_b -= 1
                acc += histogram.get(lo_b, 0.0)
                included.add(lo_b)

        poc_s[i] = (poc_bin + 0.5) * bin_size
        val_s[i] = lo_b * bin_size
        vah_s[i] = (hi_b + 1) * bin_size

    return val_s, vah_s, poc_s


# ============================================================================
# 4. CVD PROXY & VOLUME SPIKE
# ============================================================================

def calculate_cvd(opens: List[float], closes: List[float], volumes: List[float]) -> List[float]:
    """Causal proxy Cumulative Volume Delta: +volume on up-closes, -volume on down-closes."""
    n = len(closes)
    cvd = [0.0] * n
    running = 0.0
    for i in range(n):
        if closes[i] > opens[i]:
            running += volumes[i]
        elif closes[i] < opens[i]:
            running -= volumes[i]
        cvd[i] = running
    return cvd


def is_volume_spike(volumes: List[float], idx: int, lookback: int = 20, mult: float = 2.0) -> bool:
    if idx < lookback:
        return False
    window = volumes[idx - lookback:idx]
    avg = sum(window) / len(window) if window else 0.0
    return avg > 0 and volumes[idx] >= mult * avg


# ============================================================================
# 5. THE THREE PLAYBOOKS
# ============================================================================

def _body_range_ratio(o: float, h: float, l: float, c: float) -> float:
    rng = h - l
    if rng <= 1e-9:
        return 0.0
    return abs(c - o) / rng


def evaluate_signal_at_bar(
    range_bars: List[RangeBar],
    val_series: List[float], vah_series: List[float], poc_series: List[float],
    cvd_series: List[float],
    atr_series: List[float],
    idx: int,
    params: StrategyParameters,
) -> SignalResult:
    """
    Evaluates all 3 playbooks at range_bars[idx] and returns whichever fires
    first (AAA > Squeeze > Failed Auction priority), or an empty SignalResult
    if nothing triggers. Purely causal - only uses range_bars[0..idx] and the
    matching indicator series entries.
    """
    bar = range_bars[idx]
    val, vah, poc = val_series[idx], vah_series[idx], poc_series[idx]
    atr = atr_series[idx] if idx < len(atr_series) and atr_series[idx] > 0 else 1.0
    cvd = cvd_series[idx]

    result = SignalResult(
        bar_index=idx, time=bar.time, close_price=bar.close,
        val=val, vah=vah, poc=poc, cvd=cvd,
    )

    body_ratio = _body_range_ratio(bar.open, bar.high, bar.low, bar.close)
    vol_spike = is_volume_spike([b.volume for b in range_bars], idx, params.volume_spike_lookback, params.volume_spike_mult)

    # ---- Model A: AAA Value Area Absorption Reversal ----
    buffer = params.absorption_buffer_atr * atr
    testing_val = bar.low <= val + buffer
    testing_vah = bar.high >= vah - buffer
    if vol_spike and body_ratio <= params.absorption_max_body_ratio:
        if testing_val and not testing_vah:
            sl = bar.low - buffer
            risk = bar.close - sl
            tp = poc if poc > bar.close else vah
            reward = tp - bar.close
            if risk > 0 and reward / risk >= params.min_rr_ratio:
                result.model, result.direction = "AAA", "BUY"
                result.reason = f"Absorption at VAL ${val:.1f} (vol spike, body ratio {body_ratio:.2f})"
                result.suggested_entry, result.suggested_sl, result.suggested_tp = bar.close, sl, tp
                result.risk_points, result.reward_points = risk, reward
                return result
        if testing_vah and not testing_val:
            sl = bar.high + buffer
            risk = sl - bar.close
            tp = poc if poc < bar.close else val
            reward = bar.close - tp
            if risk > 0 and reward / risk >= params.min_rr_ratio:
                result.model, result.direction = "AAA", "SELL"
                result.reason = f"Absorption at VAH ${vah:.1f} (vol spike, body ratio {body_ratio:.2f})"
                result.suggested_entry, result.suggested_sl, result.suggested_tp = bar.close, sl, tp
                result.risk_points, result.reward_points = risk, reward
                return result

    # ---- Model B: Momentum / Squeeze Breakout ----
    if vol_spike and body_ratio >= params.breakout_min_body_ratio:
        if bar.close > vah and bar.open <= vah:
            sl = bar.low
            risk = bar.close - sl
            tp = bar.close + risk * params.rr_ratio
            if risk > 0:
                result.model, result.direction = "SQUEEZE", "BUY"
                result.reason = f"Breakout above VAH ${vah:.1f} (expansion bar, vol spike)"
                result.suggested_entry, result.suggested_sl, result.suggested_tp = bar.close, sl, tp
                result.risk_points, result.reward_points = risk, risk * params.rr_ratio
                return result
        if bar.close < val and bar.open >= val:
            sl = bar.high
            risk = sl - bar.close
            tp = bar.close - risk * params.rr_ratio
            if risk > 0:
                result.model, result.direction = "SQUEEZE", "SELL"
                result.reason = f"Breakdown below VAL ${val:.1f} (expansion bar, vol spike)"
                result.suggested_entry, result.suggested_sl, result.suggested_tp = bar.close, sl, tp
                result.risk_points, result.reward_points = risk, risk * params.rr_ratio
                return result

    # ---- Model C: Failed Auction Fade ----
    if idx >= 1:
        prev = range_bars[idx - 1]
        prev_vah, prev_val = vah_series[idx - 1], val_series[idx - 1]
        pierced_high = prev.high > prev_vah + params.fade_pierce_min_points
        pierced_low = prev.low < prev_val - params.fade_pierce_min_points
        if pierced_high and bar.close < prev_vah:
            sl = prev.high
            risk = sl - bar.close
            tp = poc if poc < bar.close else prev_val
            reward = bar.close - tp
            if risk > 0 and reward / risk >= params.min_rr_ratio:
                result.model, result.direction = "FAILED_AUCTION", "SELL"
                result.reason = f"Failed breakout above VAH ${prev_vah:.1f}, closed back inside"
                result.suggested_entry, result.suggested_sl, result.suggested_tp = bar.close, sl, tp
                result.risk_points, result.reward_points = risk, reward
                return result
        if pierced_low and bar.close > prev_val:
            sl = prev.low
            risk = bar.close - sl
            tp = poc if poc > bar.close else prev_vah
            reward = tp - bar.close
            if risk > 0 and reward / risk >= params.min_rr_ratio:
                result.model, result.direction = "FAILED_AUCTION", "BUY"
                result.reason = f"Failed breakdown below VAL ${prev_val:.1f}, closed back inside"
                result.suggested_entry, result.suggested_sl, result.suggested_tp = bar.close, sl, tp
                result.risk_points, result.reward_points = risk, reward
                return result

    return result


def is_in_session_window(utc_dt: Optional[datetime], params: StrategyParameters) -> bool:
    now = utc_dt or datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    return params.session_start_utc_minutes <= minutes <= params.session_end_utc_minutes


def calculate_position_size(
    risk_per_trade_usd: float, sl_distance_points: float, point_value_per_lot: float,
    volume_min: float, volume_max: float, volume_step: float
) -> float:
    """Risk-based lot sizing: risk a fixed $ amount per trade instead of a fixed lot."""
    if sl_distance_points <= 0 or point_value_per_lot <= 0:
        return volume_min
    raw_lots = risk_per_trade_usd / (sl_distance_points * point_value_per_lot)
    steps = round(raw_lots / volume_step)
    lots = max(volume_min, min(volume_max, steps * volume_step))
    return round(lots, 2)
