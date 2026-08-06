# Why the drain has no green at/above the gate floor: real absence vs measurement artifact

**Task:** `drain-blocked-no-green-exists-at-or-above-the-gate-floor` · **Agent:** hermit-audit
(`[impl agent, opus-5]`) · **2026-08-06T03:09Z**
**Constraint:** local analysis only — no `validate-run`, no egress (box-wide 403). Every number below
comes from the on-disk ledger, the floor registry, the shared predicate, and the local hermit object
store.

## Answer, up front

**Both — and the split matters.**

1. **The premise is FALSE right now.** A qualifying green at/above the floor exists on main:
   `d53550510d1e`, 19 commits behind tip. Reproduced two independent ways (my own funnel over the raw
   ledger, and `ci-hub newest-green --no-fetch` which returns PASS). The task header
   (`verdict=FAILED`, `trustworthy_recorded_commits=1`, "GREEN and AT-OR-ABOVE-FLOOR have an EMPTY
   INTERSECTION") is **stale**, as the 2026-08-04 23:26 note already said.
2. **When it was true, it was a real absence** — produced by a structural cliff, not a broken
   measurement: the floor commit `c369be3f` landed 2026-08-04T15:45:04Z and invalidated the entire
   pre-floor green corpus at a stroke (36 of the 37 coverage-proven greens finished before that
   minute).
3. **But the diagnosis attached to it was an artifact.** The task's cited complication — *"the validate
   invocation DEFAULTS TO `portable-strict-compat-only` … every agent running validate has been
   minting non-qualifying receipts by default"* — is **refuted**. Both layers default to `full`, and
   **78% of the compat-only ledger rows are validate.sh's own child process**, not an agent choice.
4. **The current green is measurement-fragile to the point of meaninglessness.** All 6 on-main greens
   survive only via the predicate's schema<5 grandfather branch and all carry `filtered_tests=693`.
   **Either** of two one-line tightenings already contemplated in-repo takes the on-main green count
   from **6 → 0**.

## State

| Thing | Value |
| --- | --- |
| hermit `origin/main` tip | `b64d893ae9ea6404472eae9cb86102d91ec642ef` |
| effective floor | `c369be3ff8e2c751a313b27979fa8f470dafecf0` (merge-gate, landed 2026-08-04T15:45:04Z) |
| other floor in registry | `bfb0a9ef1c303d…` (producer-anchor, 2026-08-03T18:43:14Z) — older, cleared by the above |
| ledger | `ignored/validate-run-ledger.jsonl`, **585 rows**, 2026-08-03T02:15:30Z → **2026-08-05T07:35:33Z** |
| predicate | `ci-hub/validate/qualifying-receipt.json` (+ `ci-hub/qualifying_receipt.py`) |
| newest on-main green | `d53550510d1e`, finished 2026-08-05T05:22:48Z, **19 commits behind tip** |

**The ledger has not been written to in ~19.6 hours** (last row 2026-08-05T07:35:33Z; now
2026-08-06T03:09Z) while main advanced **16 commits**. Whatever the drain is doing, it is not
producing receipts.

## The funnel (denominator 585)

Clauses applied in order, from `ci-hub/qualifying_receipt.py::row_qualifies` and its JSON datum:

| clause | in | out | killed |
| --- | ---: | ---: | ---: |
| `result == pass` | 585 | 346 | 239 |
| `profile == full` | 346 | 163 | **183** |
| `selection_mode == full` | 163 | 155 | 8 |
| `commit_anchored == true` | 155 | 154 | 1 |
| `tree_dirty == false` | 154 | 154 | 0 |
| `failures == 0` | 154 | 154 | 0 |
| `executed_tests != 0` | 154 | 154 | 0 |
| counts present (`executed`+`filtered`) | 154 | 115 | 39 |
| `schema_version >= 5` | 115 | 45 | **70** |
| per-node coverage satisfied | 45 | **37** | 8 |

The last two rows are **not** hard clauses: `row_qualifies` grandfathers schema<5 rows that carry both
counts, holding them only to nonzero execution. So:

```
585 ledger rows
 → 107  QUALIFYING           (37 schema-5 + coverage) + (70 schema<5 grandfathered)
 →  56  qualifying AND at-or-above floor c369be3f
 →   6  ...AND on main's first-parent history        ← the whole landable corpus
 →   0  ...AND schema-5 + per-node coverage proven
```

`ci-hub newest-green --branch main --no-fetch --json` independently reports the same shape:
`full_green_commits_in_range: 6`, `commits_after_green: 19`, `trustworthy_recorded_commits_in_range: 8`,
`branch_commits_in_range: 63`, green `d53550510d1e`.

## Real causes (not artifacts)

### R1. The floor is a cliff, and green production cannot outrun it

`c369be3f` landed 2026-08-04T15:45:04Z. Of the 37 coverage-proven qualifying rows in the ledger, **36
finished before that timestamp** and were invalidated the moment it landed. This is the documented
`merge-gate-v2-floor-invalidates-pre-floor-greens` behaviour working as designed — but it means every
floor landing resets the green corpus to ~zero and the fleet must re-green from scratch.

### R2. Squash landing means a validated head's green never transfers to main

The last 60 main commits are **100% single-parent** (`git log --format=%p | awk '{print NF}'` → 60×`1`).
A PR head is therefore never an ancestor of main. Consequence, measured: of the **56** qualifying greens
at/above the floor, **50 are OFF main** and 6 are on it. The drain spends its validate budget on heads
whose green is structurally non-transferable, and each landing mints a brand-new, never-validated main
commit.

### R3. Main is almost entirely unmeasured

In the floor→tip range of **63 commits**, only **11 (17%)** have *any* ledger row at all; **52 have
none**. Of the 19 commits after the current green, **18 have no record whatsoever**
(`commits_without_any_record: 18`). Green-on-main production is ~6 rows across the whole 3-day ledger
against ~40 landings/day. The gap is not a measurement problem: the runs were never done.

## Measurement artifacts (the diagnosis, not the state)

### A1. "validate defaults to `portable-strict-compat-only`" — **REFUTED at both layers**

* `hermit/validate.sh:107` — `VALIDATION_LEVEL=${VALIDATE_LEVEL:-full}`. `portable-strict-compat-only`
  is set only by the explicit `--portable-strict-compat-only` flag (`validate.sh:179`, profile assigned
  at `:384`).
* `ci-hub/validate/start_unit.py:127` — `child.extend(["with-proxy", "./validate.sh",
  *(validate_args or ["full"])])`. The admission wrapper's default passthrough is literally `full`.

There is no code path in which omitting arguments yields a compat-only run. The inference *"18 of 20
ready PRs lack exact-head records because everyone was minting compat-only receipts by default"* has no
mechanism behind it.

### A2. 78% of the compat-only rows are validate.sh's own child — the ledger self-inflates

`ci/dag/portable.json` step `test.strict_compat`:

```
STRICT_COMPAT_HERMIT_BIN=$PWD/target/ci/hermit-strict ./validate.sh --portable-strict-compat-only --no-label-pr --verbose
```

A **full** run therefore re-enters `validate.sh` as a DAG node, and that nested invocation **appends its
own ledger row**. Test: a compat-only row is a child if its `[started, finished]` window is contained in
a `profile=full` row's window with the same `cwd`.

| | count |
| --- | ---: |
| compat-only rows | 179 |
| …**nested** inside a full run, same `cwd` | **139** |
| …of which same `commit` as the parent | **139** (all of them) |
| …genuinely standalone invocations | **40** |

So the real standalone-narrow-profile rate is **40/585 = 6.8%**, not 179/585 = 31%. Control: the same
test applied to the other narrow profiles finds essentially no nesting (`portable-only` 1/24,
`only-portable` 0/18, `rr-compat-only` 0/1, `quick` 0/2) — the containment is specific to the profile
that validate.sh actually spawns, which is what a real parent/child relation predicts and a coincidence
does not.

**Any consumer that counts ledger rows by profile is double-counting full runs as compat-only runs.**

### A3. With egress down the floor/green tools *fail*, they do not answer "no"

* `python3 ci-hub/validate/gate_floors.py --json` → `{"error": "git fetch origin/main failed: … CONNECT tunnel failed, response 403"}`
* `run_newest_green` fetches unless `--no-fetch` (`ci-hub/ci-hub.rs:3760-3761`), so the same 403 aborts it.
* `ci-hub newest-green --branch main --no-fetch --json` → **PASS**, green `d53550510d1e`, in 0.82 s.

A "no green" report produced today without `--no-fetch` is **unmeasurable**, not negative. This is the
same failure-to-distinguish that `env-fault = NO-RESULT` exists to prevent, one level up in the toolchain.

### A4. Schema-5 is not the bar (reconfirmed), and that is the fragility

Independently reconfirmed here: 55 of the 56 floor-clearing greens are schema 3 or 4 with
`coverage: null`; exactly **one** (`fc49593ac21c`, schema 5, 19 planned nodes) meets the modern bar, and
it is **off main**. Every green the drain can actually land on rests on the grandfather clause.

## Sensitivity: the corpus is one predicate edit from zero

| predicate change | on-main greens |
| --- | ---: |
| **current** | **6** |
| drop the schema<5 grandfather (require `schema>=5` + per-node coverage) | **0** |
| flip `gate_filtered_tests` to `true` (require `filtered_tests == 0`) | **0** |

All six on-main greens carry `filtered_tests = 693`. Both tightenings are already contemplated in-repo —
`gate_filtered_tests` is an existing flag currently set `false`, and `rebase-base-floors.json._pending`
holds a `TBD-hermit-243-counts-filtered-anchor` entry described as *"is_clean_full_pass also requires
executed_tests>0 AND filtered_tests==0"*. **Landing that pending anchor, or flipping that flag, re-creates
the exact outage this task describes, instantly and fleet-wide.**

So: the honest headline is not "there is a green" but **"there are 6 greens and the margin is zero."**

## What follows

1. **Stop treating "no green" as a discovery and start treating the floor as a scheduled outage.** Every
   floor landing invalidates the corpus (R1). A floor commit should be paired with a mandated
   green-at-tip run in the same change, or the fleet is guaranteed to stall behind it.
2. **Validate the tip, not only heads** (R2/R3). 50 of 56 greens are on commits that can never be an
   ancestor of main. One periodic full-profile run at `origin/main`'s tip is worth more to landability
   than any number of head validations.
3. **Never re-derive the profile distribution from raw ledger rows** — filter nested children first (A2),
   or every consumer over-reports narrow-profile usage by ~4.5×.
4. **Make the floor/green tools distinguish "cannot measure" from "no green."** Today a 403 produces an
   error string from `gate_floors.py` and an abort from `newest-green`; a caller that treats a non-zero
   exit as "no green exists" reports an absence that was never measured (A3).
5. **Before flipping `gate_filtered_tests` or landing the pending counts anchor, produce a schema-5,
   coverage-proven, `filtered_tests==0` green at main's tip first.** Otherwise the corpus goes to 0 the
   same minute (sensitivity table).
6. **The 16 commits landed since the last ledger row are unmeasured.** No receipt exists for any of them.

## Reproduction (all local, no egress, no validate-run)

```bash
cd ~/work/dev-hermit
./ci-hub/ci-hub newest-green --branch main --no-fetch --json      # PASS, green d53550510d1e
python3 - <<'PY'
import json,sys; sys.path.insert(0,'ci-hub'); import qualifying_receipt as qr
pred=qr.load(); rows=[json.loads(l) for l in open("ignored/validate-run-ledger.jsonl") if l.strip()]
q=[r for r in rows if r.get("commit") and qr.row_qualifies(r,r["commit"],pred)]
print(len(rows),"rows ->",len(q),"qualifying")
PY
# nested-child test: compat-only rows time-contained in a full run with the same cwd  => 139/179
git -C hermit log --format=%p origin/main -60 | awk '{print NF}' | sort | uniq -c   # 60x "1" (squash-only)
```
