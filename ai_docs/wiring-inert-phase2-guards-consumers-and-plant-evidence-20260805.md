# Wiring the INERT phase-2 guards into consumers — what fired, what is still blocked

**Task:** `wire-inert-phase2-guards-into-consumers` · hermit-clone (opus-5), 2026-08-05
**Local only, no egress, no validate-run, no concurrent validate.** Follows
`ai_docs/phase2-tightening-guards-adversarial-review-20260805.md`, which found the four guards inert.

## Result

| Guard | Consumer wired into | Fires? | Evidence |
|---|---|---|---|
| `green_class.derive_class` | `qualified_rows.is_qualified` (wired to `ci-hub.rs` → `qualified-rows`) | **YES** | planted laundered soft green refused via the production entrypoint |
| `green_class._classify_inherited` | same (reached through `derive_class`) | **YES** | every soft class + every malformed-provenance case refused |
| `anchor_select.row_qualifies` | `hermit/validate.sh:4319 resolve_selective_baseline` | **NO — BLOCKED** | consumer is in the hermit submodule; no slot allocated |
| `anchor_select._coverage_satisfied` | same | **NO — BLOCKED** | same |

Bracket debt from the review: **10 unbracketed clauses → 0**, re-verified by mutation.

---

## 1. `green_class` — wired, with plant evidence through the production path

One clause added to `ci-hub/validate/qualified_rows.py::is_qualified`:

```python
green_class, _green_reason = derive_class(dict(row))
return (
    green_class == HARD
    and row.get("result") == "pass"
    ...
```

`is_qualified` is the right consumer: it is the canonical population for green
timing/concurrency analysis and it is **already wired** (`ci-hub.rs:1654` shells out to this exact
file). A SOFT green is validation that ran on an *ancestor*; admitting it would corrupt precisely
what that view measures.

**Negative control — the plant, run through the production entrypoint** (3 real qualifying rows plus
one row copied from a real green, moved to an ancestor and stamped `green_class: "hard"` so it reads
byte-identical to a hard green):

```
$ python3 ci-hub/validate/qualified_rows.py --ledger scratch/wire-plant/planted-ledger.jsonl
  ... 3 rows emitted ...
  qualified-rows: 3/4 qualified; malformed=0; sorted=finished_at
```

The laundered row is refused and never reaches the output. This is the CLI's own code path, not a
unit-test harness.

**Positive control — no existing producer is rejected:**

```
$ python3 ci-hub/validate/qualified_rows.py --ledger ignored/validate-run-ledger.jsonl
  qualified-rows: 107/585 qualified; malformed=0; sorted=finished_at     # unchanged
```

### The honest limit on this wiring

**0 of 585 real rows carry `validated_head_sha` or `inherited_from`**, so `derive_class` returns
`hard` for all 585 by its documented version-aware default. The clause is therefore **called on every
row but cannot yet refuse one in the live population.** It becomes discriminating the moment a soft
producer writes provenance.

That is the correct order — the module's own argument is that the field must exist *before* the first
soft producer, or introducing it later is a fleet-wide flag day (the incident that once rejected
254 of 255 rows). But "wired" here means **the guard can now fire**, not "the hole is closed". I am
not claiming the latter.

## 2. `anchor_select` — NOT wired, and why

Its only consumer is `resolve_selective_baseline` at `hermit/validate.sh:4319` — inside the **hermit
submodule**. Hard Invariant 1 forbids feature work in a primary checkout, `git -C hermit branch
--show-current` is `main`, and no slot is allocated to this agent. There is no parent-side
anchor-selection consumer to wire into instead (checked: `ci-hub/` has `gate_floors.py` and
`preflight_anchor.py`, neither of which selects a baseline).

