"""Canonical runbooks route to the intended bounded operation."""

import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

from gtm_agent_runtime.cli import _crm_root, main

ROOT = Path(__file__).resolve().parents[1]


class CliRouteTests(unittest.TestCase):
    def test_default_crm_root_resolves_shared_runtime(self):
        root = _crm_root(argparse.Namespace(crm_root=None))
        self.assertEqual(root, ROOT / "runtime")
        self.assertTrue((root / "sql/schema.sql").is_file())

    def test_agent_reply_runbook_routes_check(self):
        runbook = ROOT / "agents/agent-reply-agent/README.md"
        with patch("gtm_agent_runtime.cli._run_agent_reply", return_value=0) as execute:
            self.assertEqual(main(["check", str(runbook), "--input", "prepared.json"]), 0)
        self.assertEqual(execute.call_args.args[0].command, "check")

    def test_ops_runbook_routes_handoff(self):
        runbook = ROOT / "agents/ops-agents/README.md"
        with patch("gtm_agent_runtime.cli._run_ops", return_value=0) as execute:
            self.assertEqual(main(["run", str(runbook), "--once", "--handoff", "handoff.json"]), 0)
        self.assertTrue(execute.call_args.args[0].once)

    def test_snapshot_rejects_producer_runbook(self):
        runbook = ROOT / "agents/agent-reply-agent/README.md"
        with patch("gtm_agent_runtime.cli._run_ops") as execute:
            with self.assertRaisesRegex(SystemExit, "only ops agents"):
                main(["snapshot", str(runbook), "--once", "--type", "agent"])
        execute.assert_not_called()
