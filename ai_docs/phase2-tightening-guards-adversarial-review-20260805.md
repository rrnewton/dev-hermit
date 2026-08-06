# Adversarial review of the phase-2 tightening guards — per-guard verdict, mutation brackets, denominators

**Task:** `adversarial-review-phase2-tightening-artifacts` · **Reviewer:** hermit-clone (opus-5), 2026-08-05
**Method:** plant-a-violation + positive-control on every guard, executed locally. No egress, no
validate-run, no concurrent validate. **No green claimed for any product change** — this reviews
guards, and every number below was produced by running them.

## Headline

Two of the six executable guards are **complete, well-tested, and wired to nothing**. The four that
are wired are real and discriminating. Ten guard clauses across the set are held in place by no test.

| Guard | Wired? | Mutation bracket | Population | **Verdict** |
|---|---|---|---|---|
| `anchor_select.row_qualifies` | **0 consumers** | 12 clauses, **4 unbracketed** | 107/585 qualify (18.3%), 5 distinct refusal reasons | **REAL logic, INERT in production** |
| `anchor_select._coverage_satisfied` | **0 consumers** | 4 clauses, **2 unbracketed** | 8 rows refused on this clause | **REAL logic, INERT in production** |
| `green_class.derive_class` | **0 consumers** | **5/5 bracketed** ✅ | n/a | **REAL logic, INERT in production** |
| `green_class._classify_inherited` | **0 consumers** | 11 clauses, **4 unbracketed** | n/a | **INERT in production** |
| `qualified_rows.is_qualified` | **WIRED** (`ci-hub.rs:1654`) | 9 conjuncts, **2 unbracketed** (type-guards) | 107/585 (18.3%) | **REAL** |
| `kill_signature.classify_kill` | **WIRED** (`attribution.py:80`, `query.py:483`) | **5/5 bracketed** ✅ | n/a | **REAL — the model to copy** |

Baseline before any mutation: **110 tests pass** across the five suites.

---

## Finding 1 (most important) — `anchor_select.py` and `green_class.py` have ZERO code consumers

```
grep -rn "anchor_select" --include=*.py --include=*.sh --include=*.yml --include=*.rs .
  -> 0 hits outside the module itself, its own test, and ai_docs prose
grep -rn "green_class"  (same filters)          -> 0 hits
grep -c "anchor_select" hermit/validate.sh      -> 0
```

Both are finished modules with real logic and passing suites (`anchor_select` 766 lines / 30 tests;
`green_class` 493 lines / 41 tests). Nothing calls either one. The design doc is explicit that the
wiring step was never taken — `ai_docs/green-inheritance-anchor-selection-20260805.md:304` still
reads *"**Fix:** replace the body of `resolve_selective_baseline` with a call to
`anchor_select.py`"*, and `validate.sh:4319 resolve_selective_baseline` still carries its own body.

**This is the textbook present-but-inert guard**: a passing test suite proves the *logic* works and
says nothing about whether the guard can ever fire. It corroborates the recorded
"anchor-picker BYPASSES the qualifying predicate" finding, and it means the anchor actually used in
production is still chosen by the old path, not by this predicate.

The fix is a wiring change, not a logic change — the expensive part is already done.

---

## Finding 2 — the wired guards are discriminating, not over-broad (the positive control)

Run over the **real** ledger, `ignored/validate-run-ledger.jsonl`, **585 rows, 0 malformed**:

```
anchor_select.row_qualifies (predicate: ci-hub/validate/qualifying-receipt.json)
  QUALIFY 107/585 (18.3%)   REFUSE 478
  refusal reasons (first failing clause):
     168  result
     163  profile
     100  commit_anchored
      39  pre-count receipt cannot prove nonzero execution
       8  count-capable receipt coverage unsatisfied
```

Neither flags-everything nor flags-nothing, and the refusals spread across five distinct clauses
rather than piling onto one — so the population is being genuinely discriminated, not gated by a
single dominant term.

**Independent cross-check:** `qualified_rows.is_qualified` — a *different* implementation, in a
different file, wired to a different consumer — selects **exactly 107 of the same 585 rows**. Two
independently-written predicates agreeing on the same 107 is meaningful corroboration that 107 is a
property of the data, not of one implementation.

---

## Finding 3 — 10 unbracketed clauses; the 7 tested ones are LIVE, not dead code

Method: for each clause, rewrite its condition so it never fires, then run **that guard's own test
suite**. Suite still passes ⇒ nothing holds the clause in place.

```
anchor_select.row_qualifies      12 mutable clauses,  4 UNBRACKETED
    L162  if row.get("commit") in (None, "", "unknown"):
    L177  if failures is not None and failures > require["failures_max"]:
    L188  count-capable receipt: executed_tests missing / below min
    L193  pre-count receipt: executed_tests missing / below min
anchor_select._coverage_satisfied 4 mutable clauses,  2 UNBRACKETED
    L144  planned_test_nodes missing or <= 0
    L146  zero_executed_nodes non-empty
green_class.derive_class          5 mutable clauses,  0 UNBRACKETED   ✅
green_class._classify_inherited  11 mutable clauses,  4 UNBRACKETED
    L241 / L253 / L265 / L267  (type-guards on the `inherited` record)
```

