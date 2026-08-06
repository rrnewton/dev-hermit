# Fixing the slice-5 findings: a real ratchet for `.gitmodules`, and 11 unbracketed clauses → 0

**Task:** `fix-inert-process-infra-guards` · hermit-clone (opus-5), 2026-08-05
**Local ci-hub only, no egress, no validate-run.** Fixes my own review findings in
`ai_docs/adv-review-process-infra-artifacts-slice5-20260805.md`.

## 0. Premise correction (checked before acting)

The task states *"adv-review-process-infra found EVERY guard it could exercise in this slice was
INERT"*. That is the opposite of what the review found: **slice 5 contained no inert guards** — every
guard I could locate and exercise was wired. The four inert guards were **slice 2**
(`anchor_select`, `green_class`).

The specific `.gitmodules` claim also needed checking before I built anything around it:

| claim | ground truth |
|---|---|
| default-checkout absent from **reverie**/.gitmodules | **refuted** — all 3 entries carry `update = checkout` |
| default-checkout absent from **hermit**/.gitmodules | **true** — neither entry carries it |
| "cold-clone-verify would fail" | **refuted** — see below |

`git-submodule(1)`: *"If the key `submodule.$name.update` is either **not explicitly set** or set to
`checkout`, this option is implicit."* **Absence ≡ `checkout`.** So hermit's missing directive is a
style inconsistency, not a functional defect, and a lint that failed on it would fire on a
non-defect — the failure mode that trains readers to ignore a check.

The **real** cold-clone hazard is `shallow = true` (a shallow submodule makes verify pass by removing
the history it would have checked). That is absent from all three files: **the fix is applied.** My
review's actual finding stands — it had *no ratchet*. That is what I built.

## 1. `gitmodules_lint.py` — the missing ratchet

`ci-hub/validate/gitmodules_lint.py`. Checks the hazards, not cosmetics:

| hazard | why |
|---|---|
| `shallow = true` | silently skips cold-clone verify |
| `update = none` | leaves an empty directory where a consumer expects a tree (the mode the 2026-08-02 checked-out-by-default policy retired) |
| `branch = …` | parent guide forbids it: turns an exact gitlink into a moving target |

Deliberately **not** checked: absence of an explicit `update = checkout`, for the reason above. Such
entries are *reported* as informational so the inconsistency stays visible, and never fail.

Live tree: **0 hazards across 9 submodule entries in 3 files, 0.12 s.**

```
gitmodules-lint: note …/hermit/.gitmodules: 2 entr(ies) rely on the implicit `update = checkout`
  default (third-party/rr, agent-utils) -- equivalent to setting it, not a hazard
gitmodules-lint: 0 hazard(s) across 9 submodule entr(ies) in 3 file(s)
```

**Wiring:** `ci-hub/validate/tests/test_gitmodules_lint.py::test_live_tree_has_no_gitmodules_hazard`
runs on every ci-hub suite invocation, so the fix can no longer regress silently. 11 brackets:

- **planted** `shallow = true` (and `yes`/`on`/`1`/`TRUE`/whitespace spellings), `update = none`,
  `branch = main`, and all three at once — each caught
- **negative control**: `shallow = false` is *not* a hazard (guard against flags-everything)
- **positive control**: a clean file, and the implicit-default file, both pass
- **CLI contract**: exit 1 on a planted hazard, exit 0 on clean
- **anti-vacuity**: discovery must reach the parent's `.gitmodules` *and* at least one submodule's,
  so the ratchet cannot pass by scanning nothing

## 2. The two thinly-held classifiers: 11 unbracketed clauses → 0

Mutation (kill each clause, re-run its own suite), before and after:

| function | before | after |
|---|---|---|
| `check_outcome.classify_check` | 4 clauses, **1 unbracketed** | 4, **0** |
| `attribution.attribute` | 24 clauses, **8 unbracketed** | 24, **0** |
| `attribution.host_under_pressure` | 6 clauses, **2 unbracketed** | 6, **0** |

