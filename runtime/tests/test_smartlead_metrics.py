import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from crm.activity import Ledger
from crm.smartlead import SmartleadClient
from crm.smartlead_metrics import number, queue, replied_people


class Provider:
    def __init__(self):
        self.calls = []
        self.period_wrong = False
        self.rows = []

    def campaign_analytics(self, campaign_id):
        self.calls.append(campaign_id)
        return {
            "id": campaign_id,
            "sent_count": "3",
            "reply_count": "2",
            "unique_sent_count": "1",
            "unique_open_count": "1",
            "unique_click_count": "0",
        }

    def request(self, method, path, *, params):
        if method != "GET":
            raise AssertionError("Metrics must remain read-only")
        self.calls.append(path)
        if path.endswith("analytics-by-date"):
            return {
                "id": 99 if self.period_wrong else 3935204,
                **params,
                "sent_count": "2",
                "reply_count": "1",
            }
        return {"total_leads": str(len(self.rows)), "data": self.rows}


class SmartleadMetricTests(unittest.TestCase):
    def test_current_client_supports_read_only_metrics(self):
        provider = Provider()
        calls = []

        def transport(method, path, params, payload):
            calls.append((method, path))
            self.assertEqual(method, "GET")
            self.assertIsNone(payload)
            if path.endswith("/analytics"):
                return provider.campaign_analytics(3935204)
            return provider.request(method, path, params=params)

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "activity.sqlite3")
            result = queue(ledger, SmartleadClient("test-key", transport=transport))
            self.assertEqual(result["daily_status"], "provider date-range report")
            self.assertEqual(len(calls), 3)

    def test_scope_strict_counts_and_period_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "activity.sqlite3")
            provider = Provider()
            provider.period_wrong = True
            queue(ledger, provider, datetime(2026, 9, 10, tzinfo=timezone.utc))
            with ledger.connect() as conn:
                event = json.loads(conn.execute("SELECT payload_json FROM snapshots").fetchone()[0])
            self.assertEqual(event["properties"]["sends"], 3)
            self.assertEqual(event["properties"]["unique_sent"], 1)
            self.assertIsNone(event["properties"]["sends_yesterday"])
            self.assertEqual(event["properties"]["unique_replied"], 0)
            self.assertTrue(all("3935204" in str(call) for call in provider.calls))
        for value in (None, -1, "1.5", True):
            with self.assertRaises((ValueError, TypeError)):
                number({"metric": value}, "metric")
        with self.assertRaises(KeyError):
            number({}, "metric")

    def test_unique_people_require_reply_evidence(self):
        provider = Provider()
        provider.rows = [{"lead": {"id": 123}, "reply_count": "2"}]
        self.assertEqual(replied_people(provider), 1)
        provider.rows = [{"lead": {"id": 123}}]
        with self.assertRaises(ValueError):
            replied_people(provider)
