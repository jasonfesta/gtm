import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.human_inbox import GmailReader, Watcher, digest, gmail_message, write_run_markdown


class Reader:
    def __init__(self, messages, identity=None):
        self.messages = messages
        self.expected = identity or {"id": "me"}
        self.fail = False
        self.after_identity = None
        self.identities = 0
        self.partial = False
        self.incomplete = False

    def identity(self):
        self.identities += 1
        return self.after_identity if self.after_identity and self.identities > 1 else self.expected

    def page(self, cursor, limit):
        if self.fail:
            raise TimeoutError("secret credentials must not be logged")
        return dict(
            messages=self.messages,
            cursor=cursor + 1,
            done=not self.partial,
            readback_complete=not self.incomplete,
        )


def message(**changes):
    return dict(
        dict(
            channel="x",
            account="operator",
            provider_id="m1",
            conversation="thread",
            sender="them",
            recipient="me",
            direction="inbound",
            kind="dm",
            occurred_at="2026-09-10T13:00:00Z",
            body="does this work with python?",
            classification="unknown",
            evidence="private-readback-1",
        ),
        **changes,
    )


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "watch.sqlite3"
        self.w = Watcher(self.path)
        self.stream = self.w.configure("x", "operator", {"id": "me"}, "x-dm", 0)
        self.msg = message()
        self.event = digest(["x", "operator", "m1"])

    def tearDown(self):
        self.temp.cleanup()

    def bind(self):
        self.w.associate(
            "x",
            "operator",
            "thread",
            "them",
            "p1",
            "verified exact identity",
            verify_person=lambda p: p == "p1",
        )

    def ingest(self):
        return self.w.poll(self.stream, Reader([self.msg]))

    def human(self):
        self.bind()
        self.ingest()
        self.w.review(self.event, "human", "reviewer", "actual question reviewed")

    def shared_factory(self):
        shared_path = self.root / "shared.sqlite3"
        if not shared_path.exists():
            with sqlite3.connect(shared_path) as connection:
                connection.executescript(
                    (Path(__file__).resolve().parents[1] / "sql/schema.sql").read_text()
                )

        def factory():
            connection = sqlite3.connect(shared_path)
            connection.row_factory = sqlite3.Row
            return connection

        return factory

    def test_atomic_restart_and_duplicate(self):
        self.assertEqual(self.ingest()["status"], "complete")
        self.w = Watcher(self.path)
        self.ingest()
        with self.w.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
            self.assertEqual(json.loads(c.execute("SELECT cursor FROM streams").fetchone()[0]), 2)
        self.assertEqual(len(self.w.pending("queue")), 1)

    def test_changed_same_message_rolls_back_entire_page(self):
        self.ingest()
        result = self.w.poll(
            self.stream, Reader([message(provider_id="m2"), message(body="edited")])
        )
        self.assertEqual(result["status"], "uncertain")
        with self.w.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT cursor FROM streams").fetchone()[0], "1")

    def test_wrong_account_and_switching_account(self):
        for reader in (Reader([self.msg], {"id": "other"}), Reader([self.msg])):
            if reader.expected == {"id": "me"}:
                reader.after_identity = {"id": "other"}
            self.assertEqual(self.w.poll(self.stream, reader)["status"], "uncertain")
        with self.w.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    def test_message_account_and_recipient_rejected(self):
        for changes in (
            {"account": "wrong"},
            {"recipient": "wrong"},
            {"sender": "me"},
            {"direction": "outbound"},
        ):
            self.assertEqual(
                self.w.poll(self.stream, Reader([message(**changes)]))["status"], "uncertain"
            )

    def test_timeout_cursor_retry_and_error_redaction(self):
        reader = Reader([self.msg])
        reader.fail = True
        self.w.poll(self.stream, reader)
        with self.w.connection() as c:
            row = c.execute("SELECT * FROM streams").fetchone()
            self.assertEqual(row["cursor"], "0")
            self.assertNotIn("secret", row["error"])
        reader.fail = False
        self.assertEqual(self.w.poll(self.stream, reader)["status"], "complete")

    def test_partial_and_incomplete_reads_hold(self):
        reader = Reader([self.msg])
        reader.partial = True
        self.assertEqual(self.w.poll(self.stream, reader, max_pages=1)["status"], "partial")
        self.assertFalse(self.w.gate("p1", [self.stream], max_age_seconds=900)["allowed"])
        reader.incomplete = True
        self.assertEqual(self.w.poll(self.stream, reader)["status"], "uncertain")
        with self.w.connection() as c:
            self.assertEqual(c.execute("SELECT cursor FROM streams").fetchone()[0], "1")

    def test_concurrent_poll_does_not_overwrite_cursor(self):
        outer = self

        class RacingReader(Reader):
            def page(self, cursor, limit):
                outer.ingest()
                return super().page(cursor, limit)

        self.assertEqual(self.w.poll(self.stream, RacingReader([self.msg]))["status"], "uncertain")
        with self.w.connection() as c:
            self.assertEqual(c.execute("SELECT cursor FROM streams").fetchone()[0], "1")

    def test_review_signal_ack_restart_and_no_automatic_restart(self):
        self.human()
        events = self.w.pending("queue")
        for event in events:
            self.w.acknowledge("queue", event["signal_id"])
            self.w.acknowledge("queue", event["signal_id"])
        self.assertEqual(self.w.pending("queue"), [])
        self.assertEqual(len(self.w.pending("other")), 2)
        self.w = Watcher(self.path)
        self.w.review(self.event, "automated", "reviewer", "reclassification")
        self.assertIn(
            "stop_cold_sequence", self.w.gate("p1", [self.stream], max_age_seconds=900)["reasons"]
        )

    def test_unknown_is_not_human_and_automated_can_resolve_hold(self):
        self.bind()
        self.ingest()
        self.assertIn(
            "incoming_review_required",
            self.w.gate("p1", [self.stream], max_age_seconds=900)["reasons"],
        )
        self.w.review(self.event, "automated", "reviewer", "newsletter")
        self.assertTrue(self.w.gate("p1", [self.stream], max_age_seconds=900)["allowed"])
        self.assertFalse(any(x["kind"] == "stop_cold_sequence" for x in self.w.pending("queue")))

    def test_late_association_and_conflict(self):
        self.ingest()
        self.w.review(self.event, "human", "reviewer", "actual reply")
        self.bind()
        self.assertIn(
            "stop_cold_sequence", self.w.gate("p1", [self.stream], max_age_seconds=900)["reasons"]
        )
        with self.assertRaises(ValueError):
            self.w.associate(
                "x", "operator", "thread", "them", "p2", "different", verify_person=lambda p: True
            )

    def test_optout_prevents_draft_even_after_new_human_review(self):
        self.human()
        self.w.review(self.event, "opt_out", "reviewer", "please stop")
        self.w.review(self.event, "human", "reviewer", "later review")
        with self.assertRaises(ValueError):
            self.w.reply_context(self.event)
        self.assertIn("suppress", self.w.gate("p1", [self.stream], max_age_seconds=900)["reasons"])

    def test_opt_in_followup_requires_reviewed_request_and_three_quiet_days(self):
        self.human()
        receipt = {
            "provider_id": "dm-info-1",
            "confirmed_at": "2026-09-10T14:00:00Z",
            "account": "operator",
            "author_handle": "operator",
            "recipient_handle": "them",
            "body": "here is the info you asked for",
            "evidence": "exact X post-send readback",
        }
        with self.assertRaisesRegex(ValueError, "explicit reviewed opt-in"):
            self.w.register_x_info_delivery(
                self.event,
                explicit_opt_in=False,
                reviewer="reviewer",
                opt_in_evidence="they asked to try it",
                receipt=receipt,
            )
        delivery = self.w.register_x_info_delivery(
            self.event,
            explicit_opt_in=True,
            reviewer="reviewer",
            opt_in_evidence="exact message says they want to try it",
            receipt=receipt,
        )
        factory = self.shared_factory()
        self.assertEqual(
            self.w.due_x_opt_in_followups(as_of="2026-09-13T13:59:59Z", shared_factory=factory),
            [],
        )
        due = self.w.due_x_opt_in_followups(as_of="2026-09-13T14:00:00Z", shared_factory=factory)
        self.assertEqual([item["delivery_id"] for item in due], [delivery])
        followup = self.w.reserve_x_opt_in_followup(
            delivery,
            "did you get a chance to try it?",
            as_of="2026-09-13T14:00:00Z",
            shared_factory=factory,
        )
        with self.w.connection() as connection:
            payload = json.loads(
                connection.execute(
                    "SELECT payload FROM x_opt_in_followups WHERE followup_id=?", (followup,)
                ).fetchone()[0]
            )
        self.w.approve_x_opt_in_followup(
            followup, approver="operator", content_sha256=digest(payload)
        )
        followup_receipt = {
            "provider_id": "dm-followup-1",
            "confirmed_at": "2026-09-13T14:01:00Z",
            "account": "operator",
            "author_handle": "operator",
            "recipient_handle": "them",
            "body": payload["text"],
            "evidence": "exact X follow-up readback",
        }
        receipt_id = self.w.confirm_x_opt_in_followup(followup, followup_receipt)
        self.assertEqual(receipt_id, self.w.confirm_x_opt_in_followup(followup, followup_receipt))
        self.assertEqual(
            self.w.due_x_opt_in_followups(as_of="2026-09-20T14:00:00Z", shared_factory=factory),
            [],
        )

    def test_later_inbound_response_cancels_opt_in_followup(self):
        self.human()
        self.w.register_x_info_delivery(
            self.event,
            explicit_opt_in=True,
            reviewer="reviewer",
            opt_in_evidence="exact message says interested",
            receipt={
                "provider_id": "dm-info-1",
                "confirmed_at": "2026-09-10T14:00:00Z",
                "account": "operator",
                "author_handle": "operator",
                "recipient_handle": "them",
                "body": "here is the info you asked for",
                "evidence": "exact X post-send readback",
            },
        )
        later = message(
            provider_id="m2",
            occurred_at="2026-09-11T14:00:00Z",
            body="thanks, i will take a look",
        )
        self.w.poll(self.stream, Reader([later]))
        self.assertEqual(
            self.w.due_x_opt_in_followups(
                as_of="2026-09-14T14:00:00Z", shared_factory=self.shared_factory()
            ),
            [],
        )

    def test_bounce_holds_but_does_not_count_human(self):
        self.bind()
        self.msg = message(classification="bounce")
        self.ingest()
        self.assertEqual(self.w.pending("queue")[0]["kind"], "hold_delivery")
        self.assertFalse(self.w.gate("p1", [self.stream], max_age_seconds=900)["allowed"])

    def test_public_thread_all_responders_and_unowned_root(self):
        self.msg = message(kind="public_reply", root_id="our-root", sender="new-person")
        self.assertEqual(self.ingest()["status"], "uncertain")
        self.w.register_thread("x", "operator", "our-root", "root author me readback")
        self.assertEqual(self.ingest()["status"], "complete")
        self.w.review(self.event, "human", "reviewer", "reply from another participant")
        self.assertIn(
            "unassociated_incoming",
            self.w.gate("p1", [self.stream], max_age_seconds=900)["reasons"],
        )

    def test_linkedin_public_reply_supported(self):
        stream = self.w.configure("linkedin", "operator", {"id": "me"}, "own-posts", 0)
        self.w.register_thread("linkedin", "operator", "root", "author readback")
        result = self.w.poll(
            stream, Reader([message(channel="linkedin", kind="public_reply", root_id="root")])
        )
        self.assertEqual(result["status"], "complete")

    def test_like_stops_sequence_without_inventing_conversation(self):
        self.bind()
        self.w.register_thread("x", "operator", "root", "author readback")
        self.msg = message(kind="reaction", body="", root_id="root")
        self.ingest()
        self.w.review(self.event, "engagement", "reviewer", "attributable like")
        with self.assertRaises(ValueError):
            self.w.reply_context(self.event)
        self.assertIn(
            "stop_cold_sequence", self.w.gate("p1", [self.stream], max_age_seconds=900)["reasons"]
        )

    def test_freshness_missing_and_future(self):
        self.assertFalse(self.w.gate("p1", ["missing"], max_age_seconds=900)["allowed"])
        self.w.poll(self.stream, Reader([]))
        with patch("crm.human_inbox.now", return_value="2020-01-01T00:00:00Z"):
            self.assertFalse(self.w.gate("p1", [self.stream], max_age_seconds=900)["allowed"])

    def test_local_copy_actual_context_hash_revisions_and_limits(self):
        self.human()
        ctx = self.w.reply_context(self.event)
        alternatives = {
            "a": "which python version are you using?",
            "b": "what version of python is this for?",
        }
        first = self.w.save_draft(
            self.event,
            alternatives,
            ctx["context_sha256"],
            "reviewer",
            "actual question; no unsupported claim",
        )
        self.assertEqual(
            first,
            self.w.save_draft(
                self.event,
                alternatives,
                ctx["context_sha256"],
                "reviewer",
                "actual question; no unsupported claim",
            ),
        )
        with self.assertRaises(ValueError):
            self.w.save_draft(self.event, alternatives, "stale", "reviewer", "checked")
        with self.assertRaises(ValueError):
            self.w.save_draft(
                self.event,
                {"a": "word " * 36, "b": "short"},
                ctx["context_sha256"],
                "reviewer",
                "checked",
            )
        with self.w.connection() as c:
            payload = json.loads(c.execute("SELECT payload FROM drafts").fetchone()[0])
            self.assertFalse(payload["send_authorized"])
            self.assertEqual(payload["context"]["incoming"]["body"], self.msg["body"])

    def test_crm_intake_match_insert_replay_private_content(self):
        self.ingest()
        self.w.review(self.event, "human", "reviewer", "actual reply")
        db = self.root / "synthetic.sqlite3"
        c = sqlite3.connect(db)
        c.executescript((Path(__file__).resolve().parents[1] / "sql/schema.sql").read_text())
        c.close()

        def factory():
            c = sqlite3.connect(db)
            c.row_factory = sqlite3.Row
            return c

        kwargs = dict(
            full_name="Synthetic Person",
            contact_value="https://x.com/them",
            public_source_url="https://example.org/team",
            reviewer="reviewer",
            identity_evidence="public identity checked",
            ai_relevance=dict(
                status="confirmed",
                reason="Builds AI tools",
                task_evidence="synthetic task receipt",
                source_url="https://example.org/team",
                evidence="synthetic public review",
            ),
            shared_factory=factory,
        )
        with self.assertRaisesRegex(ValueError, "AI relevance"):
            self.w.ensure_person(self.event, **{**kwargs, "ai_relevance": None})
        with self.w.connection() as local:
            local.execute("DELETE FROM crm_intents WHERE event_id=?", (self.event,))
        person = self.w.ensure_person(self.event, **kwargs)
        self.assertEqual(person, self.w.ensure_person(self.event, **kwargs))
        with factory() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            row = c.execute("SELECT * FROM people").fetchone()
            self.assertIsNotNone(
                c.execute("SELECT last_verified_at FROM contact_points").fetchone()[0]
            )
            self.assertIsNone(row["current_role"])
            self.assertIsNone(row["primary_company_id"])
            self.assertNotIn(self.msg["body"], str(dict(row)))
            self.assertEqual(
                c.execute("SELECT value FROM contact_points").fetchone()[0], "https://x.com/them"
            )
        self.assertEqual(self.w.reply_context(self.event)["person_id"], person)

    def test_markdown_run_and_draft_exports_are_immutable(self):
        self.human()
        context = self.w.reply_context(self.event)
        key = self.w.save_draft(
            self.event,
            {"a": "thanks for the context.", "b": "that helps, thanks."},
            context["context_sha256"],
            "reviewer",
            "context checked",
        )
        path = self.w.export_draft(key, self.root / "drafts")
        self.assertIn(self.msg["body"], path.read_text())
        self.assertEqual(path, self.w.export_draft(key, self.root / "drafts"))
        receipt = dict(
            run_id="run1",
            observed_at="2026-09-10T13:00:00Z",
            surfaces={},
            crm="held",
            drafts="local only",
            next_action="resolve read",
        )
        report = write_run_markdown(receipt, self.root / "runs")
        self.assertIn("not checked", report.read_text())
        self.assertEqual(report.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            write_run_markdown({**receipt, "crm": "changed"}, self.root / "runs")

    def test_shared_commit_timeout_reconciles_exact_person_without_duplicate(self):
        self.ingest()
        self.w.review(self.event, "human", "reviewer", "actual reply")
        db = self.root / "shared-fixture.sqlite3"
        with sqlite3.connect(db) as c:
            c.executescript((Path(__file__).resolve().parents[1] / "sql/schema.sql").read_text())

        class AmbiguousCommit(sqlite3.Connection):
            def __exit__(self, *args):
                result = super().__exit__(*args)
                if args[0] is None:
                    raise RuntimeError("shared commit response lost")
                return result

        def factory(ambiguous=False):
            c = sqlite3.connect(db, factory=AmbiguousCommit if ambiguous else sqlite3.Connection)
            c.row_factory = sqlite3.Row
            return c

        kwargs = dict(
            full_name="Synthetic",
            contact_value="https://x.com/them",
            public_source_url="https://example.org/team",
            reviewer="reviewer",
            identity_evidence="verified",
            ai_relevance=dict(
                status="confirmed",
                reason="Builds AI tools",
                task_evidence="synthetic task receipt",
                source_url="https://example.org/team",
                evidence="synthetic public review",
            ),
        )
        with self.assertRaises(RuntimeError):
            self.w.ensure_person(self.event, **kwargs, shared_factory=lambda: factory(True))
        person = self.w.ensure_person(self.event, **kwargs, shared_factory=factory)
        with factory() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM people").fetchone()[0], 1)
        self.assertEqual(self.w.reply_context(self.event)["person_id"], person)

    def test_crm_unavailable_no_fallback_no_automatic_retry(self):
        self.ingest()
        self.w.review(self.event, "human", "reviewer", "actual reply")
        with patch("crm.database.configured", return_value=None), self.assertRaises(RuntimeError):
            self.w.ensure_person(
                self.event,
                full_name="name",
                contact_value="https://x.com/them",
                public_source_url="https://example.org/team",
                reviewer="reviewer",
                identity_evidence="checked",
            )
        with self.w.connection() as c:
            self.assertEqual(c.execute("SELECT state FROM crm_intents").fetchone()[0], "pending")


class GmailTests(unittest.TestCase):
    def row(self):
        return dict(
            id="g1",
            thread_id="t1",
            internal_date="1789042785000",
            label_ids=["INBOX"],
            payload=dict(
                mime_type="text/plain",
                headers=[
                    dict(name="From", value="Person <person@example.org>"),
                    dict(name="To", value="me@example.org"),
                    dict(name="In-Reply-To", value="<original@example.org>"),
                ],
                body=dict(content="a real response"),
            ),
        )

    def test_normalize_unknown_preserves_rfc_and_plain_text(self):
        result = gmail_message(self.row(), "operator", "me@example.org")
        self.assertEqual(result["classification"], "unknown")
        self.assertEqual(result["headers"]["in-reply-to"], ["<original@example.org>"])
        self.assertEqual(result["body"], "a real response")

    def test_automated_and_dsn_classification(self):
        row = self.row()
        row["payload"]["headers"].append(dict(name="Auto-Submitted", value="auto-replied"))
        self.assertEqual(
            gmail_message(row, "operator", "me@example.org")["classification"], "automated"
        )
        row["payload"]["parts"] = [dict(mime_type="message/delivery-status")]
        self.assertEqual(
            gmail_message(row, "operator", "me@example.org")["classification"], "bounce"
        )

    def test_wrong_mailbox_draft_forwarded_and_html(self):
        row = self.row()
        with self.assertRaises(ValueError):
            gmail_message(row, "operator", "other@example.org")
        row["label_ids"].append("DRAFT")
        with self.assertRaises(ValueError):
            gmail_message(row, "operator", "me@example.org")
        row = self.row()
        row["payload"]["mime_type"] = "text/html"
        row["payload"]["parts"] = [
            dict(mime_type="message/rfc822", parts=[copy.deepcopy(self.row()["payload"])])
        ]
        self.assertEqual(gmail_message(row, "operator", "me@example.org")["body"], "")

    def test_frozen_query_cursor_restart_and_readback_missing(self):
        queries = []

        def search(**kwargs):
            queries.append(kwargs)
            return dict(message_ids=["g1"], next_page_token="next")

        reader = GmailReader(
            "operator",
            lambda: dict(id="id", email="me@example.org"),
            search,
            lambda **kwargs: dict(responses=[self.row()]),
            "from:person@example.org",
        )
        cursor = dict(after=100, before=200, token=None)
        page = reader.page(cursor, 10)
        reader.page(page["cursor"], 10)
        self.assertEqual(queries[0]["query"], queries[1]["query"])
        self.assertEqual(queries[1]["next_page_token"], "next")
        self.assertFalse(page["done"])
        reader.read = lambda **kwargs: dict(responses=[])
        with self.assertRaises(ValueError):
            reader.page(cursor, 10)

    def test_complete_moves_watermark_with_overlap(self):
        reader = GmailReader(
            "operator",
            lambda: dict(id="id", email="me@example.org"),
            lambda **kwargs: dict(message_ids=[]),
            lambda **kwargs: {},
            "subject:professional",
        )
        page = reader.page(dict(after=100, before=200), 10)
        self.assertTrue(page["done"])
        self.assertEqual(page["cursor"], dict(after=200, before=None, token=None))


if __name__ == "__main__":
    unittest.main()
