# TaskGraph nonterminal census, 2026-08-07

Read-only census of every OPEN / IN_PROGRESS / BACKLOG task in the `hermit` TaskGraph.
Produced for task `taskgraph-hygiene-full-open-census`. **Nothing was closed, demoted or
edited.** Numbers are as of one read transaction; the graph is live and moved three times
while this was being written (605 -> 612 -> 616 nonterminal in ~25 minutes), so a census is
only meaningful as of an instant.

## Method

`sqlite3` opened **read-only** (`file:...?mode=ro`) on `~/.tg/hermit.db`, with every query
inside a single `BEGIN` so all three counting methods see the same snapshot. Without the
transaction the denominator drifts under you: the first attempt read 606 by aggregate and
605 by walk, purely because a task closed between two statements.

Three independent counts, required to agree:

| method | how | result |
| --- | --- | --- |
| A aggregate | `SELECT status,COUNT(*) ... GROUP BY status` | BACKLOG 325 + IN_PROGRESS 209 + OPEN 82 = **616** |
| B cursor walk | `WHERE local_id > :cursor ORDER BY local_id LIMIT 100`, 7 pages | **616** rows, 616 distinct, 0 duplicates, strictly increasing |
| C full fetch | unordered `SELECT local_id` | **616** |

`A == B == C`, and set(B) == set(C), which is what rules out both gaps and extras. No
`LIMIT K` from the head was used anywhere.

## Category counts

Mutually exclusive, assigned by strict precedence in the order listed. Sum equals the
denominator exactly and every task is classified exactly once.

| category | count | share |
| --- | ---: | ---: |
| active | 18 | 2.9% |
| ready | 232 | 37.7% |
| blocked | 102 | 16.6% |
| implemented-awaiting-land | 203 | 33.0% |
| owner-decision | 34 | 5.5% |
| stale-premise | 9 | 1.5% |
| subsumed/duplicate | 1 | 0.2% |
| obsolete | 11 | 1.8% |
| orphaned | 6 | 1.0% |
| malformed | 0 | 0.0% |
| **TOTAL** | **616** | **100%** |

## The finding

**203 of 616 nonterminal tasks (33%) are `implemented-awaiting-land`.** That is the burial
mechanism the task was filed about. It also explains the shape of `IN_PROGRESS`: there are
209 IN_PROGRESS rows but only **20 have an owner**, because 185 of the ownerless ones are
implemented work correctly parked at `in_progress` by policy until it lands. Only **4**
tasks are IN_PROGRESS, unowned, and not implemented -- so the graph is not full of abandoned
running work; it is full of *finished* work waiting on the landing pipeline.

Read that way, `IN_PROGRESS` is not a measure of activity at all, and any view that treats it
as one will overstate the fleet by an order of magnitude. The real active set is 18.

At P0 the same shape holds: of 224 P0 tasks, 86 are implemented-awaiting-land and only 15 are
active.

## Two classifier defects found and fixed during the census

Recorded because both would have produced confidently wrong closure advice.

1. **The 6 "orphaned" tasks are project containers, not orphans.** The first pass defined
   orphaned as "no outgoing `blocks` edge", which is exactly the shape of a *goal root*.
   It flagged `__adhoc__` (776 in-project children), `dev_hermit_parent` (25),
   `reproducible_builds` (56), `reverie_backends` (33), `qemu_linux` (11) and
   `test_power_to_weight` (5). Closing those would detach the entire graph. Corrected to
   "no blocks edge in **either** direction", and reported split by whether the node is a
   structural container. **None of the 6 is a closure candidate.**
2. **The `obsolete` regex was mostly false positives.** A loose match on
   `superseded|obsolete|no longer|moot` over notes returned 33 rows; sampling six showed
   five were incidental prose -- "left on a superseded reverie commit", "a script which no
   longer exists", "reap obsolete process groups", "batch-bump-43 was moot", and a quotation
   of the closure policy itself. Tightened to statements that are *about this task* (a note
   beginning `SUPERSEDED`/`OBSOLETE`, or `SUPERSEDED by <id>`), which yields 11. The same
   tightening was applied to `stale-premise` (12 -> 9).

## Closure and reprioritisation candidates

Every one of these needs a human decision; this census closed nothing.

**subsumed/duplicate (1), mapped to survivor**

- `e2e_perl_hash_order_2` -> survivor `e2e_perl_hash_order` (identical normalised title)

**near-duplicate candidates (token-set similarity >= 0.75, NOT auto-classified)**

- 1.00 `e2e_perl_hash_order` ~~ `e2e_perl_hash_order_2`
- 0.75 `dbi-close-remaining-cells` ~~ `liteinst-close-remaining-cells` (probably legitimately
  parallel per-backend work -- listed so it is checked, not closed)

Only 2 pairs clear the threshold, so title-level duplication is **not** a significant
contributor to the 616. The burial is landing latency, not duplication.

