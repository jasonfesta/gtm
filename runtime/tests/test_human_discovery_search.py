import json
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tests.test_assistant_fit import BUILDER, item

from crm.assistant_fit import score_post
from crm.cli import initialize
from crm.human_discovery_search import (
    ACTORS,
    MAX_HITS,
    _review_scorer,
    bounded_collection,
    brief,
    collect,
    collect_pending,
    day_brief,
    finalize_pending,
    hour_slots,
    hour_stamp,
    hour_window,
    load_catalog,
    qualify,
    ready,
    reconcile_leads,
    recover_x_dataset,
    rotate,
    run,
)
from crm.human_inbox import Watcher
from crm.live_channels import allowed as channel_live
from crm.local_secrets import apify_token, persist_apify_token
from crm.outreach_queue import CRMHistory
from crm.publication import ingest
from crm.reply_locations import ReplyLocations
from crm.team_contacts import APOLLO_HEALTH


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class HourlySearchTests(unittest.TestCase):
    def catalog(self):
        return {
            "version": "agentic-search-v5",
            "entries": [
                {
                    "id": "A02",
                    "family": "A",
                    "priority": 1,
                    "x": "AI agent",
                    "linkedin": "AI agent",
                },
                {
                    "id": "B01",
                    "family": "B",
                    "priority": 2,
                    "x": "AI assistant",
                    "linkedin": "AI assistant",
                },
                {
                    "id": "E01",
                    "family": "E",
                    "priority": 3,
                    "x": "MCP server",
                    "linkedin": "MCP server",
                },
            ],
        }

    def opener(self):
        datasets = {
            ACTORS["x"].replace("/", "~"): [],
            ACTORS["linkedin"].replace("/", "~"): [],
            ACTORS["hacker_news"].replace("/", "~"): [
                {
                    "author": "demeyer1",
                    "id": "49743478",
                    "title": "we shipped our voice agent",
                    "storyText": BUILDER,
                    "hnUrl": "https://news.ycombinator.com/item?id=49743478",
                    "createdAt": "2026-09-17T18:10:00Z",
                },
                {
                    "author": "ygjb",
                    "id": "49716388",
                    "title": "browser agent eval",
                    "storyText": BUILDER,
                    "hnUrl": "https://news.ycombinator.com/item?id=49716388",
                    "createdAt": "2026-09-17T18:40:00Z",
                },
            ],
            ACTORS["github"].replace("/", "~"): [
                {
                    "full_name": "builder/agent",
                    "url": "https://github.com/builder/agent",
                    "description": BUILDER,
                    "updated_at": "2026-09-17T18:20:00Z",
                },
                {
                    "full_name": "dependabot/agent",
                    "url": "https://github.com/acme/agent/pull/1",
                    "description": BUILDER,
                    "updated_at": "2026-09-16T18:00:00Z",
                },
            ],
            ACTORS["product_hunt"].replace("/", "~"): [],
        }

        def open_url(request, timeout=None):
            del timeout
            url = request.full_url
            if "/acts/" in url and "/runs" in url:
                actor = url.split("/acts/")[1].split("/runs")[0]
                return FakeResponse({"data": {"id": "run1", "defaultDatasetId": actor}})
            if "/datasets/" in url and "/items" in url:
                dataset = url.split("/datasets/")[1].split("/items")[0]
                return FakeResponse(datasets.get(dataset, []))
            return FakeResponse({"data": {"id": "run1", "defaultDatasetId": None}})

        return open_url

    def test_rotate_stays_small(self):
        chosen = rotate(self.catalog(), "2026-09-17T18", "linkedin", 1)
        self.assertEqual(len(chosen), 1)
        self.assertIn(chosen[0]["query"], ("AI agent", "AI assistant", "MCP server"))

    def test_live_catalog_is_pruned_to_agent_and_assistant_builders(self):
        catalog = load_catalog()
        self.assertEqual(catalog["version"], "agentic-search-v3-hourly-builders")
        self.assertEqual(len(catalog["entries"]), 11)
        self.assertEqual({row["family"] for row in catalog["entries"]}, {"A", "B"})
        broad = {"agentic", "AI agent", "AI agents", "AI assistant", "personal AI assistant"}
        self.assertFalse(broad & {row["x"] for row in catalog["entries"]})

    def test_qualify_uses_assistant_fit(self):
        self.assertEqual(qualify(item(), scorer=score_post)["action"], "comment")
        self.assertEqual(
            qualify(
                item(text="hiring a software engineer for our backend team"),
                scorer=score_post,
            )["action"],
            "skip",
        )

    def test_run_skips_existing_hour(self):
        with tempfile.TemporaryDirectory() as temp:
            drops = Path(temp) / "drops"
            dest = drops / "2026-09-17T18.json"
            dest.parent.mkdir()
            dest.write_text("{}\n")
            skipped = run("2026-09-17T18", catalog=self.catalog(), drops=drops, ingest_later=False)
            self.assertTrue(skipped["hour_exists"])
            dest.unlink()
            result = run(
                "2026-09-17T18",
                catalog=self.catalog(),
                drops=drops,
                handoff=Path(temp) / "handoff",
                pending=Path(temp) / "pending",
                opener=self.opener(),
                auth="",
                apollo_auth="",
                ingest_later=False,
            )
            self.assertFalse(result["hour_exists"])
            self.assertTrue(Path(result["path"]).is_file())
            self.assertLessEqual(len(result["queries"]["x"]), 5)
            self.assertIn("brief", result)
            self.assertFalse(result["brief"]["missing"])

    def test_later_use_can_ingest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "crm.sqlite3"
            initialize(db).close()

            def shared():
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                return conn

            pending = root / "pending"
            pending.mkdir()
            path = pending / "later-github.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "Builder",
                        "channel": "github",
                        "profile_url": "https://github.com/builder",
                        "parent_url": "https://github.com/builder/agent",
                        "hold": "later_use",
                        "observed_at": "2026-09-17T18:00:00+00:00",
                        "evidence": "synthetic hourly search",
                        "sources": [{"url": "https://github.com/builder/agent", "type": "github"}],
                    }
                )
            )
            results = ingest(
                [path],
                pending=pending,
                processed=root / "processed",
                held=root / "held",
                shared_factory=shared,
                locations=ReplyLocations(Watcher(root / "watch.sqlite3")),
                history=CRMHistory(db, synthetic=True),
            )
            self.assertEqual(results[0]["status"], "held")
            with shared() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM people").fetchone()[0], 1)
                self.assertEqual(
                    conn.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 0
                )

    def test_search_hit_adds_new_lead_to_review_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "crm.sqlite3"
            initialize(db).close()

            def shared():
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                return conn

            receipt = root / "search-hit.json"
            receipt.write_text(
                json.dumps(
                    {
                        "name": "Agent Builder",
                        "channel": "x",
                        "profile_url": "https://x.com/agent_builder",
                        "parent_url": "https://x.com/agent_builder/status/1",
                        "hold": "search_hit",
                        "observed_at": "2026-09-17T18:00:00+00:00",
                        "evidence": "synthetic hourly search",
                        "sources": [{"url": "https://x.com/agent_builder/status/1", "type": "x"}],
                    }
                )
            )
            results = ingest(
                [receipt],
                pending=root / "pending",
                processed=root / "processed",
                held=root / "held",
                shared_factory=shared,
            )
            self.assertEqual(results[0]["status"], "held")
            self.assertTrue(results[0]["queued"])
            self.assertIsNotNone(results[0]["person_id"])
            with shared() as conn:
                lead = conn.execute("SELECT * FROM v_contact_crm").fetchone()
                task = conn.execute("SELECT * FROM v_review_queue").fetchone()
                self.assertEqual(lead["person_id"], results[0]["person_id"])
                self.assertEqual(task["entity_id"], lead["person_id"])
                self.assertEqual(task["issue"], "verify_identity")

    def test_reconcile_retries_only_failed_search_hits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "crm.sqlite3"
            initialize(db).close()

            def shared():
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                return conn

            held = root / "held"
            held.mkdir()
            receipt = held / "failed.json"
            receipt.write_text(
                json.dumps(
                    {
                        "name": "Retry Builder",
                        "channel": "x",
                        "profile_url": "https://x.com/retry_builder",
                        "parent_url": "https://x.com/retry_builder/status/1",
                        "hold": "search_hit",
                        "observed_at": "2026-09-17T18:00:00+00:00",
                        "evidence": "synthetic hourly search",
                        "sources": [{"url": "https://x.com/retry_builder/status/1", "type": "x"}],
                    }
                )
            )
            receipt.with_suffix(".json.result.json").write_text(
                json.dumps({"error": "OperationalError"})
            )
            result = reconcile_leads(
                held=held,
                pending=root / "pending",
                processed=root / "processed",
                record_kwargs={"shared_factory": shared},
            )
            self.assertEqual(result["retried"], 1)
            self.assertTrue(result["results"][0]["queued"])
            with shared() as conn:
                self.assertEqual(
                    conn.execute("SELECT count(*) FROM v_contact_crm").fetchone()[0], 1
                )
                self.assertEqual(
                    conn.execute("SELECT count(*) FROM v_review_queue").fetchone()[0], 1
                )

    def test_run_injects_later_use_links_and_keeps_people_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "crm.sqlite3"
            initialize(db).close()

            def shared():
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                return conn

            result = run(
                "2026-09-17T18",
                catalog=self.catalog(),
                drops=root / "drops",
                handoff=root / "handoff",
                pending=root / "pending",
                opener=self.opener(),
                auth="test-token",
                apollo_auth="",
                ingest_later=True,
                record_kwargs={
                    "shared_factory": shared,
                    "locations": ReplyLocations(Watcher(root / "watch.sqlite3")),
                    "history": CRMHistory(db, synthetic=True),
                },
            )
            self.assertFalse(result["hour_exists"])
            self.assertEqual(result["comments"], [])
            self.assertEqual(result["later_use"], [])
            holds = {row["channel"]: row.get("hold") for row in result["runs"]}
            self.assertEqual(holds.get("hacker_news"), "channel_paused")
            self.assertEqual(holds.get("github"), "channel_paused")
            self.assertEqual(holds.get("product_hunt"), "channel_paused")
            self.assertEqual(holds.get("apollo"), "channel_paused")
            outreach = list((root / "handoff" / "outreach").glob("*.json"))
            self.assertEqual(len(outreach), 0)
            with shared() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM people").fetchone()[0], 0)

    def test_local_secret_and_ready_do_not_start_actors(self):
        with tempfile.TemporaryDirectory() as temp:
            creds = Path(temp) / "creds"
            persist_apify_token("secret-token", credentials=creds)
            self.assertEqual(apify_token(env={}, credentials=creds), "secret-token")
            seen = []

            def opener(request, timeout=None):
                del timeout
                seen.append(request.full_url)
                return FakeResponse({"data": {"username": "darwinso", "plan": {"id": "SCALE"}}})

            def shared():
                conn = sqlite3.connect(":memory:")
                conn.row_factory = sqlite3.Row
                conn.execute("CREATE TABLE people (person_id TEXT)")
                conn.execute("INSERT INTO people VALUES ('p1')")
                return conn

            with (
                patch(
                    "crm.human_discovery_search.token",
                    return_value=apify_token(env={}, credentials=creds),
                ),
                patch("crm.human_discovery_search.apollo_token", return_value=""),
                patch("crm.human_discovery_search.configured", return_value=True),
            ):
                result = ready(opener=opener, persist=False, shared_factory=shared)
            self.assertTrue(result["ready"])
            self.assertTrue(any("/users/me" in url for url in seen))
            self.assertFalse(any("/acts/" in url for url in seen))
            self.assertFalse(any("mixed_people/search" in url for url in seen))
            self.assertFalse(any("bulk_match" in url for url in seen))
            self.assertNotIn("secret-token", json.dumps(result))
            self.assertIn("apollo", result)
            self.assertFalse(result["apollo"]["enriches"])

    def test_hour_window_is_one_utc_hour(self):
        start, end = hour_window("2026-09-17T18")
        self.assertEqual(start.isoformat(), "2026-09-17T18:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-09-17T19:00:00+00:00")

    def test_default_stamp_is_last_completed_utc_hour(self):
        self.assertEqual(
            hour_stamp(datetime.fromisoformat("2026-09-21T22:03:00+00:00")),
            "2026-09-21T21",
        )

    def test_collect_uses_cheapest_apify_and_one_hour(self):
        seen = []

        def opener(request, timeout=None):
            del timeout
            seen.append((request.full_url, request.data))
            return self.opener()(request)

        result = collect(
            "2026-09-17T18",
            catalog=self.catalog(),
            opener=opener,
            auth="test-token",
            apollo_auth="",
        )
        urls = [url for url, _ in seen]
        self.assertTrue(urls)
        self.assertTrue(all("api.apify.com" in url for url in urls))
        self.assertFalse(any("algolia" in url or "api.github.com" in url for url in urls))
        self.assertFalse(any("api.apollo.io" in url for url in urls))
        actors = {row["actor"] for row in result["runs"] if row.get("actor")}
        self.assertEqual(actors, {ACTORS["x"]})
        self.assertTrue(channel_live("x"))
        holds = {row["channel"]: row.get("hold") for row in result["runs"]}
        self.assertEqual(holds.get("linkedin"), "channel_paused")
        self.assertEqual(holds.get("hacker_news"), "channel_paused")
        self.assertEqual(holds.get("github"), "channel_paused")
        self.assertEqual(holds.get("apollo"), "channel_paused")
        self.assertEqual(result["lookback"], "1h")
        bodies = [json.loads(body.decode()) for _url, body in seen if body]
        self.assertFalse(any("postedLimit" in body for body in bodies))
        x_body = next(body for body in bodies if "since_time" in body)
        start, end = hour_window("2026-09-17T18")
        self.assertEqual(x_body["since_time"], str(int(start.timestamp())))
        self.assertEqual(x_body["until_time"], str(int(end.timestamp())))
        self.assertEqual(result["items"], [])

    def test_recover_dataset_never_starts_actor(self):
        seen = []

        def opener(request, timeout=None):
            del timeout
            seen.append(request.full_url)
            return FakeResponse([])

        result = recover_x_dataset(
            "2026-09-17T18",
            "dataset123",
            run_id="run12345",
            catalog=self.catalog(),
            opener=opener,
            auth="test-token",
        )
        self.assertEqual(len(seen), 1)
        self.assertIn("/datasets/dataset123/items", seen[0])
        self.assertFalse(any("/acts/" in url for url in seen))
        self.assertTrue(result["runs"][0]["recovered"])
        self.assertEqual(result["estimated_charge_usd"], 0.0)

    def test_collect_pending_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "pending"
            first = collect_pending(
                "2026-09-17T18",
                catalog=self.catalog(),
                folder=folder,
                opener=self.opener(),
                auth="",
            )
            second = collect_pending(
                "2026-09-17T18",
                catalog=self.catalog(),
                folder=folder,
                opener=lambda *_args, **_kwargs: self.fail("duplicate provider call"),
                auth="",
            )
            self.assertFalse(first["skipped"])
            self.assertEqual(second["reason"], "pending_exists")

    def test_provider_overflow_is_bounded_before_model_review(self):
        collected = {"items": [{"parent_url": f"https://x.com/a/status/{i}"} for i in range(100)]}
        bounded = bounded_collection(collected)
        self.assertEqual(len(bounded["items"]), MAX_HITS)
        self.assertEqual(bounded["provider_item_count"], 100)
        self.assertEqual(bounded["bounded_item_count"], MAX_HITS)
        self.assertEqual(bounded["overflow_item_count"], 100 - MAX_HITS)
        self.assertEqual(len(collected["items"]), 100)

    def test_builder_lead_does_not_require_tool_discovery_flag(self):
        scorer = _review_scorer(
            {
                "model": "task-model",
                "route": "codex-task",
                "judgments": [
                    {
                        "parent_url": "https://x.com/builder/status/1",
                        "builder": 40,
                        "specificity": 20,
                        "substance": 20,
                        "total": 80,
                        "action": "comment",
                        "reason": "substantive_agent_builder",
                        "tool_discovery": False,
                        "topic": "agent",
                    }
                ],
            }
        )
        self.assertEqual(
            scorer({"parent_url": "https://x.com/builder/status/1"})["action"],
            "comment",
        )

    def test_finalize_requires_exact_model_route_and_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pending = root / "pending"
            pending.mkdir()
            pending.joinpath("2026-09-17T18.json").write_text(
                json.dumps(
                    {
                        "hour": "2026-09-17T18",
                        "catalog_version": self.catalog()["version"],
                        "source": "apify",
                        "lookback": "1h",
                        "window": {},
                        "queries": {"x": []},
                        "runs": [],
                        "items": [],
                        "estimated_charge_usd": 0.0,
                    }
                )
            )
            review = root / "review.json"
            review.write_text(
                json.dumps(
                    {
                        "hour": "2026-09-17T18",
                        "model": "task-configured-model",
                        "route": "codex-task",
                        "judgments": [],
                    }
                )
            )
            result = finalize_pending(
                review,
                drops=root / "drops",
                handoff=root / "handoff",
                pending=root / "receipts",
                search_pending=pending,
            )
            self.assertEqual(result["model"], "task-configured-model")
            self.assertEqual(result["model_route"], "codex-task")

    def test_hour_slots_cover_the_new_york_day(self):
        slots = hour_slots("2026-09-17")
        self.assertEqual(len(slots), 24)
        self.assertEqual(slots[0], "2026-09-17T04")
        self.assertEqual(slots[-1], "2026-09-18T03")

    def test_brief_names_missing_and_present_hours(self):
        with tempfile.TemporaryDirectory() as temp:
            drops = Path(temp)
            missing = brief("2026-09-17T20", drops=drops)
            self.assertTrue(missing["missing"])
            drops.joinpath("2026-09-17T18.json").write_text(
                json.dumps(
                    {
                        "hour": "2026-09-17T18",
                        "comments": [
                            {
                                "name": "Ada",
                                "channel": "x",
                                "parent_url": "https://x.com/ada/status/1",
                            }
                        ],
                        "later_use": [
                            {
                                "name": "demeyer1",
                                "channel": "hacker_news",
                                "parent_url": "https://news.ycombinator.com/item?id=1",
                            }
                        ],
                        "skipped": [{}],
                    }
                )
            )
            present = brief("2026-09-17T18", drops=drops)
            self.assertFalse(present["missing"])
            self.assertIn("Ada", present["tell_main"])
            self.assertIn("demeyer1", present["tell_main"])
            day = day_brief(
                "2026-09-17",
                now=datetime.fromisoformat("2026-09-17T20:00:00+00:00"),
                drops=drops,
            )
            self.assertEqual(day["hours_present"], 1)
            self.assertEqual(len(day["comments"]), 1)
            self.assertEqual(len(day["later_use"]), 1)
            self.assertIn("2026-09-17T19", day["missed"])
            self.assertIn("2026-09-17T20", day["remaining"])

    def test_collect_sources_apollo_without_enrich(self):
        seen = []

        def opener(request, timeout=None):
            del timeout
            seen.append((request.full_url, request.data, dict(request.headers)))
            if "api.apollo.io" in request.full_url:
                return FakeResponse(
                    {
                        "people": [
                            {
                                "id": "apollo-ada",
                                "name": "Ada",
                                "title": BUILDER,
                                "linkedin_url": "https://www.linkedin.com/in/ada",
                                "organization": {"name": "Poke"},
                            }
                        ]
                    }
                )
            return self.opener()(request)

        result = collect(
            "2026-09-17T18",
            catalog=self.catalog(),
            opener=opener,
            auth="test-token",
            apollo_auth="apollo-secret",
        )
        apollo_calls = [row for row in seen if "api.apollo.io" in row[0]]
        self.assertFalse(apollo_calls)
        self.assertEqual(result["source"], "apify")
        apollo_runs = [row for row in result["runs"] if row.get("channel") == "apollo"]
        self.assertTrue(apollo_runs)
        self.assertTrue(all(row.get("hold") == "channel_paused" for row in apollo_runs))
        self.assertTrue(all(not row.get("enriches") for row in apollo_runs))
        self.assertFalse(any(item.get("channel") == "apollo" for item in result["items"]))
        self.assertNotIn("apollo-secret", json.dumps(result))

    def test_apollo_http_error_holds_without_killing_the_hour(self):
        def opener(request, timeout=None):
            del timeout
            if "api.apollo.io" in request.full_url:
                raise urllib.error.HTTPError(
                    request.full_url, 422, "Unprocessable", hdrs=None, fp=None
                )
            return self.opener()(request)

        result = collect(
            "2026-09-17T18",
            catalog=self.catalog(),
            opener=opener,
            auth="test-token",
            apollo_auth="apollo-secret",
        )
        apollo_runs = [row for row in result["runs"] if row.get("channel") == "apollo"]
        self.assertTrue(apollo_runs)
        self.assertTrue(all(row.get("hold") == "channel_paused" for row in apollo_runs))
        self.assertEqual(result["source"], "apify")
        self.assertNotIn("apollo-secret", json.dumps(result))

    def test_ready_checks_apollo_health_without_search(self):
        seen = []

        def opener(request, timeout=None):
            del timeout
            seen.append(request.full_url)
            if "api.apollo.io" in request.full_url:
                return FakeResponse({"healthy": True})
            return FakeResponse({"data": {"username": "darwinso", "plan": {"id": "SCALE"}}})

        def shared():
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE people (person_id TEXT)")
            conn.execute("INSERT INTO people VALUES ('p1')")
            return conn

        with patch("crm.human_discovery_search.apollo_token", return_value="apollo-secret"):
            result = ready(opener=opener, persist=False, shared_factory=shared)
        self.assertFalse(any(APOLLO_HEALTH in url for url in seen))
        self.assertFalse(any("mixed_people/search" in url for url in seen))
        self.assertFalse(any("bulk_match" in url for url in seen))
        self.assertFalse(any("/acts/" in url for url in seen))
        self.assertEqual(result["apollo"]["hold"], "channel_paused")
        self.assertFalse(result["apollo"]["verified"])
        self.assertFalse(result["apollo"]["enriches"])
        self.assertNotIn("apollo-secret", json.dumps(result))

    def test_run_keeps_apollo_people_in_crm_not_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "crm.sqlite3"
            initialize(db).close()

            def shared():
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                return conn

            def opener(request, timeout=None):
                del timeout
                if "api.apollo.io" in request.full_url:
                    return FakeResponse(
                        {
                            "people": [
                                {
                                    "id": "apollo-ada",
                                    "name": "Ada",
                                    "title": BUILDER,
                                    "linkedin_url": "https://www.linkedin.com/in/ada",
                                    "organization": {"name": "Poke"},
                                }
                            ]
                        }
                    )
                return self.opener()(request)

            result = run(
                "2026-09-17T18",
                catalog=self.catalog(),
                drops=root / "drops",
                handoff=root / "handoff",
                pending=root / "pending",
                opener=opener,
                auth="test-token",
                apollo_auth="apollo-secret",
                ingest_later=True,
                record_kwargs={
                    "shared_factory": shared,
                    "locations": ReplyLocations(Watcher(root / "watch.sqlite3")),
                    "history": CRMHistory(db, synthetic=True),
                },
            )
            self.assertFalse(any(row["channel"] == "apollo" for row in result["comments"]))
            self.assertFalse(any(row["name"] == "Ada" for row in result["later_use"]))
            self.assertNotIn("apollo-secret", json.dumps(result))
            with shared() as conn:
                people = {row["full_name"] for row in conn.execute("SELECT full_name FROM people")}
                self.assertNotIn("Ada", people)


if __name__ == "__main__":
    unittest.main()
