# Wiring the inert determinism/parity guards

**Task:** `fix-inert-determinism-parity-guards` (P1) · **Date:** 2026-08-06 · **Author:** hermit-design
**Follows:** `ai_docs/adv-review-determinism-parity-artifacts-20260806.md` (my own review)
**Status:** one guard wired and bracketed through its real consumers; the rest are blocked or
explicitly not-done — see §5. Local only, no egress, no validate run.

---

## 0. What changed, and what did not

| Review finding | Action | State |
| --- | --- | --- |
| `green_class` REAL but **0 consumers** | **WIRED** into the shared predicate + its live Python consumers | done, 8/8 brackets |
| DBI exit-RPC fix **INERT BY ABSENCE** | **explicitly NOT DONE** — reasons in §4 | not done, stated |
| parity: **XPASS never fails** | needs a hermit slot | specified, §5 |
| parity: `--check` rates unlabelled / ran-vs-listed | needs a hermit slot | specified, §5 |
| parity: ratchet never compared | needs a hermit slot | specified, §5 |
| *"empty-reason gaps / L1-gap-must-also-be-L2 not enforced"* | **PREMISE WRONG** — both already enforced | §1 |

Files changed (both parent-owned, uncommitted):
* `ci-hub/validate/qualifying-receipt.json` — added `accepts_green_class: ["hard"]` + its rationale
* `ci-hub/qualifying_receipt.py` — the class clause, delegating to `green_class`
* `ci-hub/validate/test_green_class_wiring.py` — new, 8 brackets

---

## 1. First: the task's premise about the gap validators is wrong

The task text lists *"gap-validation holes (empty-reason gaps, L1-gap-must-also-be-L2-gap not
enforced)"*. **Both are enforced**, and I measured them yesterday in the review itself:

| Planted | Result |
| --- | --- |
| gap with an empty reason | refused — `hello_stdout/dbi: known gap needs a reason` |
| L1 gap absent from `L2_GAPS` | refused — `hello_stdout/dbi: an L1 gap must also be an L2 gap` |

Exit code measured unpiped: **2** planted, **0** control. `validate_catalog` runs on every invocation
(`run_matrix.py:904`, before the backend loop), so it is live on the CI path too.

"Fixing" a working guard would have been the worst outcome available here, so this is recorded rather
than acted on. The real parity findings are the four in §5 — none of which is a missing gap validator.

---

## 2. The wiring: `green_class` now decides landing eligibility

### The change

`ci-hub/validate/qualifying-receipt.json` gains:

```json
"accepts_green_class": ["hard"]
```

and `ci-hub/qualifying_receipt.py::row_qualifies` becomes a wrapper:

```python
def row_qualifies(row, sha, pred) -> bool:
    if not _row_qualifies_without_class(row, sha, pred):
        return False
    return green_class_of(row) in _green_class.accepted_classes(pred)
```

Three deliberate properties:

* **It delegates, never restates.** `green_class_of` calls `_green_class.derive_class`. A second copy
  of the derivation inside the shared predicate would be precisely the drift the shared predicate
  exists to eliminate.
* **It is applied LAST, so it can only narrow.** A row that already failed a value clause stays
  refused. Putting the class check first would let it mask a value failure and change which reason a
  refusal reports — `test_the_clause_only_NARROWS` pins the ordering.
* **It is behaviour-neutral today**, by measurement, not by hope: `accepts_green_class` defaults to
  `["hard"]`, and a row with no `validated_head_sha` derives its class as `hard`.

### It fires in real consumers, not in a leaf

The review's complaint was that wiring into something nothing calls just moves the inertness. So:
`ci-hub/qualifying_receipt.py` is imported and its `row_qualifies` invoked by
**`history/query.py:939`** and **`validation/publish_receipt.py:45`**. Both still import cleanly after
the change (smoke-tested).

---

## 3. Brackets — both sides, with the denominator

`ci-hub/validate/test_green_class_wiring.py` — **8/8 pass**. Every test goes through
`qualifying_receipt.row_qualifies`, not through `green_class` directly; testing the classifier in
isolation is the exact weakness the review named.

