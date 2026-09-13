"""Unit tests for news_filter.py - pure logic only, no live network calls."""

import unittest
import sys
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.news_filter import _parse_events, is_near_high_impact_news


SAMPLE_RAW_EVENTS = [
    {"title": "FOMC Statement", "country": "USD", "date": "2026-09-16T14:00:00-04:00",
     "impact": "High", "forecast": "", "previous": ""},
    {"title": "BusinessNZ Services Index", "country": "NZD", "date": "2026-09-13T18:30:00-04:00",
     "impact": "Low", "forecast": "", "previous": "50.6"},
    {"title": "CPI m/m", "country": "CAD", "date": "2026-09-14T08:30:00-04:00",
     "impact": "High", "forecast": "-0.1%", "previous": "0.5%"},
    {"title": "Malformed date event", "country": "USD", "date": "not-a-date",
     "impact": "High", "forecast": "", "previous": ""},
]


class TestParseEvents(unittest.TestCase):
    def test_parses_valid_events_and_converts_to_utc(self):
        events = _parse_events(SAMPLE_RAW_EVENTS)
        # 3 valid events parsed, 1 malformed date silently skipped (fail-open, not crash)
        self.assertEqual(len(events), 3)
        fomc = next(e for e in events if e["title"] == "FOMC Statement")
        # 2026-09-16T14:00:00-04:00 -> 18:00:00 UTC
        self.assertEqual(fomc["date_utc"], datetime(2026, 9, 16, 18, 0, 0, tzinfo=timezone.utc))

    def test_empty_input_returns_empty(self):
        self.assertEqual(_parse_events([]), [])


class TestIsNearHighImpactNews(unittest.TestCase):
    def setUp(self):
        self.events = _parse_events(SAMPLE_RAW_EVENTS)

    def test_blocks_within_buffer_of_usd_high_impact_event(self):
        # FOMC Statement at 18:00 UTC; check 10 minutes before
        now = datetime(2026, 9, 16, 17, 50, 0, tzinfo=timezone.utc)
        blocked, matched = is_near_high_impact_news(now, self.events, buffer_minutes=15.0)
        self.assertTrue(blocked)
        self.assertEqual(matched["title"], "FOMC Statement")

    def test_blocks_after_event_too_not_just_before(self):
        now = datetime(2026, 9, 16, 18, 10, 0, tzinfo=timezone.utc)
        blocked, _ = is_near_high_impact_news(now, self.events, buffer_minutes=15.0)
        self.assertTrue(blocked)

    def test_does_not_block_outside_buffer(self):
        now = datetime(2026, 9, 16, 17, 0, 0, tzinfo=timezone.utc)  # 1 hour before
        blocked, matched = is_near_high_impact_news(now, self.events, buffer_minutes=15.0)
        self.assertFalse(blocked)
        self.assertIsNone(matched)

    def test_ignores_non_usd_events_by_default(self):
        # CAD CPI is High impact but not USD - should not block with default currencies=("USD",)
        now = datetime(2026, 9, 14, 12, 30, 0, tzinfo=timezone.utc)  # exactly at CAD CPI time
        blocked, _ = is_near_high_impact_news(now, self.events, buffer_minutes=15.0)
        self.assertFalse(blocked)

    def test_respects_custom_currency_filter(self):
        now = datetime(2026, 9, 14, 12, 30, 0, tzinfo=timezone.utc)
        blocked, matched = is_near_high_impact_news(now, self.events, buffer_minutes=15.0, currencies=("CAD",))
        self.assertTrue(blocked)
        self.assertEqual(matched["title"], "CPI m/m")

    def test_ignores_low_impact_events(self):
        now = datetime(2026, 9, 13, 22, 30, 0, tzinfo=timezone.utc)  # NZD Low-impact event time
        blocked, _ = is_near_high_impact_news(now, self.events, buffer_minutes=15.0, currencies=("NZD",))
        self.assertFalse(blocked)

    def test_empty_events_never_blocks(self):
        blocked, matched = is_near_high_impact_news(datetime.now(timezone.utc), [], buffer_minutes=15.0)
        self.assertFalse(blocked)
        self.assertIsNone(matched)


if __name__ == "__main__":
    unittest.main()
