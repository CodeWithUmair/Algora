"""
Background-thread live engine for the XAU/USD (XAUUSDm) Fib Pivot + EMA9 scalper.

One engine instance = ONE timeframe (M1 or M5) = one thread, with its OWN magic number,
SQLite trade DB, circuit breaker and cooldown state. Several instances can run side by side
on one MT5 account (see run_vps.py) and never see each other's positions.

Execution semantics match trading_bot/run_backtest_gold_fib.py (the validated backtest):
  * the strategy is evaluated ONCE per CLOSED bar (the still-forming bar is dropped),
  * a signal on closed bar i is sent as a market order right after the close (~ next bar open),
  * SL/TP are the absolute prices computed from the signal bar's close,
  * one open position per engine, fixed lot, per-level cooldown counted in bars.
Realised P&L of every closed position is read back from MT5 deal history and written to the
engine's DB and to its daily-loss circuit breaker.
"""

import sys
import threading
import time
import collections
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable

from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.gold_strategy import GoldStrategyParameters, eval_gold_signal, GoldSignalResult
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage
from trading_bot.news_filter import fetch_calendar, is_near_high_impact_news

try:
    import MetaTrader5 as mt5
except Exception:  # non-Windows / not installed -> simulation mode, no deal history
    mt5 = None

TF_SECONDS = {"M1": 60, "M5": 300}
# Bars fetched per timeframe: must cover the previous FULL UTC day (pivots) + EMA warm-up.
TF_FETCH_BARS = {"M1": 4000, "M5": 1500}
# Distinct magic number per timeframe so positions/history of each instance stay separate.
GOLD_MAGIC = {"M1": 9212001, "M5": 9212005}