| Bracket | Result |
| --- | --- |
| **Positive control** — a hard row still qualifies ("if this fails, the wiring broke landing for everyone") | pass |
| Policy key present and defaults to `["hard"]` | pass |
| **Planted soft row REFUSED** — and `_row_qualifies_without_class` still *accepts* it, proving the class clause is what refuses it and not some incidental field | pass |
| Planted `refused` row (soft claimed with no provenance) refused | pass |
| Planted **label forgery** (`green_class: "hard"` on a carried row) refused | pass |
| Clause only narrows — a non-`full` profile stays refused despite being class-hard | pass |
| Widening to `["hard","soft-rebase-only"]` admits exactly that class, still refuses `soft-upstream-delta` | pass |
| **Live-ledger control** — all **585** rows classify `hard`; **0** rows newly refused | pass |

The last row is the one that matters for safety: the whole existing population is unaffected, counted
explicitly rather than asserted vacuously.

### The regression checks that had to keep passing

* **Rust consumers unchanged.** `QualifyingPredicate` has no `deny_unknown_fields`, so serde ignores
  the new key. Verified end-to-end rather than assumed: `ci-hub validate-status --sha fc49593a` returns
  `verdict: VALIDATED, qualifying_count: 1` both before and after the JSON change — identical.
* **The existing five-consumer unanimity panel** (`test_qualifying_receipt_mutation.py`): **4 passed**.

---

## 4. The honest gap this wiring creates, and why I stopped here

**The clause is enforced by the Python consumers only.** The Rust readers
(`lib/qualifying_receipt.rs`, `lib/validate_status.rs`, and everything behind `ci-hub validate-status`
/ `newest-green`) silently ignore `accepts_green_class` — that is exactly *why* the change is safe
today, and also why it is incomplete.

So the honest status is **partially wired**: one authority, two engines, one of which now knows about
the class. That is a two-engine divergence, which is the same shape as the defects this line of work
keeps finding. I did not close it because completing it means editing `lib/qualifying_receipt.rs` and
rebuilding `ci-hub` — a shared tool other agents are actively using for landing decisions right now —
and doing that mid-session, under them, to fix a hazard that is currently *prospective* (no soft
producer exists) is the wrong trade. The Rust edit is small and is specified in
`ai_docs/soft-vs-hard-green-tracking-design-20260806.md` §7.

Until it lands, the accurate claim is: **the class clause fires in `history/query.py` and
`publish_receipt.py`; it does not yet fire in `validate-status` or `newest-green`.**

### The DBI exit-RPC guard: explicitly NOT DONE

The task offers "implement the absent one or mark it explicitly not-done". **Not done**, deliberately:

* It is a reverie change (`reverie-dbi/src/sync_rpc.rs`), needing a slot I do not have and cannot
  allocate.
* More importantly, per the design, the bounded exit wait should land **after** Bug A (#1147's
  `PrepareExec`), because landing it first would convert a loud 900 s hang into a tidy timeout and risk
  masking the panic that actually poisons the scheduler mutex. Wiring it now would make the artifact
  *look* fixed while making the underlying failure quieter — the exact anti-pattern this task exists to
  remove.

---

## 5. Parity findings that still need a hermit slot

None is a missing gap validator (§1); all four are about the gap list being unexecuted territory:

1. **Make XPASS fail** under `--probe-gaps` (`run_matrix.py:967-969` labels it; `:981-982` excludes
   gaps from `failures`), and run `--probe-gaps` on a schedule. Highest value: it turns the gap list
   from a growing liability into a self-cleaning one.
2. **Label `--check` output** as catalog-expected — those rates are `28 − len(gaps)` arithmetic and
   read identically on a box with no backend installed.
3. **Report parity as ran/listed**, never a bare percentage — gap cells record `seconds=0.000` and
   never execute.
4. **Compare the scorecard rate** to the last recorded one and refuse a decrease without an override,
   turning that ratchet from social into mechanical.

All four touch `hermit/`, which needs a slot and a PR; egress is 403 regardless.

---

## 6. Not established

* **No validate run, no hermit build, no network.** The Rust-unchanged claim rests on one
  `validate-status` probe before/after on one SHA plus the absence of `deny_unknown_fields`; I did not
  exercise every Rust consumer.
* **The live-ledger control proves only what today's population is** (585 rows, all hard). It cannot
  prove the clause behaves correctly on a soft row *written by a real producer*, because no such
  producer exists — the soft rows in the brackets are hand-built.
* **`accepts_green_class` is now read by two engines with different behaviour** (§4). Anyone reading
  the predicate file should not assume it is universally enforced.
* The changes are **uncommitted**; landing them is coordinator-owned and egress-gated.
