# EBADF sandbox classifier — the bracket, run against current main

**Task:** `sandbox-failure-classifier-misses-ebadf` (P1)
**Date:** 2026-08-06 · **Bound to:** hermit main `b64d893a`, `validate.sh:1788-1794`
**Mode:** local verification. No build, no egress, **no file edited**.

---

## Headline: the fix exists as PR #1566 and is **not on main**. The gap is live today.

Verified at `b64d893a`:

- `grep -in 'bad file descriptor' validate.sh` → **zero hits**
- `scripts/validate-env-block-test.sh` (the committed self-test from #1566) → **does not exist**
- `is_environmental_block` still uses the pre-#1566 inline regex with 8 alternation groups; all three
  errno-bearing groups (`fatal error:`, `CMake Error`, `cannot open|…`) carry only
  `operation not permitted|permission denied`. No `bad file descriptor`, no binutils tool anchor.

## The bracket the task asked for, run against main's *shipped* regex

Regex extracted verbatim from `validate.sh:1788-1794` — not a paraphrase.

| Fixture | Classified | Expected |
|---|---|---|
| **Planted sandbox failures** | | |
| `/usr/bin/objcopy: ../bin64/drconfig.debug: Bad file descriptor` | **TEST-FAILURE** ❌ | SANDBOX |
| `/usr/bin/strip: foo.so: Bad file descriptor` | **TEST-FAILURE** ❌ | SANDBOX |
| `/usr/bin/ld: out.o: Bad file descriptor` | **TEST-FAILURE** ❌ | SANDBOX |
| **Positive controls (real failures must stay real)** | | |
| `write(3) failed: Bad file descriptor` (guest) | TEST-FAILURE ✅ | TEST-FAILURE |
| `assertion failed: left == right` | TEST-FAILURE ✅ | TEST-FAILURE |
| **Masking check** | | |
| `panicked at /x/reverie-dbi/build.rs:339` | **SANDBOX** ✅ | SANDBOX |

## This reconciles the two contradictory prior claims — both are true

The record contains "the premise is REFUTED" (hermit-243, twice-verified) alongside a task title
asserting a live gap. The bracket shows **both hold, for different inputs**:

- **The gap is real in isolation.** An isolated binutils EBADF matches *nothing* in main's regex and
  is attributed to the code. That is the task's premise, now confirmed against main rather than
  argued.
- **It is masked in the real-world shape.** `reverie-dbi/build.rs:339` is
  `assert!(status.success(), …)`, so *any* DynamoRIO build failure — objcopy EBADF included — always
  fires group 8. The real captured log therefore classifies correctly today. That is the refutation,
  also confirmed.

So the live defect is **fragility, not misclassification**: correct classification currently depends
on a *crate-path coincidence* (`reverie-dbi` appearing in the panic string) rather than on the
failure's own identity. Any EBADF arising outside the reverie-dbi build path — a different vendored
build, a relocated crate, a direct `strip`/`ld` invocation — is misread as a product failure. The
`objcopy|strip|ld|ar|ranlib|as` × errno-class anchor in #1566 is the principled fix precisely
because it binds to the tool and errno rather than to who happened to call it.

**The positive controls are the important half:** guest-side EBADF phrasings stay `TEST-FAILURE`
under main's regex, and #1566's 17-fixture self-test showed they still do after the widening. The
dangerous direction — an over-broad errno match hiding a genuine failure — is guarded on both sides.

## Nothing was implemented, deliberately

1. **PR #1566 already implements all four deliverables** at `c2e15f12` (errno set extracted to
   `readonly ENV_BLOCK_ERRNOS`/`ENV_BLOCK_PATTERN`, binutils tool anchor, committed self-test
   17/17, and a reasoned decision to keep the gate binary while `INDETERMINATE` lives in
   `ci-hub/attribution/attribution.py`). It was independently adversarially verified against the
   **real** log, with the new anchor firing exactly once on the real line — proven non-inert.
   Re-implementing would collide with an open PR, which is the exact trap hermit-231b avoided.
2. **`validate.sh` is in the hermit primary.** Hard Invariant 1 forbids feature edits there; no slot
   is assigned to this task. (Slot 243, which once held the uncommitted edit, has since been
   reassigned to branch `validate/pr-1397-rebase` — that work is gone from there, superseded by the
   pushed PR.)
3. **Egress is down**, so nothing could be pushed, PR'd, or CI-verified.

## What is actually needed

**Land PR #1566.** Its last recorded state was OPEN draft, MERGEABLE, `core-review-protocol=SUCCESS`,
portable *Regular tests* QUEUED, `merge-gate=FAILURE` — the expected pre-green state, not a product
failure. Unverifiable now (egress). Until it lands, the classifier keeps working by coincidence.

Deliverable #4's recommendation is worth preserving verbatim on land: **do not** add an "unknown
errno" advisory list to the gate. Such a list would itself be sample-derived — the precise
anti-pattern this task was opened against. Tool-identity × errno-class anchoring is the durable
answer, and the diagnostic layer already carries `ENVIRONMENT=1` / `INDETERMINATE=2`.

## Provenance

| Claim | Status |
|---|---|
| Main lacks `bad file descriptor`, lacks the self-test, retains the 8-group inline regex | **verified this session** @ `b64d893a` |
| The 6-fixture bracket above, using the regex extracted verbatim from main | **executed this session** |
| Slot 243 now on `validate/pr-1397-rebase` | **observed this session** |
| PR #1566 contents, 17/17 self-test, real-log anchor count=1 | inherited from 2026-08-03 notes — **not re-executed** |
| PR #1566 open/mergeable state | inherited; **not verifiable — egress down** |
