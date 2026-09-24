import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.database import schema_connection
from crm.dm_reconciliation import reconcile


class DMReconciliationTests(unittest.TestCase):
    def test_body_free_idempotent_shared_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / "dm.json"
            evidence.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "account_profile_url": "https://x.com/jasonfesta",
                        "checked_at": "2026-09-21T22:25:00Z",
                        "conversations": [
                            {
                                "name": "Person",
                                "handle": "person",
                                "profile_url": "https://x.com/person",
                                "conversation_url": "https://x.com/i/chat/1-2",
                                "outbound": {
                                    "message_id": "00000000-0000-4000-8000-000000000001",
                                    "occurred_at": "2026-09-21T21:00:00Z",
                                    "body": "private outbound",
                                },
                                "reply": {
                                    "message_id": "00000000-0000-4000-8000-000000000002",
                                    "occurred_at": "2026-09-21T21:05:00Z",
                                    "body": "private reply",
                                },
                            }
                        ],
                    }
                )
            )
            template = schema_connection()
            database_path = root / "crm.sqlite3"
            database = sqlite3.connect(database_path)
            database.executescript("\n".join(template.iterdump()))
            database.close()

            def factory():
                connection = sqlite3.connect(database_path)
                connection.row_factory = sqlite3.Row
                return connection

            with patch("crm.dm_reconciliation.ROOT", root):
                result = reconcile(evidence, connection_factory=factory)
                self.assertEqual(result["conversations"], 1)
                self.assertEqual(result["replies"], 1)
                self.assertEqual(result["created_people"], 1)
                self.assertEqual(result["created_events"], 2)
                again = reconcile(evidence, connection_factory=factory)
                self.assertEqual(again["created_people"], 0)
                self.assertEqual(again["created_events"], 0)
            with factory() as database:
                rows = database.execute(
                    "SELECT direction,template_id,external_reference,notes FROM outreach_events ORDER BY direction"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertNotIn("private outbound", json.dumps([dict(row) for row in rows]))
            self.assertNotIn("private reply", json.dumps([dict(row) for row in rows]))


if __name__ == "__main__":
    unittest.main()
