# The cpu/wall Kill Signature: separating a REAL red from a flake, from the failing run's own logs

**Date:** 2026-08-05
**Task:** `flaky-failure-attribution-capability`
**Status:** implemented locally; committed to the parent, **not pushed** (egress 403)
**Scope:** local design + implementation + verification. No `validate-run`, no egress, no product change.

## The question this answers

A recorded `FAILED` that is actually a flake **permanently condemns a healthy PR**. So for
every failing run the ledger must answer: *is this red REAL, or is it noise?* The expensive
answer is a re-run. This note is about the cheap answer that was already sitting in the data
and was not being read.

The prior capability (landed `d83a34b`) classifies a failing run into
`INFRASTRUCTURE` / `HERMIT_NONDETERMINISM` / `ENVIRONMENT` from a preserved bundle, using
five signals: failure shape, host pressure, divergence point, external reads, and a low-load
control re-run. Its weakest spot was the **hang**: with no divergence to point at, a hang fell
through to `INDETERMINATE` unless you paid for K extra runs at low load. Hangs are the single
largest unattributed bucket, and the low-load control is exactly the thing you cannot afford
when triaging a backlog.

## The finding: the discriminator existed, in the wrong place

The cpu/wall ratio at a kill separates two opposite causes:

- **LIVELOCK** — CPU burned at ~a full core for the whole budget. A product spin. Retry can
  **never** clear it. The red is **REAL**.
- **CONTENTION** — low CPU against high wall. The step was *waiting*. Environmental. A
  re-dispatch works. The red is a **FLAKE**.

This test was already implemented — in `ci-hub/history/query.py` (`kill-taxonomy`), over the
ledger's `step_profiles` population. Meanwhile `ci-hub/attribution/attribution.py`, the tool
whose entire job is attributing one failing run, **had no CPU signal at all**. Its producers
recorded `wall_s` and host-wide CPU *pressure* (PSI), but never the subject's own CPU seconds.

So: one physical fact, two consumers, one of them structurally blind. Plus a standing drift
risk — the 2026-08-04 coherence note on this task had already flagged duplicated
infra-signature tables as a live hazard.

Measured signature, from the local store (`query.py kill-taxonomy`, 27 kills):

| node | kill | wall (s) | cpu (s) | cpu/wall | verdict |
|---|---|---|---|---|---|
| `test.detcore_misc` | wall_timeout | 600.013 | 607.785 | **1.013** | livelock |
| `test.liteinst_strict` | wall_timeout | 900.013 | 901.205 | **1.001** | livelock |
| `e2e.metadata` | wall_timeout | 60.739 | 61.595 | **1.014** | livelock |
| `test.strict_compat` | **oom** | 64.462 | 8221.18 | **127.5** | oom |

## The two gates that make it correct (and one asymmetry that makes it honest)

**Gate 1 — it must be a kill.** A ratio of 0.9 on a run that failed in 3s means the work was
CPU-bound, nothing more. The signature is only defined when a budget actually fired.

**Gate 2 — OOM must be excluded *before* the ratio test.** This is not a nicety; it is the
difference between a working classifier and a broken one. OOM-killed rows carry ratios up to
**127.5**, because they are massively parallel builds hitting a memory ceiling. Applying
`ratio >= 0.8` without excluding OOM first would label **17 of the 27 kills** in the live store
as livelocks — i.e. would declare seventeen flaky infra events to be real product failures and
condemn their commits. OOM is a *memory* kill, orthogonal to the spin question.

**The asymmetry — the signature is decisive in ONE direction only.** A high ratio means the
subject was definitely computing, and only the product can burn a core for a whole budget. A
**low** ratio only means *not spinning*. It does **not** imply infrastructure: a starved
process and a futex/deadlock wedge both sit at `cpu ≈ 0`, and they are opposite causes
(infra vs product). This was caught by an existing test during implementation — a first cut
returned `INFRASTRUCTURE` with high confidence for any wait-bound hang, which would have
misfiled every hermit deadlock as a runner problem.

So the wiring is deliberately lopsided:

| kill verdict | attribution | confidence | why |
|---|---|---|---|
| `livelock` | `HERMIT_NONDETERMINISM` | high | spin to budget is product-side; retry-futile ⇒ **REAL red** |
| `oom` | `INFRASTRUCTURE` | medium | memory ceiling, not a determinism defect |
| `contention` | *(does not decide)* | — | rules out livelock; falls through to the low-load control |
| `ambiguous` (0.3–0.8) | *(does not decide)* | — | the ratio does not carry the answer |
| `unknown` | *(does not decide)* | — | no kill, or no cpu recorded |

