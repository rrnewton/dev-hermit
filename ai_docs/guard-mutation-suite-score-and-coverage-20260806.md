# A mutation suite for guards: score 26/26 — and why that number is not the finding

**Task:** `mutation-testing-for-guards-a-measurable-score-not-ad-hoc-plants` · hermit-clone (opus-5), 2026-08-06
**Local, no egress.** Delivered: `ci-hub/validate/mutation_suite.py` (runnable catalogue + score),
`ci-hub/validate/tests/test_mutation_suite.py` (9 meta-bracket tests).

## Run it

```
python3 ci-hub/validate/mutation_suite.py           # score + survivors
python3 ci-hub/validate/mutation_suite.py --json
python3 ci-hub/validate/mutation_suite.py --guard qualified_rows
```

Exit 0 **iff** every mutant was killed, every population control held, and nothing was skipped.

```
  ancestry-primitive:  killed 1/1  | population control: NONE (mutants only)
  check_outcome:       killed 2/2  | control HOLDS: 0 flagged of 2 legitimate
  gitmodules_lint:     killed 3/3  | control HOLDS: 0 flagged of 9 legitimate
  green_class:         killed 3/3  | control HOLDS: 0 flagged of 1 legitimate
  landing-preflight:   killed 9/9  | control HOLDS: 0 flagged of 6 legitimate
  qualified_rows:      killed 5/5  | control HOLDS: 0 flagged of 585 legitimate
  zero-test-chain:     killed 3/3  | control HOLDS: 0 flagged of 1 legitimate

MUTATION SCORE: 26/26 (100.0%)
POPULATION CONTROLS: 6/6 holding
```

## The finding: 100% is over a catalogue I chose

**A mutation score is only as honest as its denominator, and there are two.** The mutant denominator
is 26. The one that matters is the **guard denominator**, and it is where the coverage actually is:

| the 5 guards the owner named as known-leaky | in the catalogue? |
|---|---|
| zero-test detector (empty-log gap) | **yes** — probed against the live chain |
| ancestry primitive (PR-head form) | **partially** — one call site only |
| backend-abstraction lint (3 of 6 backends) | **no** |
| `check-reverie-pin.rs` (consistency, not correctness) | **no** |
| retry classifier (greps text for a condition) | **no** |

**2 of 5.** So the correct reading of this run is *"26 of 26 mutants killed across 7 guards, of which
2 are from the known-leaky list"* — **not** "the guards are 100% effective". Zero survivors on a
self-chosen catalogue mostly measures the catalogue. The three uncovered guards are where survivors
are most likely, and they are named individually above so the next pass starts there rather than
re-deriving the list.

## What makes the 100% believable: the meta-bracket

A suite that has only ever printed 100% is unproven as a detector — the same present-but-inert
failure it exists to find, one level up. So the runner itself is bracketed (9 tests):

- an injected unkillable mutant is reported as **SURVIVED**, drops the score to 50%, is **named
  individually** in the rendered output, and makes the exit code 1
- a **kill-everything guard fails its population control** even at a 100% mutation score, and that
  combination still exits 1 — part 2 of the three-part bracket, enforced rather than described
- a probe that **raises is SKIPPED, never counted as killed**, and skipped mutants block. Scoring an
  errored probe as a kill would be the zero-executed-tests defect in a new costume
- **positive control**: an all-killed catalogue genuinely exits 0

## The hard precondition is executable, not remembered

> "I asked an agent to plant a `locally-validated` label on a real PR; it REFUSED, correctly — on a
> cold PR that label can satisfy merge-gate and AUTO-MERGE."

Every mutant declares a `hazard` class, and `Mutant.__post_init__` **raises `UnsafeMutant` for
anything authorisation-capable** — refused at construction, before it can be run, with the reason in
the exception. A test asserts an authorisation mutant cannot be built and that inert ones still can.
A precondition you have to remember is one you will eventually forget at 3am.

Every probe is read-only and calls the **real** guard. A probe that reimplemented a predicate would
be scoring itself, which is precisely the failure the suite exists to detect. Part 3 of the bracket
(cleanup) is satisfied structurally: nothing is planted on disk, so nothing can outlive the run.

## The recorded zero-test gap, probed against the live chain

The rule carried *"KNOWN GAP: an EMPTY log still passes this check."* Probed rather than assumed:

- `nonzero_result.executed_test_count("")` → **`None`**, not `0` — the counter correctly reports *no
  evidence* rather than *zero tests*
- `executed_test_count("running 0 tests")` → `0` — the `--features`-gating shape is detected
- both ledger consumers (`qualified_rows.is_qualified`, `anchor_select.row_qualifies`) **refuse a row
  whose `executed_tests` is null**

So at **those** consumers the gap is closed, and it is closed by the counter distinguishing None from
0 rather than by anyone grepping for a zero. Stated narrowly on purpose: I verified two consumers,
not every consumer of that counter.

## The allow-list caught my own suite

`test_no_undeclared_ledger_readers` failed on `validate/mutation_suite.py` — my population control
reads the raw ledger. Declared with its reason (it must see rows the accessor *rejects*, which is the
whole point of a kill-everything control). **Third time this session that guard has fired on genuinely
new work** — the strongest evidence in this document that a guard is real, and none of it planted.

## Verification

`346 passed` across `ci-hub/validate` + `ci-hub/landing` (excluding the known pre-existing
`test_failure_evidence` collision). Suite exits 0; `--json` emits score, controls, and survivors.

## Honest limits

- **3 of the 5 named-leaky guards are uncovered**, as tabled above. That is the top of the next pass.
- **The ancestry mutant covers one call site.** `check_landed_by_ancestry` structurally cannot test
  the PR head — a spy asserts the callable is invoked with the merge commit and nothing else — but
  the leak lives in *any other* call site still using the head form, and this suite cannot speak for
  those. The mutant carries that caveat in its `note` field.
- **The suite is not wired to run anywhere.** It is runnable and discoverable, not enforced on any
  path. Flagging it rather than leaving it quiet: an unwired gate is the exact shape this lane has
  spent the session finding.
- Guards implemented in Rust and shell (`validate.sh`, `check-detcore-backend-abstraction.sh`,
  `check-reverie-pin.rs`) need a different probe mechanism — subprocess with fixtures — which the
  catalogue supports in principle but does not yet use.

## Files

`ci-hub/validate/mutation_suite.py` (new) · `ci-hub/validate/tests/test_mutation_suite.py` (new) ·
`ci-hub/validate/tests/test_ledger_reader_allowlist.py` (+1 declared reader). Uncommitted, egress down.
