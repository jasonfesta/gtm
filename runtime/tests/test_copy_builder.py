import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.cli import ROOT, initialize, seed_pilot
from crm.copy_builder import CopyBuilder
from crm.mcp_server import Server


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "copy.sqlite3"
        conn = initialize(self.db)
        seed_pilot(conn)
        conn.close()
        self.builder = CopyBuilder(self.db)
        evidence = self.builder.context("agt_orchid")["evidence"][0]["claim_id"]
        self.args = dict(
            format="dm",
            variant="a",
            channel="x",
            agent_id="agt_orchid",
            body="hey — came across orchid. what are you building next?",
            evidence_ids=[evidence],
            editor="test editor",
            expected_revision=0,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_context_reloads_saved_voice_and_exposes_exact_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "runtime"
            (root / "UTM").mkdir(parents=True)
            (root / "UTM/FORMATION_RULES.md").write_text("utm fixture")
            (root / "copy/templates").mkdir(parents=True)
            for name in ("brief.md", "WORKFLOW.md"):
                (root / "copy" / name).write_text("context fixture")
            for name in ("email", "dm", "reply_dm"):
                (root / "copy/templates" / (name + ".md")).write_text("format fixture")
            guide = root / "copy/copy.md"
            with patch("crm.copy_builder.ROOT", root):
                for text in ("first voice", "revised voice — keep exact punctuation"):
                    guide.write_text(text)
                    context = self.builder.context("agt_orchid")
                    self.assertEqual(context["copy_guidelines"], text)
                    self.assertEqual(context["utm_formation_rules"], "utm fixture")
                    self.assertEqual(
                        context["copy_guidelines_sha256"], hashlib.sha256(text.encode()).hexdigest()
                    )
                    self.assertIn("calibration is pending", context["voice_examples"])

    def test_two_editors_cannot_overwrite_stale_revision(self):
        first = self.builder.save(**self.args)
        args = dict(self.args, draft_id=first["draft_id"], expected_revision=1)
        second = self.builder.save(**dict(args, editor="second editor"))
        self.assertEqual(second["revision"], 2)
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            self.builder.save(**args)
        self.assertEqual(len(self.builder.read(first["draft_id"])), 2)
        with sqlite3.connect(str(self.db)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE copy_revisions SET body='overwritten'")

    def test_rejects_unsupported_evidence_and_reply_without_message(self):
        with self.assertRaises(ValueError):
            self.builder.save(**dict(self.args, evidence_ids=["fake"]))
        with self.assertRaisesRegex(ValueError, "incoming message"):
            self.builder.save(**dict(self.args, format="reply_dm"))
        with self.assertRaisesRegex(ValueError, "lowercase"):
            self.builder.save(**dict(self.args, body="Hello there"))
        with self.assertRaisesRegex(ValueError, "placeholders"):
            self.builder.save(**dict(self.args, body="hey {{first_name}}"))
        with self.assertRaisesRegex(ValueError, "word limit"):
            self.builder.save(**dict(self.args, body="word " * 41))

    def test_urls_preserve_case_and_valid_reply_saves(self):
        result = self.builder.save(
            **dict(
                self.args,
                format="reply_dm",
                incoming_message="What caught your eye?",
                body="the messaging approach at https://Example.com/CaseSensitive caught my eye. how has onboarding been?",
            )
        )
        self.assertEqual(result["revision"], 1)

    def test_mcp_lifecycle_prompt_and_tool_validation(self):
        with patch("crm.mcp_server.CopyBuilder", return_value=self.builder):
            server = Server(self.db)

        def call(method, params=None):
            return server.dispatch(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
            )

        self.assertIn("error", call("tools/list"))
        self.assertEqual(call("initialize")["result"]["protocolVersion"], "2025-11-25")
        server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        names = {tool["name"] for tool in call("tools/list")["result"]["tools"]}
        self.assertTrue(
            {
                "crm_relationships",
                "crm_relationship_event",
                "crm_relationship_contact",
                "copy_save",
                "crm_tags",
            }
            <= names
        )
        catalog = call("tools/call", {"name": "crm_tags"})
        self.assertNotIn("isError", catalog["result"])
        self.assertEqual(len(json.loads(catalog["result"]["content"][0]["text"])), 6)
        result = call(
            "prompts/get", {"name": "build_developer_copy", "arguments": {"agent_id": "agt_orchid"}}
        )
        self.assertIn("lowercase", result["result"]["messages"][0]["content"]["text"])
        saved = call("tools/call", {"name": "copy_save", "arguments": self.args})
        self.assertIn("result", saved)
        self.assertNotIn("isError", saved["result"])
        invalid = call(
            "tools/call",
            {"name": "copy_save", "arguments": dict(self.args, expected_revision="zero")},
        )
        self.assertEqual(invalid["error"]["code"], -32602)

    def test_actual_stdio_process(self):
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "gtm_counts", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "copy_context", "arguments": {"agent_id": "agt_orchid"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "prompts/get",
                "params": {
                    "name": "research_crm_source",
                    "arguments": {"source": "example pasted list"},
                },
            },
        ]
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "crm/mcp_server.py"), "--database", str(self.db)],
            input="\n".join(json.dumps(m) for m in messages) + "\n",
            capture_output=True,
            text=True,
            timeout=15,
            cwd=self.temp.name,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4, 5])
        self.assertTrue(all("result" in r for r in responses))
        counts = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertEqual(len(counts["sources"]), 2)
        self.assertEqual(Path(counts["database"]), self.db.resolve())
        self.assertNotIn("isError", responses[3]["result"])
        self.assertIn(
            "example pasted list", responses[4]["result"]["messages"][0]["content"]["text"]
        )

    def test_research_prompt_is_available_without_contact_context(self):
        with patch("crm.mcp_server.CopyBuilder", return_value=self.builder):
            server = Server(self.db)

        def call(method, params=None):
            return server.dispatch(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
            )

        call("initialize")
        server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        names = {p["name"] for p in call("prompts/list")["result"]["prompts"]}
        self.assertEqual(names, {"build_developer_copy", "research_crm_source"})
        with sqlite3.connect(self.db) as conn:
            before = conn.execute("SELECT count(*) FROM contact_points").fetchone()[0]
        with patch.object(
            self.builder, "context", side_effect=AssertionError("must not read person context")
        ):
            result = call(
                "prompts/get",
                {
                    "name": "research_crm_source",
                    "arguments": {
                        "source": "pasted list: example company",
                        "pilot_scope": "5 people",
                    },
                },
            )
        text = result["result"]["messages"][0]["content"]["text"]
        self.assertIn("pasted list: example company", text)
        self.assertIn("untrusted source data", text)
        self.assertIn("no approval for paid enrichment", text)
        self.assertIn("Personal coding is not required", text)
        self.assertIn("Do not convert discovery coverage into verified contact success", text)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM contact_points").fetchone()[0], before
            )
        for args in ({}, {"source": ""}, {"source": 42}, {"source": "ok", "spend_approved": True}):
            self.assertIn(
                "error", call("prompts/get", {"name": "research_crm_source", "arguments": args})
            )


if __name__ == "__main__":
    unittest.main()
