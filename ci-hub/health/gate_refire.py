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
import time
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


def gh_json(args: Sequence[str], timeout: int):
    """One gh call with a HARD per-call timeout.

    An unbounded call is how this tool first shipped and it is why it timed out at
    62s against tick-hub's 30s budget -- producing NO RESULT every tick, which is
    the exact failure mode the tool exists to detect. Every call is bounded now.
    """
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

    The newest run per workflow name wins: an older cancelled gate beside a newer
    successful one is not a parked gate, and a stale green leg beside a fresh
    queued one is not a completed leg. Reading the list unordered inverts both.
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
        return PARKED_WAIT, gid, concl, legs
    if any(not v.startswith("completed/") for v in legs.values()):
        return PARKED_WAIT, gid, concl, legs
    return REFIRE_DUE, gid, concl, legs


@dataclass
class Survey:
    verdicts: list = field(default_factory=list)
    heads_total: int = 0
    heads_covered: int = 0
    truncated: bool = False
    error: str = ""


def survey_per_pr(repo: str, limit: int, call_timeout: int) -> Survey:
    """EXHAUSTIVE: one runs-query per open PR head. Accurate, and far too slow for a
    tick (measured 62s over 121 PRs). For manual triage, never for the timed gate."""
    s = Survey()
    prs = gh_json(["pr", "list", "--repo", repo, "--state", "open",
                   "--limit", str(limit), "--json", "number,headRefOid"], call_timeout)
    if prs is None:
        s.error = "could not list open PRs"
        return s
    s.heads_total = len(prs)
    for pr in prs:
        head = pr.get("headRefOid") or ""
        data = gh_json(["api", f"repos/{repo}/actions/runs?head_sha={head}&per_page=50"],
                       call_timeout)
        if data is None:
            continue
        s.heads_covered += 1
        state, gid, concl, legs = classify_head(data.get("workflow_runs") or [])
        s.verdicts.append(PrVerdict(number=pr["number"], head=head, state=state,
                                    gate_run_id=gid, gate_conclusion=concl, legs=legs))
    return s


def survey(repo: str, limit: int, deadline_secs: float, call_timeout: int,
           runs_per_workflow: int) -> Survey:
    """Classify open PR heads using O(1) API calls, not one per PR.

    THE ORIGINAL SHAPE WAS THE BUG: one `actions/runs?head_sha=` call per open PR,
    121 calls, 62s. Instead, list the recent runs of each workflow ONCE and
    correlate by head_sha in memory -- 1 + len(workflows) calls regardless of PR
    count.

    Bounded on every axis, and every bound is REPORTED rather than applied
    silently: a run window that does not reach a PR's head leaves that head
    uncovered, and `heads_covered/heads_total` says so.
    """
    started = time.monotonic()
    s = Survey()

    prs = gh_json(["pr", "list", "--repo", repo, "--state", "open",
                   "--limit", str(limit), "--json", "number,headRefOid"], call_timeout)
    if prs is None:
        s.error = "could not list open PRs (call failed or timed out)"
        return s
    heads = {p["headRefOid"]: p["number"] for p in prs if p.get("headRefOid")}
    s.heads_total = len(heads)

    # PER-WORKFLOW windows, not one mixed window. A single unfiltered 100-run page
    # is dominated by whichever workflow ran most recently and reached only 7 of 121
    # heads in practice -- technically honest but useless. One page per workflow
    # gives each its own window, so head coverage scales with the workflows that
    # matter rather than with total repo activity.
    wfs = gh_json(["api", f"repos/{repo}/actions/workflows?per_page=100"], call_timeout)
    if wfs is None:
        s.truncated = True
        s.error = "could not list workflows (call failed or timed out)"
        return s
    wanted = {GATE_WORKFLOW, *LEG_WORKFLOWS}
    ids = {w["name"]: w["id"] for w in (wfs.get("workflows") or [])
           if w.get("name") in wanted}
    missing = wanted - set(ids)
    if missing:
        # A renamed workflow must be loud: silently dropping it would shrink the
        # wait-set and turn PARKED_WAIT into a false REFIRE_DUE.
        s.truncated = True
        s.error = f"workflow(s) not found by name: {sorted(missing)}"
        return s

    by_head: dict[str, list] = {}
    for name, wid in ids.items():
        if time.monotonic() - started > deadline_secs:
            s.truncated = True
            s.error = f"deadline {deadline_secs}s exceeded before reading '{name}'"
            return s
        for page in (1,):
            data = gh_json(["api", f"repos/{repo}/actions/workflows/{wid}/runs"
                                   f"?per_page={runs_per_workflow}&page={page}"],
                           call_timeout)
            if data is None:
                s.truncated = True
                s.error = f"could not read runs for '{name}' (call failed or timed out)"
                return s
            runs = data.get("workflow_runs") or []
            for r in runs:
                r.setdefault("name", name)
                h = r.get("head_sha")
                if h in heads:
                    by_head.setdefault(h, []).append(r)
            if len(runs) < runs_per_workflow:
                break

    for head, num in heads.items():
        runs = by_head.get(head)
        if runs is None:
            continue  # head outside the run window -- counted as uncovered below
        s.heads_covered += 1
        state, gid, concl, legs = classify_head(runs)
        v = PrVerdict(number=num, head=head, state=state,
                      gate_run_id=gid, gate_conclusion=concl, legs=legs)
        if state == REFIRE_DUE:
            v.reason = "gate CANCELLED and every leg completed -> refire is safe"
        elif state == PARKED_WAIT:
            v.reason = ("gate CANCELLED, leg(s) still running -> refiring would "
                        "re-dispatch and re-cancel (busy-loop)")
        s.verdicts.append(v)
    return s