"Unbracketed" alone would be an ambiguous finding — a clause can be untested because it is
*redundant*. So each of the seven `anchor_select` clauses got a discriminating input constructed for
it, checked against the **unmutated** guard:

```
baseline good row -> (True, 'qualifies')                       [positive control]
L162 commit=='unknown'                    -> refused 'no-commit'
L177 failures>max                         -> refused 'failures=5'
L188 count-capable, executed=None         -> refused 'count-capable receipt missing executed_tests'
L193 pre-count schema, executed=None      -> refused 'pre-count receipt cannot prove nonzero execution'
L144 planned_test_nodes absent            -> refused 'coverage unsatisfied'
L144 planned_test_nodes=0                 -> refused 'coverage unsatisfied'
L146 zero_executed_nodes non-empty        -> refused 'coverage unsatisfied'
```

All seven reject an input that **no other clause rejects**. They are **LIVE and untested** — a
refactor could delete any of them and every test would stay green. Most consequential:
**L162 would let a receipt with `commit == "unknown"` be selected as a green anchor.**

**Severity split, stated honestly.** The two unbracketed conjuncts in the *wired*
`qualified_rows.is_qualified` (`isinstance(executed, int)`, `isinstance(ran, int)`) are type-guards:
by Python comparison semantics their removal makes `"5" > 0` raise `TypeError` rather than return a
false green. That is a robustness gap, materially less severe than `anchor_select`'s, which would
produce **false accepts**. (Reasoned from language semantics, not mutation-verified.)

---

## Finding 4 — a recorded defect is now FIXED (stale memory corrected)

The standing note that the qualified-rows guard *"landed but its own bracket missed
`result == 'pass'`"* no longer holds. Dropping that conjunct **fails** the suite:

```
DROP row.get("result") == "pass"      tests_pass=False   bracketed ✅
```

7 of its 9 conjuncts are now bracketed, including the subtle `not isinstance(executed, bool)`
(`True > 0` is `True` in Python, so the bool exclusion is load-bearing — and it is held by a test).

## Finding 5 — `kill_signature.classify_kill` is the model

**5/5 clauses bracketed**, including the order-sensitive OOM gate whose comment declares
*"MUST precede the ratio test"* — killing it fails the suite, so the ordering constraint is enforced
by a test and not merely asserted in a comment. This is what the other guards should look like.

---

## Method caveats (the harness is itself a proxy — stated, not hidden)

- The line-based mutator only mutates **single-line `if …:`** conditions. The OOM gate was **silently
  skipped** on the first pass because of a trailing comment, and needed a targeted second mutation.
  A harness that skips silently reports "all bracketed" when it means "all bracketed among those I
  tried" — I therefore report *mutable clauses attempted*, not just failures. Multi-line conditions
  in these files remain unmutated and their bracket status is **UNKNOWN**, not "bracketed".
- Coverage denominator: **6 executable guards reviewed** out of the ~35 claimed phase-2 artifacts.
  The remainder are research/design documents with no executable guard to plant against
  (`ai_docs/*-audit-*.md`, `*-design-*.md`), or live outside the parent tree. This review makes **no
  claim** about those.

## Plant hygiene

All mutation was done on a scratch copy (`scratch/phase2-review/`). Two late probes ran with a
relative root that resolved to the **real** `ci-hub/` and briefly mutated
`ci-hub/lib/kill_signature.py` before restoring it in the same script — disclosed because another
agent could in principle have read the file during that window. Verified afterwards:
`git diff --quiet HEAD -- ci-hub/lib/kill_signature.py` → **identical to HEAD**; no `MUTANT` string
anywhere in the four guard files; the OOM-gate line intact at `:105`; and all **110 tests pass** on
the real tree. No authorization artifact was ever planted.

## Recommendations

1. **Wire `anchor_select.py`** into `resolve_selective_baseline` — the guard exists, is tested, and
   discriminates 107/585; it just cannot fire. Highest value per hour in this set.
2. **Wire or explicitly shelve `green_class.py`.** An unwired guard in-tree reads as coverage that
   does not exist; if it is waiting on something, say so beside the module.
3. Add the 7 missing brackets in `anchor_select` (the discriminating inputs are listed above and can
   be pasted in as test cases), prioritising **L162**.
4. Adopt the `classify_kill` pattern: every clause whose comment asserts an ordering or a "MUST" gets
   a mutation test proving it.

## Reproduction

```
cp -a ci-hub scratch/phase2-review/ci-hub          # never mutate the shared tree
python3 scratch/phase2-review/mutate.py            # if-clause brackets
python3 scratch/phase2-review/mutate2.py           # conjunct + classify_kill brackets
python3 scratch/phase2-review/discriminate.py      # live-vs-dead for unbracketed clauses
python3 scratch/phase2-review/population.py        # 585-row denominator
```
