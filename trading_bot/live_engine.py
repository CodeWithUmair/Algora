"""
Background-thread live trading engine for the NASDAQ-100 (USTECm) order-flow
scalper. Same architecture as the sibling gold bot's live_engine.py: a
LiveTradingEngine runs the loop on a daemon thread, process-wide singleton via
get_engine(), so the Streamlit dashboard can Start/Stop it with a button
instead of needing a second terminal.

Each cycle: fetch recent M15 bars -> reconstruct range bars -> compute Volume
Profile/CVD/ATR -> evaluate the 3 playbooks on the latest CLOSED range bar ->
execute via MT5 if a signal fires and every risk shield clears.

Timeframe is M15, not M1 (changed 2026-09-14) - the tuned StrategyParameters
defaults (range_size_points=8.0 etc.) were searched and validated against
range bars built from M15 source candles (see optimize_parameters.py /
BACKTEST_REPORT.md's "Parameter optimization" section), not M1. Feeding this
config M1 candles instead would be running an untested combination.
"""

import sys
import threading
import time
import collections
from datetime import datetime, timezone
from typing import Optional

from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.strategy import (
    StrategyParameters,
    build_range_bars,
    calculate_volume_profile,
    calculate_cvd,
    calculate_atr,
    calculate_ema,
    evaluate_htf_trend,
    evaluate_signal_at_bar,
    is_in_session_window,
)
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage
from trading_bot.news_filter import fetch_calendar, is_near_high_impact_news


