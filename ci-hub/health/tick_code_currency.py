#!/usr/bin/env python3
"""Page when the tick is executing code that is not what landed on main.

WHY THIS EXISTS. The tick runs from the SHARED PARENT WORKING TREE:
`hermitOperationalTick` does `cd <parent> && ./ci-hub/bin/health-tick`, and
`health-tick` resolves ROOT from its own location. So the code the tick executes
is whatever is on disk right now -- not what is on `origin/main`.

That is the correct design and this file does not change it. The shared parent
is the ONLY tree on this box that carries every gate's subject: a linked
worktree pinned at `origin/main` shares `.git` refs (so ref-based gates survive)
but does NOT carry gitignored directories or uninitialised submodules, so
`ignored/ci-hub/agent-snapshot.json`, `hermit/`, and `.tick-hub` are simply
absent there. Measured 2026-08-08, same tool and same moment:

    slot_disk_residue from the shared parent : 0 reclaimable of 77 on disk (49 occupied)
    slot_disk_residue from a pinned worktree : 0 reclaimable of  0 on disk ( 0 occupied)

0-of-77 becomes 0-OF-0, exit 0, plausible summary, nothing scanned. Pinning
would trade today's LOUD failure for a silent one inside the monitoring layer.

WHAT ACTUALLY FAILED, TWICE, ON THE SAME GATE. The parent is chronically behind
`origin/main` (it cannot fast-forward while other agents hold dirty paths), and
a behind-checkout displays LANDED content as MODIFIED. So `git restore` -- the
most ordinary cleanup command there is -- silently rewinds live tick code to a
pre-fix version. Nobody has to be careless. On 2026-08-08 `unpushed_parent_commits`
was fixed from a 30s timeout to 0.24s, ran clean for several ticks, and was then
reverted by exactly that command; the whole tick went 4s -> 36s and the detector
for unpushed work went blind again.

THE PROPERTY THIS RESTORES IS NOT "the fix cannot be reverted" -- nothing can
give that, and a required human step is a step that eventually does not happen.
It is that **a reverted fix cannot sit silently.** Drift pages on the next tick,
naming the paths, instead of waiting for someone to notice a gate has gone quiet.

FAIL CLOSED, AND PER PATH. If currency cannot be established (no `origin/main`
ref, unreadable file, git failure) that is `unknown`, never `current` -- "I could
not check" must not read as "it matches". Reporting is per path so one drifted
tool names itself rather than casting doubt over all thirteen gates.

DETECT ONLY. Reads git and prints. It never restores, fetches, checks out, or
edits anything -- repairing drift means landing or syncing, both of which are
decisions with owners.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
TICK_CONFIG = ROOT / "ci-hub" / "health" / "tick-hub.yaml"
TARGET_REF = "origin/main"

# Tool paths referenced by a gate command in tick-hub.yaml. The config itself is
# always policed: a reverted tick-hub.yaml changes which command runs at all,
# which is exactly how the 2026-08-08 regression re-armed `--rescue`.
_TOOL_RE = re.compile(
    r"(?:\./)?((?:ci-hub|scripts|agent-utils)/[A-Za-z0-9_./-]+\.(?:py|rs|sh))"
)

CURRENT = 0
DRIFTED = 1
UNKNOWN = 2


@dataclass(frozen=True)
class Verdict:
    path: str
    state: str  # "current" | "stale" | "modified" | "unknown"
    detail: str = ""


# STALE and MODIFIED are both "not what landed", and conflating them would make
# this gate useless. Measured 2026-08-08: all four then-drifting tick tools were
# clean-but-behind, the exact silent-reversion signature; meanwhile agents edit
# gate tools constantly, and paging on that would train everyone to ignore this.
#
#   stale     working tree == HEAD's content, and HEAD's content != origin/main
#             -> a landed version is NOT the one executing. The incident. PAGE.
#   modified  differs from HEAD too -> somebody's live work-in-progress. Report,
#             do not page: it is expected, and it is already visible as dirt.
#
# The discriminator is deliberately content-based, not status-based: after a
# `git restore` the file is CLEAN by `git status`, which is precisely why the
# reversion was invisible the first two times.


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True
    )


def policed_paths(config: Path = TICK_CONFIG) -> list[str]:
    """Every tool a gate invokes, plus the config that decides the invocation."""
    try:
        text = config.read_text()
    except OSError:
        return []
    found = set(_TOOL_RE.findall(text))
    try:
        found.add(str(config.resolve().relative_to(ROOT.resolve())))
    except ValueError:
        # A config outside ROOT (tests, or an explicit --config elsewhere): police
        # it by the path we were given rather than dropping it silently. Losing
        # the config from the set would be the worst omission here -- it is what
        # decides which command each gate runs.
        found.add(str(config))
    return sorted(found)


def compare(paths: Sequence[str], ref: str = TARGET_REF) -> list[Verdict]:
    """Working-tree content versus `ref`, per path, fail-closed to unknown."""
    if not paths:
        return [Verdict(str(TICK_CONFIG), "unknown", "no gate tools parsed from tick-hub.yaml")]
    resolved = _git("rev-parse", "--verify", f"{ref}^{{commit}}")
    if resolved.returncode != 0:
        return [
            Verdict(p, "unknown", f"cannot resolve {ref}: {resolved.stderr.strip()[:60]}")
            for p in paths
        ]
    out: list[Verdict] = []
    for path in paths:
        # `git diff --quiet <ref> -- <path>` exits 0 when identical, 1 when it
        # differs, and >1 on error. The error case must not be read as either.
        diff = _git("diff", "--quiet", ref, "--", path)
        if diff.returncode == 0:
            out.append(Verdict(path, "current"))
            continue
        if diff.returncode != 1:
            out.append(
                Verdict(path, "unknown", (diff.stderr or "git diff failed").strip()[:80])
            )
            continue
        # Differs from the target. Split the dangerous case from the ordinary one.
        local = _git("diff", "--quiet", "HEAD", "--", path)
        if local.returncode == 0:
            out.append(Verdict(
                path, "stale",
                f"clean at local HEAD but {ref} has moved -- the landed version is "
                f"not the one executing",
            ))
        elif local.returncode == 1:
            out.append(Verdict(path, "modified", "uncommitted local edits (work in progress)"))
        else:
            out.append(
                Verdict(path, "unknown", (local.stderr or "git diff failed").strip()[:80])
            )
    return out


def render(verdicts: Sequence[Verdict], ref: str = TARGET_REF) -> tuple[int, str]:
    stale = [v for v in verdicts if v.state == "stale"]
    modified = [v for v in verdicts if v.state == "modified"]
    unknown = [v for v in verdicts if v.state == "unknown"]
    lines = []
    for v in stale:
        lines.append(f"  STALE     {v.path}  ({v.detail})")
    for v in unknown:
        lines.append(f"  UNKNOWN   {v.path}  ({v.detail})")
    for v in modified:
        lines.append(f"  modified  {v.path}  ({v.detail})")

    if stale:
        subjects = ", ".join(v.path for v in stale[:3])
        residue = f" (+{len(stale) - 3} more)" if len(stale) > 3 else ""
        summary = (
            f"{len(stale)} of {len(verdicts)} tick tool(s) are STALE against {ref} -- "
            f"the tick is executing a version older than what landed, and `git status` "
            f"shows them clean: {subjects}{residue}. Sync the parent."
        )
        code = DRIFTED
    elif unknown:
        summary = (
            f"could not establish currency for {len(unknown)} of {len(verdicts)} tick "
            f"tool(s) against {ref}"
        )
        code = UNKNOWN
    else:
        # Work-in-progress is reported but never paged: it is expected, and it is
        # already visible as dirt to anyone who looks.
        wip = f"; {len(modified)} carrying work-in-progress edits" if modified else ""
        summary = f"all {len(verdicts)} tick tool(s) match {ref}{wip}"
        code = CURRENT

    lines.append(f"summary={summary}")
    return code, "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ref", default=TARGET_REF, help="currency target (default origin/main)")
    ap.add_argument("--config", type=Path, default=TICK_CONFIG)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument(
        "--gate", action="store_true",
        help="exit 1 on drift and 2 on unknown; without it the report still prints "
             "but always exits 0",
    )
    args = ap.parse_args(argv)

    verdicts = compare(policed_paths(args.config), args.ref)
    code, text = render(verdicts, args.ref)
    if args.as_json:
        print(json.dumps(
            {"ref": args.ref, "code": code,
             "verdicts": [{"path": v.path, "state": v.state, "detail": v.detail}
                          for v in verdicts]},
            indent=2, sort_keys=True))
    else:
        print(text)
    return code if args.gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
