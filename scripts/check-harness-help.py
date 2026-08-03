#!/usr/bin/env python3
"""Verify every coordinator-harness entrypoint treats -h/--help/--version as a PURE safe probe.

A safe probe MUST: exit 0, print usage, and cause NO side effects (no writes, no network, no
heavy work, no dependency on being inside the repo). This guards the DISCOVERY PATH -- the thing a
user runs precisely because they do not yet know what a tool does, and therefore have the strongest
reason to expect nothing to change.

Motivated by two real defects (task coordinator_harness_scripts_mishandle, 2026-08-03):
  * scripts/sync-memory-skill.rs --help MUTATED the working tree (created .claude/skills/*.md).
  * scripts/prepare-demo08-assets.sh --help ran the asset build instead of showing help.
plus seven entrypoints that ran real behavior, errored, or leaked raw comments instead of usage.

This mirrors agent-utils/scripts/check_deps.py: stdlib-only, so it runs in the same bare environment
where the tools are invoked. `make lint` runs it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (relative path, [probe args]). Every listed arg must be a PURE safe probe.
ENTRYPOINTS: list[tuple[str, list[str]]] = [
    ("scripts/sync-memory-skill.rs", ["-h", "--help", "--version"]),
    ("scripts/lint-memory-skill-sync.rs", ["-h", "--help", "--version"]),
    ("scripts/memory-skill-contradiction-scan.rs", ["-h", "--help", "--version"]),
    ("scripts/check-parent-gitmodules.sh", ["-h", "--help"]),
    ("scripts/check-portable-paths.sh", ["-h", "--help"]),
    ("scripts/e2e-union-rebase.sh", ["-h", "--help"]),
    ("scripts/check-demo-review.sh", ["-h", "--help"]),
    ("scripts/prepare-demo08-assets.sh", ["-h", "--help"]),
    ("agent-utils/setup", ["-h", "--help"]),
]

# Substrings that must NEVER appear in a safe-probe's output: crashes, mandatory-arg errors,
# unresolved-path errors, and the "doing the real work" signatures of the two mutating scripts.
FORBIDDEN = (
    "Traceback",
    "panicked",
    "thread 'main'",
    "can't read",
    "cannot find",
    "not initialized",
    "No such file or directory",
    "unknown arg",
    # real-work signatures that must not show up on the discovery path:
    "wrote mapped skill",
    "refreshed",
    "flattened",
    "would refresh",
    "assets ready",
    "crash seed ready",
)


def _run(rel: str, arg: str) -> subprocess.CompletedProcess | None:
    """Run `<rel> <arg>` BY RELATIVE PATH from the repo root -- exactly how a coordinator standing
    in dev-hermit/ would type it (this is what exposed `agent-utils/setup -h`'s relative-path bug).
    Returns None if the interpreter is missing (SKIP)."""
    try:
        return subprocess.run(
            [rel, arg],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return None


def _check_probe(rel: str, arg: str, failures: list[str], skips: list[str]) -> None:
    proc = _run(rel, arg)
    if proc is None:
        skips.append(f"{rel} {arg}: interpreter not found (skipped)")
        return
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        failures.append(f"{rel} {arg}: exit {proc.returncode} (safe probe must exit 0)\n    {out.strip()[:200]}")
        return
    if arg == "--version":
        # A version probe prints a version string (has a digit), not usage text.
        if not any(c.isdigit() for c in out):
            failures.append(f"{rel} {arg}: output has no version string\n    {out.strip()[:200]}")
            return
    elif "usage" not in out.lower():
        failures.append(f"{rel} {arg}: output has no usage text (did it run real behavior?)\n    {out.strip()[:200]}")
        return
    for bad in FORBIDDEN:
        if bad in out:
            failures.append(f"{rel} {arg}: output contains forbidden marker {bad!r} (side effect / error / real work)")
            return


def _check_no_mutation(failures: list[str], skips: list[str]) -> None:
    """Regression guard for the HEADLINE bug: sync-memory-skill.rs --help must not write files.

    Build an isolated fake repo root with one `core_memory` that maps to a flat skill, point the
    tool at it, run --help, and assert the skills directory stays EMPTY. Before the fix this created
    <sandbox>/.claude/skills/demo-core.md."""
    tool = ROOT / "scripts" / "sync-memory-skill.rs"
    if not tool.exists():
        skips.append("mutation guard: sync-memory-skill.rs missing (skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        sb = Path(td)
        (sb / "hermit").mkdir()
        (sb / "reverie").mkdir()
        skills = sb / ".claude" / "skills"
        skills.mkdir(parents=True)
        (sb / ".gitmodules").write_text("")
        mem = sb / "mem"
        mem.mkdir()
        (mem / "demo-core.md").write_text(
            "---\nname: demo-core\ndescription: \"demo\"\nmetadata:\n"
            "  core_memory: true\n  core_skill: .claude/skills/demo-core.md\n"
            "  node_type: memory\n  type: reference\n---\n\nBody.\n"
        )
        env = dict(os.environ, HERMIT_MEMORY_DIR=str(mem))
        try:
            proc = subprocess.run(
                [str(tool), "--help"], cwd=sb, env=env,
                capture_output=True, text=True, timeout=300,
            )
        except FileNotFoundError:
            skips.append("mutation guard: rust-script not found (skipped)")
            return
        wrote = list(skills.iterdir())
        if wrote:
            names = ", ".join(p.name for p in wrote)
            failures.append(f"MUTATION: sync-memory-skill.rs --help wrote into the skills dir: {names}")
        elif proc.returncode != 0:
            failures.append(f"mutation guard: sync-memory-skill.rs --help exit {proc.returncode} in sandbox")


def main() -> int:
    failures: list[str] = []
    skips: list[str] = []
    n = 0
    for rel, args in ENTRYPOINTS:
        for arg in args:
            n += 1
            _check_probe(rel, arg, failures, skips)
    _check_no_mutation(failures, skips)

    for s in skips:
        print(f"check-harness-help: SKIP {s}")
    if failures:
        print("check-harness-help: FAIL -- safe probes are not pure:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(
        f"check-harness-help: ok -- {n} safe-probe invocations across "
        f"{len(ENTRYPOINTS)} harness entrypoints print usage, exit 0, and cause no side effects "
        f"(incl. mutation guard on sync-memory-skill.rs --help)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
