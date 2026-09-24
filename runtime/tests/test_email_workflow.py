import tempfile
import unittest
from pathlib import Path

from crm.cli import initialize, seed_pilot, utc_now
from crm.copy_builder import CopyBuilder
from crm.email_workflow import EmailWorkflow, validate_body_links


class EmailWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"
        c = initialize(self.db)
        seed_pilot(c)
        c.execute(
            "INSERT INTO people(person_id,primary_company_id,full_name,normalized_name,identity_status) "
            "VALUES('person','cmp_orchid','Test Person','test person','single_source')"
        )
        source = c.execute("SELECT source_id FROM sources LIMIT 1").fetchone()[0]
        c.execute(
            "INSERT INTO contact_points(contact_id,person_id,contact_type,value,normalized_value,verification_status,source_id,first_seen_at) "
            "VALUES('email','person','email','test@example.com','test@example.com','confirmed',?,?)",
            (source, utc_now()),
        )
        c.commit()
        c.close()
        builder = CopyBuilder(self.db)
        evidence = builder.context("agt_orchid")["evidence"][0]["claim_id"]
        self.copy = builder.save(
            format="email",
            variant="a",
            channel="email",
            agent_id="agt_orchid",
            person_id="person",
            subject="orchid question",
            body="hey — saw orchid works in messages. what are you building next?",
            evidence_ids=[evidence],
            editor="test",
            expected_revision=0,
        )
        self.w = EmailWorkflow(self.db)
        self.w.initialize()
        with self.w.connection() as c:
            c.execute(
                "INSERT INTO email_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "review",
                    "email",
                    "test@example.com",
                    "draft_ok",
                    "https://example.com",
                    "reviewed",
                    utc_now(),
                    "sender@example.com",
                    "in:anywhere to:test@example.com",
                    "no_match",
                    utc_now(),
                    "test",
                ),
            )
        self.args = dict(
            campaign="pilot",
            account="sender@example.com",
            contact_id="email",
            review_id="review",
            draft_id=self.copy["draft_id"],
            revision=1,
            selection_reason="specific question",
            utm_reason="no approved link",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_verified_call_link_and_unapproved_destinations(self):
        builder = CopyBuilder(self.db)
        evidence = builder.context("agt_orchid")["evidence"][0]["claim_id"]
        copy = builder.save(
            format="email",
            variant="a",
            channel="email",
            agent_id="agt_orchid",
            person_id="person",
            subject="orchid question",
            body="hey — saw orchid works in messages. open to a quick call? https://darwin.so/call",
            evidence_ids=[evidence],
            editor="test",
            expected_revision=0,
        )
        prepared = self.w.prepare(
            **dict(
                self.args,
                draft_id=copy["draft_id"],
                utm_reason="verified call destination; untagged",
            )
        )
        self.assertEqual(
            self.w.prepare(
                **dict(
                    self.args,
                    draft_id=copy["draft_id"],
                    utm_reason="verified call destination; untagged",
                )
            )["preparation_id"],
            prepared["preparation_id"],
        )
        for body in (
            "https://darwin.so/call?utm_source=x",
            "https://darwin.so/call/extra",
            "https://darwin.so.evil.test/call",
            "https://darwin.so/call https://darwin.so/call",
            "[darwin.so/call](https://darwin.so/call)",
            '<a href="https://darwin.so/call">call</a>',
            "http://darwin.so/call",
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                validate_body_links(body)
        validate_body_links("open to a chat? https://darwin.so/call")

    def test_idempotent_prepare_and_ambiguous_create_cannot_retry(self):
        p = self.w.prepare(**self.args)
        self.assertEqual(self.w.prepare(**self.args)["preparation_id"], p["preparation_id"])
        self.w.reserve_gmail_create(p["preparation_id"])
        with self.assertRaisesRegex(ValueError, "reconcile"):
            self.w.reserve_gmail_create(p["preparation_id"])
        self.w.record_gmail_draft(p["preparation_id"], "draft", "message", "thread")
        self.w.record_gmail_draft(p["preparation_id"], "draft", "message", "thread")
        self.assertEqual(self.w.audit()["counts"]["outreach_events"], 0)

    def test_suppression_after_preparation_blocks_gmail_create(self):
        p = self.w.prepare(**self.args)
        with self.w.connection() as c:
            c.execute(
                "INSERT INTO suppressions(suppression_id,contact_id,reason,effective_at) VALUES('stop','email','opt_out',?)",
                (utc_now(),),
            )
        with self.assertRaisesRegex(ValueError, "suppressed"):
            self.w.reserve_gmail_create(p["preparation_id"])

    def test_cross_channel_stop_after_preparation_blocks_create(self):
        p = self.w.prepare(**self.args)
        with self.w.connection() as c:
            c.execute(
                "INSERT INTO outreach_events(event_id,person_id,channel,direction,occurred_at,outcome) "
                "VALUES('stop','person','x','inbound',?,'opted_out')",
                (utc_now(),),
            )
        with self.assertRaisesRegex(ValueError, "recipient_stop"):
            self.w.reserve_gmail_create(p["preparation_id"])

    def test_x_opt_out_suppression_blocks_email_without_event(self):
        with self.w.connection() as c:
            c.execute(
                "INSERT INTO suppressions(suppression_id,person_id,channel,reason,effective_at) "
                "VALUES('stop','person','x','opt_out',?)",
                (utc_now(),),
            )
        with self.assertRaisesRegex(ValueError, "suppressed"):
            self.w.prepare(**self.args)

    def test_stale_history_and_address_change_block(self):
        with self.w.connection() as c:
            c.execute("UPDATE email_reviews SET history_checked_at='2000-01-01T00:00:00Z'")
        with self.assertRaisesRegex(ValueError, "fresh"):
            self.w.prepare(**self.args)
        with self.w.connection() as c:
            c.execute("UPDATE email_reviews SET history_checked_at=?", (utc_now(),))
        p = self.w.prepare(**self.args)
        with self.w.connection() as c:
            c.execute("UPDATE contact_points SET value='new@example.com'")
        with self.assertRaisesRegex(ValueError, "address changed"):
            self.w.reserve_gmail_create(p["preparation_id"])

    def test_campaign_cannot_bypass_recipient_reservation(self):
        self.w.prepare(**self.args)
        with self.assertRaisesRegex(ValueError, "another campaign"):
            self.w.prepare(**dict(self.args, campaign="another"))

    def test_wrong_sender_or_copy_cannot_prepare(self):
        with self.assertRaisesRegex(ValueError, "same-account"):
            self.w.prepare(**dict(self.args, account="wrong@example.com"))
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.w.prepare(**dict(self.args, revision=2))

    def test_later_hold_invalidates_previous_review(self):
        p = self.w.prepare(**self.args)
        with self.w.connection() as c:
            c.execute(
                "INSERT INTO email_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "later",
                    "email",
                    "test@example.com",
                    "hold",
                    "https://example.com",
                    "purpose uncertain",
                    utc_now(),
                    "sender@example.com",
                    "in:anywhere to:test@example.com",
                    "no_match",
                    utc_now(),
                    "test",
                ),
            )
        with self.assertRaisesRegex(ValueError, "fresh"):
            self.w.prepare(**self.args)
        with self.assertRaisesRegex(ValueError, "review again"):
            self.w.reserve_gmail_create(p["preparation_id"])

    def test_later_route_review_hold_blocks_reserved_draft(self):
        from test_route_review import review_fixture

        from crm.route_review import apply_review

        p = self.w.prepare(**self.args)
        with self.w.connection() as c:
            review = review_fixture(c)
        apply_review(self.db, review)
        with self.assertRaisesRegex(ValueError, "route_review_research_needed"):
            self.w.reserve_gmail_create(p["preparation_id"])


if __name__ == "__main__":
    unittest.main()
