import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm import database
from crm.human_reply_planning import (
    QUALIFICATION_GATES,
    SCORE_ANCHORS,
    SEARCH_CATALOG,
    TOPICS,
    Watcher,
    readiness,
)

AT = "2026-09-10T19:00:00+00:00"


def capture(channel="x"):
    profile = (
        "https://x.com/SyntheticDev"
        if channel == "x"
        else "https://www.linkedin.com/in/synthetic-dev/"
    )
    url = (
        "https://x.com/SyntheticDev/status/123"
        if channel == "x"
        else "https://www.linkedin.com/feed/update/urn:li:activity:123/"
    )
    return {
        "route": "manual_capture",
        "binding": {
            "account": "synthetic-operator",
            "user_id": "999",
            "method": "manual_self_observation",
            "evidence": "synthetic fixture only",
            "observed_at": AT,
        },
        "posts": [
            {
                "channel": channel,
                "id": "123",
                "author_id": "456",
                "handle": "SyntheticDev",
                "url": url,
                "profile_url": profile,
                "text": "AI agent discovery needs useful context.",
                "context_complete": True,
                "available": True,
                "created_at": "2026-09-10T18:57:00+00:00",
                "observed_at": AT,
            }
        ],
    }


class TimelineTests(unittest.TestCase):
    def test_expanded_platform_catalog_and_matcher(self):
        for channel in ("x", "linkedin"):
            terms = [entry[channel].casefold() for entry in SEARCH_CATALOG["entries"]]
            self.assertGreaterEqual(len(set(terms)), 50)
            self.assertEqual(len(terms), len(set(terms)))
        for text in ("Hermes Studio deployment", "Built with Mastra", "Pipecat integration"):
            self.assertIsNotNone(TOPICS.search(text))
        for text in ("Hermes handbag", "steel beams", "modal window", "mastracorp"):
            self.assertIsNone(TOPICS.search(text))
        cap = capture()
        cap["posts"][0]["text"] = "Built with Hermes Studio"
        cap["posts"][0]["author_id"] = None
        outcome = self.w.ingest(cap, "synthetic-operator", AT)["outcomes"][0]
        self.assertEqual(outcome["disposition"], "held")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.sqlite3"
        self.w = Watcher(self.path)

    def tearDown(self):
        self.w.close()
        self.tmp.cleanup()

    def reviewed(self, channel="x"):
        cap = capture(channel)
        result = self.w.ingest(cap, "synthetic-operator", AT)
        review = {
            "account": "synthetic-operator",
            "author_id": "456",
            "observation_hash": result["outcomes"][0]["observation_hash"],
            "identity_status": "verified",
            "is_person": True,
            "conflicts": [],
            "full_name": "Synthetic Developer",
            "relevance_reason": "Builds AI discovery",
            "channels": {
                "x": "supported_association",
                "linkedin": "supported_association",
                "email": "not_found_in_checked_sources",
            },
            "identity_proof": {
                "kind": "official_self_link",
                "source_url": "https://example.test/team",
                "profile_url": cap["posts"][0]["profile_url"],
                "name": "Synthetic Developer",
                "text": "Synthetic Developer builds AI discovery.",
                "links": [cap["posts"][0]["profile_url"]],
                "observed_at": AT,
            },
        }
        review["qualification"] = {
            "policy": "social-discovery-v3",
            "reviewer": "synthetic reviewer",
            "reviewed_at": AT,
            "evidence": {
                "fixture": {
                    "url": "https://example.test/evidence",
                    "text": "Synthetic test evidence",
                }
            },
            "gates": {
                g: {
                    "status": "pass",
                    "reason": "synthetic reviewed scope",
                    "evidence_ids": ["fixture"],
                }
                for g in QUALIFICATION_GATES
            },
            "scores": {
                k: {"value": max(v), "reason": "synthetic anchor", "evidence_ids": ["fixture"]}
                for k, v in SCORE_ANCHORS.items()
            },
            "authored_sample": [
                {
                    "url": cap["posts"][0]["url"] if i == 0 else f"https://example.test/post/{i}",
                    "created_at": f"2026-09-0{7 + i}T19:00:00+00:00",
                    "author_id": "456",
                    "substantive": True,
                    "relevant": True,
                    "reason": "synthetic authored work",
                    "evidence_ids": ["fixture"],
                }
                for i in range(3)
            ],
            "hydration": {
                "workflow": "hydrate/SOCIAL_DISCOVERY.md",
                "receipt": "synthetic hydration",
                "channel_outcomes": review["channels"],
            },
        }
        return channel + ":123", review

    def test_restart_and_channel_dedup(self):
        for channel in ("x", "linkedin"):
            self.reviewed(channel)
        self.w.close()
        self.w = Watcher(self.path)
        r = self.w.ingest(capture(), "synthetic-operator", AT)
        self.assertTrue(r["outcomes"][0]["duplicate"])
        self.assertEqual(self.w.db.execute("SELECT count(*) FROM posts").fetchone()[0], 2)

    def test_wrong_account_and_browser_route(self):
        with self.assertRaises(ValueError):
            self.w.ingest(capture(), "wrong-account", AT)
        c = capture()
        c["route"] = "browser_automation"
        with self.assertRaises(ValueError):
            self.w.ingest(c, "synthetic-operator", AT)

    def test_old_future_missing_context_held(self):
        for changes in (
            {"created_at": "2026-09-10T18:44:00+00:00"},
            {"created_at": "2026-09-10T19:01:00+00:00"},
            {"context_complete": False},
            {"available": False},
        ):
            c = capture()
            c["posts"][0].update(changes)
            r = self.w.ingest(c, "synthetic-operator", AT)
            self.assertEqual(r["outcomes"][0]["disposition"], "held")

    def test_identity_and_account_fail_closed(self):
        key, r = self.reviewed()
        for field, value in [
            ("identity_status", "guessed"),
            ("account", "wrong"),
            ("is_person", False),
            ("conflicts", ["namesake"]),
        ]:
            changed = {**r, field: value}
            with self.assertRaises(ValueError):
                self.w.qualify(key, changed, AT)
        r["identity_proof"]["links"] = []
        with self.assertRaises(ValueError):
            self.w.qualify(key, r, AT)

    def test_exact_approval_expiry_and_changed_context(self):
        key, r = self.reviewed()
        d = self.w.prepare(
            key,
            r,
            {"a": "context makes discovery useful.", "b": "discovery needs that context."},
            "grounded in actual post",
            AT,
        )
        approval = {
            "actor": "human",
            "decision_reference": "synthetic-user-message",
            "account": r["account"],
            "target_url": d["target_url"],
            "target_post_id": key,
            "exact_text": d["variants"]["a"],
            "decision": "approved",
            "decided_at": AT,
        }
        for changes in ({"exact_text": "edited"}, {"target_url": "wrong"}, {"actor": "agent"}):
            with self.assertRaises(ValueError):
                self.w.record_approval(d["draft_id"], {**approval, **changes}, AT)
        self.assertFalse(self.w.record_approval(d["draft_id"], approval, AT)["send_capable"])
        with self.assertRaises(ValueError):
            self.w.record_approval(d["draft_id"], approval, "2026-09-10T19:16:00+00:00")
        c = capture()
        c["posts"][0]["text"] = "AI agents need something different now."
        self.w.ingest(c, "synthetic-operator", AT)
        with self.assertRaises(ValueError):
            self.w.record_approval(d["draft_id"], approval, AT)

    def test_both_channels_crm_idempotence(self):
        for channel in ("x", "linkedin"):
            c = database.schema_connection()
            key, r = self.reviewed(channel)
            pid = self.w.import_lead(key, r, AT, fixture_connection=c)
            self.assertEqual(self.w.import_lead(key, r, AT, fixture_connection=c), pid)
            self.assertEqual(c.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            self.assertEqual(
                c.execute("SELECT value FROM contact_points").fetchone()[0],
                capture(channel)["posts"][0]["profile_url"],
            )
            self.assertEqual(c.execute("SELECT count(*) FROM sources").fetchone()[0], 2)
            c.close()

    def test_no_fallback_or_automatic_replay(self):
        key, r = self.reviewed()
        with patch("crm.human_reply_planning.database.configured", return_value=None):
            with self.assertRaises(RuntimeError):
                self.w.import_lead(key, r, AT)
        c = database.schema_connection()
        with patch("crm.human_reply_planning.upsert_lead", side_effect=RuntimeError("timeout")):
            with self.assertRaises(RuntimeError):
                self.w.import_lead(key, r, AT, fixture_connection=c)
        with self.assertRaises(RuntimeError):
            self.w.import_lead(key, r, AT, fixture_connection=c)
        c.close()

    def test_links_held_and_readiness_off(self):
        key, r = self.reviewed()
        with self.assertRaises(ValueError):
            self.w.prepare(
                key, r, {"a": "try https://example.test", "b": "another"}, "reviewed", AT
            )
        self.assertTrue(readiness()["ready"])
        self.assertFalse(readiness()["send_capable"])

    def test_linkedin_draft_only_end_to_end_without_import_or_approval(self):
        key, review = self.reviewed("linkedin")
        draft = self.w.prepare(
            key,
            review,
            {
                "a": "what context helps rank the agents?",
                "b": "how do you distinguish agents with overlapping capabilities?",
            },
            "Synthetic rehearsal: questions address the actual fixture topic. No product pitch or invented experience.",
            AT,
        )
        self.assertFalse(draft["send_capable"])
        self.assertEqual(self.w.db.execute("SELECT count(*) FROM imports").fetchone()[0], 0)
        self.assertEqual(self.w.db.execute("SELECT count(*) FROM approvals").fetchone()[0], 0)
        self.assertEqual(self.w.db.execute("SELECT count(*) FROM drafts").fetchone()[0], 1)
        self.w.close()
        self.w = Watcher(self.path)
        self.assertEqual(
            self.w.db.execute("SELECT id FROM drafts").fetchone()[0], draft["draft_id"]
        )
        self.assertTrue(Path(draft["markdown"]).is_file())

    def test_browser_receipts_and_old_research(self):
        c = capture()
        c["route"] = "browser_observation"
        c["binding"]["method"] = "browser_self_observation"
        c["binding"]["user_id"] = None
        with self.assertRaises(ValueError):
            self.w.ingest(c, "synthetic-operator", AT)
        c["source"] = {
            "url": "https://pro.x.com/i/decks/123",
            "sort": "Latest",
            "query_or_column": "OpenClaw",
            "evidence": "synthetic UI receipt",
        }
        c["posts"][0]["created_at"] = "2026-09-09T18:00:00+00:00"
        r = self.w.ingest(c, "synthetic-operator", AT)
        self.assertEqual(r["outcomes"][0]["disposition"], "needs_identity_review")
        self.w.post("x:123", AT, research=True)
        with self.assertRaises(ValueError):
            self.w.post("x:123", AT)
        c["posts"][0]["author_id"] = None
        r = self.w.ingest(c, "synthetic-operator", AT)
        self.assertEqual(r["outcomes"][0]["disposition"], "held")

    def test_high_score_cannot_override_screening_or_missing_evidence(self):
        import copy

        key, review = self.reviewed()
        for gate in QUALIFICATION_GATES:
            for state in ("hold", "exclude", None):
                r = copy.deepcopy(review)
                r["qualification"]["gates"][gate]["status"] = state
                with self.assertRaises(ValueError):
                    self.w.qualify(key, r, AT)
        r = copy.deepcopy(review)
        r["qualification"]["scores"]["identity"]["value"] = 10
        with self.assertRaises(ValueError):
            self.w.qualify(key, r, AT)
        r = copy.deepcopy(review)
        r["qualification"]["evidence"] = {}
        with self.assertRaises(ValueError):
            self.w.qualify(key, r, AT)
        r = copy.deepcopy(review)
        r["qualification"]["authored_sample"] = r["qualification"]["authored_sample"][:2]
        with self.assertRaises(ValueError):
            self.w.qualify(key, r, AT)

    def test_expired_reply_can_still_import_research(self):
        key, r = self.reviewed()
        later = "2026-09-10T19:30:00+00:00"
        c = database.schema_connection()
        self.assertTrue(self.w.import_lead(key, r, later, fixture_connection=c))
        with self.assertRaises(ValueError):
            self.w.prepare(key, r, {"a": "a", "b": "b"}, "reason", later)
        c.close()

    def test_connection_failure_stays_uncertain(self):
        key, r = self.reviewed()
        with (
            patch("crm.human_reply_planning.database.configured", return_value=True),
            patch(
                "crm.human_reply_planning.database.connect",
                side_effect=RuntimeError("connection failed"),
            ),
        ):
            with self.assertRaises(RuntimeError):
                self.w.import_lead(key, r, AT)
        self.assertEqual(self.w.db.execute("SELECT state FROM imports").fetchone()[0], "uncertain")

    def test_expanded_developer_discovery_terms_are_only_match_signals(self):
        from crm.human_reply_planning import TOPICS

        for text in (
            "assistant developers",
            "assistant devs",
            "assistant engineers",
            "coding assistants",
            "voice assistant",
            "LLM engineers",
            "LLM applications",
            "Model Context Protocol",
            "function calling",
            "open-source assistant",
            "AI developers",
            "agentic builders",
        ):
            self.assertIsNotNone(TOPICS.search(text), text)
        for text in (
            "administrative assistant jobs",
            "travel agency",
            "executive assistant",
            "property agent commissions",
        ):
            # Generic agent remains a discovery match and must be rejected by qualification.
            if "agent " not in text:
                self.assertIsNone(TOPICS.search(text), text)
