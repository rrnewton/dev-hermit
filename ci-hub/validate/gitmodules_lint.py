#!/usr/bin/env python3
"""Ratchet for `.gitmodules` hazards across the parent and its submodules.

WHY THIS EXISTS
---------------
The cold-clone-verify fix (removing `shallow = true`) was APPLIED but had no
guard: nothing asserted the absence, so it could silently come back. A fix
without a control is a state, not a control. This is the control.

WHAT IT CHECKS — the actual hazards, not cosmetics
--------------------------------------------------
1. `shallow = true`  -- a shallow submodule silently SKIPS cold-clone verify;
   the verification passes because the history it would check is not there.
2. `update = none`   -- the submodule is not checked out by an ordinary
   `git submodule update --init --recursive`, so a consumer that assumes the
   tree is present gets an empty directory rather than an error.
3. `branch = ...`    -- the parent guide forbids it: a branch field turns an
   exact gitlink into a moving target, which is the opposite of the
   reproducibility the pins exist for.

WHAT IT DELIBERATELY DOES *NOT* CHECK
-------------------------------------
The ABSENCE of an explicit `update = checkout`. Git's own documentation
(`git-submodule(1)`): "If the key submodule.$name.update is either not
explicitly set or set to checkout, this option is implicit." Absence is
therefore EXACTLY EQUIVALENT to `update = checkout`, and flagging it would be a
guard that fires on a non-defect — the failure mode where a check trains its
readers to ignore it. Entries without an explicit `update` are REPORTED as
informational so the inconsistency is visible, and never fail the lint.

Exit codes: 0 = no hazard, 1 = hazard found, 2 = bad input.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

# `key = value`, tolerating the tab/space indentation git writes.
_ENTRY = re.compile(r"^\s*\[submodule\s+\"(?P<name>[^\"]+)\"\]\s*$")
_KV = re.compile(r"^\s*(?P<key>[A-Za-z0-9_-]+)\s*=\s*(?P<value>.*?)\s*$")

HAZARDS = ("shallow", "update-none", "branch")


def parse_gitmodules(text: str) -> list[dict[str, str]]:
    """Parse into a list of {name, key: value, ...}. Tolerant by design: a file
    git accepts must not crash the lint."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        header = _ENTRY.match(line)
        if header:
            current = {"name": header.group("name")}
            entries.append(current)
            continue
        if current is None:
            continue
        kv = _KV.match(line)
        if kv:
            current[kv.group("key").lower()] = kv.group("value")
    return entries


def hazards_in(entry: dict[str, str]) -> list[str]:
    """Return the hazard ids this entry trips. Empty list = clean."""
    found: list[str] = []
    shallow = entry.get("shallow", "").strip().lower()
    if shallow in {"true", "yes", "on", "1"}:
        found.append("shallow")
    if entry.get("update", "").strip().lower() == "none":
        found.append("update-none")
    if "branch" in entry:
        found.append("branch")
    return found


def lint_file(path: Path) -> dict[str, object]:
    entries = parse_gitmodules(path.read_text())
    violations = []
    implicit_update = []
    for entry in entries:
        for hazard in hazards_in(entry):
            violations.append({"submodule": entry["name"], "hazard": hazard})
        if "update" not in entry:
            implicit_update.append(entry["name"])
    return {
        "file": str(path),
        "entries": len(entries),
        "violations": violations,
        # Informational only -- absence of `update` IS `checkout` (see docstring).
        "implicit_update_checkout": implicit_update,
    }


def discover(root: Path, max_depth: int = 2) -> list[Path]:
    """Every `.gitmodules` in the parent and its immediate submodules.

    Prunes during the walk rather than filtering afterwards: this tree contains
    build outputs and other agents' worktrees, and an unpruned `rglob` over it
    does not return in reasonable time (measured: >120s).
    """
    skip = {
        "scratch", "ignored", ".claude", "worktrees", "target", "third-party",
        ".git", "node_modules", "experiments", "__pycache__", "buck-out",
    }
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = 0 if rel == Path(".") else len(rel.parts)
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        if ".gitmodules" in filenames:
            found.append(Path(dirpath) / ".gitmodules")
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--file", type=Path, action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    paths = args.file or discover(args.root)
    if not paths:
        print("gitmodules-lint: no .gitmodules found", file=sys.stderr)
        return 2

    reports = []
    for path in paths:
        if not path.is_file():
            print(f"gitmodules-lint: not a file: {path}", file=sys.stderr)
            return 2
        reports.append(lint_file(path))

    total = sum(len(r["violations"]) for r in reports)
    if args.json:
        print(json.dumps({"violations": total, "reports": reports}, indent=2))
    else:
        for report in reports:
            for violation in report["violations"]:
                print(
                    f"gitmodules-lint: HAZARD {violation['hazard']} in "
                    f"[submodule \"{violation['submodule']}\"] of {report['file']}"
                )
            if report["implicit_update_checkout"]:
                print(
                    f"gitmodules-lint: note {report['file']}: "
                    f"{len(report['implicit_update_checkout'])} entr(ies) rely on the implicit "
                    f"`update = checkout` default ({', '.join(report['implicit_update_checkout'])}) "
                    "-- equivalent to setting it, not a hazard"
                )
        print(
            f"gitmodules-lint: {total} hazard(s) across "
            f"{sum(r['entries'] for r in reports)} submodule entr(ies) in {len(reports)} file(s)"
        )
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
