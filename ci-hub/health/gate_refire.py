#!/usr/bin/env python3
"""Detect merge gates that parked on CANCELLED and will never be refired.

THE DEADLOCK, established from the runs themselves rather than inferred.
`merge-gate-v4` is fail-closed by design: when a required leg is queued or
missing it records NO_RESULT, RE-DISPATCHES the leg, and CANCELS ITSELF, leaving
`refire-on-ci-completion` to restart it when the leg finishes. That job is wired to

    on: workflow_run:
      workflows: ["CI (GitHub-managed portable)", "CI (privileged)", "P0 Demo Gate ..."]
      types: [completed]

and it is correct. It still cannot fire, because the gate re-dispatches those
workflows with `GH_TOKEN: ${{ github.token }}`, so the resulting runs carry
`event=workflow_dispatch, triggering_actor=github-actions[bot]` -- and GitHub
suppresses `workflow_run` events for runs triggered by `GITHUB_TOKEN` (the
recursion guard). Measured on hermit#1750 at head 2637391f: both
`CI (GitHub-managed portable)` and `CI (privileged)` completed SUCCESS with
`actor=github-actions[bot]`, and no refire ever arrived.

So the gate's own re-dispatch is what guarantees its refire will not come. This is
a structural deadlock, not a flake, and it is why a PR can sit BLOCKED with 30/30
checks green and no red anywhere -- invisible in every count-based view, because
"blocked" and "waiting on a message that will never be delivered" render the same.

WHAT THIS TOOL DOES, AND DELIBERATELY DOES NOT DO. It classifies; it does not merge
and it does not land. `refire` re-dispatches the gate run only for PRs it has
already proven are in the DUE state. The distinction that matters:

  REFIRE_DUE   gate CANCELLED and EVERY leg it waits on is completed -> safe to
               refire; the gate will re-evaluate against finished legs.
  PARKED_WAIT  gate CANCELLED but at least one leg is still queued/in_progress ->
               MUST NOT refire. Refiring here re-runs the same NO_RESULT
               computation, re-dispatches the legs again, and cancels again -- a
               busy-loop that also starves the runners the legs need.

That asymmetry is the whole design: refiring early is not merely useless, it is
actively harmful, so the incomplete-leg case is a hard refusal rather than a
best-effort skip.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Iterable, Sequence

GATE_WORKFLOW = "Merge Gate"
# The legs merge-gate-v4 waits on. Kept as data so a workflow rename shows up as an
# unmatched leg rather than silently reducing the wait-set to nothing.
LEG_WORKFLOWS = (
    "CI (GitHub-managed portable)",
    "CI (privileged)",
    "P0 Demo Gate (Hermit hot paths)",
)

REFIRE_DUE = "REFIRE_DUE"
PARKED_WAIT = "PARKED_WAIT"
GATE_OK = "GATE_OK"
NO_GATE = "NO_GATE"


def gh_json(args: Sequence[str], timeout: int = 180):
    try:
        p = subprocess.run(["with-proxy", "gh", *args], capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


@dataclass
class PrVerdict:
    number: int
    head: str
    state: str
    gate_run_id: int | None = None
    gate_conclusion: str | None = None
    legs: dict = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def classify_head(runs: Iterable[dict]) -> tuple[str, int | None, str | None, dict]:
    """Classify one head from its workflow runs.

    `runs` is every workflow run at the PR head. The newest run per workflow name
    wins: an older cancelled gate beside a newer successful one is not a parked
    gate, and reading the list unordered would invert that.
    """
    newest: dict[str, dict] = {}
    for r in runs:
        name = r.get("name") or ""
        prev = newest.get(name)
        if prev is None or (r.get("run_started_at") or "") >= (prev.get("run_started_at") or ""):
            newest[name] = r

    gate = newest.get(GATE_WORKFLOW)
    if gate is None:
        return NO_GATE, None, None, {}

    gid = gate.get("id")
    concl = gate.get("conclusion")
    if concl != "cancelled":
        return GATE_OK, gid, concl, {}

    legs = {n: f"{newest[n].get('status')}/{newest[n].get('conclusion') or '-'}"
            for n in LEG_WORKFLOWS if n in newest}
    if not legs:
        # Parked with nothing to wait on that we can see. NOT due: refiring would
        # recompute the same NO_RESULT. Report it rather than guessing.
        return PARKED_WAIT, gid, concl, legs
    incomplete = {n: v for n, v in legs.items() if not v.startswith("completed/")}
    if incomplete:
        return PARKED_WAIT, gid, concl, legs
    return REFIRE_DUE, gid, concl, legs


def survey(repo: str, limit: int = 200) -> list[PrVerdict]:
    prs = gh_json(["pr", "list", "--repo", repo, "--state", "open",
                   "--limit", str(limit), "--json", "number,headRefOid"]) or []
    out: list[PrVerdict] = []
    for pr in prs:
        head = pr.get("headRefOid") or ""
        data = gh_json(["api", f"repos/{repo}/actions/runs?head_sha={head}&per_page=50"])
        runs = (data or {}).get("workflow_runs") or []
        state, gid, concl, legs = classify_head(runs)
        v = PrVerdict(number=pr["number"], head=head, state=state,
                      gate_run_id=gid, gate_conclusion=concl, legs=legs)
        if state == REFIRE_DUE:
            v.reason = "gate CANCELLED and every leg completed -> refire is safe"
        elif state == PARKED_WAIT:
            v.reason = ("gate CANCELLED, leg(s) still running -> refiring would "
                        "re-dispatch and re-cancel (busy-loop)")
        out.append(v)
    return out


def render(verdicts: Sequence[PrVerdict], repo: str) -> str:
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.state] = counts.get(v.state, 0) + 1
    L = [f"merge-gate refire watchdog -- {repo}", f"  open PRs examined: {len(verdicts)}"]
    for k in (REFIRE_DUE, PARKED_WAIT, GATE_OK, NO_GATE):
        L.append(f"  {k:<12} {counts.get(k,0)}")
    due = [v for v in verdicts if v.state == REFIRE_DUE]
    wait = [v for v in verdicts if v.state == PARKED_WAIT]
    if due:
        L.append("")
        L.append("  REFIRE DUE (gate parked, all legs finished):")
        for v in due:
            L.append(f"    #{v.number} run={v.gate_run_id} legs={v.legs}")
    if wait:
        L.append("")
        L.append("  PARKED, STILL WAITING (must NOT refire):")
        for v in wait:
            L.append(f"    #{v.number} run={v.gate_run_id} legs={v.legs or '{}'}")
    return "\n".join(L)


def do_refire(repo: str, verdicts: Sequence[PrVerdict]) -> int:
    """Re-dispatch only the gates proven DUE. Never touches PARKED_WAIT."""
    fired = 0
    for v in verdicts:
        if v.state != REFIRE_DUE or not v.gate_run_id:
            continue
        p = subprocess.run(["with-proxy", "gh", "run", "rerun", str(v.gate_run_id),
                            "--repo", repo], capture_output=True, text=True, timeout=180)
        ok = p.returncode == 0
        print(f"  refire #{v.number} run={v.gate_run_id}: {'ok' if ok else p.stderr.strip()[:80]}")
        fired += 1 if ok else 0
    return fired


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="rrnewton/hermit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--refire", action="store_true",
                    help="re-dispatch gates proven REFIRE_DUE (never PARKED_WAIT)")
    ap.add_argument("--fail-on-due", action="store_true",
                    help="exit 1 if any PR is REFIRE_DUE (page instead of act)")
    args = ap.parse_args(argv)
    verdicts = survey(args.repo)
    if args.json:
        print(json.dumps([v.to_dict() for v in verdicts], indent=2))
    else:
        print(render(verdicts, args.repo))
    due = sum(1 for v in verdicts if v.state == REFIRE_DUE)
    if args.refire and due:
        do_refire(args.repo, verdicts)
    if args.fail_on_due:
        return 1 if due else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
