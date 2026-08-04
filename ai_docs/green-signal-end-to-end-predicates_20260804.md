# Green-Signal, End-to-End — as Executable Predicates

**Date:** 2026-08-04 · **Author:** hermit-coord (co-coordinator, Opus 4.8)
**Purpose:** the owner asked, repeatedly, for the green signal stated as *what is true*, not prose.
This doc is the answer as **predicates you can run**. Every claim below cites the exact function and
line that enforces it, verified against live source this session (validate.sh @ hermit working tree
2026-08-04; validate_status.rs @ ci-hub 2026-08-04).

The one sentence the whole system reduces to:

> **A merge is authorized by a LEDGER RECORD that exists at the current head SHA. The label is a cache; the ledger is the truth.**

---

## The three states — only the third authorizes a merge

These are three *different facts*, routinely conflated. Two of them are worthless for landing.

| # | State | What it proves | Executable tell |
|---|-------|----------------|-----------------|
| a | a **unit STARTS** | boxing is active — NOT a result | log line `re-exec inside transient systemd scope` / `cgroup boxing ACTIVE` |
| b | a **node PASSES** | that one node — NOT a record | a green cell in the DAG central log |
| c | a **RECORD EXISTS** (clean/full/full/pass at head) | landing is authorized | `ci-hub … assess <HEAD>` → exit `0` (VALIDATED) |

Only **(c)** authorizes a merge, via `assess(rows, sha) → Verdict::Validated`
(`ci-hub/lib/validate_status.rs:241-247`). A started unit and a passing node are necessary
work products on the way to a record; neither IS the record.

---

## PRODUCER — writes exactly one ledger record

**Function:** `append_validation_ledger()` — `hermit/validate.sh:998-1068`.
Writes **one JSONL line per run** to the parent ledger.

- **Ledger path:** `$DEV_HERMIT_PARENT/ignored/validate-run-ledger.jsonl`
  (`validate.sh:371-373`; override `HERMIT_VALIDATE_LEDGER`). Consumer-side constant:
  `LEDGER_REL = "ignored/validate-run-ledger.jsonl"` (`validate_status.rs:73`).
- **result=pass IFF** `exit_status == 0 && failures == 0` (`validate.sh:1009-1010`).
- **Stamps `schema_version:3`** (`validate.sh:1037`) and emits **no** `executed_tests` /
  `filtered_tests` → it is a **GRANDFATHERED writer** (see PREDICATE 4-strict/grandfather below).
- **Anchor fields carry the condition with the value:** `commit_anchored` (`:1030`),
  `tree_dirty` (`:1031`), `selection_mode`, `profile`, `commit`.

**PREDICATE P-produce:** *a record exists only if this function ran to completion.*

### How to actually produce one from an agent sandbox

```bash
systemd-run --user --unit=validate-<slug> --description='validate producer' \
  --working-directory=<worktree> --collect \
  --setenv=HOME=$HOME --setenv=PATH=$PATH \
  /bin/bash -c 'exec env PR_NUMBER=<n> with-proxy ./validate.sh > <durable-log> 2>&1'
```

**PREDICATE P-notboxed (the failure tell):** a **bare** `./validate.sh` under BpfJailer
**exits 3 in ~9s at CPU/wall 1.0x** on a many-core box — it was denied creation of its own cgroup,
so it **never boxed and never ran, and produced NOTHING**. Reads like a broken PR; is not.

```bash
# TELL of a run that never boxed: near-instant exit 3 with CPU≈wall.
# A real full run is minutes of wall with CPU/wall >> 1 on a multicore host.
grep -E 'CPU/wall 1\.0x' <durable-log>   # present  => never boxed, no record produced
```

---

## CONSUMER — reads the ledger, decides landing

**File:** `ci-hub/lib/validate_status.rs`. Entry point `assess(rows, sha)` (`:224-247`).

**Verdict → exit code** (`:91-93`):

| Verdict | exit | meaning |
|---------|------|---------|
| `Validated` | `0` | a qualifying record exists at this SHA → **land** |
| `FailedOnRecord` | `3` | a clean/full record exists and is **known-bad** → do not land |
| `NotValidated` | `4` | **no** qualifying record → **re-dispatch** (unverified, not failed) |

**The qualifying predicate:** `is_clean_full_pass(row, sha)` (`:175-198`), layered:

1. **Coverage prerequisite** — `is_clean_full_coverage` (`:123-129`), ALL of:
   - `row.commit == sha`
   - `commit_anchored == Some(true)`
   - `tree_dirty == Some(false)`
   - `selection_mode == Some("full")`
   - `profile == Some("full")`
   …**and** `result == Some("pass")` (`:176`).
2. **Universal guards** (hold at EVERY schema, never grandfathered):
   - `executed_tests != Some(0)` — a demonstrated zero-test run is a no-result, never green (`:181`).
   - NOT `filtered_tests > 0` — any name-filtered subset is a narrowed scope, never a full green (`:184`).
