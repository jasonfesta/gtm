import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ResearchDocsTests(unittest.TestCase):
    def test_reusable_document_links_resolve(self):
        for name in (
            "CRM_INTAKE.md",
            "CRM_RESEARCH.md",
            "OUTREACH_ROUTES.md",
        ):
            path = ROOT.parent / "docs" / name
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if "://" not in target and not target.startswith("#"):
                    self.assertTrue((path.parent / target.split("#")[0]).exists(), (name, target))

    def test_new_run_example_defaults_to_no_external_actions(self):
        data = json.loads((ROOT / "config/research-run.example.json").read_text())
        permissions = data["permissions"]
        for flag in (
            "outreach",
            "external_drafts",
            "publishing",
            "scheduling",
            "hosted_crm_creation",
            "apollo_enrichment_approved",
            "personal_email_reveal",
            "phone_reveal",
            "smtp_probe",
        ):
            self.assertIs(permissions[flag], False)
        self.assertEqual(permissions["max_credits"], 0)
        self.assertFalse(data["scope"]["full_run_approved"])
