"""
Background-thread live trading engine for XAU/USD Gold (XAUUSDm) Fib Pivot + EMA9 scalper.
Runs on an independent daemon thread, process-wide singleton via get_gold_engine(),
allowing Streamlit to Start/Stop the Gold bot independently from the NASDAQ bot.
"""

import sys
import threading
import time
import collections
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.gold_strategy import (
    GoldStrategyParameters,
    eval_gold_signal,
    compute_fibonacci_pivots,
    GoldSignalResult
)
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage
from trading_bot.news_filter import fetch_calendar, is_near_high_impact_news


class GoldLiveTradingEngine:
    """Runs the Gold (XAUUSDm) Fib Pivot + EMA9 scalper loop on a background daemon thread."""

    def __init__(self, symbol: str = "XAUUSDm", db_path: str = "nasdaq_trades.sqlite"):
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
            "last_signal": None,
            "last_price": 0.0,
            "ema9": 0.0,
            "pp": 0.0,
            "r1": 0.0,
            "s1": 0.0,
            "open_positions": 0,
            "news_block": None,
            "last_update": None,
        }

        self.params = GoldStrategyParameters()
        self.last_level_trade_bars: Dict[str, int] = {}

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        for line in str(msg).split("\n"):
            self.log_lines.append(f"[{ts}] {line}")
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "ascii"
            print(msg.encode(encoding, errors="replace").decode(encoding), flush=True)

    def start(self):
        with self._lock:
            if self.is_running():
                return
            self._stop_event.clear()
            self.running = True
            self.error = None
            self.started_at = datetime.now(timezone.utc)
            self._thread = threading.Thread(target=self._run, daemon=True, name="GoldLiveEngine")
            self._thread.start()
            self._log(f"⚡ [GOLD BOT] Engine started for {self.symbol} (Magic #{self.params.magic_number})")

    def stop(self):
        with self._lock:
            if not self.is_running():
                self.running = False
                return
            self._log("🛑 [GOLD BOT] Stop requested, shutting down daemon thread...")
            self._stop_event.set()

    def _run(self):
        storage = BotStorage(self.db_path)
        mt5_bridge = MT5Bridge()

        # Independent Circuit Breaker for Gold ($10 daily loss cap)
        cb_config = CircuitBreakerConfig(
            max_daily_loss_usd=self.params.daily_loss_cap_usd,
            magic_number=self.params.magic_number,
            enforce_demo_only=True
        )
        breaker_mgr = CircuitBreakerManager(cb_config)

        ok, conn_msg = mt5_bridge.connect()
        if not ok:
            self.error = f"Failed to connect to MT5 bridge: {conn_msg}"
            self._log(f"❌ [GOLD BOT] {self.error}")
            self.running = False
            return

        acc_info = mt5_bridge.get_account_info()
        self.status["connected"] = True
        self.status["account_login"] = acc_info.login if acc_info else None
        self.status["balance"] = acc_info.balance if acc_info else 0.0

        self._log(f"✅ [GOLD BOT] MT5 Connected. Account: {self.status['account_login']} | Symbol: {self.symbol}")
        self._log(f"🛡️ [GOLD BOT] Safety: Daily Loss Cap = ${self.params.daily_loss_cap_usd:.2f} (Profit Uncapped)")

        # Fetch news calendar
        calendar_events = fetch_calendar()

        last_evaluated_bar_time = None

        try:
            while not self._stop_event.is_set():
                now_utc = datetime.now(timezone.utc)

                # 1. Update account balance & positions
                acc = mt5_bridge.get_account_info()
                if acc:
                    self.status["balance"] = acc.balance

                positions = mt5_bridge.get_open_positions(symbol=self.symbol, magic_number=self.params.magic_number)
                self.status["open_positions"] = len(positions)

                # 2. Check News Block
                is_news_blocked, news_reason = is_near_high_impact_news(now_utc, calendar_events, buffer_minutes=15)
                self.status["news_block"] = news_reason if is_news_blocked else None

                # 3. Fetch M5 bars for Gold
                df_bars = mt5_bridge.fetch_recent_dataframe(count=1000, timeframe_str=self.params.timeframe_str, symbol=self.symbol)
                if df_bars.empty:
                    self._log("⚠️ [GOLD BOT] No price bars returned from MT5 feed")
                    time.sleep(5)
                    continue

                curr_bar_time = str(df_bars['time'].iloc[-1])
                self.status["last_price"] = float(df_bars['close'].iloc[-1])

                # Evaluate strategy on new bar close
                sig_res, self.last_level_trade_bars = eval_gold_signal(df_bars, self.params, self.last_level_trade_bars)

                if sig_res.pivots:
                    self.status["pp"] = sig_res.pivots.pp
                    self.status["r1"] = sig_res.pivots.r1
                    self.status["s1"] = sig_res.pivots.s1
                self.status["ema9"] = sig_res.ema9_val
                self.status["last_signal"] = sig_res.signal_type
                self.status["last_update"] = now_utc.strftime("%H:%M:%S")

                # If new signal & no position open
                if curr_bar_time != last_evaluated_bar_time:
                    last_evaluated_bar_time = curr_bar_time

                    if sig_res.signal_type in ["BUY", "SELL"] and len(positions) == 0:
                        self._log(f"🎯 [GOLD BOT] SIGNAL FOUND: {sig_res.signal_type} @ {sig_res.close_price:.2f} ({sig_res.reason})")

                        # Verify Circuit Breakers
                        can_trade, refusal_reason = breaker_mgr.can_open_trade(
                            mt5_bridge=mt5_bridge,
                            is_demo=acc.is_demo if acc else True
                        )

                        if is_news_blocked:
                            self._log(f"📰 [GOLD BOT] Trade BLOCKED by News Filter: {news_reason}")
                        elif not can_trade:
                            self._log(f"🛡️ [GOLD BOT] Trade BLOCKED by Safety Breaker: {refusal_reason}")
                        else:
                            # Execute Order
                            self._log(f"🚀 [GOLD BOT] EXECUTING {sig_res.signal_type} {self.params.fixed_lot_size} lot...")
                            res = mt5_bridge.send_order(
                                symbol=self.symbol,
                                order_type=sig_res.signal_type,
                                volume=self.params.fixed_lot_size,
                                sl=sig_res.suggested_sl,
                                tp=sig_res.suggested_tp,
                                magic=self.params.magic_number,
                                comment=f"GOLD_{sig_res.trigger_level}"
                            )
                            if res and res.get("status") == "SUCCESS":
                                self._log(f"✅ [GOLD BOT] ORDER PLACED! Ticket #{res.get('ticket')}")
                                storage.save_trade({
                                    "ticket": res.get("ticket"),
                                    "direction": sig_res.signal_type,
                                    "model": f"FIB_{sig_res.trigger_level}",
                                    "symbol": self.symbol,
                                    "entry_price": sig_res.suggested_entry,
                                    "stop_loss": sig_res.suggested_sl,
                                    "take_profit": sig_res.suggested_tp,
                                    "lot_size": self.params.fixed_lot_size,
                                    "magic_number": self.params.magic_number
                                })
                            else:
                                self._log(f"❌ [GOLD BOT] ORDER EXECUTION FAILED: {res.get('error') if res else 'Unknown error'}")

                time.sleep(3)

        except Exception as e:
            self.error = str(e)
            self._log(f"❌ [GOLD BOT] Exception in engine loop: {e}")
        finally:
            self.running = False
            self._log("🛑 [GOLD BOT] Engine background thread terminated.")


# Process-wide singleton instance for Gold Live Engine
_gold_engine_instance: Optional[GoldLiveTradingEngine] = None
_gold_engine_lock = threading.Lock()


def get_gold_engine(symbol: str = "XAUUSDm", db_path: str = "nasdaq_trades.sqlite") -> GoldLiveTradingEngine:
    """Returns the process-wide singleton instance of GoldLiveTradingEngine."""
    global _gold_engine_instance
    with _gold_engine_lock:
        if _gold_engine_instance is None:
            _gold_engine_instance = GoldLiveTradingEngine(symbol=symbol, db_path=db_path)
        return _gold_engine_instance
