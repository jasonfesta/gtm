import datetime as dt
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm import database
from crm import human_reply_capture as watcher

AT = dt.datetime(2026, 9, 21, 16, 0, tzinfo=dt.timezone.utc)


def post(number, handle="builder", minutes=1, lane="Agent tool", text="agent tool routing"):
    created = AT - dt.timedelta(minutes=minutes)
    return {
        "id": str(number),
        "handle": handle,
        "created_at": created.isoformat(),
        "text": text,
        "url": f"https://x.com/{handle}/status/{number}",
        "lane": lane,
    }


def capture(rows, columns=("reads_", "Agent tool")):
    return {
        "account": "jasonfesta",
        "expected_columns": list(columns),
        "columns": [{"name": name, "scanned": True, "cutoff_reached": True} for name in columns],
        "posts": rows,
    }


class FakePortkey:
    def __init__(self, selected=()):
        self.selected = set(selected)
        self.classify_calls = 0
        self.draft_calls = 0

    def classify(self, posts):
        self.classify_calls += 1
        return self.selected

    def draft(self, posts):
        self.draft_calls += 1
        return {item.id: "interesting way to frame the discovery problem" for item in posts}


class FakeGuard:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def check(self, handles, *, at):
        return {
            handle: {
                "allowed": self.allowed,
                "reason": "eligible" if self.allowed else "held",
                "person_id": "person_" + handle,
            }
            for handle in handles
        }


