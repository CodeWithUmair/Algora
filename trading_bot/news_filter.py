"""
Economic calendar / high-impact-news avoidance filter.

Pulls the (unofficial, community-run) ForexFactory calendar mirror at
nfs.faireconomy.media/ff_calendar_thisweek.json - a widely used feed in
retail algo/EA news filters. Manually verified reachable 2026-09-14 (200 OK,
97 events, correctly showing that week's FOMC meeting). No official SLA -
it's a third-party mirror, not ForexFactory's own API, so it can go down or
change format without notice.

HONEST LIMITATION: this feed only ever exposes the CURRENT week, never a
historical archive. That means this filter's real-world effect on returns
could NOT be backtested against the ~11.7 months of history used elsewhere
in this repo (see BACKTEST_REPORT.md) - it's a live-only addition, checked
by manual inspection and unit tests against synthetic events, not validated
the way every other change in this repo has been. Don't present it to the
user as backtest-proven; it isn't.

Fail-open by design: if the feed is unreachable and no usable cache exists,
trading proceeds WITHOUT the news filter rather than silently blocking every
trade whenever an unofficial third-party mirror hiccups. The daily loss cap
and per-trade stop-loss (see live_engine.py, backtest.py) are the real,
backtested backstops - this filter is an extra layer on top, not a
replacement for them.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DEFAULT_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".news_calendar_cache.json")
DEFAULT_TIMEOUT_SECONDS = 10.0


def fetch_calendar(
    cache_path: str = DEFAULT_CACHE_PATH, max_age_seconds: float = 1800.0,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    """
    Returns parsed events: [{"title", "country", "impact", "date_utc" (aware datetime)}, ...].

    Uses a local cache (default 30 min) to avoid hammering the feed on every
    call. On any fetch failure, falls back to a stale cache, then to an empty
    list - never raises, since a calendar hiccup must not be able to take
    the whole trading loop down.
    """
    now = time.time()
    cached = _read_cache(cache_path)
    if cached is not None and (now - cached.get("_fetched_at", 0)) < max_age_seconds:
        return _parse_events(cached.get("events", []))

    try:
        req = urllib.request.Request(CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        _write_cache(cache_path, raw, now)
        return _parse_events(raw)
    except Exception:
        if cached is not None:
            return _parse_events(cached.get("events", []))
        return []


def _read_cache(cache_path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(cache_path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(cache_path: str, raw_events: List[Dict[str, Any]], fetched_at: float) -> None:
    try:
        with open(cache_path, "w") as f:
            json.dump({"_fetched_at": fetched_at, "events": raw_events}, f)
    except Exception:
        pass


def _parse_events(raw_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parsed = []
    for e in raw_events:
        try:
            dt = datetime.fromisoformat(e["date"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            parsed.append({
                "title": e.get("title", ""), "country": e.get("country", ""),
                "impact": e.get("impact", ""), "date_utc": dt.astimezone(timezone.utc),
            })
        except Exception:
            continue
    return parsed


def is_near_high_impact_news(
    now_utc: datetime, events: List[Dict[str, Any]],
    buffer_minutes: float = 15.0, impact_levels: Tuple[str, ...] = ("High",),
    currencies: Tuple[str, ...] = ("USD",),
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    True + the matched event if now_utc falls within buffer_minutes of a
    qualifying event's release time - checked both before AND after, since
    the spike risk exists on both sides of the print, not just leading into it.
    """
    buffer = timedelta(minutes=buffer_minutes)
    for ev in events:
        if ev["impact"] not in impact_levels:
            continue
        if currencies and ev["country"] not in currencies:
            continue
        if abs(ev["date_utc"] - now_utc) <= buffer:
            return True, ev
    return False, None