- `ci-hub/tests/test_check_outcome_status_gate.py` (6 tests) — the in-flight status gate:
  `in_progress` + stale `success` → NO_RESULT (the false-green case), the symmetric stale-red case,
  every non-completed status × every conclusion, plus positive controls that `completed` still
  resolves normally and an *absent* status still falls through to the conclusion (so the gate can't
  be widened to "always NO_RESULT" without failing).
  **Calibration preserved in the docstring**: 0 of the 1,572 live checks would have flipped; this is
  defence-in-depth against a shape the API can emit, not a fix for an observed incident.
- `ci-hub/attribution/tests/test_attribution_clause_brackets.py` (9 tests) — the whole
  `SHAPE_NONZERO` branch (1 test reference before, vs 13 for `SHAPE_HANG`), the CRASH external-read
  arm, the weak-evidence `clean and not pressure` arm asserting it is *not* reported at `high`
  confidence, both `host_under_pressure` thresholds isolated so only the threshold under test can
  move the verdict, and the `signals` payload (the three signal-only clauses, which no verdict
  assertion can hold).

Each test pins the **specific verdict**, never merely "not the default" — a fixture landing in
`INDETERMINATE` would satisfy a weaker assertion while proving nothing.

## 3. Three self-catches, disclosed

1. **My lint's first `discover()` used `rglob` then filtered** — >120 s on this tree (build outputs,
   other agents' worktrees). Rewritten to prune during `os.walk`; 0.12 s.
2. **My test's `ROOT = parents[2]` pointed at `ci-hub`, not the parent repo**, so discovery returned
   nothing. Caught by the anti-vacuity test I had written for exactly this — it is the reason the
   ratchet isn't silently scanning an empty set.
3. **cwd drift lost work silently.** A `cat >>` append landed in the *scratch copy* rather than the
   real file; the "9 passed" I then saw was the copy, and deleting the copy removed the tests. Caught
   only because the mutation re-run still reported 3 unbracketed when it should have reported 0.
   Third cwd-drift incident this session — **use absolute paths for every write.**

## 4. Verification

```
407 passed, 2 failed   (ci-hub/validate + attribution + tests + health)
```

Neither failure is mine:

- `test_failure_evidence::test_measured_flake_is_bound_to_failed_cell` — pre-existing, imports none
  of the touched modules, fails identically at `HEAD`.
- `test_no_undeclared_ledger_readers` — now flags
  `validate/test_green_class_predicate_wiring.py`, a file created at **21:32:45** (7 minutes before I
  checked) by another agent still working. Not mine to declare; **flagged for its author** — the
  declaration needs their reason, not my guess.

No `MUTANT` residue in `ci-hub/`; all mutation ran on a scratch copy.

## 5. Still open

- **hermit/.gitmodules explicit-`update` consistency** — cosmetic only (absence ≡ checkout), and it
  is a hermit-submodule edit, so it needs the same slot as the parked `anchor_select` patch. Not
  worth a slot on its own; fold into the cascade.
- **Artifacts 2, 3, 4, 6, 8, 9, 10** still need either a locatable guard or an explicit
  "documentation only, nothing to enforce" disposition. Unchanged from the review.

## Files (parent, uncommitted — egress down)

`ci-hub/validate/gitmodules_lint.py` (new) · `ci-hub/validate/tests/test_gitmodules_lint.py` (new) ·
`ci-hub/tests/test_check_outcome_status_gate.py` (new) ·
`ci-hub/attribution/tests/test_attribution_clause_brackets.py` (new).
**No existing module was modified** — every fix is additive, so none of it can collide with the
agents currently working in `ci-hub/validate/`.

## Reproduction

```
python3 ci-hub/validate/gitmodules_lint.py                    # 0 hazards, 9 entries, 3 files
python3 -m pytest ci-hub/validate/tests/test_gitmodules_lint.py \
                  ci-hub/tests/test_check_outcome_status_gate.py \
                  ci-hub/attribution/tests/test_attribution_clause_brackets.py -q
python3 scratch/slice5/mut.py                                  # 34 clauses, 0 UNBRACKETED
```
