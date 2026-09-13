"""
Causal Backtesting Engine for the NASDAQ-100 (USTECm) Order-Flow Scalper.

Same statistical rigor as the sibling gold bot's backtest.py (Trade/Metrics/
Result shapes, Monte Carlo noise-control gate are near-identical, proven
code) - what's different is the simulation loop, which walks RANGE bars
(reconstructed from M1 OHLC, see strategy.py) instead of time bars, and
evaluates the 3 order-flow playbooks instead of the 5-step checklist.

Strict Non-Negotiable Requirements (same as gold bot):
1. No Lookahead Bias: Signal generated on range-bar i fills on bar i+1 Open.
2. Realistic Wick Fills: SL/TP evaluated against bar High/Low.
3. Realistic Broker Costs: spread + commission.
4. Train/Test Split: distinct In-Sample / Out-of-Sample evaluation.
5. Noise Control Gate: Monte Carlo reshuffling for empirical p-value.
"""

import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from trading_bot.strategy import (
    StrategyParameters,
    build_range_bars,
    calculate_volume_profile,
    calculate_cvd,
    calculate_atr,
    evaluate_signal_at_bar,
    is_in_session_window,
)


@dataclass
class Trade:
    """Represents a simulated executed trade."""
    id: int
    direction: str  # "BUY" or "SELL"
    model: str       # "AAA", "SQUEEZE", "FAILED_AUCTION"
    signal_bar: int
    signal_time: str
    entry_bar: int
    entry_time: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_points: float
    reward_points: float
    lot_size: float = 0.05
    exit_bar: Optional[int] = None
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_pnl_usd: float = 0.0
    net_pnl_usd: float = 0.0
    pnl_r_multiple: float = 0.0
    spread_paid_usd: float = 0.0
    commission_paid_usd: float = 0.0
    duration_bars: int = 0
    is_out_of_sample: bool = False


@dataclass
class BacktestMetrics:
    """Summary metrics for a backtest segment. Identical shape to the gold bot's."""
    segment_name: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy_r: float
    expectancy_usd: float
    total_net_pnl_usd: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    average_trade_bars: float
    avg_win_usd: float
    avg_loss_usd: float
    payoff_ratio: float
    max_consecutive_losses: int
    sharpe_ratio: float
    noise_gate_passed: bool
    noise_p_value: float
    z_score: float
    shuffled_expectancies: List[float] = field(default_factory=list)


@dataclass
class BacktestResult:
    params: StrategyParameters
    in_sample_metrics: BacktestMetrics
    out_of_sample_metrics: BacktestMetrics
    overall_metrics: BacktestMetrics
    trades: List[Trade]
    equity_curve: List[Dict[str, Any]]
    split_index: int
    initial_balance: float
    final_balance: float
    symbol: str = "USTECm"
    timeframe: str = "RANGE"
    spread_points: float = 2.0
    commission_per_lot_usd: float = 3.0
    num_range_bars: int = 0
    daily_loss_cap_usd: Optional[float] = None
    daily_cap_trip_count: int = 0
    daily_pnl_log: List[Dict[str, Any]] = field(default_factory=list)


def run_noise_control_gate(
    trades: List[Trade], num_shuffles: int = 100, alpha_threshold: float = 0.05, min_trade_count: int = 20
) -> Tuple[bool, float, float, List[float]]:
    """Identical logic to the gold bot's noise gate - random sign-flip permutation test."""
    if len(trades) < min_trade_count:
        return False, 1.0, 0.0, [0.0] * num_shuffles

    r_multiples = [t.pnl_r_multiple for t in trades]
    real_expectancy = sum(r_multiples) / len(r_multiples)
    if real_expectancy <= 0:
        return False, 1.0, 0.0, [0.0] * num_shuffles

    shuffled_expectancies = []
    better_or_equal = 0
    for _ in range(num_shuffles):
        shuffled = [r if random.random() > 0.5 else -abs(r) for r in r_multiples]
        exp = sum(shuffled) / len(shuffled)
        shuffled_expectancies.append(round(exp, 4))
        if exp >= real_expectancy:
            better_or_equal += 1

    p_value = round((better_or_equal + 1) / (num_shuffles + 1), 4)
    mean_null = sum(shuffled_expectancies) / len(shuffled_expectancies)
    var_null = sum((x - mean_null) ** 2 for x in shuffled_expectancies) / max(len(shuffled_expectancies) - 1, 1)
    std_null = math.sqrt(max(var_null, 1e-6))
    z_score = round((real_expectancy - mean_null) / std_null, 2)
    passed = (p_value <= alpha_threshold and real_expectancy > 0 and len(trades) >= min_trade_count)
    return passed, p_value, z_score, shuffled_expectancies


