# TaskGraph taxonomy: the P0/P1 label application, and how much of it is trustworthy

**Date:** 2026-08-06 (UTC 2026-08-07) · **Agent:** hermit-w3
**Tasks:** `apply-taskgraph-label-taxonomy-to-priority-work`, `apply-taskgraph-taxonomy-evidence-backed-subset`

## Question

Every nonterminal P0/P1 task should carry exactly one workstream and one lifecycle label
(`ci-hub/taskgraph/label_taxonomy.py`, landed `53200775`). This records **which labels were actually
applied, on what evidence, and which of them should not be trusted.**

## The headline, stated before the numbers

`--gate p01` exits 0. **That means every P0/P1 carries one label per axis. It does NOT mean the labels
are right.** Of the 260 rows applied, only 63 rest on hard evidence; **197 are keyword proposals** and
are marked `needs-label-review` in the graph. Treat `keyword-proposals-UNVERIFIED.json` as a worksheet,
never as the reason a task sits in a workstream.

## Results

Full table in `results.csv`. The load-bearing rows:

| | before | after |
| --- | --- | --- |
| nonterminal denominator | 411 | 407 |
| fully classified (1 workstream + 1 lifecycle) | 5 | 267 |
| — evidence-backed | — | 72 |
| — provisional (`needs-label-review`) | — | 196 |
| P0/P1 violations | 262 | **0** |
| conflicting | 4 | 1 (pre-existing) |
| unlabelled on ≥1 axis (all P2+) | — | 140 |

Every count is a single read-only snapshot with the cursor walk and the aggregate agreeing. The
denominator moved 411 → 407 *during* the pass, so each figure is quoted with its instant rather than as
one number — the graph drains continuously and a count taken across two statements cannot reconcile.

## What makes the 72 defensible without re-reading the task

- tag `implemented` → `awaiting-land` (that is its definition under the landed close-on-implemented lifecycle)
- an existing `backend:*` tag → that backend workstream
- an existing `release:0.3` tag → `release:0.3`
- status `IN_PROGRESS` → `active-implementation`
- plus 4 P0/P1 that appeared mid-pass, each read individually from its own subject

No keyword inference in any of them.

## Two defects found in my own work, recorded because the green would otherwise hide them

**1. Descriptions quote the taxonomy, so the first classifier matched everything.** Many task
descriptions contain `Workstream axis: release:0.3, strictness, …`. Scanning descriptions therefore
matched every axis label on every task that cited the axes. Concretely wrong before the fix:
`make-validate-quick-and-reliable → backend:dynamorio`, `cross-backend-detlog-parity-sweep →
backend:kvm` (it is *cross*-backend), and the taxonomy task itself → `release:0.3` from its own quoted
Context line. Fixed by stripping quoted-taxonomy blocks and anchoring detection to id+title. A residual
collision survived even that: `apply-taskgraph-label-taxonomy-to-priority-work` → `strictness`, from
the word "strict" in its own title.

**2. A union is not an assignment — I created 37 conflicts.** The first apply took
`new = old ∪ {workstream, lifecycle}`. For a task that already had one label on an axis and was in the
set only because the *other* axis was empty, that added a second, different label to the populated
axis. Conflicts went 4 → 38. Repaired in the same pass by removing *my* label wherever the task already
carried exactly one on that axis — theirs predates mine and belongs to the task's owner. All 37 cleared.
On a single-valued axis, adding is overwriting by accident.

## Files

| file | what it is | trust |
| --- | --- | --- |
| `applied-audit.json` | 260 rows: before tags, added labels, after tags, and the reason code per axis | record of fact |
| `evidence-split.json` | the 63 hard-evidence ids vs the 197 review ids | record of fact |
| `keyword-proposals-UNVERIFIED.json` | the classifier's output, wrapped in an explicit `STATUS: UNVERIFIED` header | **worksheet only** |
| `results.csv`, `metadata.json` | counts and provenance | record of fact |

The UNVERIFIED marker is inside the JSON as well as in the filename: a filename is lost by a copy or a
viewer, and the whole risk here is a provisional label being mistaken for an evidenced one.

## The unresolved manual-review set: 196, queryable

```
tg ... WHERE tags LIKE '%needs-label-review%'
```

Weakest first, by what produced them: **74 `operations`** (the fallback — it means "unclassified" as
often as "fleet operations", so review these first), 41 `anchor:ci/main-health`, 40
`anchor:backend-name`, 30 `anchor:strictness/parity`, 8 `anchor:owner`, 4 `anchor:multi-backend`.

Clearing a row: read the task, confirm or correct the two labels, drop `needs-label-review`.

## Reproduction

```bash
# counts, from the landed validator (read-only)
python3 ci-hub/taskgraph/label_taxonomy.py --gate p01
python3 ci-hub/taskgraph/label_taxonomy.py --gate none --json
```

Nothing here re-runs the application; it mutated live TaskGraph tags and is not idempotent to replay.
The audit files are the record of what it did.

## Scope

TaskGraph **tags only**. No status, owner, priority, dependency, or description was changed, and every
read was inside a read-only transaction. 140 tasks remain unlabelled on at least one axis; all are P2+
and belong to a separate lower-priority pass.
