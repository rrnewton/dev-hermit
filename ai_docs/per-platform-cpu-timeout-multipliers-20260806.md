# Per-platform CPU-timeout multipliers: mechanism landed, target platform still deaf

**Task:** `per-platform-cpu-timeout-multipliers`
**Date:** 2026-08-06 (measurements 2026-08-05T21:55 – 2026-08-06T00:15 PDT)
**Agent:** `egress-probe2` (opus-5)
**Implementation:** agent-utils branch `codex/cpu-timeout-platform-multiplier` @
`7dc20de8f8b884a0fa8648ff4deb4a5853b83a9f`, worktree `scratch/au-cputo-mult`,
base `570e786` (the currently pinned agent-utils SHA).
**Publication:** NOT pushed. GitHub egress is 403 on CONNECT this session.

---

## 1. The headline

The mechanism the task asks for now exists and is verified end to end. **But the
platform it was designed for — GitHub hosted runners — cannot use it yet, and
cannot even supply the data needed to calibrate it.** Both halves of that
blockage have the *same single root cause*, which is worth stating precisely
because the task's own design notes treat them as two separate problems.

## 2. Root cause: one fact, two consequences

Measured, plant-both-ways, same graph and same box. A one-step DAG declaring
`cpu_timeout: 3` on a step that spins 20 CPU-seconds:

| lane shape | boxing | outcome | CPU actually burned |
| --- | --- | --- | --- |
| default (local) | `cgroup boxing ACTIVE` | **FAIL** `CPU-TIMEOUT >3s cpu` at 4.1 s | ~3 s |
| `GITHUB_ACTIONS=1 CI=1 --allow-cgroup-failure` | `running UNBOXED … no per-step memory/CPU caps` | **PASS**, rc=0 at 20.1 s | ~20 s = **6.7× over budget** |

The chain, source-verified at the current pin:

```
GITHUB_ACTIONS set
  → hermit/ci/run-node.sh:124-126 adds --allow-cgroup-failure
  → cgroup.py:642 / cgroup.rs:264 reexec_in_scope() returns true ("skipped in CI")
  → _resolve_cgroup_manager() returns (None, 0)  = UNBOXED
```

and from that one fact, two consequences:

**(a) Enforcement is dead.** `scheduler.py:436-445` polls
`self.cgroups.cpu_stats(tag)`; its own comment says the guard is *"inert when
cgroup boxing is off (cpu_stats is None)"*.

**(b) Measurement is dead.** `perflog.py:96-104` — the step-profile `user_s` /
`sys_s` columns are `cpu.stat user_usec / system_usec`, *"Captured UNDER cgroup
boxing on Linux and left BLANK when unavailable (an un-boxed run)"*. Confirmed in
the downloaded artifacts: **every** hosted `step_profiles_*.csv` row has
`user_s,sys_s` empty.

So a hosted multiplier is simultaneously **unenforceable** (nothing reads the
scaled budget) and **underivable** (no hosted CPU-second distribution exists to
measure the ratio from). The task's design point 3 — *"the multiplier itself must
be DERIVED and stated… not picked"* — is blocked at the data source, not at the
analysis.

## 3. What already existed (do not rebuild)

`ci-hub/history/query.py:346` already defines `HOSTED_CPU_MULTIPLIER = 2.0`, a
single named constant with a `--hosted-multiplier` override, emitting a
`suggested_cpu_timeout_hosted` column. Its comment is honest and correct:

> This is a CANONICAL CONSTANT, NOT a measured ratio… hosted step_profiles carry
> WALL ONLY… Deriving one from hosted-wall / local-cpu would pair two different
> quantities — the exact proxy-binding error this store exists to avoid.

Verified against live output: every hosted value is exactly `base × 2`
(2591→5182, 402→804, 28→56, 3333→6667). So the "picked, not derived" state is
already labelled as such at the one place it lives. Good.

## 4. The real gap this work closes

The multiplier was applied at **derivation** time, producing a second *column*.
But a DAG step has exactly **one** `cpu_timeout` field, and the runner had **no
multiplier concept at all** (grepping `py/safe_ci_dag_runner` and
`rs/safe-ci-dag-runner/src` for multiplier/platform/scale returns only
`mem_cap_factor` and `outer_mem_safety_factor`).

