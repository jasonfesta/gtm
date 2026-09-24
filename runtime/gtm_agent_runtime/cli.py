"""Manual-only command line entry point for Darwin GTM operation agents."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("agent_file", type=Path)
    check.add_argument("--input", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("agent_file", type=Path)
    run.add_argument("--once", action="store_true")
    run.add_argument("--handoff", type=Path)
    run.add_argument("--input", type=Path)
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--database", type=Path)
    run.add_argument("--crm-root", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--output", type=Path)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("agent_file", type=Path)
    snapshot.add_argument("--once", action="store_true")
    snapshot.add_argument("--type", choices=("human", "agent"), required=True)
    snapshot.add_argument("--id")
    snapshot.add_argument("--audience")
    snapshot.add_argument("--limit", type=int, default=200)
    snapshot.add_argument("--offset", type=int, default=0)
    snapshot.add_argument("--database", type=Path)
    snapshot.add_argument("--crm-root", type=Path)
    snapshot.add_argument("--output", type=Path)
    posthog = commands.add_parser("posthog")
    posthog.add_argument("agent_file", type=Path)
    posthog.add_argument("--once", action="store_true")
    source = posthog.add_mutually_exclusive_group(required=True)
    source.add_argument("--inbox", type=Path)
    source.add_argument("--handoff", type=Path)
    posthog.add_argument("--dry-run", action="store_true")
    posthog.add_argument("--output", type=Path)
    scan = commands.add_parser("scan")
    scan.add_argument("agent_file", type=Path)
    scan.add_argument("--once", action="store_true")
    scan.add_argument("--snapshot", type=Path, required=True)
    scan.add_argument("--config", type=Path, required=True)
    scan.add_argument("--output", type=Path, required=True)
    engage = commands.add_parser("engage")
    engage.add_argument("agent_file", type=Path)
    engage.add_argument("--once", action="store_true")
    engage.add_argument("--snapshot", type=Path, required=True)
    engage.add_argument("--config", type=Path, required=True)
    engage.add_argument("--assets", type=Path, required=True)
    engage.add_argument("--output-dir", type=Path, required=True)
    engage.add_argument("--dry-run", action="store_true")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("agent_file", type=Path)
    prepare.add_argument("--once", action="store_true")
    prepare.add_argument("--snapshot", type=Path, required=True)
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--assets", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    record = commands.add_parser("record")
    record.add_argument("agent_file", type=Path)
    record.add_argument("--once", action="store_true")
    record.add_argument("--input", type=Path, required=True)
    record.add_argument("--receipt", type=Path, required=True)
    record.add_argument("--output-dir", type=Path, required=True)
    return root


def _crm_root(args):
    selected = getattr(args, "crm_root", None)
    root = selected.resolve() if selected else Path(__file__).resolve().parents[1]
    if selected:
        if not (root / "crm" / "database.py").is_file():
            raise SystemExit("--crm-root must point to a CRM package root")
        os.environ["GTM_CRM_ROOT"] = str(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def _write_result(result, output=None):
    text = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    print(text, end="")


def _run_ops(args):
    if not args.once:
        raise SystemExit("run requires --once; scheduling is not supported")
    crm_root = _crm_root(args)
    if args.command == "posthog":
        from .posthog import submit_handoff, submit_inbox

        try:
            result = (
                submit_handoff(args.handoff, dry_run=args.dry_run)
                if args.handoff
                else submit_inbox(args.inbox, dry_run=args.dry_run)
            )
        except (OSError, ValueError, RuntimeError) as error:
            result = {
                "status": "failed",
                "error": str(error),
                "source": str(args.handoff or args.inbox),
            }
    elif args.command == "run":
        if args.handoff is None:
            raise SystemExit("ops run requires --handoff")
        if args.database is None and not (crm_root / "accounts" / "database.json").is_file():
            raise SystemExit("configured CRM not found; supply --crm-root or --database")
        from .crm import apply_handoff

        result = apply_handoff(
            json.loads(args.handoff.read_text()), database=args.database, dry_run=args.dry_run
        )
    else:
        if args.database is None and not (crm_root / "accounts" / "database.json").is_file():
            raise SystemExit("configured CRM not found; supply --crm-root or --database")
        from .crm import export_snapshot

        result = export_snapshot(
            database=args.database,
            entity_type=args.type,
            entity_id=args.id,
            audience=args.audience,
            limit=args.limit,
            offset=args.offset,
        )
    _write_result(result, getattr(args, "output", None))
    if args.command == "snapshot":
        return 0
    return 0 if result["status"] in ("completed", "submitted", "empty", "dry_run") else 1


def _run_agent_reply(args):
    from .agent_reply import record_external, run_once, validate
    from .agent_reply_engage import engage_once, prepare_once
    from .agent_reply_scan import scan_once

    if args.command == "check":
        validate(json.loads(args.input.read_text()))
        _write_result({"status": "ready", "agent": str(args.agent_file)})
        return 0
    if not args.once:
        raise SystemExit(f"{args.command} requires --once; scheduling is not supported")
    if args.command == "scan":
        candidates = scan_once(
            json.loads(args.snapshot.read_text()), json.loads(args.config.read_text())
        )
        args.output.write_text(json.dumps(candidates, indent=2, sort_keys=True) + "\n")
        result = {"candidates": len(candidates), "output": str(args.output)}
    elif args.command == "engage":
        result = engage_once(
            json.loads(args.snapshot.read_text()),
            json.loads(args.config.read_text()),
            json.loads(args.assets.read_text()),
            args.output_dir,
            dry_run=args.dry_run,
        )
    elif args.command == "prepare":
        prepared = prepare_once(
            json.loads(args.snapshot.read_text()),
            json.loads(args.config.read_text()),
            json.loads(args.assets.read_text()),
        )
        if prepared is None:
            result = {"status": "no_relevant_conversation"}
        else:
            args.output.write_text(json.dumps(prepared, indent=2, sort_keys=True) + "\n")
            result = {"prepared_reply": str(args.output)}
    elif args.command == "record":
        result = record_external(
            json.loads(args.input.read_text()),
            args.output_dir,
            json.loads(args.receipt.read_text()),
        )
    else:
        if args.input is None or args.output_dir is None:
            raise SystemExit("agent reply run requires --input and --output-dir")
        result = run_once(json.loads(args.input.read_text()), args.output_dir, dry_run=args.dry_run)
    _write_result(result)
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.agent_file.is_file():
        raise SystemExit(f"agent file not found: {args.agent_file}")
    if (
        args.command in ("snapshot", "posthog")
        or args.agent_file.parent.name == "ops-agents"
        and args.agent_file.name == "README.md"
    ):
        if args.agent_file.parent.name != "ops-agents" or args.agent_file.name != "README.md":
            raise SystemExit("this command runs only ops agents")
        return _run_ops(args)
    if args.agent_file.parent.name != "agent-reply-agent" or args.agent_file.name != "README.md":
        raise SystemExit("agents/agent-reply-agent/README.md is required")
    return _run_agent_reply(args)


if __name__ == "__main__":
    raise SystemExit(main())