def render(s: "Survey", repo: str, elapsed: float) -> str:
    counts: dict[str, int] = {}
    for v in s.verdicts:
        counts[v.state] = counts.get(v.state, 0) + 1
    L = [f"merge-gate refire watchdog -- {repo}"]
    L.append(f"  open PR heads          : {s.heads_total}")
    L.append(f"  heads covered by window: {s.heads_covered}/{s.heads_total}"
             + ("  <- REST OUTSIDE THE RUN WINDOW, not classified"
                if s.heads_covered < s.heads_total else ""))
    L.append(f"  elapsed                : {elapsed:.1f}s")
    if s.error:
        L.append(f"  PARTIAL DATA           : {s.error}")
    bounded = s.heads_covered < s.heads_total or counts.get(NO_GATE, 0)
    for k in (REFIRE_DUE, PARKED_WAIT, GATE_OK, NO_GATE):
        mark = " (LOWER BOUND)" if bounded and k == REFIRE_DUE else ""
        L.append(f"  {k:<12} {counts.get(k,0)}{mark}")
    if bounded:
        L.append("  NOTE: NO_GATE here means 'no gate run inside the sampled window',")
        L.append("        NOT 'no gate run exists'. The bounded scan UNDER-REPORTS due")
        L.append("        PRs by design so it fits tick-hub's 30s budget; measured")
        L.append("        2026-08-07 it found 2 where the exhaustive --per-pr scan")
        L.append("        found 41. Use --per-pr for the real census (~62s).")
    due = [v for v in s.verdicts if v.state == REFIRE_DUE]
    wait = [v for v in s.verdicts if v.state == PARKED_WAIT]
    if due:
        L.append("")
        L.append("  REFIRE DUE (gate parked, all legs finished):")
        for v in due[:25]:
            L.append(f"    #{v.number} run={v.gate_run_id} legs={v.legs}")
        if len(due) > 25:
            L.append(f"    ... and {len(due)-25} more (display cap, not a data cap)")
    if wait:
        L.append("")
        L.append("  PARKED, STILL WAITING (must NOT refire):")
        for v in wait:
            L.append(f"    #{v.number} run={v.gate_run_id} legs={v.legs or '{}'}")
    return "\n".join(L)


def do_refire(repo: str, verdicts: Sequence[PrVerdict], call_timeout: int = 180) -> int:
    """Re-dispatch only the gates proven DUE. Never touches PARKED_WAIT."""
    fired = 0
    for v in verdicts:
        if v.state != REFIRE_DUE or not v.gate_run_id:
            continue
        p = subprocess.run(["with-proxy", "gh", "run", "rerun", str(v.gate_run_id),
                            "--repo", repo], capture_output=True, text=True,
                           timeout=call_timeout)
        ok = p.returncode == 0
        print(f"  refire #{v.number} run={v.gate_run_id}: "
              f"{'ok' if ok else p.stderr.strip()[:80]}")
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
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--deadline-secs", type=float, default=24.0,
                    help="hard budget; under tick-hub's 30s gate timeout")
    ap.add_argument("--call-timeout", type=int, default=12)
    ap.add_argument("--runs-per-workflow", type=int, default=100)
    ap.add_argument("--per-pr", action="store_true",
                    help="exhaustive one-query-per-PR census; accurate, ~62s, NOT for the gate")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    s = (survey_per_pr(args.repo, args.limit, args.call_timeout) if args.per_pr
         else survey(args.repo, args.limit, args.deadline_secs, args.call_timeout,
                     args.runs_per_workflow))
    elapsed = time.monotonic() - t0
    if args.json:
        print(json.dumps({"heads_total": s.heads_total,
                          "heads_covered": s.heads_covered,
                          "truncated": s.truncated, "error": s.error,
                          "elapsed_secs": round(elapsed, 2),
                          "verdicts": [v.to_dict() for v in s.verdicts]}, indent=2))
    else:
        print(render(s, args.repo, elapsed))

    # PARTIAL DATA IS NOT A CLEAN RESULT. A check that times out or truncates must
    # be distinguishable from one that looked and found nothing -- that confusion is
    # the whole defect this tool exists to catch, and it is not going to be
    # reproduced here.
    if s.error or s.truncated:
        print(f"REFUSED: partial data ({s.error or 'truncated'}); no verdict claimed",
              file=sys.stderr)
        return 2

    due = sum(1 for v in s.verdicts if v.state == REFIRE_DUE)
    if args.refire and due:
        do_refire(args.repo, s.verdicts, args.call_timeout)
    if args.fail_on_due:
        return 1 if due else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