def calculate_metrics_from_trades(
    trades: List[Trade], segment_name: str, initial_balance: float = 10000.0, num_shuffles: int = 100
) -> BacktestMetrics:
    """Identical logic to the gold bot's metrics computation."""
    if not trades:
        return BacktestMetrics(
            segment_name=segment_name, total_trades=0, winning_trades=0, losing_trades=0,
            win_rate_pct=0.0, profit_factor=0.0, expectancy_r=0.0, expectancy_usd=0.0,
            total_net_pnl_usd=0.0, max_drawdown_usd=0.0, max_drawdown_pct=0.0,
            average_trade_bars=0.0, avg_win_usd=0.0, avg_loss_usd=0.0, payoff_ratio=0.0,
            max_consecutive_losses=0, sharpe_ratio=0.0, noise_gate_passed=False,
            noise_p_value=1.0, z_score=0.0, shuffled_expectancies=[]
        )

    total_trades = len(trades)
    winning_trades = len([t for t in trades if t.net_pnl_usd > 0])
    losing_trades = len([t for t in trades if t.net_pnl_usd <= 0])
    win_rate_pct = round((winning_trades / total_trades) * 100.0, 2)

    gross_wins = sum(t.net_pnl_usd for t in trades if t.net_pnl_usd > 0)
    gross_losses = abs(sum(t.net_pnl_usd for t in trades if t.net_pnl_usd < 0))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (99.0 if gross_wins > 0 else 0.0)

    total_net_pnl_usd = round(sum(t.net_pnl_usd for t in trades), 2)
    expectancy_r = round(sum(t.pnl_r_multiple for t in trades) / total_trades, 3)
    expectancy_usd = round(total_net_pnl_usd / total_trades, 2)

    avg_win_usd = round(gross_wins / winning_trades, 2) if winning_trades > 0 else 0.0
    avg_loss_usd = round(gross_losses / losing_trades, 2) if losing_trades > 0 else 0.0
    payoff_ratio = round(avg_win_usd / avg_loss_usd, 2) if avg_loss_usd > 0 else 0.0

    balance = initial_balance
    peak = initial_balance
    max_dd_usd = max_dd_pct = 0.0
    curr_consec = max_consec = 0
    pnl_returns = []

    for t in trades:
        balance += t.net_pnl_usd
        peak = max(peak, balance)
        dd_usd = peak - balance
        dd_pct = (dd_usd / peak) * 100.0 if peak > 0 else 0.0
        max_dd_usd = max(max_dd_usd, dd_usd)
        max_dd_pct = max(max_dd_pct, dd_pct)
        if t.net_pnl_usd <= 0:
            curr_consec += 1
            max_consec = max(max_consec, curr_consec)
        else:
            curr_consec = 0
        pnl_returns.append(t.net_pnl_usd)

    avg_duration = round(sum(t.duration_bars for t in trades) / total_trades, 1)

    if len(pnl_returns) > 1:
        mean_ret = sum(pnl_returns) / len(pnl_returns)
        var_ret = sum((r - mean_ret) ** 2 for r in pnl_returns) / (len(pnl_returns) - 1)
        std_ret = math.sqrt(max(var_ret, 1e-6))
        sharpe = round((mean_ret / std_ret) * math.sqrt(252 * 50), 2)
    else:
        sharpe = 0.0

    passed_gate, p_val, z_sc, shuffles = run_noise_control_gate(trades, num_shuffles=num_shuffles)

    return BacktestMetrics(
        segment_name=segment_name, total_trades=total_trades, winning_trades=winning_trades,
        losing_trades=losing_trades, win_rate_pct=win_rate_pct, profit_factor=profit_factor,
        expectancy_r=expectancy_r, expectancy_usd=expectancy_usd, total_net_pnl_usd=total_net_pnl_usd,
        max_drawdown_usd=round(max_dd_usd, 2), max_drawdown_pct=round(max_dd_pct, 2),
        average_trade_bars=avg_duration, avg_win_usd=avg_win_usd, avg_loss_usd=avg_loss_usd,
        payoff_ratio=payoff_ratio, max_consecutive_losses=max_consec, sharpe_ratio=sharpe,
        noise_gate_passed=passed_gate, noise_p_value=p_val, z_score=z_sc, shuffled_expectancies=shuffles
    )


