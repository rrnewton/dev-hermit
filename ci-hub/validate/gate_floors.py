#!/usr/bin/env python3
"""Enumerate every rebase-base floor and DERIVE the current EFFECTIVE floor.

THE PROBLEM (memory: merge-gate-v2-floor-invalidates-pre-floor-greens,
validate-producer-version-coupled-rebase-before-greening). A commit can be fully
GREEN and still sit on the WRONG SIDE of a schema transition: some consumer
FAILS CLOSED on a field that did not exist before a particular commit, so every
head predating that commit is refused no matter how green. Each such transition
is a FLOOR. Two kinds have been seen, identical in structure:
  * producer-anchor -- the producer (validate.sh) predating the floor emits the
    required receipt field NULL; the fail-closed certifier rejects every record.
  * merge-gate -- the head predating the floor validates GREEN yet the merge gate
    / branch protection refuses the landing.

A rebase base must clear EVERY floor, not the one someone remembers. The floor
must not live in an agent's head (or a lone hard-coded constant): this tool reads
the enumerated registry (rebase-base-floors.json) and DERIVES, at call time, the
EFFECTIVE FLOOR = the NEWEST floor on the branch's first-parent history. A
consumer (e.g. hermit-251's newest-green-main) gates its "green" answer on the
`effective_floor` this tool returns.

WHAT IT DOES.
  * default (publish): walk `origin/<branch>` first-parent history; locate every
    registry floor in it; the EFFECTIVE floor is the one CLOSEST to the tip
    (smallest first-parent index). If ANY floor is not on that history, REFUSE
    (exit 2) -- refusing to guess a usable floor is the point. In a shallow
    repository, report UNVERIFIABLE-SHALLOW-HISTORY with the visible depth and
    the full-history requirement instead of claiming no green exists. Prints
    the full enumeration and the effective floor.
  * --head <sha-or-ref>: does this candidate base/head CLEAR ALL floors (contain
    every floor commit)? exit 0 if it clears all; exit 2 (REFUSED) naming the
    specific floor(s) it predates. This is the both-directions verifier: a
    pre-floor head is refused as a base even when green; a post-floor head passes.

    exit 0  OK        -- effective floor derived (publish) / head clears all floors
    exit 2  REFUSED or UNVERIFIABLE-SHALLOW-HISTORY
                       -- a floor is off visible history / head predates a floor
    exit 3  ERROR     -- could not read the registry or resolve git history

The refusal-when-empty is deliberate: when GREEN and at-or-after-the-effective-
floor have an EMPTY INTERSECTION, the consumer must refuse, never fall back to a
pre-floor "green" base. This tool supplies the floor half of that contract; the
green half lives in the ledger query (ci-hub newest-green / newest-green-main).

Usage:
  gate_floors.py [--branch main] [--head <sha-or-ref>]
                 [--repo-checkout <path>] [--registry <path>]
                 [--no-fetch] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = "rrnewton/hermit"
DEFAULT_CHECKOUT = "/home/newton/work/dev-hermit/hermit"
DEFAULT_REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "rebase-base-floors.json")
LOCAL_TIMEOUT = 30.0
NETWORK_TIMEOUT = 120.0

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_ERROR = 3

VERDICT_EFFECTIVE = "EFFECTIVE-FLOOR"
VERDICT_REFUSED = "REFUSED"
VERDICT_SHALLOW = "UNVERIFIABLE-SHALLOW-HISTORY"


class FloorError(Exception):
    """Registry unreadable or git history unresolvable (an ERROR, not a REFUSE)."""


def _short(sha: str) -> str:
    return sha[:12] if sha else "?"


def load_floors(path: str) -> list[dict]:
    """Return the enumerated floor objects (the `anchors` array).

    A missing/malformed registry is an ERROR, not an empty OK -- silently
    returning no floors would let every pre-floor base through. Every enforced
    floor's `sha` must be 40-hex; a placeholder (TBD) belongs in `_pending`,
    never in `anchors`, because it would refuse (or misgate) every head.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as err:
        raise FloorError(f"cannot read floor registry {path}: {err}") from err
    if isinstance(doc, list):
        raw = doc
    elif isinstance(doc, dict):
        raw = doc.get("anchors", [])
    else:
        raise FloorError(f"floor registry {path} is not an object or array")
    floors = []
    for entry in raw:
        sha = (entry.get("sha") or "").strip().lower()
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise FloorError(
                f"floor entry has a non-40-hex sha {entry.get('sha')!r} in "
                f"{path}; a placeholder floor would misgate every head -- keep "
                "it in `_pending` until its commit lands")
        floors.append({
            "sha": sha,
            "kind": entry.get("kind") or "?",
            "field": entry.get("field") or "?",
            "landed_utc": entry.get("landed_utc") or "?",
            "consumer": entry.get("consumer") or "",
            "reason": entry.get("reason") or "",
        })
    if not floors:
        raise FloorError(
            f"floor registry {path} enumerates zero floors; refusing to treat "
            "an empty registry as 'no floor' -- that would pass every base")
    return floors


