# Wiring the inert phase-2 guards: the anchor_select half

**Task:** `wire-inert-phase2-guards-into-consumers`
**Date:** 2026-08-06
**Agent:** `egress-probe2` (opus-5), completing the half `hermit-clone` left slot-blocked
**Change:** hermit branch `fix/anchor-select-qualifying-baseline` @ `58082897d`
(`d45ea7565` wiring + `58082897d` formatting fix), slot `worktrees/anchorwire/hermit`.
**Not pushed** — egress 403.

---

## 1. All four, enumerated

| guard | consumer it is now wired into | state |
| --- | --- | --- |
| `green_class.derive_class` | `ci-hub/validate/qualified_rows.py::is_qualified` | wired by `hermit-clone`; **committed** at parent `13c791e` |
| `green_class._classify_inherited` | reached through `derive_class` | same commit |
| `anchor_select.row_qualifies` | `hermit/validate.sh::resolve_selective_baseline` | **wired here** |
| `anchor_select._coverage_satisfied` | reached through `row_qualifies` (`anchor_select.py:190`, the `predicate.coverage.per_node` branch) | **wired here** |

I verified the first two survived rather than trusting the handoff: `git status`
is clean for `qualified_rows.py` and the clause is live at lines 62–64.

## 2. The hole, re-measured rather than inherited

`resolve_selective_baseline` inferred the last-known-green baseline with:

```
jq 'select(.result == "pass" and .commit != "unknown" and .slot == $slot)'
```

Two fields. It did not check `commit_anchored`, `tree_dirty`, `profile`,
`selection_mode` (the 1-hop clause), `failures`, `executed_tests`, or per-node
coverage — the seven other clauses of the shared qualifying-receipt predicate.

Two rows identical but for **one** field, against the real ancestor `b64d893a`:

| decider | `executed_tests: 0` | `executed_tests: 942` |
| --- | --- | --- |
| old jq filter | **ACCEPTED** `b64d893a` | ACCEPTED `b64d893a` |
| `row_qualifies` | **REFUSED** — `executed_tests==0` | QUALIFIES |

The old filter literally cannot tell them apart. So a receipt that **passed while
executing zero tests** was eligible as the green anchor — and inheriting from it
shrinks the test set on the strength of a run that tested nothing, while the lane
still reports green. That is the divergence the wiring closes.

## 3. Why it is safe

The branch now calls `anchor_select.py`, which loads the same
`qualifying-receipt.json` every other consumer reads — one verifier, not a
restatement. Its contract is **exit 0 = inherit, every non-zero exit = run the
full lane**, so no failure mode of the tool can yield a smaller test set.

Parent-helper availability uses the guarded idiom already in this file for
`failure_evidence.py` and `nonzero_result.py`: a bare hermit checkout has no
`ci-hub/`, so selection is disabled and the full suite runs — slower, never
weaker.

## 4. Plant and positive control, at both layers

**(a) Predicate layer, real data** — the table in §2. The refusal names the
failing clause, so it is auditable rather than a bare `False`.

**(b) Bash glue layer, stubbed verifier** — 9 cases:

```
exit 0 + valid anchor                  -> b64d893ae9ea6404472eae9cb86102d91ec642ef   (POSITIVE CONTROL)
exit 2 / 3 / 4 / 5                     -> <empty: FULL LANE>
exit 0, anchor null                    -> <empty: FULL LANE>
exit 0, sha absent from this checkout  -> <empty: FULL LANE>
explicit --selective-baseline          -> b64d893a…   (override deliberately exempt)
no dev-hermit parent (bare checkout)   -> <empty: FULL LANE>
```

**Why the glue layer needed its own stub, rather than the live CLI:** on this
synthetic ledger the real `anchor_select.py` returns **rc=3 RE-ANCHOR-NOW** (an
anchor exists, but the selected set is the full set), so an end-to-end rc=0 was
not reachable here. Without the stub there would have been **no positive
control**, and every "falls back to the full lane" assertion would have been
vacuous — they all pass trivially if the function always returns empty. The
predecessor hit exactly that failure twice (once a `%r` format spec meant the
stub was never written; once the harness ran outside the git repo so the trailing
`git cat-file -e` rejected valid SHAs). I kept their rule: **one case must
succeed.**

**Mutation bracket on the guard itself:** reverting the wiring makes
`scripts/check-selective-baseline-qualification.sh` refuse with rc=1 — *"no
longer routes through anchor_select.py"* — and restoring it passes 9/9.

## 5. The guard is not itself inert

This task exists because four guards had real logic and zero consumers. Adding a
fifth would have repeated the bug, so the new check is registered as
`check.selective_baseline_qualification` in `ci/dag/portable.json` and **executed
through the real runner**:

```
$ ./ci/run-node.sh portable check.selective_baseline_qualification
[check.selective_baseline_qualification] ✓ PASS  … (3s)
safe-ci-dag-runner: PASS - 1 passed, 0 failed
```

`ci/test_harness.sh validate` passes: 306 E2E tests valid, DAG corresponds with
no dangling deps.

## 6. A deliberate remaining bypass — flagged, not hidden

`--selective-baseline` and `$HERMIT_LAST_GREEN_SHA` still skip the predicate. A
human naming a baseline is an instruction, not an inference, so this is
intentional — but it *is* a bypass, and it is now **pinned by a test** so it stays
a decision rather than drifting into an accident. The owner should confirm it
rather than inherit it from me. Qualifying the overrides, or at least logging
when one skips the predicate, is a one-line follow-up.

## 7. Two things worth recording against my own work

* **A formatting slip.** My first DAG insert used a `json.dump` round-trip and
  reformatted the whole file — 1029 changed lines for a one-node insert, which
  buries the real change and guarantees a conflict with any concurrent edit.
  Re-inserted as text in the file's existing style (`+8/-0`), fixed in a
  follow-up commit rather than an amend.
* **A correction to the handoff.** The predecessor recorded status in plain text
  because "`tg update --tags` is not sticking on this task set". It did stick —
  the task already carries `implemented`. Their belief was wrong, and anyone
  relying on it would have double-tagged or mistrusted the tag.

## 8. Residue

1. **Nothing pushed** (egress 403). Needs a PR against `rrnewton/hermit:main`
   plus an exact-head validate receipt.
2. **Transient, not caused here:** the first `test_harness.sh validate` run
   reported *"1 of 4 concurrent real Reverie-pin checker builds failed"*; a clean
   re-run passes. Same transient seen on an unrelated task earlier today.
3. **The green_class half's honest limit still stands** (recorded by
   `hermit-clone`, unchanged by this work): 0 of 585 real ledger rows carry
   `validated_head_sha` or `inherited_from`, so `derive_class` returns `hard` for
   all of them via its version-aware default. That clause is *called* on every row
   but cannot yet refuse one in the live population; it becomes discriminating the
   moment a soft producer writes provenance. "Wired" means the guard can now fire,
   not that the hole is closed.
4. **`tail -n 1` orders by file position, not event time** in the surrounding
   ledger code — noted by the predecessor, still unaddressed, out of scope here.
