"""
Streamlit Dashboard for the NASDAQ-100 (USTECm) Order-Flow Scalper.
Run locally with: streamlit run trading_bot/streamlit_app.py

Same design system, fragment-based real-time pattern, and Start/Stop-toggle
architecture as the sibling gold bot's dashboard (proven working) - content
adapted for the 3-playbook order-flow strategy instead of a single checklist.
"""

import os
import sys
from datetime import datetime, timezone
import json

import pandas as pd

DB_PATH = "nasdaq_trades.sqlite"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    import streamlit as st
except ImportError:
    st = None

from trading_bot.strategy import (
    StrategyParameters,
    build_range_bars,
    calculate_volume_profile,
    calculate_cvd,
    calculate_atr,
    evaluate_signal_at_bar,
)
from trading_bot.backtest import run_causal_backtest
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage
from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.live_engine import get_engine


# ============================================================================
# DESIGN SYSTEM - same dark/gold token set as the sibling gold bot
# ============================================================================
_CSS = """
<style>
:root{
  --bg:#0B0E14; --bg-card:#121722; --bg-card-2:#161c29;
  --border: rgba(255,255,255,.08);
  --text:#E8ECF3; --text-muted:#8C94A6;
  --accent:#5B8DEF; --accent-soft: rgba(91,141,239,.14);
  --success:#34C77B; --success-soft: rgba(52,199,123,.13);
  --danger:#F0576B; --danger-soft: rgba(240,87,107,.13);
  --radius: 12px;
}
.block-container{ padding-top:2rem !important; padding-bottom:3rem !important; max-width:1180px; }
footer{ visibility:hidden; height:0; }
[data-testid="stDecoration"]{ background:linear-gradient(90deg,var(--accent),transparent); }
h1,h2,h3,h4{ letter-spacing:-0.01em; font-weight:650 !important; }
.app-title{ font-size:1.45rem; font-weight:700; line-height:1.2; color:var(--text); }
.app-sub{ display:block; font-size:.8rem; color:var(--text-muted); font-weight:500; margin-top:.15rem; }
.pill-wrap{ text-align:center; padding-top:.55rem; }
.pill{ display:inline-flex; align-items:center; gap:.4rem; padding:.32rem .8rem; border-radius:999px; font-size:.8rem; font-weight:650; }
.pill-run{ background:var(--success-soft); color:var(--success); }
.pill-stop{ background:rgba(140,148,166,.14); color:var(--text-muted); }
.pill-err{ background:var(--danger-soft); color:var(--danger); }
.setup-head{ font-size:1.02rem; font-weight:700; margin-bottom:.5rem; }
.chip{ display:inline-block; padding:.3rem .65rem; margin:0 .3rem .35rem 0; border-radius:999px; font-size:.76rem; font-weight:650; }
.chip-pass{ background:var(--success-soft); color:var(--success); }
.chip-fail{ background:rgba(140,148,166,.10); color:var(--text-muted); }
.chip-model{ background:var(--accent-soft); color:var(--accent); }
[data-testid="stMetric"]{
  background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius);
  padding:.8rem 1rem .65rem; height:100px; overflow:hidden;
  display:flex; flex-direction:column; justify-content:center;
}
[data-testid="stMetricLabel"]{ color:var(--text-muted) !important; font-size:.7rem; text-transform:uppercase; letter-spacing:.06em; }
[data-testid="stMetricValue"]{ font-size:1.3rem; font-weight:700; }
.stButton>button{ border-radius:9px; font-weight:650; border:1px solid var(--border); }
.stButton>button[kind="primary"]{ background:var(--accent); border-color:var(--accent); color:#0a1020; }
.stTabs [data-baseweb="tab-list"]{ gap:2px; border-bottom:1px solid var(--border); }
.stTabs [data-baseweb="tab"]{ height:38px; padding:0 14px; color:var(--text-muted); font-weight:600; font-size:.85rem; }
.stTabs [aria-selected="true"]{ color:var(--text) !important; }
[data-testid="stSidebar"]{ border-right:1px solid var(--border); }
[data-testid="stExpander"]{ border:1px solid var(--border); border-radius:var(--radius); background:var(--bg-card); }
[data-testid="stDataFrame"]{ border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; }
[data-testid="stAlert"]{ border-radius:var(--radius); border:1px solid var(--border); }
[data-testid="stCaptionContainer"]{ color:var(--text-muted) !important; }
[data-testid="stTextArea"] textarea{
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
  font-size:.78rem !important; line-height:1.5;
  background:#0d1017 !important; color:#c9d1e0 !important;
  border-radius:var(--radius) !important; border-color:var(--border) !important;
}
</style>
"""


