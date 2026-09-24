import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from crm.apollo_discovery import lookup, lookup_payload, main, verification_result


class ApolloValidationTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "identity_key": "profile:example",
            "source_url": "https://example.org/posts/1",
            "linkedin_url": "https://uk.linkedin.com/in/example/?trk=test",
        }
        self.person = {
            "id": "fixture",
            "linkedin_url": "https://www.linkedin.com/in/example",
            "email": "person@example.org",
            "email_status": "verified",
        }

    def test_verified_identity_bound_evidence(self):
        result = verification_result(
            self.candidate, {"person": self.person}, observed_at="fixture-time"
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["email_verification_evidence"]["observed_at"], "fixture-time")
        self.assertTrue(result["requires_ops_identity_and_suppression"])
        self.assertFalse(result["sends"])

    def test_search_availability_never_verifies(self):
        result = verification_result(self.candidate, {"people": [{"has_email": True}]})
        self.assertNotIn("email", result)

    def test_unverified_missing_masked_or_mismatched(self):
        for changes in (
            {"email_status": "guessed"},
            {"email_status": None},
            {"email": None},
            {"email": "***@example.org"},
            {"email": "email_not_unlocked@domain.com"},
            {"linkedin_url": "https://www.linkedin.com/in/other"},
            {"linkedin_url": "https://linkedin.com.evil.org/in/example"},
        ):
            with self.subTest(changes=changes):
                result = verification_result(self.candidate, {"person": self.person | changes})
                self.assertEqual(result["status"], "unverified")
                self.assertNotIn("email_verification_evidence", result)

    def test_source_and_identity_required_before_request(self):
        for field in ("identity_key", "source_url", "linkedin_url"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                lookup_payload(self.candidate | {field: ""})

    def test_missing_auth_no_request(self):
        self.assertEqual(lookup(self.candidate, auth="")["reason"], "apollo_token_missing")

    def test_request_is_single_native_lookup(self):
        calls = []

        def open_request(request, timeout):
            calls.append(request)
            payload = json.loads(request.data)
            self.assertFalse(payload["run_waterfall_email"])
            self.assertFalse(payload["reveal_phone_number"])
            self.assertFalse(payload["reveal_personal_emails"])
            self.assertEqual(timeout, 30)
            return io.BytesIO(json.dumps({"person": self.person}).encode())

        self.assertEqual(
            lookup(self.candidate, auth="fixture", opener=open_request)["status"], "verified"
        )
        self.assertEqual(len(calls), 1)

    def test_errors_do_not_leak(self):
        def fail(*args, **kwargs):
            raise urllib.error.URLError("secret credential in response")

        result = lookup(self.candidate, auth="fixture", opener=fail)
        self.assertEqual(result["reason"], "apollo_lookup_failed")
        self.assertNotIn("secret", json.dumps(result))


class ApolloNameCompanyTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "identity_key": "source:one",
            "source_url": "https://example.org/team",
            "name": "Ada Example",
            "company": "Example Labs",
        }
        self.person = {
            "id": "one",
            "name": "Ada Example",
            "organization": {"name": "Example Labs, Inc.", "primary_domain": "example.org"},
            "email": "ada@example.org",
            "email_status": "verified",
        }

    def test_name_company_native_payload(self):
        payload = lookup_payload(self.candidate)
        self.assertEqual(payload["name"], "Ada Example")
        self.assertEqual(payload["organization_name"], "Example Labs")
        self.assertNotIn("linkedin_url", payload)
        self.assertEqual(
            verification_result(self.candidate, {"person": self.person})["status"], "verified"
        )

    def test_wrong_name_company_or_low_confidence_never_verifies(self):
        for person in (
            self.person | {"name": "Ada Other"},
            self.person | {"organization": {"name": "Other Labs"}},
            self.person | {"match_confidence": "low"},
            self.person | {"organization": None},
        ):
            self.assertEqual(
                verification_result(self.candidate, {"person": person})["status"], "unverified"
            )
        self.assertEqual(
            verification_result(
                self.candidate, {"person": self.person, "match_confidence": "none"}
            )["status"],
            "unverified",
        )

    def test_company_domain_is_additional_constraint(self):
        candidate = self.candidate | {"company_domain": "https://www.example.org/"}
        self.assertEqual(lookup_payload(candidate)["domain"], "example.org")
        self.assertEqual(
            verification_result(candidate, {"person": self.person})["status"], "verified"
        )
        self.assertEqual(
            verification_result(
                candidate | {"company_domain": "other.org"}, {"person": self.person}
            )["status"],
            "unverified",
        )

    def test_incomplete_name_or_company_rejected(self):
        for changes in ({"name": "Ada"}, {"company": ""}, {"company_domain": "bad domain"}):
            with self.assertRaises(ValueError):
                lookup_payload(self.candidate | changes)

    def test_matched_contact_without_verified_email_not_ready(self):
        result = verification_result(
            self.candidate, {"person": self.person | {"email_status": "unverified"}}
        )
        self.assertTrue(result["identity_matched"])
        self.assertEqual(result["contact"]["name"], "Ada Example")
        self.assertNotIn("email", result)

    def test_cli_replay_does_not_charge_again_and_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "contact.json"
            args = [
                "apollo",
                "--name",
                "Ada Example",
                "--company",
                "Example Labs",
                "--source-url",
                "https://example.org/team",
                "--output",
                str(output),
            ]
            with (
                patch("sys.argv", args),
                patch("sys.stdout", new_callable=io.StringIO),
                patch(
                    "crm.apollo_discovery.lookup",
                    return_value={"status": "unverified", "sends": False},
                ) as request,
            ):
                main()
                main()
                request.assert_called_once()
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                args[2] = "Different Person"
                with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                    main()
                request.assert_called_once()