def _run(cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def fetch_branch(checkout: str, branch: str) -> None:
    """Refresh origin/<branch> so the derived floor reflects live history."""
    refspec = f"refs/heads/{branch}:refs/remotes/origin/{branch}"
    cmd = ["git", "-C", checkout, "fetch", "--quiet", "origin", refspec]
    if _on_path("with-proxy"):
        cmd = ["with-proxy", *cmd]
    cp = _run(cmd, timeout=NETWORK_TIMEOUT)
    if cp.returncode != 0:
        raise FloorError(
            f"git fetch origin/{branch} failed: {(cp.stderr or cp.stdout).strip()}")


def _on_path(prog: str) -> bool:
    return any(os.access(os.path.join(d, prog), os.X_OK)
               for d in os.environ.get("PATH", "").split(os.pathsep) if d)


def repository_state(checkout: str) -> dict:
    """Return repository identity and depth without letting git climb upward.

    `git -C empty/submodule rev-parse` otherwise discovers the parent repository,
    which can turn an uninitialized product checkout into a plausible history
    answer about the wrong repository. Check the exact checkout's `.git` marker
    first; `--show-toplevel` is not usable on this fleet because a work-tree
    checkout may deliberately carry `core.bare=true`.
    """
    expected = str(Path(checkout).resolve())
    marker = Path(expected, ".git")
    if not marker.exists():
        raise FloorError(
            f"UNVERIFIABLE-CHECKOUT: requested checkout {expected} is not an "
            f"initialized repository (missing {marker})")
    cp = _run(["git", "-C", checkout, "rev-parse", "--absolute-git-dir",
               "--is-shallow-repository"], timeout=LOCAL_TIMEOUT)
    if cp.returncode != 0:
        raise FloorError(
            f"UNVERIFIABLE-CHECKOUT: cannot inspect repository at {checkout}: "
            f"{(cp.stderr or cp.stdout).strip()}")
    lines = [line.strip() for line in cp.stdout.splitlines() if line.strip()]
    if len(lines) != 2 or lines[1] not in ("true", "false"):
        raise FloorError(
            f"UNVERIFIABLE-CHECKOUT: git returned unexpected repository state "
            f"for {checkout}: {cp.stdout.strip()!r}")
    return {
        "checkout": expected,
        "git_dir": str(Path(lines[0]).resolve()),
        "is_shallow": lines[1] == "true",
    }


def first_parent(checkout: str, branch_ref: str) -> list[str]:
    """First-parent commit list of branch_ref, newest first (index 0 == tip)."""
    cp = _run(["git", "-C", checkout, "rev-list", "--first-parent", branch_ref],
              timeout=LOCAL_TIMEOUT)
    if cp.returncode != 0:
        raise FloorError(
            f"cannot walk {branch_ref}: {(cp.stderr or cp.stdout).strip()}")
    commits = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    if not commits:
        raise FloorError(f"{branch_ref} has no commits")
    return commits


def is_ancestor(checkout: str, ancestor: str, head: str) -> bool:
    """True iff `head` CONTAINS `ancestor` (git merge-base --is-ancestor)."""
    for rev in (ancestor, head):
        cp = _run(["git", "-C", checkout, "rev-parse", "--verify", "-q",
                   f"{rev}^{{commit}}"], timeout=LOCAL_TIMEOUT)
        if cp.returncode != 0:
            raise FloorError(f"cannot resolve revision {rev} in {checkout}")
    cp = _run(["git", "-C", checkout, "merge-base", "--is-ancestor",
               ancestor, head], timeout=LOCAL_TIMEOUT)
    if cp.returncode in (0, 1):
        return cp.returncode == 0
    raise FloorError(
        f"merge-base --is-ancestor {_short(ancestor)} {_short(head)} errored: "
        f"{(cp.stderr or cp.stdout).strip()}")


def derive_effective(floors: list[dict], commits: list[str]) -> dict:
    """Locate every floor on the first-parent list and pick the newest.

    Newest == smallest first-parent index (closest to the tip). A floor absent
    from the list is off-history: we RECORD it and REFUSE rather than guess.
    """
    index = {sha: i for i, sha in enumerate(commits)}
    resolved = []
    off_history = []
    for f in floors:
        i = index.get(f["sha"])
        rec = {**f, "first_parent_index": i, "on_history": i is not None}
        resolved.append(rec)
        if i is None:
            off_history.append(rec)
    # Newest first-parent floor = smallest index among those on history.
    on = [r for r in resolved if r["on_history"]]
    effective = min(on, key=lambda r: r["first_parent_index"]) if on else None
    for r in resolved:
        r["effective"] = effective is not None and r["sha"] == effective["sha"]
    return {
        "branch_tip": commits[0],
        "floors": sorted(resolved, key=lambda r: (r["first_parent_index"]
                                                  if r["on_history"] else 1 << 60)),
        "off_history": off_history,
        "effective_floor": effective["sha"] if effective else None,
        "effective_kind": effective["kind"] if effective else None,
        "effective_landed_utc": effective["landed_utc"] if effective else None,
        "ok": not off_history and effective is not None,
    }


def classify_history(res: dict, *, is_shallow: bool) -> dict:
    """Name a missing floor as unverifiable when history is truncated."""
    res["is_shallow"] = is_shallow
    if res["ok"]:
        res["verdict"] = VERDICT_EFFECTIVE
    elif is_shallow:
        res["verdict"] = VERDICT_SHALLOW
        res["required_history_depth"] = "full"
        res["required_history_depth_min"] = res["history_depth"] + 1
    else:
        res["verdict"] = VERDICT_REFUSED
    return res


def clears_all(floors: list[dict], checkout: str, head: str) -> dict:
    """Does `head` contain every floor? Names the floor(s) it predates."""
    resolved = []
    unmet = []
    for f in floors:
        contained = is_ancestor(checkout, f["sha"], head)
        rec = {**f, "contained": contained}
        resolved.append(rec)
        if not contained:
            unmet.append(rec)
    return {"head": head, "floors": resolved, "unmet": unmet, "ok": not unmet}


def render_publish(res: dict) -> str:
    lines = []
    if res["ok"]:
        eff = next(f for f in res["floors"] if f["effective"])
        lines.append(
            f"EFFECTIVE-FLOOR {_short(res['effective_floor'])} kind={eff['kind']} "
            f"landed={eff['landed_utc']} first-parent-index={eff['first_parent_index']} "
            f"tip={_short(res['branch_tip'])}")
    elif res.get("verdict") == VERDICT_SHALLOW:
        lines.append(
            f"{VERDICT_SHALLOW}: visible-first-parent-depth="
            f"{res['history_depth']} required-depth=full "
            f"required-depth-min={res['required_history_depth_min']} -- "
            f"{len(res['off_history'])} required floor(s) are outside the "
            f"visible shallow history; deepen or unshallow before deciding.")
    else:
        lines.append(
            f"REFUSE: cannot derive an effective floor -- "
            f"{len(res['off_history'])} floor(s) are NOT on this branch's "
            f"first-parent history; refusing to guess a usable floor.")
    for f in res["floors"]:
        mark = " <== EFFECTIVE" if f["effective"] else ""
        idx = (f"idx={f['first_parent_index']}" if f["on_history"]
               else "idx=OFF-HISTORY")
        lines.append(
            f"  {_short(f['sha'])} {f['kind']:<15} landed {f['landed_utc']} "
            f"{idx} field={f['field']}{mark}")
        if not f["on_history"]:
            lines.append(
                f"      OFF-HISTORY: this floor is not reachable on first-parent; "
                f"a base cannot be proven to clear it.")
    return "\n".join(lines)


def render_head(res: dict) -> str:
    if res["ok"]:
        return (f"OK: head {_short(res['head'])} clears all {len(res['floors'])} "
                f"rebase-base floors; it is a usable base w.r.t. schema floors.")
    lines = []
    for f in res["unmet"]:
        lines.append(
            f"REFUSE: head {_short(res['head'])} predates {f['kind']} floor "
            f"{_short(f['sha'])} ({f['field']}, landed {f['landed_utc']}); "
            f"{f['reason']} Rebase onto current origin/main (>= "
            f"{_short(f['sha'])}) before validating/landing.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--branch", default="main",
                    help="branch whose first-parent history is walked")
    ap.add_argument("--head",
                    help="check whether this head/base clears ALL floors")
    ap.add_argument("--repo-checkout", default=DEFAULT_CHECKOUT)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--no-fetch", action="store_true",
                    help="do not refresh origin/<branch> first (offline)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        floors = load_floors(args.registry)
        repo_state = repository_state(args.repo_checkout)
        if args.head:
            res = clears_all(floors, args.repo_checkout, args.head)
            body = render_head(res)
        else:
            if not args.no_fetch:
                fetch_branch(args.repo_checkout, args.branch)
            branch_ref = f"origin/{args.branch}"
            commits = first_parent(args.repo_checkout, branch_ref)
            res = derive_effective(floors, commits)
            res["history_depth"] = len(commits)
            res = classify_history(res, is_shallow=repo_state["is_shallow"])
            res["branch"] = args.branch
            body = render_publish(res)
    except FloorError as err:
        if args.json:
            print(json.dumps({"error": str(err)}, indent=2))
        else:
            print(f"ERROR: {err}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(body)
    return EXIT_OK if res["ok"] else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
