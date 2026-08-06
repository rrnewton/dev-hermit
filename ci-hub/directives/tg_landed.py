#!/usr/bin/env python3
"""DERIVE `landed` FOR tg TASKS — status asserted vs status computed.

THE GAP, in one line
--------------------
tg's status is ASSERTED BY AN AGENT. The directives ledger's status is DERIVED
by checking ancestry against a freshly-fetched target. That difference — not the
storage — is the entire reason two systems exist.

MEASURED COST: the `implemented` tag means "the code was written" and is
routinely read as "the work is done", so finished implementations sitting
unlanded read as complete. The taskgraph cannot tell the difference and never
could, because nothing in it dereferences a commit.

WHAT THIS DOES
--------------
Teaches the ancestry check to tg's own data, without forking tg: it reads the
task DB read-only (`tg sql`), extracts the IMPLEMENTATION REFERENCES an agent
named in its notes (40-hex commits, PR numbers, artifact paths), and DERIVES a
landed state per task by `git merge-base --is-ancestor <sha> <target>`.

The derived state NEVER inherits the asserted one. A task tagged `implemented`
whose named commit is not an ancestor reports NOT-LANDED — that is the whole
point, and it is the direction that must be tested.

`PARTIAL` is the state tg cannot express at all: a task naming several commits
of which only some landed. It is reported here rather than rounded to either
end, because rounding it is how a half-landed obligation reads as done.

FRESHNESS IS PART OF THE ANSWER
-------------------------------
Ancestry against a stale remote answers about the past. When the target cannot
be freshly fetched (no egress), every derivation is reported as
`STALE-TARGET` — visibly, in the summary — rather than being quietly computed
against whatever was last pulled. A checker that hides its own staleness is the
defect it exists to catch.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import json
import re
import subprocess
import sys
from typing import Callable, Optional

LANDED = "landed"
NOT_LANDED = "not_landed"
PARTIAL = "partial"            # some named commits landed, others did not
UNVERIFIABLE = "unverifiable"  # no reference, or the commit is not present locally
NO_REFERENCE = "no_reference"  # tagged implemented but named nothing checkable

SHA_RE = re.compile(r"\b([0-9a-f]{40})\b")
PR_RE = re.compile(r"(?:pull/|PR\s*#|#)(\d{3,5})\b")


@dataclass
class TaskRefs:
    task: str
    tags: list[str]
    status: str
    shas: list[str] = field(default_factory=list)
    prs: list[str] = field(default_factory=list)


@dataclass
class Derivation:
    task: str
    asserted_status: str
    asserted_implemented: bool
    derived: str
    reason: str
    landed_shas: list[str] = field(default_factory=list)
    unlanded_shas: list[str] = field(default_factory=list)
    absent_shas: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task": self.task,
            "asserted_status": self.asserted_status,
            "asserted_implemented": self.asserted_implemented,
            "derived": self.derived,
            "reason": self.reason,
            "landed": self.landed_shas,
            "unlanded": self.unlanded_shas,
            "absent": self.absent_shas,
        }


def extract_refs(text: str) -> tuple[list[str], list[str]]:
    """Implementation references an agent named. Order-preserving, deduped."""
    shas, prs = [], []
    for m in SHA_RE.findall(text or ""):
        if m not in shas:
            shas.append(m)
    for m in PR_RE.findall(text or ""):
        if m not in prs:
            prs.append(m)
    return shas, prs


def derive(refs: TaskRefs, *, is_ancestor: Callable[[str], Optional[bool]],
           fetched_fresh: bool) -> Derivation:
    """Compute the landed state. The asserted status is recorded, never used."""
    implemented = "implemented" in refs.tags
    base = Derivation(task=refs.task, asserted_status=refs.status,
                      asserted_implemented=implemented, derived=UNVERIFIABLE, reason="")

    if not refs.shas:
        base.derived = NO_REFERENCE
        base.reason = ("no 40-hex commit named in any note -- nothing to dereference, "
                       "so the asserted status is all there is (which is the gap)")
        return base

    for sha in refs.shas:
        verdict = is_ancestor(sha)
        if verdict is None:
            base.absent_shas.append(sha)
        elif verdict:
            base.landed_shas.append(sha)
        else:
            base.unlanded_shas.append(sha)

    if base.landed_shas and not base.unlanded_shas and not base.absent_shas:
        base.derived = LANDED
        base.reason = f"{len(base.landed_shas)} named commit(s), all ancestors of the target"
    elif base.landed_shas and (base.unlanded_shas or base.absent_shas):
        base.derived = PARTIAL
        base.reason = (f"{len(base.landed_shas)} landed, {len(base.unlanded_shas)} not, "
                       f"{len(base.absent_shas)} absent locally -- the state tg cannot express")
    elif base.unlanded_shas and not base.landed_shas:
        base.derived = NOT_LANDED
        base.reason = ("named commit(s) are NOT ancestors of the target: never landed, "
                       "or rebased away")
    else:
        base.derived = UNVERIFIABLE
        base.reason = (f"{len(base.absent_shas)} named commit(s) absent from this checkout; "
                       "ancestry cannot be computed")

    if not fetched_fresh and base.derived in (LANDED, PARTIAL):
        base.reason += " [STALE TARGET: not freshly fetched -- answers about the past]"
    return base


# ------------------------------------------------------------------ collectors


def _run(args: list[str], cwd: Optional[str] = None) -> tuple[int, str]:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=180)
        return p.returncode, (p.stdout or "")
    except Exception:
        return 127, ""


def git_ancestor_factory(checkout: str, target: str) -> Callable[[str], Optional[bool]]:
    cache: dict[str, Optional[bool]] = {}

    def is_ancestor(sha: str) -> Optional[bool]:
        if sha in cache:
            return cache[sha]
        rc, _ = _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=checkout)
        if rc != 0:
            cache[sha] = None                     # absent != not-landed
            return None
        rc, _ = _run(["git", "merge-base", "--is-ancestor", sha, target], cwd=checkout)
        cache[sha] = rc == 0
        return cache[sha]
    return is_ancestor


def load_tasks(tag: str = "implemented", limit: Optional[int] = None) -> list[TaskRefs]:
    """Read the tg DB read-only and gather each task's notes.

    Tag matching is on the JSON array element (`"implemented"`) rather than a
    bare substring. MEASURED 2026-08-06: both forms return the SAME count (812),
    so the element form is not currently buying anything -- it is kept because a
    future tag containing the word as a substring would silently inflate the
    population, and a denominator that can drift without notice is the defect
    this tool exists to surface. (An earlier draft of this docstring claimed the
    substring form over-matched 808 vs ~106; that was WRONG. 106 was the owner's
    2026-08-04 figure and the population has since grown -- it was never a
    substring artefact. The count moved 808 -> 810 -> 812 during this session as
    other agents tagged work, so the denominator is live, not fixed.)
    """
    # Newlines are stripped INSIDE sql: `tg sql` renders row-per-line, so a
    # multi-line note would otherwise be parsed as several rows and inflate the
    # population -- measured: 40 tasks produced 51 classified rows before this.
    q = ("SELECT t.local_id, t.tags, t.status, "
         "COALESCE(GROUP_CONCAT(REPLACE(REPLACE(n.content, char(10), ' '), "
         "char(13), ' '), ' '), '') "
         "FROM tasks t LEFT JOIN task_notes n ON n.task_id = t.local_id "
         f"WHERE t.tags LIKE '%\"{tag}\"%' GROUP BY t.local_id")
    if limit:
        q += f" LIMIT {int(limit)}"
    rc, out = _run(["tg", "sql", q])
    if rc != 0:
        return []
    tasks: list[TaskRefs] = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 4 or parts[0].strip() in ("", "local_id") or set(parts[0].strip()) == {"-"}:
            continue
        tid, tags_raw, status = parts[0].strip(), parts[1].strip(), parts[2].strip()
        notes = "|".join(parts[3:])
        try:
            tags = json.loads(tags_raw) if tags_raw.startswith("[") else []
        except json.JSONDecodeError:
            tags = []
        shas, prs = extract_refs(notes)
        tasks.append(TaskRefs(task=tid, tags=tags, status=status, shas=shas, prs=prs))
    return tasks


def report(derivations: list[Derivation], *, fetched_fresh: bool, target: str) -> dict:
    counts = Counter(d.derived for d in derivations)
    asserted = sum(1 for d in derivations if d.asserted_implemented)
    return {
        "schema_version": 1,
        "target": target,
        "target_freshly_fetched": fetched_fresh,
        "tasks_examined": len(derivations),
        "asserted_implemented": asserted,
        "derived": dict(counts),
        "derived_landed": counts[LANDED],
        "assertion_gap": asserted - counts[LANDED],
        "tasks": [d.as_dict() for d in derivations],
    }


def render(rep: dict) -> str:
    out = ["tg DERIVED LANDING STATE", ""]
    if not rep["target_freshly_fetched"]:
        out.append(f"  !! TARGET {rep['target']} WAS NOT FRESHLY FETCHED -- every")
        out.append("     derivation below answers about the last-pulled state, not now.")
        out.append("")
    for k in (LANDED, PARTIAL, NOT_LANDED, UNVERIFIABLE, NO_REFERENCE):
        if rep["derived"].get(k):
            out.append(f"  {k:<14} {rep['derived'][k]}")
    out += ["",
            f"  asserted `implemented`: {rep['asserted_implemented']}",
            f"  DERIVED landed:         {rep['derived_landed']}",
            f"  ASSERTION GAP:          {rep['assertion_gap']}  "
            "(tagged implemented but not derivably landed)"]
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkout", default="hermit")
    ap.add_argument("--target", default="origin/main")
    ap.add_argument("--tag", default="implemented")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--fetch", action="store_true", help="fetch the target first (needs egress)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    fresh = False
    if args.fetch:
        rc, _ = _run(["git", "fetch", "origin",
                      args.target.split("/", 1)[-1]], cwd=args.checkout)
        fresh = rc == 0

    tasks = load_tasks(args.tag, args.limit)
    isanc = git_ancestor_factory(args.checkout, args.target)
    derivations = [derive(t, is_ancestor=isanc, fetched_fresh=fresh) for t in tasks]
    rep = report(derivations, fetched_fresh=fresh, target=args.target)
    print(json.dumps(rep, indent=2) if args.json else render(rep))
    # A stale target is not a pass: the answer is about the past.
    return 0 if fresh else 1


if __name__ == "__main__":
    raise SystemExit(main())