**This half needs a coordinator-allocated hermit slot.** The change itself is small and already
specified in `ai_docs/green-inheritance-anchor-selection-20260805.md:304`: replace the body of
`resolve_selective_baseline` with a call to `anchor_select.py --target HEAD --include-dirty --json`,
consuming `anchor.sha`, falling back to the full lane on any non-zero exit (that branch already
exists at `run_selective_suite:4376`).

## 3. Bracket debt closed: 10 → 0

Two new test files, both built so each case perturbs exactly one field of a row that **qualifies at
baseline**, so a refusal is attributable to the named clause:

- `ci-hub/validate/tests/test_anchor_select_clause_brackets.py` — the 7 live-but-untested clauses,
  including the worst one: a receipt with `commit == "unknown"` being eligible as a green **anchor**.
- `ci-hub/validate/tests/test_green_class_wiring.py` — both directions of the wiring, every soft
  class, the `NOT_GREEN` case, and the four malformed-provenance type-guards.

Re-running the review's mutation harness with the new suites included:

```
anchor_select.row_qualifies       12 mutable clauses, 0 UNBRACKETED   (was 4)
anchor_select._coverage_satisfied  4 mutable clauses, 0 UNBRACKETED   (was 2)
green_class.derive_class           5 mutable clauses, 0 UNBRACKETED   (was 0)
green_class._classify_inherited   11 mutable clauses, 0 UNBRACKETED   (was 4)
```

## 4. Two things caught against my own work

- **My first soft fixture was vacuous.** It used `kind:` where the schema is `delta_kind:`, so the
  row was refused as *malformed* rather than classified *soft* — and the assertion `derived != HARD`
  passed for the wrong reason. Fixed to assert the specific class (`SOFT_REBASE_ONLY`), which is what
  actually proves a genuine soft green is excluded. This is the exact vacuity the review flagged in
  others; it is easy to write.
- **The ledger-reader allow-list caught me.** My new test reads the real ledger for the population
  invariant, and `test_no_undeclared_ledger_readers` failed with
  `Undeclared reader(s): validate/tests/test_green_class_wiring.py`. I declared it with a reason, as
  the lint intends. Incidental evidence that **that** guard is REAL: it fired on a genuine new
  violation nobody planted.

## 5. Verification state

```
297 passed, 1 failed   (ci-hub/validate + attribution + landing)
```

The single failure is `test_failure_evidence.py::test_measured_flake_is_bound_to_failed_cell`, and it
is **pre-existing and independent**: it imports none of the modules I touched, and it fails
identically when `qualified_rows.py` is reverted to its `HEAD` content. Separately,
`ci-hub/validate/tests/test_failure_evidence.py` has a pre-existing duplicate-basename collection
collision — also reproduced with my two new files moved out of the tree.

## 6. Files changed (all parent, all uncommitted — egress down)

| file | change |
|---|---|
| `ci-hub/validate/qualified_rows.py` | +3 imports, +1 clause, +8 lines of rationale |
| `ci-hub/validate/tests/test_green_class_wiring.py` | new — wiring brackets, both directions |
| `ci-hub/validate/tests/test_anchor_select_clause_brackets.py` | new — the 7 missing clause brackets |
| `ci-hub/validate/tests/test_ledger_reader_allowlist.py` | +1 declared reader, with reason |

`anchor_select.py` and `green_class.py` themselves were **not modified** — the wiring is additive at
the consumer, so it cannot collide with the agent who authored them (both files untracked, last
touched 19:51 and 20:30; no process held either at 20:48).

## Reproduction

```
python3 ci-hub/validate/qualified_rows.py --ledger ignored/validate-run-ledger.jsonl   # 107/585
python3 ci-hub/validate/qualified_rows.py --ledger scratch/wire-plant/planted-ledger.jsonl  # 3/4
python3 -m pytest ci-hub/validate/tests/test_green_class_wiring.py \
                  ci-hub/validate/tests/test_anchor_select_clause_brackets.py -q
python3 scratch/phase2-review/mutate.py     # 32 clauses, 0 UNBRACKETED
```