def _inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


def _status_pill(engine) -> str:
    if engine.is_running():
        cls, text = "pill-run", "● Running"
    elif engine.error:
        cls, text = "pill-err", "● Crashed"
    else:
        cls, text = "pill-stop", "● Stopped"
    return f'<div class="pill-wrap"><span class="pill {cls}">{text}</span></div>'


def _chip(label: str, cls: str = "chip-pass") -> str:
    return f'<span class="chip {cls}">{label}</span>'


@st.fragment(run_every=5) if st is not None else (lambda f: f)
def _render_header(mt5_bridge, engine, cb_manager):
    hc1, hc2, hc3 = st.columns([4, 1.5, 1.5])
    with hc1:
        st.markdown(
            '<div class="app-title">📈 NASDAQ-100 Order-Flow Scalper</div>'
            '<span class="app-sub">Range Bars · Volume Profile · CVD · AAA / Squeeze / Failed-Auction</span>',
            unsafe_allow_html=True
        )
    with hc2:
        st.markdown(_status_pill(engine), unsafe_allow_html=True)
    with hc3:
        if engine.is_running():
            if st.button("⏹ Stop Bot", key="btn_stop_engine_top", use_container_width=True):
                ok_stop, msg_stop = engine.stop()
                st.toast(msg_stop)
                st.rerun()
        else:
            if st.button("▶ Start Bot", key="btn_start_engine_top", use_container_width=True, type="primary"):
                ok_start, msg_start = engine.start()
                st.toast(msg_start)
                st.rerun()

    if engine.error:
        st.error(f"Engine error: {engine.error}")

    acc = mt5_bridge.get_account_info()
    sym_info = mt5_bridge.get_symbol_info()
    today_pnl = engine.status.get("today_pnl") if engine.status.get("last_update") else cb_manager.state.daily_pnl_usd

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Account", acc.trade_mode, "Demo" if acc.is_demo else "LIVE — blocked")
    m2.metric("Balance", f"${acc.balance:,.2f}")
    m3.metric(mt5_bridge.symbol, f"${sym_info.bid:,.2f}", f"spread ${sym_info.spread_usd:.2f}")
    m4.metric("Today P&L", f"${today_pnl:+,.2f}")
    m5.metric("Positions", engine.status.get("open_positions", 0))
    m6.metric("Last Model", engine.status.get("last_model") or "—")

    return acc, sym_info


