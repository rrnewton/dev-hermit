# Scoring the LiteInst stack cells: 0 change class, because no stack-dimension rows and no tier values exist

**Task:** `score-liteinst-stack-cells-under-tightened-rules` · **Agent:** hermit-w2 · **2026-08-07**
**Snapshot caveat:** `compat-envelope/scorecard.csv` went from **646 to 618 rows during this
analysis** — another agent is writing it. Every count below is one snapshot.

## The stated ceiling is confirmed, and it is stronger than stated

> *"NO cell can currently reach `tier='bitwise'` — 0 of 2,284 rows come from a bitwise-capable comparator."*

**2284 is exact** — 618 + 454 + 1200 + 12. And the real position is worse: **0 of 2284 rows carry
*any* tier value at all**, not merely no bitwise one.

| scorecard | rows | tier column | rows with a tier value |
|---|---|---|---|
| `scorecard.csv` | 618 | **yes** | **0** |
| `e9patch-scorecard.csv` | 454 | no | 0 |
| `fullcorpus-scorecard.csv` | 1200 | no | 0 |
| `reverie-scorecard.csv` | 12 | no | 0 |
| **total** | **2284** | 1 of 4 | **0** |

Three rows *appear* to hold a tier value. They do not — they are `reason` free-text bleeding into
field 23, e.g. `" whereas ptrace remaps it through the user namespace. fchown is not correctly
implemented under DBI"`. See the next section.

## Prerequisite blocker: the scorecard CSV is ragged

`scorecard.csv` declares 23 header fields, but its rows do not agree:

```
header fields: 23
rows with 23 fields: 510
rows with 24 fields: 106
rows with 27 fields:   1
rows with 28 fields:   2
```

109 of 619 lines are malformed — unquoted commas in `reason`. **Any tier scoring driven by a column
index or `DictReader` will silently misalign on those rows**, which is exactly what happened on my
first pass: it reported every tier empty *and* surfaced three phantom values. This must be fixed
before a tier column can be populated or trusted; it is not a cosmetic issue.

## There are no LiteInst stack-dimension rows to score

The scorecard is keyed by `test_id` × `backend`. It has **no memory-dimension column** — nothing
distinguishes stack from heap from detlog. Searching every row for a stack-related `test_id` yields
exactly two, and neither is the stack-memory dimension:

```
test_id=c-programs/map-shadow-stack-enosys  backend=kvm       outcome=pass  tier=[]
test_id=c-programs/map-shadow-stack-enosys  backend=liteinst  outcome=pass  tier=[]
```

That is a **shadow-stack syscall** test — an ENOSYS check on `map_shadow_stack`, unrelated to the
stack-hash self-determinism dimension.

> **Cells changing class: 0. Denominator: 0 LiteInst stack-dimension rows exist in any scorecard.**

The stack/heap/detlog dimensions live in the **self-determinism matrix**, a different artifact
(`ai_docs/not-comparable-applied-to-the-published-scorecard_20260807.md`), which has no tier column
either.

## Which tier the LiteInst stack cell actually reaches

Stating this for the dimension cell as measured, since no scorecard row exists to carry it:

| cell | corpus / guest | executed | self-determinism | **tier REACHED** |
|---|---|---|---|---|
| liteinst stack | `notsc`, PR head `077833ad` | 410 | **410/410** | **detlog-hash-ordinal** — *not* bitwise |
| liteinst stack | `heapy`, main `86842f741` | **0** | 0/0 vacuous | **none — NOT-COMPARABLE** |
| ptrace stack (reference) | `notsc` | 44 | 44/44 | detlog-hash-ordinal |

The reached tier is bounded by the comparator used: these compare **SHA-256 ordinals of `[stack]`
regions emitted in the DETLOG at INFO**, position by position. That is a hash-ordinal comparison. It
cannot certify bitwise equality, and no bitwise-capable comparator produced any of these rows — so
`tier='bitwise'` is unreachable here, consistent with the ceiling.

**The cell is also guest-dependent**: 410 records on `notsc`, zero on `heapy`. A single "liteinst
stack" verdict without its guest is not well-formed.

## Verify clause: planted divergence IS detected

Required, and satisfied from the landed evidence at the same fix commit (`bcede29`): a planted
host-file read into live stack gives **11/414 differing (liteinst)** and **11/48 (ptrace)** at
`077833ad`. The comparator is not inert, so the 410/410 is a real negative.

## Limits carried forward

- One host, one guest per check, one run pair per backend — **presence and removal of the defect,
  not a flake rate**; no repetition, so intermittency is unbounded.
- Measured at PR head `077833ad`, **not rebased onto current main** — a rebase could change it.
- **Stack dimension only**; heap and detlog were not re-measured.
- Never measured at `gc67774dd` — the fix is not an ancestor of it.
- **Not extended to SaBRe** (different cause; and separately, SaBRe does not engage on `heapy` —
  `patched_sites=0`).
- My ptrace control disagrees with the published ledger's ptrace row on all four dimensions, so
  these numbers form a self-consistent set of their own and must not be pasted into that table.

## What must happen before a tier can be scored at all

1. Fix the ragged CSV (quote `reason`), or every index-based read stays unsafe.
2. Populate `tier` — it exists in 1 of 4 scorecards and is empty in 100% of rows.
3. Add a dimension column, or stack/heap/detlog cells have nowhere to live.
4. Default any unpopulated tier to `unknown`, never to a passing tier.