**obsolete (11)** -- each has a self-referential superseded/obsolete statement in its notes:
`ci-hub-green-time-metric`, `data-driven-pr-planning-next-10-with-soft-green-inheritance`,
`dbi_branch_count_preemption`, `demo5-fix-vtime-skew-poller-livelock`,
`drain-the-49-no-record-prs-once-the-cargo-pin-lands`, `full-validate-green-proof-with-1520-1521`,
`impl-coord-heartbeat`, `impl-dbi-dynamorio-prototype`, `mass-parallel-rebase-stale-base`,
`triage-and-review-62-abandoned-draft-prs`, `validate_hermit_prs_1470`.

**stale-premise (9)**: `detcore-wait4-nondelivery-sigkilled-child`,
`determinism_stress_order_violation_2`, `drain-implemented-to-landed`,
`matrix-tsv-schema-consolidation`, `merge-gate-trusts-label-presence-not-the-ledger`,
`staging-hermit-33-free-merges`, `unwire-executed-tests-from-remaining-consumers`,
`validate-dag-runner-p0-umbrella`, `validate_sh_retry_classifier`.

**The highest-leverage move is none of the above.** 203 implemented-awaiting-land dwarfs the
21 obsolete+stale+duplicate rows combined. Draining the landing queue would remove ~10x more
noise from the graph than every closure candidate here put together.

## Protected

32 tasks carry `release:0.3` and were not proposed for any action. The 18 `active` tasks all
have live owners and were likewise left alone.

## Raw output

Full reconciliation, per-category member lists, and the P0 active/ready list are in the
generated run log; the classification map is machine-readable at
`ignored/w6-census/classification.json` (machine-local).

```
### METHOD RECONCILIATION (all inside ONE read transaction)
A aggregate by status {'BACKLOG': 325, 'IN_PROGRESS': 209, 'OPEN': 82} sum=616
B cursor walk by local_id: 616 rows in 7 pages; distinct 616; duplicates 0; strictly increasing True
C unordered full fetch: 616
A==B==C: True   B set == C set (no gaps, no extras): True

### CATEGORY COUNTS (mutually exclusive; strict precedence in that order)
  active                         18
  ready                         232
  blocked                       102
  implemented-awaiting-land     203
  owner-decision                 34
  stale-premise                   9
  subsumed/duplicate              1
  obsolete                       11
  orphaned                        6
  malformed                       0
  TOTAL                         616  denominator 616  SUM==DENOMINATOR True
  every task classified exactly once: True

### ORPHANED, split (structural containers are NOT work items)
  __adhoc__                in_project_children= 776  project/vision container
  dev_hermit_parent        in_project_children=  25  project/vision container
  qemu_linux               in_project_children=  11  project/vision container
  reproducible_builds      in_project_children=  56  project/vision container
  reverie_backends         in_project_children=  33  project/vision container
  test_power_to_weight     in_project_children=   5  project/vision container

### CROSS-CUTTING (overlaps by construction; NOT part of the sum)
  IN_PROGRESS, no owner, not implemented : 4  <- claims to be running; nobody is running it
  tagged release:0.3 (PROTECTED)         : 32
  IN_PROGRESS total / with owner         : 209 / 20

### P0/P1 BY CATEGORY
  P0 total 224: implemented-awaiting-land=86, blocked=58, ready=42, active=15, owner-decision=10, obsolete=7, stale-premise=5, subsumed/duplicate=1
  P1 total 226: implemented-awaiting-land=86, ready=79, blocked=30, owner-decision=22, obsolete=4, stale-premise=3, active=2

### NEAR-DUPLICATE CANDIDATES (token-set >=0.75; NOT auto-classified, needs confirmation)
  1.0  e2e_perl_hash_order  ~~  e2e_perl_hash_order_2
  0.75  dbi-close-remaining-cells  ~~  liteinst-close-remaining-cells
  (2 candidate pairs total)

### STALE-PREMISE MEMBERS
  detcore-wait4-nondelivery-sigkilled-child
  determinism_stress_order_violation_2
  drain-implemented-to-landed
  matrix-tsv-schema-consolidation
  merge-gate-trusts-label-presence-not-the-ledger
  staging-hermit-33-free-merges
  unwire-executed-tests-from-remaining-consumers
  validate-dag-runner-p0-umbrella
  validate_sh_retry_classifier

### OBSOLETE MEMBERS
  ci-hub-green-time-metric
  data-driven-pr-planning-next-10-with-soft-green-inheritance
  dbi_branch_count_preemption
  demo5-fix-vtime-skew-poller-livelock
  drain-the-49-no-record-prs-once-the-cargo-pin-lands
  full-validate-green-proof-with-1520-1521
  impl-coord-heartbeat
  impl-dbi-dynamorio-prototype
  mass-parallel-rebase-stale-base
  triage-and-review-62-abandoned-draft-prs
  validate_hermit_prs_1470

### SUBSUMED/DUPLICATE MEMBERS
  e2e_perl_hash_order_2 -> survivor e2e_perl_hash_order

### MALFORMED MEMBERS

```
