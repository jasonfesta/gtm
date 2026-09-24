import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm.cli import initialize, seed_pilot, utc_now
from crm.email_workflow import EmailWorkflow
from crm.route_review import apply_review, latest_review


def review_fixture(c, pid="person"):
    channels = {}
    for channel in ("email", "linkedin", "x"):
        contacts = [
            dict(
                contact_id=r["contact_id"],
                before=dict(r),
                ownership="supported" if r["verification_status"] == "confirmed" else "candidate",
            )
            for r in c.execute(
                "SELECT * FROM contact_points WHERE person_id=? AND contact_type=?", (pid, channel)
            )
        ]
        channels[channel] = dict(
            outcome="association_supported"
            if any(x["ownership"] == "supported" for x in contacts)
            else "candidate_only"
            if contacts
            else "not_found_in_checked_sources",
            contacts=contacts,
        )
    return dict(
        manifest=dict(run_id="review-test", person_ids=[pid], denominator=1),
        reviewed_at=utc_now(),
        reviewer="test",
        reviews=[
            dict(
                person_id=pid,
                qualification="qualified",
                qualification_reason="Documented engineer",
                role_status="supported",
                outreach_score=1,
                score_status="provisional",
                readiness="research_needed",
                route_reason="Unknown meeting and activity evidence",
                next_action="Find dated primary social evidence",
                source_refs=["https://example.com/team"],
                channels=channels,
            )
        ],
    )


class RouteReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "review.sqlite3"
        c = initialize(self.db)
        seed_pilot(c)
        c.execute(
            "INSERT INTO people(person_id,primary_company_id,full_name,normalized_name,identity_status) VALUES('person','cmp_orchid','Test Person','test person','single_source')"
        )
        c.execute(
            "INSERT INTO contact_points(contact_id,person_id,contact_type,value,normalized_value,verification_status,source_id,first_seen_at) VALUES('email','person','email','test@example.com','test@example.com','confirmed',(SELECT source_id FROM sources LIMIT 1),?)",
            (utc_now(),),
        )
        c.commit()
        c.row_factory = sqlite3.Row
        self.data = review_fixture(c)
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_replay_history_and_email_hold(self):
        self.assertEqual(apply_review(self.db, self.data)["status"], "applied")
        self.assertEqual(apply_review(self.db, self.data)["status"], "already_applied")
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            self.assertEqual(latest_review(c, "person")["readiness"], "research_needed")
            self.assertIn(
                "route_review_research_needed", EmailWorkflow(self.db).blockers(c, "email")
            )
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE route_reviews SET readiness='ready_for_preparation'")
        changed = copy.deepcopy(self.data)
        changed["reviews"][0]["next_action"] = "Changed decision"
        with self.assertRaisesRegex(ValueError, "different decisions"):
            apply_review(self.db, changed)
        changed["manifest"]["run_id"] = "review-test-2"
        self.assertEqual(apply_review(self.db, changed)["status"], "applied")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM route_reviews").fetchone()[0], 2)
            self.assertEqual(latest_review(c, "person")["next_action"], "Changed decision")

    def test_complete_cohort_and_contact_mapping_required(self):
        missing = copy.deepcopy(self.data)
        missing["reviews"] = []
        with self.assertRaisesRegex(ValueError, "frozen cohort"):
            apply_review(self.db, missing, True)
        wrong = copy.deepcopy(self.data)
        wrong["reviews"][0]["channels"]["email"]["contacts"][0]["contact_id"] = (
            "other-person-contact"
        )
        with self.assertRaisesRegex(ValueError, "exact current contacts"):
            apply_review(self.db, wrong, True)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE contact_points SET value='changed@example.com'")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            apply_review(self.db, self.data, True)

    def test_relevant_founder_does_not_need_personal_coding(self):
        r = self.data["reviews"][0]
        r.update(
            qualification="adjacent",
            audience_fit="eligible",
            audience_reason="Primary company source identifies relevant founder",
        )
        self.assertEqual(apply_review(self.db, self.data, True)["status"], "valid")
        r["audience_fit"] = "needs_review"
        with self.assertRaisesRegex(ValueError, "audience fit"):
            apply_review(self.db, self.data, True)

    def test_new_review_replaces_only_its_predecessor_queue_item(self):
        apply_review(self.db, self.data)
        later = copy.deepcopy(self.data)
        later["manifest"]["run_id"] = "later-review"
        apply_review(self.db, later)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute(
                    "SELECT count(*) FROM research_tasks WHERE entity_id='person' AND status='blocked'"
                ).fetchone()[0],
                1,
            )

    def test_company_location_cannot_support_meeting(self):
        r = self.data["reviews"][0]
        r.update(
            outreach_score=3,
            score_status="supported",
            meeting_triggers=[
                dict(
                    type="sf_local",
                    subject="company",
                    city="San Francisco",
                    source_url="https://example.com",
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "person-level"):
            apply_review(self.db, self.data, True)
        r["meeting_triggers"][0]["subject"] = "person"
        self.assertEqual(apply_review(self.db, self.data, True)["status"], "valid")

    def test_activity_needs_authorship_and_recent_absolute_date(self):
        r = self.data["reviews"][0]
        r.update(
            outreach_score=2,
            score_status="supported",
            social_activity=dict(
                url="https://example.com/post", date="2000-01-01", authorship_supported=True
            ),
        )
        with self.assertRaisesRegex(ValueError, "dated authored"):
            apply_review(self.db, self.data, True)
        r["social_activity"]["date"] = self.data["reviewed_at"][:10]
        r["social_activity"]["authorship_supported"] = False
        with self.assertRaisesRegex(ValueError, "dated authored"):
            apply_review(self.db, self.data, True)
        r["social_activity"]["authorship_supported"] = True
        self.assertEqual(apply_review(self.db, self.data, True)["status"], "valid")

    def test_provider_candidate_cannot_be_promoted_without_public_proof(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE contact_points SET verification_status='unverified'")
            c.row_factory = sqlite3.Row
            self.data = review_fixture(c)
        r = self.data["reviews"][0]["channels"]["email"]
        r["outcome"] = "association_supported"
        r["contacts"][0]["ownership"] = "supported"
        with self.assertRaisesRegex(ValueError, "exact public link"):
            apply_review(self.db, self.data, True)

    def test_failure_rolls_back_earlier_record_changes(self):
        self.data["founder_task_decisions"] = [
            dict(task_id="missing", status="complete", reason="invalid")
        ]
        with self.assertRaises(TypeError):
            apply_review(self.db, self.data)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM route_reviews").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT count(*) FROM route_review_runs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