@st.fragment(run_every=6) if st is not None else (lambda f: f)
def _render_trade_history(storage, magic_num):
    st.caption(
        f"Store: `{storage.db_path}`  ·  Magic #{magic_num}  ·  "
        "a row appears the moment the engine opens a position; exit price & P&L fill in on close."
    )
    raw_trades = storage.get_all_trades(1000)
    if not raw_trades:
        st.info("No trades recorded yet. Start the Auto-Bot, or use Manual Override, to see history here.")
        return

    df = pd.DataFrame(raw_trades)
    for col in ["net_pnl_usd", "pnl_r_multiple", "entry_price", "stop_loss", "take_profit", "exit_price", "lot_size"]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["net_pnl_usd"] = df["net_pnl_usd"].fillna(0.0)

    def _is_closed(x):
        return x is not None and not pd.isna(x) and float(x) != 0.0
    df["status"] = df["exit_price"].apply(lambda x: "CLOSED" if _is_closed(x) else "OPEN")
    df = df.sort_values("id")
    closed_mask = df["status"] == "CLOSED"
    df["cumulative_pnl_usd"] = 0.0
    df.loc[closed_mask, "cumulative_pnl_usd"] = df.loc[closed_mask, "net_pnl_usd"].cumsum()
    df["cumulative_pnl_usd"] = df["cumulative_pnl_usd"].ffill().fillna(0.0)

    closed = df[closed_mask]
    wins = closed[closed["net_pnl_usd"] > 0]
    losses = closed[closed["net_pnl_usd"] < 0]
    total_pnl = float(closed["net_pnl_usd"].sum())
    win_rate = (len(wins) / len(closed) * 100.0) if len(closed) else 0.0
    gross_win = float(wins["net_pnl_usd"].sum())
    gross_loss = abs(float(losses["net_pnl_usd"].sum()))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 0.0

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Trades", len(df), f"{int((df['status'] == 'OPEN').sum())} open")
    k2.metric("Win Rate", f"{win_rate:.1f}%", f"{len(wins)}W / {len(losses)}L")
    k3.metric("Net P&L", f"${total_pnl:+,.2f}")
    k4.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor else "—")
    k5.metric("Closed", len(closed))

    if len(closed) >= 1:
        st.markdown("**Cumulative Realized P&L ($)**")
        st.line_chart(closed.set_index("id")["cumulative_pnl_usd"], height=200)

    with st.expander("🎯 Performance by Playbook", expanded=True):
        df["model"] = df["model"].fillna("UNTAGGED")
        rows = []
        for model in sorted(set(df["model"])):
            grp = df[df["model"] == model]
            g_closed = grp[grp["status"] == "CLOSED"]
            g_wins = g_closed[g_closed["net_pnl_usd"] > 0]
            rows.append({
                "Model": model, "Trades": len(grp), "Closed": len(g_closed),
                "Win %": round(len(g_wins) / len(g_closed) * 100, 1) if len(g_closed) else 0.0,
                "Net P&L ($)": round(float(g_closed["net_pnl_usd"].sum()), 2),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    view = df.sort_values("id", ascending=False)

    def _clean_ts(series):
        return series.fillna("").astype(str).str.replace("T", " ", regex=False).str.slice(0, 19)

    disp = pd.DataFrame({
        "Ticket": view["ticket"], "Dir": view["direction"], "Model": view["model"],
        "Status": view["status"], "Entry Time (UTC)": _clean_ts(view["entry_time"]),
        "Entry": view["entry_price"].round(2), "SL": view["stop_loss"].round(2), "TP": view["take_profit"].round(2),
        "Lot": view["lot_size"], "Exit": view["exit_price"].round(2),
        "Net P&L ($)": view["net_pnl_usd"].round(2), "R": view["pnl_r_multiple"].round(2),
        "Reason": view["exit_reason"].fillna(""),
    })

    def _color_pnl(v):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return ""
        if fv > 0:
            return "color: #34C77B; font-weight: 600"
        if fv < 0:
            return "color: #F0576B; font-weight: 600"
        return "color: #8C94A6"

    styled = disp.style.map(_color_pnl, subset=["Net P&L ($)", "R"])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=400)

    st.download_button(
        "⬇️ Download CSV", data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"nasdaq_trades_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv", key="btn_download_trades",
    )


@st.fragment(run_every=5) if st is not None else (lambda f: f)
def _render_engine_live(engine):
    if engine.status.get("last_update"):
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("Price", f"${engine.status.get('last_price', 0.0):,.2f}")
        d2.metric("VAL / VAH", f"${engine.status.get('val', 0.0):,.0f} / ${engine.status.get('vah', 0.0):,.0f}")
        d3.metric("POC", f"${engine.status.get('poc', 0.0):,.0f}")
        d4.metric("CVD", f"{engine.status.get('cvd', 0.0):+,.0f}")
        d5.metric("M15 Trend", engine.status.get("htf_trend", "—"))
        st.write(
            f"Range bars built: **{engine.status.get('range_bars_built', 0)}**  ·  "
            f"In session window: **{'Yes' if engine.status.get('in_session') else 'No'}**  ·  "
            f"Last model checked: **{engine.status.get('last_model') or '—'}**"
        )
    else:
        st.info("Diagnostics appear here a few seconds after you press Start.")

    st.markdown("**Live log**")
    log_text = "\n".join(engine.log_lines) if engine.log_lines else "(no log lines yet — press Start)"
    st.text_area("Engine log", value=log_text, height=380, disabled=True,
                 key="engine_log_area", label_visibility="collapsed")


def main():
    if st is None:
        print("Streamlit is not installed. Install via: pip install streamlit")
        return

    st.set_page_config(page_title="NASDAQ Order-Flow Scalper", page_icon="📈", layout="wide", initial_sidebar_state="expanded")
    _inject_css()

    if "storage" not in st.session_state:
        st.session_state.storage = BotStorage(DB_PATH)
    if "cb_manager" not in st.session_state:
        st.session_state.cb_manager = CircuitBreakerManager(config=CircuitBreakerConfig(bypass_noise_gate_for_demo=True))
    if "mt5_bridge" not in st.session_state or not hasattr(st.session_state.mt5_bridge, "get_rates"):
        st.session_state.mt5_bridge = MT5Bridge(symbol="USTECm")
        st.session_state.mt5_bridge.connect()

    storage = st.session_state.storage
    cb_manager = st.session_state.cb_manager
    cb_manager.config.bypass_noise_gate_for_demo = True
    mt5_bridge = st.session_state.mt5_bridge

    engine = get_engine(symbol="USTECm", db_path=DB_PATH)
    acc, sym_info = _render_header(mt5_bridge, engine, cb_manager)

    saved_strategy = storage.get_setting("strategy_config", {}) or {}
    saved_safety = storage.get_setting("safety_config", {}) or {}

    st.sidebar.markdown("### ⚙️ Controls")
    st.sidebar.caption("Saved automatically — persists across restarts.")
    st.sidebar.info("📊 **Timeframe: M15** — every tuned parameter below was searched and validated "
                     "against M15 range bars (see `BACKTEST_REPORT.md`). The live engine and this "
                     "dashboard both fetch M15 candles from MT5, not M1.")
    with st.sidebar.expander("Strategy Parameters", expanded=False):
        range_size = st.number_input("Range Bar Size (points)", 5.0, 100.0, float(saved_strategy.get("range_size_points", 8.0)), 1.0)
        bin_size = st.number_input("Volume Profile Bin (points)", 1.0, 50.0, float(saved_strategy.get("profile_bin_size_points", 5.0)), 1.0)
        value_area = st.slider("Value Area %", 0.5, 0.9, float(saved_strategy.get("value_area_pct", 0.68)), 0.01)
        vol_spike_mult = st.slider("Volume Spike Multiplier", 1.2, 4.0, float(saved_strategy.get("volume_spike_mult", 2.5)), 0.1)
        rr_ratio = st.number_input("Squeeze Risk:Reward", 1.0, 5.0, float(saved_strategy.get("rr_ratio", 3.0)), 0.5)
        min_rr = st.number_input("Min R:R (AAA / Failed Auction)", 1.0, 5.0, float(saved_strategy.get("min_rr_ratio", 1.5)), 0.5)
        enable_htf = st.checkbox("Enable M15 HTF Trend Filter", value=bool(saved_strategy.get("enable_htf_filter", True)))
        enable_session = st.checkbox("Restrict to Session Window (NY Open)", value=bool(saved_strategy.get("enable_session_filter", True)))

    params = StrategyParameters(
        range_size_points=range_size, profile_bin_size_points=bin_size, value_area_pct=value_area,
        volume_spike_mult=vol_spike_mult, rr_ratio=rr_ratio, min_rr_ratio=min_rr,
        enable_htf_filter=enable_htf, enable_session_filter=enable_session,
    )
    current_strategy_cfg = {
        "range_size_points": range_size, "profile_bin_size_points": bin_size, "value_area_pct": value_area,
        "volume_spike_mult": vol_spike_mult, "rr_ratio": rr_ratio, "min_rr_ratio": min_rr,
        "enable_htf_filter": enable_htf, "enable_session_filter": enable_session,
    }
    if current_strategy_cfg != saved_strategy:
        storage.set_setting("strategy_config", current_strategy_cfg)

    with st.sidebar.expander("Safety & Circuit Breakers", expanded=False):
        max_daily_loss = st.number_input("Max Daily Loss ($)", 5.0, 1000.0, float(saved_safety.get("max_daily_loss_usd", 10.0)))
        max_consec_losses = st.number_input("Max Consec Losses", 1, 10, int(saved_safety.get("max_consecutive_losses", 3)))
        magic_num = st.number_input("Magic Number", 100000, 9999999, int(saved_safety.get("magic_number", 9312001)))
    cb_manager.config.max_daily_loss_usd = max_daily_loss
    cb_manager.config.max_consecutive_losses = max_consec_losses
    cb_manager.config.magic_number = magic_num
    current_safety_cfg = {"max_daily_loss_usd": max_daily_loss, "max_consecutive_losses": max_consec_losses, "magic_number": magic_num}
    if current_safety_cfg != saved_safety:
        storage.set_setting("safety_config", current_safety_cfg)

    st.sidebar.button("🔄 Refresh Market Data", key="btn_refresh_market_data", use_container_width=True, on_click=st.rerun)

    raw_bars = mt5_bridge.get_rates(count=1000, timeframe_str="M15")  # matches live_engine.py's validated timeframe
    opens = [b.open for b in raw_bars]
    highs = [b.high for b in raw_bars]
    lows = [b.low for b in raw_bars]
    closes = [b.close for b in raw_bars]
    times = [b.time for b in raw_bars]
    volumes = [b.tick_volume for b in raw_bars]

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📋 Live Signal", "📊 Backtest", "📈 Volume Profile", "📜 History", "🤖 Engine"])

    range_bars = build_range_bars(opens, highs, lows, closes, times, volumes, params.range_size_points)
    signal = None
    if len(range_bars) >= 60:
        rb_highs = [b.high for b in range_bars]; rb_lows = [b.low for b in range_bars]
        rb_closes = [b.close for b in range_bars]; rb_opens = [b.open for b in range_bars]
        rb_volumes = [b.volume for b in range_bars]; rb_times = [b.time for b in range_bars]
        val_s, vah_s, poc_s = calculate_volume_profile(rb_highs, rb_lows, rb_volumes, rb_times, params.profile_bin_size_points, params.value_area_pct)
        cvd_s = calculate_cvd(rb_opens, rb_closes, rb_volumes)
        atr_s = calculate_atr(rb_highs, rb_lows, rb_closes, params.atr_period)
        idx = len(range_bars) - 1
        signal = evaluate_signal_at_bar(range_bars, val_s, vah_s, poc_s, cvd_s, atr_s, idx, params)

    with tab1:
        if signal is None:
            st.info("Not enough range bars formed yet from the live feed - waiting for more data.")
        else:
            st.markdown(f'<div class="setup-head">Latest Range Bar · ${signal.close_price:,.2f}</div>', unsafe_allow_html=True)
            chips = "".join([
                _chip(f"VAL ${signal.val:,.0f}", "chip-model"),
                _chip(f"VAH ${signal.vah:,.0f}", "chip-model"),
                _chip(f"POC ${signal.poc:,.0f}", "chip-model"),
                _chip(f"CVD {signal.cvd:+,.0f}", "chip-model"),
            ])
            st.markdown(chips, unsafe_allow_html=True)

            if signal.all_passed:
                cls = "chip-pass" if signal.direction == "BUY" else "chip-fail"
                st.success(
                    f"🎯 **{signal.model} · {signal.direction}** — {signal.reason}\n\n"
                    f"Entry ${signal.suggested_entry:,.2f} · SL ${signal.suggested_sl:,.2f} · TP ${signal.suggested_tp:,.2f} "
                    f"(R:R {signal.reward_points/signal.risk_points:.2f})" if signal.risk_points else ""
                )
            else:
                st.caption("No playbook triggered on the latest closed range bar.")

            with st.expander("⚡ Manual Order Override", expanded=False):
                st.caption("Places a one-off order at the current signal's SL/TP, independent of the Auto-Bot toggle above.")
                if signal.all_passed:
                    if st.button(f"🚀 {signal.direction} Now ({signal.model})", key="btn_manual_order", type="primary"):
                        ok, ticket, msg = mt5_bridge.send_order(
                            direction=signal.direction, volume=sym_info.volume_min,
                            sl_price=signal.suggested_sl, tp_price=signal.suggested_tp,
                            magic_number=magic_num, comment=f"Manual_{signal.model}"
                        )
                        if ok:
                            st.success(msg)
                            storage.record_trade({
                                "order_id": ticket, "direction": signal.direction, "model": signal.model,
                                "volume": sym_info.volume_min, "entry_price": signal.close_price,
                                "sl": signal.suggested_sl, "tp": signal.suggested_tp, "status": "OPEN",
                                "opened_at": datetime.now(timezone.utc).isoformat(),
                            })
                        else:
                            st.error(msg)
                else:
                    st.caption("No active signal to trade right now.")

    with tab2:
        st.caption("Zero-lookahead backtest on range bars reconstructed from **real MT5 M15 history** "
                   "(matches the live engine's timeframe — not synthetic data, not M1). "
                   "75/25 In-Sample/Out-of-Sample split + Monte Carlo noise gate.")
        bt_bars = st.slider("M15 Bars to Test", 2000, 23000, 8000, 1000, key="slider_bt_bars",
                             help="This broker's real M15 history for USTECm goes back ~11.7 months "
                                  "(~23,000 M15 candles) - see BACKTEST_REPORT.md.")
        bt_lot = st.number_input("Fixed Lot Size", 0.01, 5.0, 0.1, 0.01, key="bt_lot_size",
                                  help="Matches the live engine's validated fixed-lot sizing, not risk-based.")
        bt_daily_cap = st.number_input("Daily Loss Cap ($)", 1.0, 1000.0, 10.0, 1.0, key="bt_daily_cap")
        if st.button("▶️ Run Backtest & Gate Check", key="btn_run_full_backtest", type="primary"):
            with st.spinner("Fetching real M15 history from MT5 and running causal simulation..."):
                bt_data = mt5_bridge.fetch_recent_bars(count=bt_bars, timeframe_str="M15")
                res = run_causal_backtest(
                    bt_data["opens"], bt_data["highs"], bt_data["lows"], bt_data["closes"],
                    bt_data["times"], bt_data["volumes"], params,
                    initial_balance=acc.balance or 100.0, split_ratio=0.75,
                    spread_points=sym_info.spread_usd, fixed_lot_size=bt_lot,
                    daily_loss_cap_usd=bt_daily_cap,
                    volume_min=sym_info.volume_min, volume_max=sym_info.volume_max, volume_step=sym_info.volume_step,
                    num_noise_shuffles=50
                )
                st.session_state.bt_result = res

        if "bt_result" in st.session_state:
            res = st.session_state.bt_result
            st.caption(f"{res.num_range_bars} range bars reconstructed from {bt_bars} real M15 candles.")
            c1, c2, c3 = st.columns(3)
            for col, m, title in [(c1, res.in_sample_metrics, "📘 In-Sample"), (c2, res.out_of_sample_metrics, "📙 Out-of-Sample"), (c3, res.overall_metrics, "🌐 Overall")]:
                with col:
                    st.markdown(f"**{title}**")
                    st.metric("Trades", m.total_trades)
                    st.metric("Win Rate", f"{m.win_rate_pct:.1f}%")
                    st.metric("Profit Factor", m.profit_factor)
                    st.metric("Expectancy (R)", f"{m.expectancy_r:+.2f}R")
                    st.metric("Net PnL", f"${m.total_net_pnl_usd:+,.2f}")
                    st.metric("Max Drawdown", f"${m.max_drawdown_usd:,.2f} ({m.max_drawdown_pct:.1f}%)")
            m_all = res.overall_metrics
            if m_all.noise_gate_passed:
                st.success(f"🎉 Passed the noise gate (p = {m_all.noise_p_value:.4f} ≤ 0.05, Z = {m_all.z_score:.2f})")
            else:
                st.error(f"🛑 Failed the noise gate (p = {m_all.noise_p_value:.4f} > 0.05) — edge not distinguishable from noise.")

    with tab3:
        st.caption("Session Volume Profile computed on the reconstructed range-bar series.")
        if signal is not None:
            i1, i2, i3, i4 = st.columns(4)
            i1.metric("VAL", f"${signal.val:,.1f}")
            i2.metric("VAH", f"${signal.vah:,.1f}")
            i3.metric("POC", f"${signal.poc:,.1f}")
            i4.metric("CVD", f"{signal.cvd:+,.0f}")
            chart_df = pd.DataFrame({"close": rb_closes[-200:]})
            st.line_chart(chart_df, height=280)
        else:
            st.info("Not enough data yet.")

    with tab4:
        _render_trade_history(storage, magic_num)

    with tab5:
        if engine.is_running() and engine.started_at:
            st.caption(f"Running since {engine.started_at.strftime('%H:%M:%S')} UTC")
        elif engine.error:
            st.caption(f"⚠️ Last error: {engine.error}")
        else:
            st.caption("Stopped — use ▶ Start Bot at the top of the page to begin trading.")
        _render_engine_live(engine)


if __name__ == "__main__":
    main()
