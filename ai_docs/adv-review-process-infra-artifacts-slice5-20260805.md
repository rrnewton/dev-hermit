# Adversarial review — slice 5 (process / ci-infra / backend-CLI artifacts)

**Task:** `adv-review-process-infra-artifacts` · hermit-clone (opus-5), 2026-08-05
**Local, no egress, no validate-run.** Same discipline as the review that found the four inert
guards (`ai_docs/phase2-tightening-guards-adversarial-review-20260805.md`). Every number was
produced by running the guard.

## Headline

**No inert guards in this slice** — unlike slice 2, every guard I could locate and exercise is
wired. The findings are elsewhere: one wired classifier has its central clause held by no test, the
flake-attribution classifier has 8 of 24 clauses unbracketed, and one "fix" has no ratchet
preventing its own regression.

**Coverage, stated up front:** of the 11 named artifacts, **2 were reviewed with the full
plant + mutation + denominator discipline**, **3 more got a wiring verdict only**, and **6 had no
executable guard I could locate offline**. This review makes no claim about those 6.

| # | artifact | wired? | bracket | verdict |
|---|---|---|---|---|
| 5 | cancelled-scheduled-run-silent (`check_outcome.classify_check`) | **7 consumers** | 4 clauses, **1 unbracketed** | **REAL** |
| 11 | flaky-failure-attribution (`attribution.attribute`) | **13 consumers** | 24 clauses, **8 unbracketed** | **REAL, thinly held** |
| 11b | `attribution.host_under_pressure` | (same) | 6 clauses, **2 unbracketed** | **REAL, thinly held** |
| 7 | cargo-build-parallelism-provenance (`configure-build-jobs.sh`) | **3 consumers** | not exercised | wired; not reviewed |
| 1 | reverie-gitmodules-shallow-skipping | n/a | **no ratchet** | fix applied, **unprotected** |
| 2,3,4,6,8,9,10 | host-dependent-deps audit, backend-prefix-match, backend short `-b`, wip-limit-open-prs, third-party downstream DAG, extend-safe-ci-dag-runner, dag-parallelism-surface | — | — | no executable guard located offline |

---

## Artifact 5 — cancelled-scheduled-run-silent: **core claim holds**

`ci-hub/check_outcome.py::classify_check`, imported by 7 files (`health/pr_status.py:58`,
`github_main_health.py`, `history/query.py`, `remediation/protocol.py`, `landing/land-pr.sh`, …).

**Positive control — the artifact's own claim, verified:**

```
status=completed conclusion=cancelled -> NO_RESULT
status=completed conclusion=skipped   -> NO_RESULT
status=completed conclusion=stale     -> NO_RESULT
status=completed conclusion=neutral   -> NO_RESULT
```

A cancelled run is not a result. `PASS_CONCLUSIONS = {success}`,
`FAIL_CONCLUSIONS = {error, failure, startup_failure, timed_out}` — everything else falls through to
NO_RESULT, so the set is closed by construction rather than by enumeration of the bad cases.

**Denominator (over 1,572 real checks in `ignored/open-prs-rollup.json`):**

```
592 NO_RESULT   566 PASSED   414 FAILED
```

Discriminating across all three outcomes — not flags-everything, not flags-nothing.

**Finding 5.1 — the in-flight status gate is unbracketed.** Mutation: 4 mutable clauses, 1 survives.

```
L257  UNBRACKETED  if normalized_status and normalized_status != "completed":
```

That clause is what stops a check that has **not finished** from being read as a result. Deleting it
changes the verdict on constructible inputs:

| input | with clause | without |
|---|---|---|
| `in_progress` + stale `success` | NO_RESULT | **PASSED** (false green) |
| `queued` + stale `failure` | NO_RESULT | **FAILED** (false red) |
| `in_progress` + empty conclusion | NO_RESULT | NO_RESULT (no change) |

