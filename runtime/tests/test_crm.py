import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from crm import cli


class CrmTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "test.sqlite3"
        self.conn = cli.initialize(self.db)

    def tearDown(self):
        self.conn.close()
        self.tempdir.cleanup()

    def test_seed_is_idempotent(self):
        cli.seed_pilot(self.conn)
        cli.seed_pilot(self.conn)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM agents").fetchone()[0], 5)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM companies").fetchone()[0], 5)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM listings").fetchone()[0], 10)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM message_templates").fetchone()[0], 6
        )

    def test_contact_view_derives_last_dates(self):
        self.conn.execute(
            "INSERT INTO companies(company_id, canonical_name, normalized_name) VALUES ('cmp_test', 'Test Co', 'test co')"
        )
        self.conn.execute(
            """
            INSERT INTO people(person_id, primary_company_id, full_name, normalized_name)
            VALUES ('per_test', 'cmp_test', 'Test Founder', 'test founder')
            """
        )
        self.conn.execute(
            """
            INSERT INTO outreach_events(event_id, person_id, channel, direction, occurred_at, outcome)
            VALUES ('evt_1', 'per_test', 'x', 'outbound', '2026-01-02T00:00:00Z', 'sent')
            """
        )
        row = self.conn.execute(
            "SELECT contacted, last_contacted_x FROM v_contact_crm WHERE person_id='per_test'"
        ).fetchone()
        self.assertEqual(row["contacted"], 1)
        self.assertEqual(row["last_contacted_x"], "2026-01-02T00:00:00Z")

    def test_integrity_rejects_unsubstantiated_confirmed_email(self):
        self.conn.execute(
            "INSERT INTO companies(company_id, canonical_name, normalized_name) VALUES ('cmp_test', 'Test Co', 'test co')"
        )
        self.conn.execute(
            "INSERT INTO people(person_id, primary_company_id, full_name, normalized_name) VALUES ('per_test', 'cmp_test', 'Test Founder', 'test founder')"
        )
        self.conn.execute(
            """
            INSERT INTO contact_points(
                contact_id, person_id, contact_type, value, normalized_value,
                verification_status, confidence, first_seen_at
            ) VALUES ('con_test', 'per_test', 'email', 'founder@example.com', 'founder@example.com', 'confirmed', 90, '2026-01-01T00:00:00Z')
            """
        )
        self.conn.commit()
        args = type("Args", (), {"db": self.db})()
        output = io.StringIO()
        with redirect_stdout(output):
            result = cli.command_integrity(args)
        self.assertEqual(result, 1)
        self.assertIn("confirmed contact missing source", output.getvalue())


if __name__ == "__main__":
    unittest.main()