So a declaration author had to pick one number for both platforms — canonical
(false-kills hosted once boxing lands) or hosted (2× too loose locally, hiding
exactly the core-pinning hangs the budget exists to catch). That is the
"two independently-maintained timeout tables drift" failure mode the task
forbids, in embryo. Design point 2 says the multiplier must be *"applied at
execution time, NOT a second set of hardcoded numbers"* — and it wasn't.

Also worth recording: **0 of 55 DAG nodes** (47 portable + 8 privileged) declare
`cpu_timeout` today. The canonical table the multiplier scales is still empty.

## 5. What was implemented

Both engines, at parity. The graph keeps ONE canonical budget; the platform
scales it at execution:

```
canonical_cpu_timeout(step, default)      -- what the graph declares
scale_cpu_timeout(canonical, multiplier)  -- what this platform enforces
```

Resolution: `--cpu-timeout-multiplier`, else
`$SAFE_CI_DAG_RUNNER_CPU_TIMEOUT_MULTIPLIER` (so a lane sets policy once for its
whole platform), else `1.0`. `$SAFE_CI_DAG_RUNNER_CPU_TIMEOUT_PLATFORM` supplies
the label. The multiplier lives on `DagConfig` as caller/platform policy and is
**never written back into the graph**, so re-serializing cannot leak a
platform-specific number into the canonical table.

Design choices that are load-bearing, not incidental:

* **1.0 is a strict no-op.** Same budgets, same breach strings. A platform that
  never opts in is untouched — so this cannot wedge any existing lane.
* **Scaling can never become an opt-out.** A disabled budget (canonical 0) stays
  disabled; a live budget never rounds down to 0. A sub-unity multiplier
  silently deleting the guard would convert a scaling policy into a hole.
* **A bad multiplier is REFUSED (exit 2), not ignored.** A typo that quietly
  reverted to 1.0 would loosen enforcement invisibly — the precise failure mode
  this mechanism exists to prevent.
* **The breach carries the policy** (design point 4):
  `CPU-TIMEOUT >6s cpu (canonical 3s x2 github-hosted)`. The historical
  `CPU-TIMEOUT >Ns cpu` prefix is preserved for existing grep consumers, and the
  suffix is silent at 1.0.
* **Applied after `apply_plan_to_config`**, so the planner never sees — and
  cannot bake in — a platform-specific number.

### A real cross-language bug the tests caught

Python's `round()` is banker's rounding; Rust's `f64::round()` is
half-away-from-zero. The naive port disagreed by a whole second at every `.5`
tie (`round(4.5)` = 4 in Python, 5 in Rust). Both engines now round **half away
from zero**, and the Rust test asserts the exact tie values the Python suite
asserts (3→5, 7→11, 5→8, 1→2, 9→14 at ×1.5). At a tie the more generous budget
is also the right default for a guard whose purpose is to avoid false-killing a
healthy-but-slow platform.

### A latent scheduler hazard this surfaced

Threading the policy through `StepOutcome.failed` initially missed
`protocols.py`, so a `TypeError` was raised inside a step worker thread. That did
**not** surface as an error — it **hung the scheduler**: the step never reported
and the supervisor waited forever. Two `test_scheduler` tests wedged at a
120-second timeout with no output. An unexpected exception in a worker thread
becoming an indefinite hang rather than a loud failure is worth hardening
separately; it is exactly the "silent kill is indistinguishable from mystery
failure" shape the owner's breach spec objects to.

## 6. Verification

End to end under **real cgroup boxing**, same graph declaring `cpu_timeout: 3`:

| engine | multiplier | killed at | breach message |
| --- | --- | --- | --- |
| rust | none | 4.1 s | `CPU-TIMEOUT >3s cpu` |
| rust | ×2 (`github-hosted`) | 6.1 s | `CPU-TIMEOUT >6s cpu (canonical 3s x2 github-hosted)` |
| python | ×1.5 (`github-hosted`) | 5.1 s | `CPU-TIMEOUT >5s cpu (canonical 3s x1.5 github-hosted)` |

