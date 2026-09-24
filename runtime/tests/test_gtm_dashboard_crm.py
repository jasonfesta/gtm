import unittest

from crm.gtm_dashboard_crm import normalize_handle, prepare_records


class DashboardEvidenceTests(unittest.TestCase):
    def blob(self):
        return {
            "account": "operator",
            "records": [
                {
                    "record_id": "dm:1",
                    "area": "dms",
                    "channel": "x",
                    "account": "operator",
                    "direction": "inbound",
                    "provider_id": None,
                    "provider_id_status": "pending_readback",
                    "occurred_at": "2026-09-22T00:45:00Z",
                    "body": None,
                    "contact_handle": "Person",
                    "timestamp_source": "conversation-level minute",
                }
            ],
        }

    def test_preserves_missing_native_id_body_and_approximate_time(self):
        r = prepare_records(self.blob(), {"person": {"person-1"}})[0]
        self.assertIsNone(r[5])
        self.assertEqual(r[6], "pending_readback")
        self.assertIsNone(r[9])
        self.assertEqual(r[8], "conversation-level minute")
        self.assertEqual(r[12], "person-1")

    def test_ambiguous_identity_is_not_assigned(self):
        self.assertIsNone(prepare_records(self.blob(), {"person": {"p1", "p2"}})[0][12])

    def test_duplicate_and_false_confirmation_rejected(self):
        b = self.blob()
        b["records"] *= 2
        with self.assertRaises(ValueError):
            prepare_records(b, {})
        b = self.blob()
        b["records"][0]["provider_id_status"] = "confirmed"
        with self.assertRaises(ValueError):
            prepare_records(b, {})

    def test_account_binding_and_normalization(self):
        self.assertEqual(normalize_handle("https://x.com/Person?lang=en"), "person")
        b = self.blob()
        b["records"][0]["account"] = "other"
        with self.assertRaises(ValueError):
            prepare_records(b, {})