class LiveTradingEngine:
    """Runs the NASDAQ order-flow scalper loop on a background daemon thread."""

    def __init__(self, symbol: str = "USTECm", db_path: str = "nasdaq_trades.sqlite"):
        self.symbol = symbol
        self.db_path = db_path

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.running = False
        self.error: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.log_lines = collections.deque(maxlen=300)

        self.status = {
            "connected": False,
            "account_login": None,
            "balance": 0.0,
            "today_pnl": 0.0,
            "session_pnl": 0.0,
            "in_session": False,
            "htf_trend": "NEUTRAL",
            "last_model": None,
            "last_price": 0.0,
            "val": 0.0,
            "vah": 0.0,
            "poc": 0.0,
            "cvd": 0.0,
            "atr": 0.0,
            "open_positions": 0,
            "range_bars_built": 0,
            "news_block": None,
            "last_update": None,
        }

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        for line in str(msg).split("\n"):
            self.log_lines.append(f"[{ts}] {line}")
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            # Windows consoles often default to a narrow codepage (cp1252) that can't
            # encode this bot's emoji-heavy log lines - silently dropping the whole
            # line (the old behavior) meant most real status output vanished on a
            # plain Windows terminal. Re-encode with replacement chars instead so
            # something always prints, rather than nothing.
            encoding = getattr(sys.stdout, "encoding", None) or "ascii"
            print(msg.encode(encoding, errors="replace").decode(encoding), flush=True)

    def start(self):
        with self._lock:
            if self.is_running():
                return False, "Engine is already running."
            self._stop_event.clear()
            self.error = None
            self._thread = threading.Thread(target=self._run, daemon=True, name="NasdaqLiveEngine")
            self._thread.start()
            self.started_at = datetime.now(timezone.utc)
            return True, "Engine starting..."

    def stop(self):
        with self._lock:
            if not self.is_running():
                self.running = False
                return False, "Engine is not running."
            self._stop_event.set()
            return True, "Stop signal sent - engine will halt within a few seconds."

    def _sleep(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end and not self._stop_event.is_set():
            time.sleep(min(0.5, max(0.0, end - time.time())))

    def _run(self):
        self.running = True
        mt5_bridge = MT5Bridge(symbol=self.symbol)
        try:
            self._log("=" * 85)
            self._log("🚀 STARTING NASDAQ-100 ORDER-FLOW SCALPER ENGINE (USTECm)")
            self._log("🛡️ Playbooks: AAA Absorption Reversal | Squeeze Breakout | Failed Auction Fade")
            self._log("=" * 85)

            params = StrategyParameters()
            trade_symbol = self.symbol
            fixed_lot_size = 0.1  # Restored 2026-09-14 after a brief, well-intentioned but mistaken revert
                                   # to 0.05 by a concurrent session that (correctly, given what it could see)
                                   # found no persisted evidence in the repo that 0.1 had ever been backtested.
                                   # It had been - in an earlier chat, run_causal_backtest(fixed_lot_size=0.1)
                                   # really was executed and showed real numbers - but that comparison was
                                   # never saved to a script/file, so a session reading only the repo had no
                                   # way to know. Now it is: see run_backtest_risk_capped.py's LOT_SIZES list
                                   # and BACKTEST_REPORT.md's "Lot size comparison: 0.05 vs 0.1" section for
                                   # the actual persisted numbers (177 trades either way, same 42.9% win rate
                                   # /PF 3.01 - lot size doesn't change those ratios - but 0.1 nets $395.49
                                   # vs $197.74 at 0.05, with max drawdown 8.44% vs 4.87%, still safely under
                                   # the $10/day cap on every historical day). User's explicit, informed choice
                                   # after seeing both numbers side by side. Not risk-based sizing (the
                                   # original design here) - a fixed lot, because that's what was backtested.
            daily_profit_target_usd = 15.0
            enable_daily_profit_lock = False  # Deliberately off: every backtest in BACKTEST_REPORT.md let
                                               # winners run uncapped - only losses are capped (below). This
                                               # was True before 2026-09-14, silently capping daily upside in
                                               # a way no backtest ever accounted for - now consistent with
                                               # what was actually tested and approved for forward testing.

            cb_config = CircuitBreakerConfig(
                bypass_noise_gate_for_demo=True,
                max_consecutive_losses=3,
                max_daily_loss_usd=10.0,  # Matches the validated $10/day loss cap from BACKTEST_REPORT.md
                                           # (was 15.0 - untested value).
                cooldown_after_loss_minutes=5,
                magic_number=9312001,  # NASDAQ's own tag (was silently defaulting to gold's 9212001)
            )
            cb_manager = CircuitBreakerManager(config=cb_config)
            storage = BotStorage(self.db_path)

            ok, conn_msg = mt5_bridge.connect()
            if not ok:
                self._log(f"❌ Could not connect to MetaTrader 5 terminal: {conn_msg}")
                self.error = conn_msg
                return

            acc = mt5_bridge.get_account_info()
            algo_allowed = mt5_bridge.is_algo_trading_enabled()
            self.status["connected"] = True
            self.status["account_login"] = acc.login
            self.status["balance"] = acc.balance

            self._log(f"✅ Connected to MT5 Account: {acc.login} | Mode: {acc.trade_mode} | Balance: ${acc.balance:,.2f}")
            self._log(f"⚡ Symbol: {trade_symbol} | Fixed Lot: {fixed_lot_size} | Algo Allowed: {algo_allowed}")
            self._log(f"🛡️ Daily Loss Cap: ${cb_config.max_daily_loss_usd:.2f} (profit uncapped - lock active: {enable_daily_profit_lock})")

            # News filter (2026-09-14): avoid opening trades within a buffer window of
            # High-impact USD releases (NFP, CPI, FOMC, etc.) - see news_filter.py's
            # module docstring for the honest limitation: this could NOT be backtested
            # (the feed only ever exposes the current week), so it's a live-only,
            # fail-open safety layer on top of the backtested daily loss cap / per-trade
            # SL, not a backtested part of the edge itself.
            enable_news_filter = True
            news_buffer_minutes = 15.0
            news_impact_levels = ("High",)
            news_currencies = ("USD",)
            news_refresh_seconds = 1800.0
            news_events = []
            last_news_fetch_time = 0.0

            last_processed_range_bar_time = None
            last_loss_time = 0
            processed_deal_tickets = set()
            session_realized_pnl = 0.0
            session_consecutive_losses = 0

            while not self._stop_event.is_set():
                self._sleep(3)
                if self._stop_event.is_set():
                    break

                open_positions = mt5_bridge.get_open_positions()
                self.status["open_positions"] = len(open_positions)

                if enable_news_filter and (time.time() - last_news_fetch_time) >= news_refresh_seconds:
                    news_events = fetch_calendar()
                    last_news_fetch_time = time.time()
                    self._log(f"📰 News calendar refreshed: {len(news_events)} events loaded.")

                today_realized_pnl = 0.0
                today_midnight_utc = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                closed_deals = mt5_bridge.get_closed_deals(from_timestamp=today_midnight_utc)
                for deal in closed_deals:
                    # The account is shared with the gold bots: only count THIS symbol's deals, otherwise a
                    # gold win/loss would move NASDAQ's daily P&L and its $10 loss breaker.
                    if deal.get("symbol") != self.symbol:
                        continue
                    ticket = deal["ticket"]
                    pnl = deal["profit"]
                    today_realized_pnl += pnl
                    if ticket not in processed_deal_tickets:
                        processed_deal_tickets.add(ticket)
                        storage.update_closed_trade(ticket, deal["close_price"], pnl, exit_reason="MT5 Deal Closed")
                        cb_manager.record_trade_outcome(net_pnl_usd=pnl, current_balance=acc.balance)
                        session_realized_pnl += pnl
                        if pnl < 0:
                            session_consecutive_losses += 1
                            last_loss_time = time.time()
                            self._log(f"⚠️ [TRADE CLOSED - LOSS] Deal #{ticket} closed at -${abs(pnl):.2f}. Today PnL: ${today_realized_pnl:+.2f}")
                        else:
                            session_consecutive_losses = 0
                            self._log(f"🎉 [TRADE CLOSED - WIN] Deal #{ticket} closed at +${pnl:.2f}! Today PnL: ${today_realized_pnl:+.2f}")

                self.status["today_pnl"] = today_realized_pnl
                self.status["session_pnl"] = session_realized_pnl
                self.status["balance"] = acc.balance

                # Fetch M15 bars and reconstruct range bars (validated timeframe - see module docstring)
                bars_dict = mt5_bridge.fetch_recent_bars(count=1000, timeframe_str="M15")
                if not bars_dict or len(bars_dict["closes"]) < 100:
                    continue

                range_bars = build_range_bars(
                    bars_dict["opens"], bars_dict["highs"], bars_dict["lows"], bars_dict["closes"],
                    bars_dict["times"], bars_dict["volumes"], params.range_size_points
                )
                self.status["range_bars_built"] = len(range_bars)
                if len(range_bars) < 60:
                    continue

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

                # HTF trend using coarser range bars from the same M15 window
                htf_trend = "NEUTRAL"
                try:
                    htf_bars = build_range_bars(
                        bars_dict["opens"], bars_dict["highs"], bars_dict["lows"], bars_dict["closes"],
                        bars_dict["times"], bars_dict["volumes"], params.htf_range_size_points
                    )
                    if len(htf_bars) >= params.htf_ema_period:
                        htf_trend, _, htf_reason = evaluate_htf_trend([b.close for b in htf_bars], params.htf_ema_period)
                    else:
                        htf_reason = "Insufficient HTF range bars"
                except Exception as e:
                    htf_reason = f"HTF error: {e}"

                latest_idx = len(range_bars) - 1
                latest_bar = range_bars[latest_idx]

                in_session = True
                if params.enable_session_filter:
                    try:
                        dt = datetime.fromisoformat(latest_bar.time.replace("Z", "+00:00"))
                        in_session = is_in_session_window(dt, params)
                    except Exception:
                        in_session = True

                self.status.update({
                    "htf_trend": htf_trend,
                    "last_price": latest_bar.close,
                    "val": val_s[latest_idx], "vah": vah_s[latest_idx], "poc": poc_s[latest_idx],
                    "cvd": cvd_s[latest_idx],
                    "atr": atr_s[latest_idx] if latest_idx < len(atr_s) else 0.0,
                    "in_session": in_session,
                    "last_update": datetime.now(timezone.utc),
                })

                # Only evaluate once per NEW closed range bar (avoid re-firing mid-bar)
                if latest_bar.time == last_processed_range_bar_time:
                    continue
                last_processed_range_bar_time = latest_bar.time

                sig = evaluate_signal_at_bar(range_bars, val_s, vah_s, poc_s, cvd_s, atr_s, latest_idx, params)
                self.status["last_model"] = sig.model

                if sig.all_passed:
                    self._log(
                        f"🔎 [RANGE BAR CLOSE] Price: ${latest_bar.close:.2f} | Model: {sig.model} | "
                        f"Dir: {sig.direction} | VAL/VAH: ${val_s[latest_idx]:.1f}/${vah_s[latest_idx]:.1f} | "
                        f"POC: ${poc_s[latest_idx]:.1f} | CVD: {cvd_s[latest_idx]:+.0f} | HTF: {htf_trend}\n"
                        f"   {sig.reason}"
                    )

                # ---- Risk shields ----
                if enable_daily_profit_lock and max(session_realized_pnl, today_realized_pnl) >= daily_profit_target_usd:
                    continue
                if len(open_positions) >= 1:
                    continue
                if session_consecutive_losses >= 2 and (time.time() - last_loss_time) < 2700:
                    continue
                if (time.time() - last_loss_time) < (cb_config.cooldown_after_loss_minutes * 60):
                    continue
                if not in_session:
                    continue
                if enable_news_filter:
                    near_news, news_event = is_near_high_impact_news(
                        datetime.now(timezone.utc), news_events,
                        buffer_minutes=news_buffer_minutes, impact_levels=news_impact_levels,
                        currencies=news_currencies,
                    )
                    self.status["news_block"] = news_event["title"] if news_event else None
                    if near_news:
                        self._log(f"📰 Trade blocked - within {news_buffer_minutes:.0f}min of high-impact news: {news_event['title']}")
                        continue
                if params.enable_htf_filter:
                    if sig.direction == "BUY" and htf_trend == "BEARISH":
                        continue
                    if sig.direction == "SELL" and htf_trend == "BULLISH":
                        continue
                can_trade, reason = cb_manager.can_open_trade(
                    is_demo_account=acc.is_demo, algo_trading_enabled=mt5_bridge.is_algo_trading_enabled(),
                    current_balance=acc.balance
                )
                if not can_trade:
                    continue
                if not sig.all_passed:
                    continue

                sym_info = mt5_bridge.get_symbol_info()
                sl_clamped = min(max(sig.risk_points, params.min_sl_distance_points), params.max_sl_distance_points)
                sl_price = sig.close_price - sl_clamped if sig.direction == "BUY" else sig.close_price + sl_clamped

                lot = max(sym_info.volume_min, min(sym_info.volume_max, fixed_lot_size))
                if lot != fixed_lot_size:
                    self._log(f"⚠️ Fixed lot {fixed_lot_size} outside broker's [{sym_info.volume_min}, "
                               f"{sym_info.volume_max}] range for {trade_symbol} - clamped to {lot}.")

                self._log(f"\n🎯 >>> {sig.model} SIGNAL CONFIRMED: EXECUTING {sig.direction} ORDER (lot {lot}) <<<")
                res = mt5_bridge.send_order(
                    direction=sig.direction, volume=lot, sl_price=sl_price, tp_price=sig.suggested_tp,
                    magic_number=cb_config.magic_number, comment=f"NQ_{sig.model}"
                )
                order_ok, ticket, msg = res if (isinstance(res, tuple) and len(res) == 3) else (False, 0, str(res))
                if order_ok:
                    self._log(f"✅ {msg}")
                    storage.record_trade({
                        "order_id": ticket, "direction": sig.direction, "model": sig.model,
                        "volume": lot, "entry_price": sig.close_price, "sl": sl_price, "tp": sig.suggested_tp,
                        "status": "OPEN", "opened_at": datetime.now(timezone.utc).isoformat(),
                        "symbol": self.symbol, "magic_number": cb_config.magic_number,
                    })
                    self._sleep(30)
                else:
                    self._log(f"❌ Order Failed: {msg}")

        except Exception as e:
            self.error = str(e)
            self._log(f"❌ Engine crashed: {e}")
        finally:
            self.running = False
            self.status["connected"] = False
            self._log("🛑 NASDAQ engine stopped.")
            # NOTE: deliberately NOT calling mt5_bridge.disconnect(): mt5.shutdown() is process-wide and would
            # cut the MT5 connection out from under the gold engines running in the same process.


_singleton: Optional[LiveTradingEngine] = None
_singleton_lock = threading.Lock()


def get_engine(symbol: str = "USTECm", db_path: str = "nasdaq_trades.sqlite") -> LiveTradingEngine:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = LiveTradingEngine(symbol=symbol, db_path=db_path)
        return _singleton