def run_causal_backtest(
    opens: List[float], highs: List[float], lows: List[float], closes: List[float],
    times: List[str], volumes: List[float],
    params: StrategyParameters,
    initial_balance: float = 10000.0,
    split_ratio: float = 0.75,
    spread_points: float = 2.0,
    commission_per_lot_usd: float = 3.0,
    contract_size: float = 1.0,        # USTECm: 1 point * 1.0 lot * contract_size(1.0) = $1.00
    fixed_lot_size: Optional[float] = None,   # None = risk-based sizing via params below
    risk_per_trade_usd: float = 3.0,
    volume_min: float = 0.05, volume_max: float = 500.0, volume_step: float = 0.01,
    num_noise_shuffles: int = 100,
    daily_loss_cap_usd: Optional[float] = None,  # None = no daily loss cap (default). When set,
                                                   # no NEW trade is opened for the rest of the UTC
                                                   # calendar day once realized losses that day reach
                                                   # this amount; resumes automatically next UTC day.
                                                   # Profit is never capped - only losses halt new entries.
) -> BacktestResult:
    """
    Executes a causal, zero-lookahead backtest.

    1. Reconstructs range bars from the input M1 OHLC (see strategy.build_range_bars).
    2. Computes session Volume Profile (VAL/VAH/POC), CVD, and ATR on the range-bar series.
    3. Walks range bars causally: signal at bar i close -> fill at bar i+1 open.
    4. Optionally simulates a daily loss circuit breaker (see daily_loss_cap_usd above) -
       a backtest-side approximation of circuit_breakers.py's live daily-loss breaker,
       reimplemented here against simulated bar time rather than wall-clock time.
    """
    range_bars = build_range_bars(opens, highs, lows, closes, times, volumes, params.range_size_points)
    n = len(range_bars)
    if n < 50:
        raise ValueError(f"Insufficient range bars for backtest (got {n}, need at least 50) - "
                          f"try a smaller range_size_points or a longer input window.")

    rb_highs = [b.high for b in range_bars]
    rb_lows = [b.low for b in range_bars]
    rb_closes = [b.close for b in range_bars]
    rb_opens = [b.open for b in range_bars]
    rb_volumes = [b.volume for b in range_bars]
    rb_times = [b.time for b in range_bars]

    val_s, vah_s, poc_s = calculate_volume_profile(
        rb_highs, rb_lows, rb_volumes, rb_times,
        params.profile_bin_size_points, params.value_area_pct, params.session_anchor_hour_utc
    )
    cvd_s = calculate_cvd(rb_opens, rb_closes, rb_volumes)
    atr_s = calculate_atr(rb_highs, rb_lows, rb_closes, params.atr_period)

    split_idx = int(n * split_ratio)
    trades: List[Trade] = []
    trade_id_counter = 1
    open_trade: Optional[Trade] = None
    pending_signal: Optional[Dict[str, Any]] = None
    last_exit_bar = -999

    equity = initial_balance
    equity_curve = [{"bar_index": 0, "time": rb_times[0], "balance": equity, "equity": equity, "is_out_of_sample": False}]

    warmup = max(params.volume_spike_lookback, params.atr_period) + 5

    def point_value(lot: float) -> float:
        return lot * contract_size

    current_day_key: Optional[str] = None
    daily_realized_pnl = 0.0
    day_loss_tripped = False
    daily_cap_trip_count = 0
    daily_pnl_log: List[Dict[str, Any]] = []

    for i in range(warmup, n):
        c_open, c_high, c_low, c_close = rb_opens[i], rb_highs[i], rb_lows[i], rb_closes[i]
        c_time = rb_times[i]
        is_oos = (i >= split_idx)

        if daily_loss_cap_usd is not None:
            day_key = c_time[:10]
            if day_key != current_day_key:
                if current_day_key is not None:
                    daily_pnl_log.append({"date": current_day_key, "pnl_usd": round(daily_realized_pnl, 2),
                                           "cap_tripped": day_loss_tripped})
                current_day_key = day_key
                daily_realized_pnl = 0.0
                day_loss_tripped = False

        # 1. Fill pending signal at this bar's open
        if pending_signal is not None and open_trade is None:
            sig = pending_signal
            pv = 0.0
            if sig["direction"] == "BUY":
                fill_price = c_open + spread_points
                risk_pts = fill_price - sig["sl"]
                reward_pts = sig["tp"] - fill_price
            else:
                fill_price = c_open
                risk_pts = sig["sl"] - fill_price
                reward_pts = fill_price - sig["tp"]

            if risk_pts >= params.min_sl_distance_points:
                if fixed_lot_size is not None:
                    lot = fixed_lot_size
                else:
                    raw = risk_per_trade_usd / (risk_pts * contract_size) if risk_pts > 0 else volume_min
                    steps = round(raw / volume_step)
                    lot = max(volume_min, min(volume_max, steps * volume_step))

                open_trade = Trade(
                    id=trade_id_counter, direction=sig["direction"], model=sig["model"],
                    signal_bar=sig["bar_index"], signal_time=sig["time"],
                    entry_bar=i, entry_time=c_time, entry_price=fill_price,
                    stop_loss=sig["sl"], take_profit=sig["tp"],
                    risk_points=risk_pts, reward_points=reward_pts,
                    lot_size=round(lot, 2), is_out_of_sample=is_oos
                )
                trade_id_counter += 1
            pending_signal = None

        # 2. Check exits on active trade using this bar's wicks
        if open_trade is not None:
            hit_sl = hit_tp = False
            exit_price = 0.0
            exit_reason = ""
            if open_trade.direction == "BUY":
                if c_low <= open_trade.stop_loss:
                    hit_sl, exit_price, exit_reason = True, open_trade.stop_loss, "STOP_LOSS"
                elif c_high >= open_trade.take_profit:
                    hit_tp, exit_price, exit_reason = True, open_trade.take_profit, "TAKE_PROFIT"
            else:
                if (c_high + spread_points) >= open_trade.stop_loss:
                    hit_sl, exit_price, exit_reason = True, open_trade.stop_loss, "STOP_LOSS"
                elif (c_low + spread_points) <= open_trade.take_profit:
                    hit_tp, exit_price, exit_reason = True, open_trade.take_profit, "TAKE_PROFIT"

            if hit_sl or hit_tp:
                open_trade.exit_bar = i
                open_trade.exit_time = c_time
                open_trade.exit_price = exit_price
                open_trade.exit_reason = exit_reason
                open_trade.duration_bars = i - open_trade.entry_bar + 1

                pv = point_value(open_trade.lot_size)
                pts_diff = (exit_price - open_trade.entry_price) if open_trade.direction == "BUY" else (open_trade.entry_price - exit_price)
                gross_pnl = pts_diff * pv
                comm_usd = commission_per_lot_usd * open_trade.lot_size
                spread_usd = spread_points * pv
                net_pnl = gross_pnl - comm_usd
                initial_risk_usd = max(open_trade.risk_points * pv + comm_usd, 1.0)

                open_trade.gross_pnl_usd = round(gross_pnl, 2)
                open_trade.net_pnl_usd = round(net_pnl, 2)
                open_trade.commission_paid_usd = round(comm_usd, 2)
                open_trade.spread_paid_usd = round(spread_usd, 2)
                open_trade.pnl_r_multiple = round(net_pnl / initial_risk_usd, 2)

                trades.append(open_trade)
                equity += net_pnl
                open_trade = None
                last_exit_bar = i

                if daily_loss_cap_usd is not None:
                    daily_realized_pnl += net_pnl
                    if daily_realized_pnl <= -abs(daily_loss_cap_usd) and not day_loss_tripped:
                        day_loss_tripped = True
                        daily_cap_trip_count += 1
            else:
                # Break-even shield (Model B squeeze trades only - matches the guide's 60s rule)
                if open_trade.model == "SQUEEZE":
                    be_trigger = params.be_trigger_ratio
                    if open_trade.direction == "BUY":
                        favorable = c_high - open_trade.entry_price
                        target_dist = open_trade.take_profit - open_trade.entry_price
                        if target_dist > 0 and (favorable / target_dist) >= be_trigger:
                            be_price = open_trade.entry_price + spread_points
                            if be_price > open_trade.stop_loss:
                                open_trade.stop_loss = be_price
                    else:
                        favorable = open_trade.entry_price - c_low
                        target_dist = open_trade.entry_price - open_trade.take_profit
                        if target_dist > 0 and (favorable / target_dist) >= be_trigger:
                            be_price = open_trade.entry_price - spread_points
                            if be_price < open_trade.stop_loss:
                                open_trade.stop_loss = be_price

        # 3. Evaluate playbooks at this bar's close (fills next bar)
        cap_blocks_new_entries = daily_loss_cap_usd is not None and day_loss_tripped
        if open_trade is None and pending_signal is None and i < n - 1 and (i - last_exit_bar) >= 2 and not cap_blocks_new_entries:
            session_ok = True
            if params.enable_session_filter:
                try:
                    dt = datetime.fromisoformat(c_time.replace("Z", "+00:00"))
                    session_ok = is_in_session_window(dt, params)
                except Exception:
                    session_ok = True

            if session_ok:
                sig = evaluate_signal_at_bar(range_bars, val_s, vah_s, poc_s, cvd_s, atr_s, i, params)
                if sig.all_passed:
                    sl_clamped = min(max(sig.risk_points, params.min_sl_distance_points), params.max_sl_distance_points)
                    if sig.direction == "BUY":
                        sl = sig.close_price - sl_clamped
                    else:
                        sl = sig.close_price + sl_clamped
                    pending_signal = {
                        "direction": sig.direction, "model": sig.model,
                        "bar_index": i, "time": c_time, "sl": sl, "tp": sig.suggested_tp,
                    }

        if i % 10 == 0 or open_trade is None:
            equity_curve.append({"bar_index": i, "time": c_time, "balance": round(equity, 2),
                                  "equity": round(equity, 2), "is_out_of_sample": is_oos})

    if open_trade is not None:
        open_trade.exit_bar = n - 1
        open_trade.exit_time = rb_times[-1]
        open_trade.exit_price = rb_closes[-1]
        open_trade.exit_reason = "END_OF_DATA"
        open_trade.duration_bars = n - 1 - open_trade.entry_bar + 1
        pv = point_value(open_trade.lot_size)
        pts_diff = (rb_closes[-1] - open_trade.entry_price) if open_trade.direction == "BUY" else (open_trade.entry_price - rb_closes[-1])
        gross_pnl = pts_diff * pv
        comm_usd = commission_per_lot_usd * open_trade.lot_size
        net_pnl = gross_pnl - comm_usd
        initial_risk_usd = max(open_trade.risk_points * pv + comm_usd, 1.0)
        open_trade.gross_pnl_usd = round(gross_pnl, 2)
        open_trade.net_pnl_usd = round(net_pnl, 2)
        open_trade.pnl_r_multiple = round(net_pnl / initial_risk_usd, 2)
        trades.append(open_trade)
        equity += net_pnl

    if daily_loss_cap_usd is not None and current_day_key is not None:
        daily_pnl_log.append({"date": current_day_key, "pnl_usd": round(daily_realized_pnl, 2),
                               "cap_tripped": day_loss_tripped})

    is_trades = [t for t in trades if not t.is_out_of_sample]
    oos_trades = [t for t in trades if t.is_out_of_sample]

    is_metrics = calculate_metrics_from_trades(is_trades, "IN_SAMPLE", initial_balance, num_noise_shuffles)
    oos_metrics = calculate_metrics_from_trades(oos_trades, "OUT_OF_SAMPLE", initial_balance, num_noise_shuffles)
    overall_metrics = calculate_metrics_from_trades(trades, "OVERALL", initial_balance, num_noise_shuffles)

    return BacktestResult(
        params=params, in_sample_metrics=is_metrics, out_of_sample_metrics=oos_metrics,
        overall_metrics=overall_metrics, trades=trades, equity_curve=equity_curve,
        split_index=split_idx, initial_balance=initial_balance, final_balance=round(equity, 2),
        symbol="USTECm", timeframe="RANGE", spread_points=spread_points,
        commission_per_lot_usd=commission_per_lot_usd, num_range_bars=n,
        daily_loss_cap_usd=daily_loss_cap_usd, daily_cap_trip_count=daily_cap_trip_count,
        daily_pnl_log=daily_pnl_log
    )