3. **Version-aware tightening:**
   - `count_capable = schema_version >= COUNTS_SCHEMA` where `COUNTS_SCHEMA = 5` (`:145,187`);
     `counts_present = executed_tests.is_some() && filtered_tests.is_some()` (`:188`).
   - **STRICT** (if `count_capable || counts_present`): require
     `executed_tests == Some(n>0) && filtered_tests == Some(0)` (`:189-192`).
   - **GRANDFATHER** (a genuinely pre-count receipt that cleared the universal guards): accept (`:193-196`).

Strictness is keyed on **field presence**, not the integer alone — this is the self-liquidating
grandfather (commit **b883ace**): a schema-3 writer is grandfathered *today*, and the moment a
count-emitting writer (schema ≥ 5) ships, the same code path holds it STRICT with no consumer edit.

**Executable equivalent of the qualifying predicate (jq over the ledger):**

```bash
LEDGER="${DEV_HERMIT_PARENT:-$PWD}/ignored/validate-run-ledger.jsonl"
SHA=<40-hex-head>
jq -e --arg sha "$SHA" '
  select(.commit==$sha
     and .commit_anchored==true
     and .tree_dirty==false
     and .selection_mode=="full"
     and .profile=="full"
     and .result=="pass"
     and (.executed_tests != 0)
     and ((.filtered_tests // 0) <= 0)
     and ( (.schema_version >= 5) or (has("executed_tests") and has("filtered_tests"))
           | if . then ((.executed_tests // 0) > 0 and (.filtered_tests==0)) else true end ))
' "$LEDGER"
# The authoritative check is still: ci-hub … assess $SHA  (exit 0 = VALIDATED)
```

---

## The four predicates the owner demanded

### PREDICATE 1 — what AUTHORIZES a landing
`assess(HEAD) → Validated` (exit `0`). **Not a label. Not a prior record. Not "it was green
yesterday."** The `locally-validated` label is a CACHE of a past record; the ledger record at the
**current head** is the truth. If the two disagree, the ledger wins.

### PREDICATE 2 — why a record EXPIRES (any rebase invalidates one)
A record is **SHA-keyed**: `assess` skips every row whose `row.commit != sha`
(`validate_status.rs:229`). A rebase produces a **new head SHA**, so **no existing record matches**
→ `NotValidated` (exit 4). **The record does not follow the rebase.** This is why only 3 of 74 heads
were VALIDATED at one point tonight — not because 71 failed, but because rebasing moved their heads
out from under their records. An "anchor-OK" list goes stale the instant anything below it rebases.

### PREDICATE 3 — a STRUCTURALLY UNVALIDATABLE head
A head whose branch runs a **pre-`bfb0a9ef`** `validate.sh` ("validate: gate on dirty tree and record
commit-anchoring") emits `commit_anchored: null`. `is_clean_full_coverage` requires
`commit_anchored == Some(true)` (`:125`), so such a record **can never qualify — forever**, no matter
how green the run. **The producer travels with the branch:** the fix is not "re-run", it is **rebase
the branch onto an anchor-emitting producer**. (This is a *distinct* absence class from PREDICATE 2:
2 is "record for the wrong SHA", 3 is "record structurally cannot qualify". See the green-time doc,
parent commit `078ae61`.)

### PREDICATE 4 — the THREE STATES (restated as the closing invariant)
`(a)` unit STARTS ≠ `(b)` node PASSES ≠ `(c)` RECORD EXISTS. Only `(c)` — a clean/full/full/pass
record at the head, i.e. `assess → Validated` — authorizes a merge. See the table at the top.

---

## Quick reference — the whole flow in five commands

```bash
# 0. establish the frontier from the LEDGER, never from a handed SHA
ci-hub newest-green --branch main            # newest locally-validated commit

# 1. PRODUCE a record for a head (boxed, detached, durable log)
systemd-run --user --unit=validate-<slug> --working-directory=<wt> --collect \
  --setenv=HOME=$HOME --setenv=PATH=$PATH \
  /bin/bash -c 'exec env PR_NUMBER=<n> with-proxy ./validate.sh > <log> 2>&1'

# 2. confirm it BOXED (not the exit-3 tell) and RAN to a record
grep -E 'CPU/wall 1\.0x' <log> && echo 'NEVER BOXED — no record' || echo 'boxed ok'

# 3. ASSESS the head — this, and only this, authorizes landing
ci-hub … assess <40-hex-head>                # exit 0 VALIDATED | 3 FAILED | 4 NOT-VALIDATED
```

---

## Provenance

- Producer: `hermit/validate.sh:371-373, 998-1068` (schema_version 3, grandfathered writer).
- Consumer: `ci-hub/lib/validate_status.rs:73, 91-93, 123-129, 145, 175-198, 224-247`.
- Self-liquidating grandfather: ci-hub commit **b883ace** (presence-keyed strictness).
- Anchor-emitting producer floor: hermit commit **bfb0a9ef**.
- Companion: green-time definition (parent commit `078ae61`); 138-task classification (`16d2ec2`);
  reverie-355 livelock A/B (`1e18db7`).
- All line numbers verified against live source on 2026-08-04.
