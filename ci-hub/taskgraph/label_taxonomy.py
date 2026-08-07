#!/usr/bin/env python3
"""Validate the strict TaskGraph label taxonomy: one workstream, one lifecycle.

WHY TWO AXES AND NOT ONE LIST. The directive names fourteen labels in a single
list. Enforced as one axis, every correctly-labelled task would be "conflicting"
-- a task is always both *some workstream* and *some lifecycle stage*. They are
orthogonal, so they are validated independently and a task needs exactly one of
each.

WHY LABELS AND NOT STATUS. Status answers "who must act next" (see the Task
Lifecycle section of AGENTS.md). These labels answer "what body of work is this"
and "what stage is it in". Keeping them separate is what stops the recurring
mistake of encoding progress in status -- most recently the ~200 rows held at
`in_progress` merely because their PR had not merged.

MEASURED BEFORE-STATE (2026-08-07, single read-only snapshot, cursor walk and
aggregate agreeing at 413 nonterminal tasks): 372/413 = 90.1% carry no
workstream label and 350/413 = 84.7% carry no lifecycle label, with 3 workstream
and 1 lifecycle conflicts. So this validator opens almost entirely red, and that
is expected -- see `--gate` for how it is meant to be introduced without being
muted on day one.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB = Path.home() / ".tg" / "hermit.db"

# Exactly one of these per nonterminal task. `backend:<name>` is a family:
# any label with that prefix counts as one workstream, so a new backend does
# not require editing this file.
WORKSTREAM = ("release:0.3", "strictness", "main-health", "operations", "owner-decision")
WORKSTREAM_PREFIX = "backend:"

# Exactly one of these per nonterminal task.
LIFECYCLE = (
    "active-implementation", "research", "review", "landing",
    "awaiting-land", "stale-premise", "subsumed", "duplicate",
)

# The P0/P1 view sorts by this before priority, so release and main-health
# surface above equally-prioritised background work. Anything unlisted sorts
# last but is not an error.
WORKSTREAM_ORDER = {"release:0.3": 0, "main-health": 1, "strictness": 2}

NONTERMINAL = ("OPEN", "IN_PROGRESS", "BACKLOG")


def parse_tags(raw: str | None) -> set[str]:
    """Tags are stored as a JSON array; tolerate a bare comma list too."""
    if not raw:
        return set()
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return {str(v) for v in value}
    except json.JSONDecodeError:
        pass
    return {p.strip() for p in raw.split(",") if p.strip()}


def axes(tags: set[str]) -> tuple[list[str], list[str]]:
    ws = sorted(t for t in tags if t in WORKSTREAM or t.startswith(WORKSTREAM_PREFIX))
    lc = sorted(t for t in tags if t in LIFECYCLE)
    return ws, lc


def classify_row(local_id: str, priority: str | None, tags: set[str]) -> dict:
    ws, lc = axes(tags)
    problems = []
    if len(ws) == 0:
        problems.append("missing-workstream")
    elif len(ws) > 1:
        problems.append(f"conflicting-workstream({','.join(ws)})")
    if len(lc) == 0:
        problems.append("missing-lifecycle")
    elif len(lc) > 1:
        problems.append(f"conflicting-lifecycle({','.join(lc)})")
    return {"local_id": local_id, "priority": priority, "workstream": ws,
            "lifecycle": lc, "problems": problems, "ok": not problems}


def load(db: Path) -> tuple[list[dict], int]:
    """Read every nonterminal row inside ONE transaction.

    The graph moves ~1 task/minute under a release sprint; the census that
    preceded this tool read 606 by aggregate and 605 by cursor walk purely
    because a task closed between two statements. Counting outside a
    transaction cannot reconcile, by construction -- so both the walk and the
    aggregate are taken from the same snapshot and compared.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("BEGIN")
    marks = ",".join("?" for _ in NONTERMINAL)
    rows = cur.execute(
        f"SELECT local_id,status,priority,tags FROM tasks_v WHERE status IN ({marks})",
        NONTERMINAL,
    ).fetchall()
    agg = cur.execute(
        f"SELECT COUNT(*) FROM tasks_v WHERE status IN ({marks})", NONTERMINAL
    ).fetchone()[0]
    con.rollback()
    con.close()
    out = [classify_row(lid, prio, parse_tags(tags)) for lid, _st, prio, tags in rows]
    return out, agg


def is_p01(priority: str | None) -> bool:
    return str(priority).upper().lstrip("P") in ("0", "1")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--gate", choices=["all", "p01", "none"], default="p01",
                    help="which rows may fail the exit code. 'p01' (default) is the "
                         "introduction path: with ~90%% of the graph unlabelled, gating "
                         "on 'all' would be red for weeks and get muted, so priority "
                         "work is enforced first and the tail is reported only. "
                         "Move to 'all' once the backlog is labelled.")
    a = ap.parse_args(argv)

    if not a.db.exists():
        print(f"UNVERIFIABLE: no TaskGraph db at {a.db}", file=sys.stderr)
        return 2
    rows, agg = load(a.db)

    if len(rows) != agg:
        # Should be impossible inside one transaction; if it happens the count
        # is not trustworthy and saying so is better than reporting a number.
        print(f"UNVERIFIABLE: cursor walk {len(rows)} != aggregate {agg} in one snapshot",
              file=sys.stderr)
        return 2

    bad = [r for r in rows if not r["ok"]]
    bad_p01 = [r for r in bad if is_p01(r["priority"])]
    gated = {"all": bad, "p01": bad_p01, "none": []}[a.gate]

    missing_ws = sum(1 for r in rows if "missing-workstream" in r["problems"])
    missing_lc = sum(1 for r in rows if "missing-lifecycle" in r["problems"])
    conflict = sum(1 for r in rows
                   if any(p.startswith("conflicting") for p in r["problems"]))

    if a.json:
        print(json.dumps({
            "denominator": len(rows), "aggregate": agg,
            "ok": len(rows) - len(bad), "violations": len(bad),
            "p01_violations": len(bad_p01),
            "missing_workstream": missing_ws, "missing_lifecycle": missing_lc,
            "conflicting": conflict, "gate": a.gate,
            "gated_violations": [r["local_id"] for r in gated],
        }, indent=2))
    else:
        n = len(rows)
        print(f"nonterminal tasks (one snapshot, cursor==aggregate): {n}")
        print(f"  exactly one workstream AND one lifecycle : {n - len(bad)}/{n}")
        print(f"  missing workstream ....................... {missing_ws}/{n}")
        print(f"  missing lifecycle ........................ {missing_lc}/{n}")
        print(f"  conflicting (>1 on an axis) .............. {conflict}/{n}")
        print(f"  P0/P1 violations ......................... {len(bad_p01)}")
        print(f"  gate={a.gate} -> {len(gated)} row(s) fail the exit code")
        for r in gated[:40]:
            print(f"    {r['priority'] or '--':<4} {r['local_id']}  {';'.join(r['problems'])}")
        if len(gated) > 40:
            print(f"    ... and {len(gated) - 40} more")

    return 1 if gated else 0


if __name__ == "__main__":
    raise SystemExit(main())