`retry_futile` is reported as a first-class tri-state: `True` (real), `False` (flake),
`None` (**not established**). `None` must never be read as a confirmed real failure — that
collapse is the false-red bug in miniature.

## What was built

**One shared table** — `ci-hub/lib/kill_signature.py`. Thresholds (`LIVELOCK_RATIO = 0.8`,
`CONTENTION_RATIO = 0.3`), `cpu_wall_ratio`, `classify_kill`, `retry_futile`, `explain`.
`ci-hub/history/query.py` was converted to import it instead of holding its own copy, so the
two consumers can no longer drift. The conversion is behaviour-preserving: `kill-taxonomy`
output is **byte-identical** before and after.

**The producers now record CPU**, which they previously did not — without this the classifier
could never fire in production:
- `attribution.py capture_run()` — `resource.getrusage(RUSAGE_CHILDREN)` delta.
- `capture-run.sh` — the bash `times` builtin. **Gotcha, measured:** `times` must be captured
  with a *redirect*, never command substitution. `x=$(times)` forks, and fork zeroes
  `RUSAGE_CHILDREN`, so the children line reads `0m0.000s` every time — a silently-always-zero
  CPU, worse than no field at all. Verified on this box: redirect → `0m1.033s`,
  `$(times)` → `0m0.000s`, same child.

Bundle `schema_version` 1 → 2 (`cpu_s`, `cpu_s_is_lower_bound`, `oom`). On a timed-out run
`cpu_s` is a **lower bound** — both mechanisms count only *reaped* children, so descendants
killed alongside the subject are missing. The bundle records that rather than implying
precision it does not have. Schema-1 bundles keep `cpu_s = None` and degrade to the previous
behaviour; they never receive a fabricated ratio.

## Verification (all local)

- `attribution.py selftest` → **PASS, 39 tests** (27 pre-existing + 12 new).
- `query.py kill-taxonomy` output **byte-identical** across the dedupe (`diff` clean).
- Negative bracket: the real OOM row (64.462 s / 8221.18 s, ratio 127.5) classifies `oom`,
  **not** `livelock`, and its `retry_futile` is `None`, not `True`.
- Positive bracket, end-to-end through the bash producer:
  - spin killed at a 2 s budget → wall 2.005 / cpu 2.010 → ratio **1.002** →
    `HERMIT_NONDETERMINISM` high, "this red is REAL".
  - `sleep 30` killed at the same 2 s budget → wall 2.005 / cpu 0.011 → ratio **0.005** →
    `INDETERMINATE`, explicitly naming that starvation and a wedge are indistinguishable here.
  - Two runs that are identical as "1/N flaky, timed out at 2 s" are now separated by their
    own logs.
- Boundary tests pin 0.80 (inclusive) → livelock, 0.79 → ambiguous, 0.30 → ambiguous,
  0.29 → contention.

## Limits, stated

1. **`cpu_s` on a killed run is a lower bound** (unreaped descendants). It biases toward
   *under*-reporting CPU, i.e. toward missing a livelock, not toward inventing one. Safe
   direction, but it means a multi-process livelock can read as ambiguous.
2. **Thresholds are calibrated on runner-native step profiles from this box.** Ratios from
   different environments must not be pooled — `query.py` already records provenance per
   record for exactly this reason.
3. **`livelock → HERMIT_NONDETERMINISM` is a taxonomy fit, not a literal claim.** A spin to
   budget is a product-side defect and retry-futile; calling it *nondeterminism* is the
   closest bucket in the existing three-way vocabulary, not an assertion that the spin is
   nondeterministic. The reason string says "spin", so the verdict is auditable.
4. **The GitHub-jobs population carries no CPU field** and is therefore structurally
   unclassifiable by this signature — it must not be given a ratio-derived verdict.

## What remains (not done here)

- **The ledger consumer is not wired.** This closes the Layer-2 (run-bundle) gap. Making a
  recorded `FAILED` carry `retry_futile` so a contention kill cannot condemn a PR is a
  separate change on the ledger/producer side, and per the existing task notes is blocked on
  the validate.sh producer emitting `concurrent_validates` and `dag_jobs`.
- **Three-layer unification** (the 2026-08-04 coherence finding) is now partly addressed: the
  kill-signature table is shared. `failure_evidence._INFRA_SIGNATURES` is still a second,
  independent infra-signature source and remains a drift risk.
- **Not pushed.** Egress is 403 (`api.github.com` not allowlisted for `agent_id:
  agent:claude_code`), so the parent commit is local-only and ancestry is unconfirmed.