The ×1.5 row also confirms the rounding rule end to end (3 × 1.5 = 4.5 → 5).

Refusal bracket — a malformed multiplier must not be silently ignored:

```
SAFE_CI_DAG_RUNNER_CPU_TIMEOUT_MULTIPLIER=nonsense
  python → exit 2, "SAFE_CI_DAG_RUNNER_CPU_TIMEOUT_MULTIPLIER='nonsense' is not a number"
  rust   → exit 2, "SAFE_CI_DAG_RUNNER_CPU_TIMEOUT_MULTIPLIER=\"nonsense\" is not a number"
```

Gates, all green at `7dc20de`:

| gate | result |
| --- | --- |
| `pytest py/tests` (minus synthetic) | **332 passed** |
| `pytest py/tests/test_synthetic_runs.py` (boxed) | **11 passed** |
| new `test_cpu_timeout_multiplier.py` | **24 passed** |
| `cargo test --lib` (incl. 8 new) | **77 passed** |
| `mypy safe_ci_dag_runner` | 21 files, no issues |
| `cargo clippy --all-targets -- -D warnings` | clean |
| `cargo fmt --check` | clean |
| `cross/differential.py --tool safe-ci-dag-runner` | **378 checks / 41 fixtures agree** |

## 7. What this does NOT do, and what has to happen next

**The multiplier is still decorative on GitHub hosted**, exactly as the task
warned. Setting `SAFE_CI_DAG_RUNNER_CPU_TIMEOUT_MULTIPLIER=2` in the portable
lane today changes nothing, because nothing on that lane reads a CPU budget at
all (§2a). The mechanism is a *prerequisite* that is now in place, not a fix for
the hosted lane.

Ordered next steps:

1. **Controller-free per-step CPU measurement** (`wait4` / `getrusage`
   deltas around each step, as a fallback when `cpu.stat` is absent). Note that
   `perflog.py:78-79` *already* computes run-level `user_s`/`sys_s` from
   `RUSAGE_CHILDREN`, so the primitive exists — it is the per-step enrichment
   that is cgroup-only. This is measurement-only, so it cannot wedge a lane, and
   it is what unblocks §2b: it makes hosted CPU-seconds exist, which is the
   precondition `HOSTED_CPU_MULTIPLIER`'s own comment names for replacing the
   picked 2.0 with a measured ratio.
2. **Derive the multiplier** from the resulting paired local/hosted
   distributions, per node, and report it with its sample counts. Only then is
   design point 3 satisfied.
3. **Controller-free per-step CPU enforcement** (`RLIMIT_CPU`, per-process and
   controller-free — the path already prototyped on
   `origin/ci/cpu-time-rlimit-timeout`). This is what makes the multiplier bite
   on hosted. It is a *behavior* change and must follow the measure → declare →
   flip migration order, not lead it.
4. **Populate the canonical table.** 0/55 nodes declare `cpu_timeout`; the
   multiplier scales nothing until they do.

Do NOT set a hosted multiplier in a workflow before step 3 lands: it would look
like protection while providing none, which is worse than a visible gap.

## 8. Residue

* Nothing pushed (egress 403). Branch `codex/cpu-timeout-platform-multiplier` @
  `7dc20de` lives in `scratch/au-cputo-mult`; agent-utils lands direct-to-main
  under a serialized recipe, so this needs the owner's landing path plus a parent
  gitlink bump.
* `cross/differential.py` runs UNBOXED, so it cannot behaviorally cross-check
  enforcement. The multiplier's parity is covered by mirrored unit tests
  (including the rounding ties) and the serialization fixtures, not by a boxed
  differential run — same structural gap recorded for `cpu_timeout` itself.
* No cross-check fixture was added for the multiplier, because it is caller
  policy and deliberately not serialized into the graph; there is nothing in the
  DAG file for a fixture to compare.
* The two engines' malformed-value errors differ cosmetically in quoting
  (`'nonsense'` vs `"nonsense"`, Python repr vs Rust Debug). Same exit code, same
  meaning; worth unifying if a test ever asserts the exact string.
