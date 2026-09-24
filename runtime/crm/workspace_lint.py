"""Offline maintained-source lint; never imports application code or reads secrets."""

import argparse
import ast
import collections
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

WORKSPACE = Path(__file__).resolve().parents[2]
EXCLUDED = {
    ".git",
    ".venv",
    "archive",
    "venv",
    "node_modules",
    ".codex-worktrees",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".loop-maker-runtime",
    "data",
    "evidence",
    "exports",
    "logs",
    "history",
    "drafts",
    "backups",
    "accounts",
    "imports",
    "review",
    "outputs",
    "leads",
    "gates",
    "usage",
    "runs",
}
SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".sh", ".js", ".cjs", ".mjs", ".sql"}


def source_files(root):
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not Path(folder, d).is_symlink())
        for name in sorted(files):
            path = Path(folder, name)
            if path.is_symlink() or name.startswith(".env"):
                continue
            if path.suffix in SUFFIXES:
                yield path


def markdown_errors(path, text, source_root=None, external=None):
    errors = []
    fence = None
    body = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            body.append((number, re.sub(r"`[^`]*`", "", line)))
    if fence:
        errors.append("unclosed fenced code block")
    for number, line in body:
        for target in re.findall(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", line):
            target = target.strip().split(' "', 1)[0].strip("<>")
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            if re.fullmatch(r"\{\{[\w]+\}\}", target):
                continue
            local = unquote(target.split("#", 1)[0])
            if local and not (path.parent / local).exists():
                resolved = (path.parent / local).resolve()
                if source_root is not None and resolved.is_relative_to(source_root):
                    relative = resolved.relative_to(source_root)
                    if any(part in EXCLUDED | {"control-plane"} for part in relative.parts):
                        external.append(
                            {"file": str(path.relative_to(source_root)), "target": target}
                        )
                        continue
                errors.append(f"{number}: missing link {target}")
    return errors


def run(root, source_only=False):
    root = root.resolve()
    errors = []
    external = []
    counts = collections.Counter()
    paths = [
        p
        for p in source_files(root)
        if not source_only or "control-plane" not in p.relative_to(root).parts
    ]
    try:
        import yaml
    except ImportError:
        yaml = None
    for path in paths:
        rel = str(path.relative_to(root))
        counts[path.suffix] += 1
        try:
            text = path.read_text(encoding="utf-8")
            if re.search(r"^(?:<<<<<<< |>>>>>>> |=======$)", text, re.M):
                raise ValueError("merge conflict marker")
            if path.suffix == ".py":
                ast.parse(text, filename=rel)
            elif path.suffix == ".json":
                json.loads(text)
            elif path.suffix in (".yaml", ".yml"):
                if yaml is None:
                    raise ValueError("PyYAML required; install dev requirements")
                # Scaffold templates are parsed after substituting their declared
                # merge tokens; runtime instance YAML is validated without changes.
                if "control-plane/loops/templates/" in rel:
                    text = re.sub(r"\{\{[a-zA-Z_][a-zA-Z0-9_]*\}\}", "template_value", text)
                list(yaml.safe_load_all(text))
            elif path.suffix == ".toml":
                import tomllib

                tomllib.loads(text)
            elif path.suffix == ".md":
                errors.extend(
                    {"file": rel, "error": e}
                    for e in markdown_errors(path, text, root if source_only else None, external)
                )
            elif path.suffix in (".sh", ".js", ".cjs", ".mjs"):
                binary = shutil.which("bash" if path.suffix == ".sh" else "node")
                if not binary:
                    raise ValueError("missing shell/JavaScript syntax checker")
                p = subprocess.run(
                    [binary, "-n" if path.suffix == ".sh" else "--check", str(path)],
                    capture_output=True,
                    text=True,
                )
                if p.returncode:
                    raise ValueError("syntax check failed: " + p.stderr[:300])
        except (ValueError, SyntaxError, OSError, ImportError) as exc:
            errors.append({"file": rel, "error": str(exc)[:350]})
        except Exception as exc:
            if yaml and isinstance(exc, yaml.YAMLError):
                errors.append({"file": rel, "error": "YAML syntax: " + str(exc)[:350]})
            else:
                raise
    return dict(
        files=len(paths),
        external_dependencies=external,
        by_extension=dict(counts),
        errors=errors,
        excluded_directories=sorted(EXCLUDED),
        sql_note="SQL inventoried; SQLite tests and isolated PostgreSQL migration checks provide execution validation.",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Check standalone source; report omitted private/application links separately",
    )
    args = parser.parse_args()
    result = run(WORKSPACE, source_only=args.source_only)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "files": result["files"],
                "errors": len(result["errors"]),
                "by_extension": result["by_extension"],
            }
        )
    )
    return bool(result["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