**Calibrated severity — and this is the part not to overstate: 0 of the 1,572 real checks would
flip.** Every in-flight check in the live corpus carries an empty conclusion, so the fallthrough
already yields NO_RESULT. The clause is therefore *defence-in-depth against a shape GitHub can
emit but currently does not here*, held by no test. Worth a two-line bracket; not a live incident.

---

## Artifact 11 — flaky-failure-attribution: real, but thinly held

`ci-hub/attribution/attribution.py::attribute`, 13 consumer files. Baseline suite green.

**Mutation: 24 mutable clauses, 8 unbracketed** — with an important severity split I checked in the
source rather than assuming:

- **3 are signal-only** (`L428` divergence, `L430` low_load, `L432` external_reads). They populate
  the emitted `signals` dict and nothing else, so deleting them degrades the *evidence payload*, not
  the attribution. Lower severity.
- **5 are verdict-changing** — `L532` (`low_load.clean and not pressure`), `L577` / `L614`
  (`external_reads`), `L613` (the whole `SHAPE_NONZERO` branch), `L621`. Deleting any of these
  changes the returned class/confidence, i.e. a red could be attributed to the wrong owner.

`host_under_pressure`: 6 clauses, 2 unbracketed (`cpu_pressure_avg10`, `mem_avail_ratio` thresholds)
— both verdict-changing, since `pressure` gates several attribution branches.

**Corroborating coverage signal:** test references per shape — `SHAPE_HANG` 13, `SHAPE_MISMATCH` 6,
`SHAPE_CRASH` 3, `SHAPE_HARNESS` 2, **`SHAPE_NONZERO` 1**. The thinnest-tested shape is exactly the
one whose branch mutation survived.

---

## Artifact 1 — a fix with no ratchet

`shallow = true` is absent from both `hermit/.gitmodules` and `reverie/.gitmodules`, so the
cold-clone-verify fix is applied. **But nothing prevents its regression**: no lint, no CI check, no
test asserts the absence. The failure it guards against (a shallow submodule silently skipping
cold-clone verification) is exactly the kind that reappears silently. One-line fix: a grep assertion
in the portability lint. Classifying this as *applied but unprotected* rather than REAL, because a
fix with no guard is a state, not a control.

---

## Method self-correction (disclosed, because it nearly produced a false verdict)

My first consumer sweep filtered candidate references with a substring exclusion
(`grep -v "/$module."`), which also stripped legitimate call sites of the form
`source "$ROOT_DIR/ci/configure-build-jobs.sh"`. It reported **0 consumers** for
`configure-build-jobs.sh` — a false INERT verdict on a script that is sourced by both `run-dag.sh:41`
and `run-with-reverie-dbi-budget.sh:40`. The same filter distorted the `check_outcome` count.

Corrected by excluding the module's **own path** rather than a substring, and re-run. All verdicts in
this document come from the corrected sweep. A reviewer's own tooling is as capable of the
present-but-inert failure as the code it reviews; the only reason this was caught is that a `0` on a
script that obviously must be invoked looked wrong enough to re-check.

---

## Recommendations

1. **Bracket `classify_check` L257** — two cases (`in_progress` + `success` → NO_RESULT,
   `queued` + `failure` → NO_RESULT). Cheapest real protection in this slice.
2. **Bracket the 5 verdict-changing `attribute()` clauses**, starting with the whole `SHAPE_NONZERO`
   branch, which no test drives.
3. **Add a ratchet for artifact 1** — assert `shallow` is absent from both `.gitmodules` files in the
   portability lint, so the fix cannot silently regress.
4. Artifacts 2, 3, 4, 6, 8, 9, 10 need either a locatable guard or an explicit "documentation only,
   nothing to enforce" disposition. Six unreviewed of eleven is the honest state of this slice.

## Reproduction

```
python3 scratch/slice5/mut.py          # mutation brackets (34 clauses across 3 functions)
python3 -m pytest ci-hub/health/ ci-hub/attribution/ ci-hub/tests/test_check_outcome.py -q   # 154 passed
# denominator + flip analysis over the real 1,572-check corpus: see §Artifact 5
```