class FinalTimelineWatcherTests(unittest.TestCase):
    def test_window_deduplicates_and_drops_old_posts(self):
        fresh = post(1)
        rows = [fresh, dict(fresh), post(2, minutes=6)]
        self.assertEqual([item.id for item in watcher.compact_window(rows, at=AT)], ["1"])

    def test_abuse_requires_four_and_more_than_seventy_five_percent(self):
        exact_75 = watcher.compact_window(
            [post(i, "spam", lane="reads_") for i in range(1, 4)]
            + [post(4, "other", lane="reads_")],
            at=AT,
        )
        self.assertEqual(watcher.abusive_handles(exact_75), set())
        over_75 = watcher.compact_window(
            [post(i, "spam", lane="reads_") for i in range(1, 5)]
            + [post(5, "other", lane="reads_")],
            at=AT,
        )
        self.assertEqual(watcher.abusive_handles(over_75), {"spam"})

    def test_one_post_per_handle_and_two_batched_model_calls(self):
        model = FakePortkey(selected={"3"})
        rows = [
            post(1, minutes=1),
            post(2, minutes=2),
            post(3, handle="context", text="a new way to route assistants"),
        ]
        plan = watcher.build_plan(capture(rows), at=AT, portkey=model, guard=FakeGuard())
        self.assertEqual({item["handle"] for item in plan["items"]}, {"builder", "context"})
        self.assertEqual(len(plan["items"]), 2)
        self.assertEqual((model.classify_calls, model.draft_calls), (1, 1))

    def test_crm_failure_holds_every_handle(self):
        def unavailable():
            raise OSError("offline")

        guard = watcher.CRMGuard(connection_factory=unavailable)
        result = guard.check({"builder"}, at=AT)
        self.assertFalse(result["builder"]["allowed"])
        self.assertEqual(result["builder"]["reason"], "shared_crm_unavailable")

    def test_portkey_model_mismatch_fails_closed(self):
        response = io.BytesIO(
            json.dumps(
                {
                    "model": "gpt-5.6-sol",
                    "choices": [{"message": {"content": '{"ids":[]}'}}],
                }
            ).encode()
        )
        client = watcher.PortkeyClient(
            api_key="synthetic",
            config_id="synthetic",
            opener=lambda *_args, **_kwargs: response,
        )
        with self.assertRaisesRegex(RuntimeError, "model mismatch"):
            client.complete("system", {})

    def test_held_handle_never_reaches_portkey(self):
        model = FakePortkey(selected={"1"})
        plan = watcher.build_plan(
            capture([post(1, text="a contextual post")]),
            at=AT,
            portkey=model,
            guard=FakeGuard(allowed=False),
        )
        self.assertEqual(plan["items"], [])
        self.assertEqual((model.classify_calls, model.draft_calls), (0, 0))

    def test_plan_fails_closed_when_any_visible_column_is_incomplete(self):
        scan = capture([post(1)], columns=("reads_", "Agent tool", "OpenClaw"))
        scan["columns"][1]["cutoff_reached"] = False
        with self.assertRaisesRegex(ValueError, "all columns must reach"):
            watcher.build_plan(scan, at=AT, portkey=FakePortkey(), guard=FakeGuard())

    def test_plan_fails_closed_when_a_visible_column_is_missing(self):
        scan = capture([post(1)], columns=("reads_", "Agent tool", "OpenClaw"))
        scan["columns"].pop()
        with self.assertRaisesRegex(ValueError, "all columns required"):
            watcher.build_plan(scan, at=AT, portkey=FakePortkey(), guard=FakeGuard())

    def test_no_receipt_before_provider_confirmation(self):
        connection = database.schema_connection()
        connection.execute(
            "INSERT INTO people(person_id,full_name,normalized_name,identity_status,research_status) VALUES ('person_builder','Builder','builder','verified','complete')"
        )
        plan = {
            "items": [
                {
                    **post(1),
                    "fingerprint": "abc",
                    "person_id": "person_builder",
                    "reply": "nice",
                }
            ]
        }
        with self.assertRaises(ValueError):
            watcher.record_confirmed(
                plan,
                [{"id": "1", "status": "attempted"}],
                connection_factory=lambda: connection,
            )
        self.assertEqual(
            connection.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 0
        )

    def test_confirmed_reply_records_event_and_public_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "crm.sqlite3"
            source = database.schema_connection()
            target = sqlite3.connect(path)
            source.backup(target)
            source.close()
            target.close()

            def factory():
                connection = sqlite3.connect(path)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                return connection

            with factory() as connection:
                connection.execute(
                    "INSERT INTO people(person_id,full_name,normalized_name,identity_status,research_status) VALUES ('person_builder','Builder','builder','verified','complete')"
                )
            plan = {
                "items": [
                    {
                        **post(1),
                        "fingerprint": "abc",
                        "person_id": "person_builder",
                        "reply": "nice",
                    }
                ]
            }
            confirmation = {
                "id": "1",
                "status": "confirmed",
                "reply_url": "https://x.com/jasonfesta/status/999",
                "provider_id": "999",
                "account": "jasonfesta",
                "account_profile_url": "https://x.com/jasonfesta",
                "observed_at": AT.isoformat(),
            }
            written = watcher.record_confirmed(plan, [confirmation], connection_factory=factory)
            self.assertEqual(len(written), 1)
            with factory() as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM outreach_events").fetchone()[0],
                    1,
                )
                claim = connection.execute(
                    "SELECT claim_value FROM evidence_claims WHERE field_name='public_reply_location'"
                ).fetchone()
            self.assertIn(confirmation["reply_url"], claim["claim_value"])

    def test_lease_does_not_steal_active_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lease.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    "CREATE TABLE lease_fence(scope TEXT PRIMARY KEY,last_fence INTEGER NOT NULL);"
                    "CREATE TABLE browser_lease(scope TEXT PRIMARY KEY,token TEXT UNIQUE NOT NULL,owner TEXT NOT NULL,started_at TEXT NOT NULL,renewed_at TEXT,expires_at TEXT,fence INTEGER,read_budget_seconds INTEGER NOT NULL DEFAULT 480);"
                )
            lease = watcher.BrowserLease(path)
            first = lease.acquire("one", at=AT)
            second = lease.acquire("two", at=AT)
            self.assertTrue(first["acquired"])
            self.assertFalse(second["acquired"])
            isolated = watcher.BrowserLease(path, scope="human-reply-reddit-test")
            other = isolated.acquire("reddit", at=AT)
            self.assertTrue(other["acquired"])
            self.assertFalse(isolated.acquire("intruder", at=AT)["acquired"])
            self.assertFalse(isolated.release("one", first["token"], first["fence"])["released"])
            self.assertFalse(
                isolated.release("reddit", other["token"], other["fence"] + 1)["released"]
            )
            self.assertTrue(isolated.release("reddit", other["token"], other["fence"])["released"])
            self.assertFalse(lease.acquire("two", at=AT)["acquired"])
            again = isolated.acquire("reddit", at=AT)
            self.assertGreater(again["fence"], other["fence"])
            self.assertNotEqual(again["token"], other["token"])
            self.assertFalse(isolated.release("reddit", other["token"], other["fence"])["released"])
            self.assertTrue(lease.release("one", first["token"], first["fence"])["released"])


if __name__ == "__main__":
    unittest.main()