class GoldLiveTradingEngine:
    """Runs the Gold Fib Pivot + EMA9 loop for one timeframe on a background daemon thread."""

    def __init__(
        self,
        symbol: str = "XAUUSDm",
        db_path: Optional[str] = None,
        timeframe: str = "M1",
        magic_number: Optional[int] = None,
        daily_loss_cap_usd: Optional[float] = None,
        use_news_filter: bool = True,
        bridge_factory: Callable[..., Any] = MT5Bridge,
        poll_seconds: float = 2.0,
        log_file: Optional[str] = None,
    ):
        timeframe = timeframe.upper()
        if timeframe not in TF_SECONDS:
            raise ValueError(f"Unsupported gold timeframe {timeframe!r} (use M1 or M5)")
        self.symbol = symbol
        self.timeframe = timeframe
        self.name = f"GOLD {timeframe}"
        self.db_path = db_path or f"gold_{timeframe.lower()}_trades.sqlite"
        self.use_news_filter = use_news_filter
        self.poll_seconds = poll_seconds
        self.log_file = log_file
        self._bridge_factory = bridge_factory

        self.params = GoldStrategyParameters(
            symbol=symbol, timeframe_str=timeframe,
            magic_number=magic_number or GOLD_MAGIC[timeframe],
        )
        if daily_loss_cap_usd is not None:
            self.params.daily_loss_cap_usd = daily_loss_cap_usd

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.running = False
        self.error: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.log_lines = collections.deque(maxlen=300)

        self.status: Dict[str, Any] = {
            "connected": False, "account_login": None, "balance": 0.0, "today_pnl": 0.0,
            "session_pnl": 0.0, "last_signal": None, "last_price": 0.0, "ema9": 0.0,
            "pp": 0.0, "r1": 0.0, "s1": 0.0, "open_positions": 0, "news_block": None,
            "last_update": None,
        }

        # per-level cooldown, stored as ABSOLUTE bar numbers (survives the sliding fetch window)
        self._last_level_abs: Dict[str, int] = {}
        self._last_bar_time: Optional[str] = None
        self._known_tickets: Dict[int, Dict[str, Any]] = {}
        self._calendar = []
        self._calendar_fetched = 0.0
        self.storage: Optional[BotStorage] = None
        self.bridge = None
        self.breaker: Optional[CircuitBreakerManager] = None

    # ------------------------------------------------------------------ helpers
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _log(self, msg: str):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for line in str(msg).splitlines() or [""]:
            full = f"[{ts}] [{self.name}] {line}"
            self.log_lines.append(full)
            try:
                print(full, flush=True)
            except UnicodeEncodeError:
                enc = getattr(sys.stdout, "encoding", None) or "ascii"
                print(full.encode(enc, errors="replace").decode(enc), flush=True)
            if self.log_file:
                try:
                    with open(self.log_file, "a", encoding="utf-8") as f:
                        f.write(full + chr(10))
                except Exception:
                    pass

    def start(self):
        with self._lock:
            if self.is_running():
                return
            self._stop_event.clear()
            self.running = True
            self.error = None
            self.started_at = datetime.now(timezone.utc)
            self._thread = threading.Thread(target=self._run, daemon=True, name=f"GoldLiveEngine-{self.timeframe}")
            self._thread.start()
            self._log(f"Engine started: {self.symbol} {self.timeframe} magic #{self.params.magic_number} db={self.db_path}")

    def stop(self):
        with self._lock:
            if not self.is_running():
                self.running = False
                return
            self._log("Stop requested, shutting down thread...")
            self._stop_event.set()

    # ------------------------------------------------------------------ setup
    def _connect(self) -> bool:
        self.storage = BotStorage(self.db_path)
        self.bridge = self._bridge_factory(magic_number=self.params.magic_number, symbol=self.symbol)
        self.breaker = CircuitBreakerManager(CircuitBreakerConfig(
            max_daily_loss_usd=self.params.daily_loss_cap_usd,
            max_daily_loss_pct=100.0,          # $ cap only (percent cap disabled, as in the backtest)
            max_consecutive_losses=10 ** 9,    # not part of the validated config
            enforce_demo_only=True,
            enforce_noise_gate=False,          # this engine is not gated by the legacy noise gate
            magic_number=self.params.magic_number,
            cooldown_after_loss_minutes=0,
        ))
        ok, msg = self.bridge.connect()
        if not ok:
            self.error = f"Failed to connect to MT5: {msg}"
            self._log(f"ERROR {self.error}")
            return False
        acc = self.bridge.get_account_info()
        self.status.update(connected=True, account_login=acc.login, balance=acc.balance)
        self._log(f"MT5 connected. Account {acc.login} ({acc.trade_mode}) | balance ${acc.balance:.2f}")
        self._log(f"Safety: daily loss cap ${self.params.daily_loss_cap_usd:.2f} (profit uncapped), fixed lot {self.params.fixed_lot_size}")
        # adopt positions that survived a restart so their close is still recorded
        for p in self.bridge.get_open_positions(symbol=self.symbol, magic_number=self.params.magic_number):
            self._known_tickets[p["ticket"]] = {"direction": p.get("direction"), "entry_price": p.get("entry_price")}
        if self._known_tickets:
            self._log(f"Adopted {len(self._known_tickets)} existing open position(s)")
        return True

    # ------------------------------------------------------------------ closed-trade accounting
    def _deal_result(self, ticket: int) -> Optional[Dict[str, Any]]:
        if mt5 is None:
            return None
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return None
        out = [d for d in deals if d.entry in (getattr(mt5, "DEAL_ENTRY_OUT", 1), getattr(mt5, "DEAL_ENTRY_OUT_BY", 3))]
        if not out:
            return None
        net = sum(float(d.profit) + float(d.commission) + float(d.swap) for d in deals)
        reason_map = {getattr(mt5, "DEAL_REASON_SL", 4): "SL", getattr(mt5, "DEAL_REASON_TP", 5): "TP"}
        return {"net": net, "exit_price": float(out[-1].price), "reason": reason_map.get(out[-1].reason, "CLOSED")}

    def _reconcile_closed(self, positions):
        current = {p["ticket"] for p in positions}
        for ticket in [t for t in self._known_tickets if t not in current]:
            info = self._known_tickets.pop(ticket)
            res = self._deal_result(ticket)
            if res is None:
                self._log(f"Position #{ticket} closed (P&L unavailable from MT5 history)")
                continue
            balance = self.status.get("balance", 0.0)
            self.breaker.record_trade_outcome(res["net"], balance)
            try:
                self.storage.update_closed_trade(ticket, res["exit_price"], res["net"], res["reason"])
            except Exception as e:
                self._log(f"WARN could not update DB for #{ticket}: {e}")
            self.status["today_pnl"] = self.breaker.state.daily_pnl_usd
            self._log(f"CLOSED #{ticket} {info.get('direction')} via {res['reason']} @ {res['exit_price']:.2f} | net ${res['net']:+.2f} | day P&L ${self.breaker.state.daily_pnl_usd:+.2f}")
            if self.breaker.state.is_daily_loss_tripped:
                self._log(f"DAILY LOSS CAP HIT: {self.breaker.state.trip_reason} - no new trades until 00:00 UTC")

    # ------------------------------------------------------------------ one evaluation pass
    def _eval_with_abs_cooldown(self, df, cur_abs: int) -> GoldSignalResult:
        curr_idx = len(df) - 1
        seed = {lvl: curr_idx - (cur_abs - a) for lvl, a in self._last_level_abs.items()}
        res, out = eval_gold_signal(df, self.params, seed)
        for lvl, v in out.items():
            if v == curr_idx:
                self._last_level_abs[lvl] = cur_abs
        return res

    def tick(self, now_utc: Optional[datetime] = None):
        """One loop pass. Safe to call repeatedly; only acts when a new CLOSED bar has appeared."""
        now_utc = now_utc or datetime.now(timezone.utc)
        tf_sec = TF_SECONDS[self.timeframe]

        acc = self.bridge.get_account_info()
        self.status["balance"] = acc.balance
        positions = self.bridge.get_open_positions(symbol=self.symbol, magic_number=self.params.magic_number)
        self.status["open_positions"] = len(positions)
        self._reconcile_closed(positions)

        df = self.bridge.fetch_recent_dataframe(count=TF_FETCH_BARS[self.timeframe] + 1,
                                                timeframe_str=self.timeframe, symbol=self.symbol)
        if df is None or df.empty:
            self._log("WARN no bars returned from MT5")
            return
        df = df.iloc[:-1].reset_index(drop=True)   # drop the still-forming bar
        if len(df) < 50:
            return
        bar_time = str(df["time"].iloc[-1])
        self.status["last_price"] = float(df["close"].iloc[-1])
        if bar_time == self._last_bar_time:
            return
        self._last_bar_time = bar_time

        bar_dt = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
        if bar_dt.tzinfo is None:
            bar_dt = bar_dt.replace(tzinfo=timezone.utc)
        cur_abs = int(bar_dt.timestamp() // tf_sec)
        sig = self._eval_with_abs_cooldown(df, cur_abs)

        if sig.pivots:
            self.status.update(pp=sig.pivots.pp, r1=sig.pivots.r1, s1=sig.pivots.s1)
        self.status["ema9"] = sig.ema9_val
        self.status["last_signal"] = sig.signal_type
        self.status["last_update"] = now_utc.strftime("%H:%M:%S")

        if sig.signal_type not in ("BUY", "SELL"):
            return

        close_age = (now_utc - bar_dt).total_seconds() - tf_sec   # seconds since that bar closed
        self._log(f"SIGNAL {sig.signal_type} @ {sig.close_price:.2f} via {sig.trigger_level} (bar {bar_time}, closed {close_age:.0f}s ago)")
        if close_age > 2 * tf_sec:
            self._log("Skipped: signal bar is stale (bot restarted / feed gap) - not chasing it")
            return
        if positions:
            self._log("Skipped: this engine already has an open position")
            return

        if self.use_news_filter:
            if time.time() - self._calendar_fetched > 1800:
                self._calendar = fetch_calendar()
                self._calendar_fetched = time.time()
            blocked, ev = is_near_high_impact_news(now_utc, self._calendar, buffer_minutes=15)
            self.status["news_block"] = str(ev) if blocked else None
            if blocked:
                self._log(f"Skipped: high-impact news window ({ev})")
                return

        can, why = self.breaker.can_open_trade(
            is_demo_account=acc.is_demo, algo_trading_enabled=self.bridge.is_algo_trading_enabled(),
            current_balance=acc.balance)
        if not can:
            self._log(f"Skipped by safety breaker: {why}")
            return

        self._log(f"EXECUTING {sig.signal_type} {self.params.fixed_lot_size} lot | SL {sig.suggested_sl:.2f} TP {sig.suggested_tp:.2f}")
        ok, ticket, msg = self.bridge.send_order(
            direction=sig.signal_type, volume=self.params.fixed_lot_size,
            stop_loss=sig.suggested_sl, take_profit=sig.suggested_tp,
            symbol=self.symbol, magic_number=self.params.magic_number,
            comment=f"GOLD_{self.timeframe}_{sig.trigger_level}",
        )
        if not ok:
            self._log(f"ORDER FAILED: {msg}")
            return
        self._log(f"ORDER PLACED #{ticket}: {msg}")
        entry = sig.suggested_entry
        for p in self.bridge.get_open_positions(symbol=self.symbol, magic_number=self.params.magic_number):
            if p["ticket"] == ticket:
                entry = p.get("entry_price", entry)
        self._known_tickets[ticket] = {"direction": sig.signal_type, "entry_price": entry}
        self.storage.save_trade({
            "ticket": ticket, "direction": sig.signal_type,
            "model": f"FIB_{sig.trigger_level}_{self.timeframe}", "symbol": self.symbol,
            "entry_price": entry, "stop_loss": sig.suggested_sl, "take_profit": sig.suggested_tp,
            "lot_size": self.params.fixed_lot_size, "magic_number": self.params.magic_number,
        })

    # ------------------------------------------------------------------ thread body
    def _run(self):
        try:
            if not self._connect():
                return
            consecutive_errors = 0
            while not self._stop_event.is_set():
                try:
                    self.tick()
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    self._log(f"Loop error ({consecutive_errors}): {e}")
                    if consecutive_errors >= 30:
                        self.error = f"Too many consecutive errors, last: {e}"
                        break
                    time.sleep(5)
                    continue
                self._stop_event.wait(self.poll_seconds)
        except Exception as e:
            self.error = str(e)
            self._log(f"FATAL {e}")
        finally:
            self.running = False
            self._log("Engine thread terminated.")


# Process-wide singleton (used by the Streamlit dashboard) - defaults to the M1 instance
_gold_engine_instance: Optional[GoldLiveTradingEngine] = None
_gold_engine_lock = threading.Lock()


def get_gold_engine(symbol: str = "XAUUSDm", db_path: str = "gold_m1_trades.sqlite",
                    timeframe: str = "M1") -> GoldLiveTradingEngine:
    global _gold_engine_instance
    with _gold_engine_lock:
        if _gold_engine_instance is None:
            _gold_engine_instance = GoldLiveTradingEngine(symbol=symbol, db_path=db_path, timeframe=timeframe)
        return _gold_engine_instance
