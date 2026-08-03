# `safe-ci-dag-runner` usage audit — 2026-08-03

**Task:** `safe-ci-dag-runner-usage-audit`
**Scope:** `rrnewton/dev-hermit` and `rrnewton/hermit`
**Source binding:** dev-hermit
[`dea09de33db4618ba212425ff8b735e0fc6400b8`](https://github.com/rrnewton/dev-hermit/tree/dea09de33db4618ba212425ff8b735e0fc6400b8),
Hermit
[`baf1a7b7f3037b13d3bad2cf99701a83ea2739ad`](https://github.com/rrnewton/hermit/tree/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad).

## Executive answer

The runner is genuinely used, but exact-string presence is a poor proxy for
execution:

- **Local full/portable/privileged validation genuinely uses it as an
  ORCHESTRATOR.** `validate.sh` calls `ci/run-dag.sh`, which executes
  `safe-ci-dag-runner run` over the real lane DAG. The claim that full validate
  has only a leaf parser check is false at the audited SHA.
- **The authoritative GitHub portable workflow does not execute it at all.**
  Five steps merely initialize `agent-utils`; `ci/run-node.sh` extracts commands
  with `jq` and runs them directly with `bash`. GitHub Actions is the outer
  scheduler.
- **GitHub privileged CI and the manual DAG workflow do execute it as an
  ORCHESTRATOR.** Privileged jobs force the Python implementation and retain
  performance CSVs, but never enable `--cgroups`.
- **The only production-quality leaf-box use in dev-hermit is `debug/multisect`.**
  Every repetition uses native `box` with memory, core, wall-time, and profiling
  parameters.
- **No normal Hermit validation/CI path enables hard runner CPU/memory
  enforcement.** `-j 2` is scheduler width, not a CPU quota. The optional
  `--max-mem` in the manual workflow chooses a width; it is not `MemoryMax`.
  All 54 current Hermit DAG nodes have wall timeouts and hard-memory hints, but
  no workflow passes `--cgroups`, and all 54 have no `cpu_timeout`.

The true `test-architecture-epic` scope is therefore not “replace one 3,893-line
serial script.” It is:

1. widen and instrument the DAG paths that already exist;
2. give GitHub portable shards runner leaf/sub-DAG semantics without undoing
   their cross-runner fan-out;
3. migrate the remaining quick/super/compat/demo execution into declared DAGs;
4. leave policy-only merge-gate and analysis-only tools outside the runner; and
5. preserve the two good specialized patterns (multisect boxes and the guarded
   stress singleton DAG).

## Method and role vocabulary

The literal inventory was produced exactly as requested:

```bash
git grep -n -I -e 'safe-ci-dag-runner'
git -C hermit grep -n -I -e 'safe-ci-dag-runner'
```

Result: **86 matching lines in 24 dev-hermit files** and **22 matching lines in
9 Hermit files**. Each executable hit was then followed through wrappers to the
actual `exec`/`subprocess`/API call. “Initialize submodule,” comments, docs, and
manifest string assertions are not counted as executions.

Roles used below:

- **ORCHESTRATOR:** the runner receives a DAG and schedules graph nodes.
- **LEAF BOX:** the runner contains one command; another program owns scheduling.
- **LEAF ASSERTION/GENERATOR:** parses, validates, or emits DAG data but does not
  execute the runner.
- **OBSERVABILITY:** consumes runner output/profiles but does not execute it.
- **NON-CALLSITE:** documentation or historical evidence only.

## Exhaustive dev-hermit exact-hit catalog

Every literal hit is accounted for. Line lists are the literal grep line
numbers at the bound parent SHA.

| File and exact hit lines | Context and role | Limits at this site |
|---|---|---|
| [`ai_docs/ci-timeout-headroom-and-load-relative-analysis_20260801.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ai_docs/ci-timeout-headroom-and-load-relative-analysis_20260801.md#L30) — 30, 138 | Historical timeout analysis; **NON-CALLSITE**. | N/A. |
| [`ai_docs/dag-profiling-coverage-gap-rootcause-and-design_20260803.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ai_docs/dag-profiling-coverage-gap-rootcause-and-design_20260803.md#L5) — 5, 20, 44, 52, 59, 72, 90, 93, 95, 100, 110, 113, 184, 204, 236 | Profiling design/history; **NON-CALLSITE**. Some statements describe a different runner revision and are not used as current-source evidence here. | N/A. |
| [`ai_docs/safe-ci-dag-runner-superconsole-live-progress-design_20260802.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ai_docs/safe-ci-dag-runner-superconsole-live-progress-design_20260802.md#L1) — 1, 3, 16, 18, 35, 67, 168, 172, 231, 321, 366, 367 | UI design; **NON-CALLSITE**. | N/A. |
| [`ai_docs/transient/2026-08-02-ci-recovery-process-adversarial-review.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ai_docs/transient/2026-08-02-ci-recovery-process-adversarial-review.md#L91) — 91 | Availability observation; **NON-CALLSITE**. | N/A. |
| [`ai_docs/validate-run-global-visibility-20260803.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ai_docs/validate-run-global-visibility-20260803.md#L5) — 5, 21, 59, 60, 68, 114, 117 | Visibility/profiling report; **NON-CALLSITE**. | N/A. |
| [`benchmarks/ci-dag-portable_20260802/README.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/benchmarks/ci-dag-portable_20260802/README.md#L8) — 8, 46 | Historical result from a `run-dag.sh portable --max-mem 14G` run; the checked-in analyzer only reads CSV/DAG data. **NON-CALLSITE**. | Historical width sizing, not hard memory enforcement. |
| [`ci-hub/README.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ci-hub/README.md#L71) — 71 | Ownership documentation; **NON-CALLSITE**. | N/A. |
| [`ci-hub/bin/agent-tool`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ci-hub/bin/agent-tool#L11) — 11, 13 | Generic pinned-tool adapter. It materializes parent `agent-utils` and at [line 71](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ci-hub/bin/agent-tool#L71) runs its Python entrypoint with caller arguments. **ROLE DEFERRED TO CALLER**; no tracked caller selects the runner today. | None imposed by adapter. |
| [`ci-hub/validate/aggregate.py`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/ci-hub/validate/aggregate.py#L17) — 17, 18, 88, 90, 245, 255, 257, 291, 408, 413 | Discovers and joins retained profile CSVs; **OBSERVABILITY**, not execution. | N/A. |
| [`compat-envelope/README.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/compat-envelope/README.md#L38) — 38, 52, 178 | Describes regression versus expansion modes; **NON-CALLSITE**. | Documents intended per-cell budgets. |
| [`compat-envelope/collect-envelope.rs`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/compat-envelope/collect-envelope.rs#L19) — 19 | Comment explicitly says expansion here runs serially; **NON-CALLSITE**. | None. |
| [`compat-envelope/expansion-dag.rs`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/compat-envelope/expansion-dag.rs#L3) — 3, 12, 64, 321, 323, 579 | Emits a DAG and prints a manual command; it explicitly does not run it. **LEAF GENERATOR**. | Emitted nodes have wall and hard-memory hints; printed command recommends `--cgroups --max-mem`, but nothing automatically executes it. |
| [`compat-envelope/validate-envelope.sh`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/compat-envelope/validate-envelope.sh#L10) — 10 | Comment says the expansion runner is separate; regression mode directly calls collectors. **NON-CALLSITE**. | None. |
| [`debug/MULTISECT.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/debug/MULTISECT.md#L11) — 11, 22 | Operator documentation for the executable below; **NON-CALLSITE**. | Documents boxed execution. |
| [`debug/multisect`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/debug/multisect#L6) — 6, 644, 645 | Real **LEAF BOX** execution. The actual argv at [lines 301-315](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/debug/multisect#L301-L315) uses native `box` for each repetition while Python schedules commits. | **Yes:** defaults `--mem 6G`, `--cores 1`, `--timeout 300`, and per-repetition `--perf-dir`. |
| [`experiments/ci-cpu-time-timeout-load-robustness_20260801/README.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/ci-cpu-time-timeout-load-robustness_20260801/README.md#L1) — 1, 9, 54, 60 | Experiment documentation; **NON-CALLSITE**. | Documents CPU-time experiment. |
| [`experiments/ci-cpu-time-timeout-load-robustness_20260801/metadata.json`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/ci-cpu-time-timeout-load-robustness_20260801/metadata.json#L2) — 2, 11 | Frozen experiment metadata; **NON-CALLSITE**. | N/A. |
| [`experiments/ci-cpu-time-timeout-load-robustness_20260801/run.sh`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/ci-cpu-time-timeout-load-robustness_20260801/run.sh#L2) — 2, 9, 32 | Real experimental **ORCHESTRATOR**. At [lines 64-70](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/ci-cpu-time-timeout-load-robustness_20260801/run.sh#L64-L70) it runs a victim plus load nodes as a DAG. | CPU affinity is one core; `jobs=N+1`; victim gets wall and `cpu_timeout`; `--no-profile`; no memory/cgroup flag. |
| [`experiments/cpu-time-timeout-manifest-node_20260803/README.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/cpu-time-timeout-manifest-node_20260803/README.md#L4) — 4 | Experiment report; **NON-CALLSITE**. | N/A. |
| [`experiments/stress-test-under-load_20260731/README.md`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/stress-test-under-load_20260731/README.md#L19) — 19, 32 | Guardrail documentation; **NON-CALLSITE**. | Documents verified limits/profiles. |
| [`experiments/stress-test-under-load_20260731/guarded_run.py`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/stress-test-under-load_20260731/guarded_run.py#L5) — 5, 17 | Real programmatic singleton-DAG **ORCHESTRATOR** via `run_dag` at [line 139](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/stress-test-under-load_20260731/guarded_run.py#L139). | **Yes:** verified outer `MemoryMax`, per-step cgroups, perf CSVs, wall timeout; CPU quota intentionally omitted for a load test. |
| [`experiments/stress-test-under-load_20260731/harness.py`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/stress-test-under-load_20260731/harness.py#L33) — 33 | Comment describing the enclosing singleton; **NON-CALLSITE**. | N/A. |
| [`experiments/stress-test-under-load_20260731/metadata.json`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/stress-test-under-load_20260731/metadata.json#L28) — 28 | Frozen metadata; **NON-CALLSITE**. | N/A. |
| [`experiments/stress-test-under-load_20260731/run.sh`](https://github.com/rrnewton/dev-hermit/blob/dea09de33db4618ba212425ff8b735e0fc6400b8/experiments/stress-test-under-load_20260731/run.sh#L4) — 4, 83 | Launcher for `guarded_run.py`; **ORCHESTRATOR PATH**. | Passes profile-specific memory cap, perf directory, and step wall timeout. |

## Exhaustive Hermit exact-hit catalog

| File and exact hit lines | Context and role | Limits at this site |
|---|---|---|
| [`.claude/skills/ci-debugging.md`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/.claude/skills/ci-debugging.md#L39) — 39 | Skill reference; **NON-CALLSITE**. | N/A. |
| [`.github/workflows/ci-dag.yml`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/.github/workflows/ci-dag.yml#L37) — 37 | Exact hit is submodule initialization, but jobs invoke `run-dag.sh` at [64](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/.github/workflows/ci-dag.yml#L64) and [90](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/.github/workflows/ci-dag.yml#L90): full portable/privileged **ORCHESTRATORS**, manual only. | Portable optional `--max-mem` width sizing; privileged `-j 2`; no `--cgroups`, no perf directory, no CPU quota. |
| [`.github/workflows/ci-portable.yml`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/.github/workflows/ci-portable.yml#L252) — 252, 382, 449, 512, 557 | All five hits are init-only labels. Actual commands use `run-node.sh` and `test_harness.sh`; **NO RUNNER PROCESS**. | DAG memory/CPU/time limits are not applied. Only GitHub job and harness-specific limits remain. |
| [`.github/workflows/ci-privileged.yml`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/.github/workflows/ci-privileged.yml#L82) — 82 | Forced-Python privileged DAG **ORCHESTRATOR**. | `-j 2`, `--perf-dir`, outer 270s wall timeout; no `--cgroups`, max-memory, or CPU quota. |
| [`.github/workflows/validation-levels.yml`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/.github/workflows/validation-levels.yml#L129) — 129 | `full` forced-Python privileged DAG **ORCHESTRATOR**. `quick` also reaches the portable orchestrator indirectly through `validate.sh --portable-only`; weekly `super` does not. | Direct full call: `-j 2`, perf directory, no `--cgroups`/hard limits/CPU quota. |
| [`ci/dag/README.md`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/ci/dag/README.md#L1) — 1, 4 | Architecture documentation; **NON-CALLSITE**. | N/A. |
| [`ci/run-dag.sh`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/ci/run-dag.sh#L8) — 8, 18, 73, 74, 77, 78, 81, 82, 89 | Canonical **ORCHESTRATOR ADAPTER**. Resolver prefers a locally built Rust binary, then tracked Python; [line 103](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/ci/run-dag.sh#L103) executes `run --dag`. | Forwards caller flags only; imposes no resource policy itself. |
| [`ci/run-node.sh`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/ci/run-node.sh#L14) — 14 | Comment explains why the runner is bypassed. It extracts commands with `jq` and executes `bash` at [50-60](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/ci/run-node.sh#L50-L60). **LEAF DIRECT EXECUTOR, not runner.** | None of the DAG runner limits/profiling apply. |
| [`ci/test_harness.sh`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/ci/test_harness.sh#L350) — 350 | Literal-string correspondence check proving the privileged workflow contains the expected command; **LEAF ASSERTION**, not execution. The same function separately asserts that validate delegates to `run-dag.sh`. | N/A. |

## Actual execution paths and limits

### Local `./validate.sh`

The exact string is absent, but the process is not. At
[`validate.sh:3379-3386`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/validate.sh#L3379-L3386),
`run_ci_manifest_lane` performs one leaf manifest validation and then invokes
`run-dag.sh`. `full` calls that bridge for both lanes at
[`3605-3607`](https://github.com/rrnewton/hermit/blob/baf1a7b7f3037b13d3bad2cf99701a83ea2739ad/validate.sh#L3605-L3607).
This is full-DAG **ORCHESTRATION**, not a leaf check.

Current policy is `-j ${CI_DAG_JOBS:-2}` with no `--max-mem`, `--perf-dir`, or
`--cgroups`. On this host the resolver selects an untracked locally-built Rust
binary; clean checkouts select the tracked Python entrypoint. The pinned Rust
source explicitly warns that cgroups and perf logging are unimplemented; the
Python runner supports both only when flags are supplied.

The owner’s green ledger run for `a034f39c` is direct evidence: 499s total,
455s in the portable DAG, 26s privileged, user/real `789.095/499 = 1.58`, and
(user+sys)/real `1108.6/499 = 2.22`. It has five top-level gates, not 36
executed serially. The remaining serial `run_check` inventory belongs mostly to
quick/super/compat profiles.

### GitHub portable/CI

The authoritative workflow deliberately uses GitHub jobs as its scheduler. It
invokes `run-node.sh` for preflight/build/test shards and invokes
`test_harness.sh` directly for E2E cells. The stable `regular` job only reduces
job conclusions. It never calls `validate.sh`, `run-dag.sh`, or a runner binary.

This already fixed the former 32-minute single-runner serial lane by building
once and distributing shards across hosted runners. Therefore the correct
change is **not** to collapse it back into one runner process. Each shard should
use a runner subset/leaf-DAG entrypoint so manifest wall/CPU/memory policy,
boxing, and profiling apply inside the existing cross-runner graph.

### Demo hot path and merge gate

`demo-hot-path.yml` runs one 21,000-second wall-bounded
`scripts/super-validate.sh --demos-only`; parent `demos/run-all.sh` iterates
eight `make` targets serially. There is per-demo wall duration in a TSV, but no
CPU, peak-memory, cgroup, or structured runner result.

`merge-gate.yml` runs no product tests. It queries exact-SHA workflow/job state
and policy labels. It should remain a small policy reducer; putting the runner
there would duplicate product CI and lengthen the landing gate.

### Nightly/stress

The parent nightly job calls `scripts/super-validate.sh` without a scope. That
script runs Hermit `validate.sh super` and only after it finishes runs all eight
demos. The super profile is direct `run_check`/Cargo/test code, not a DAG; the
nightly job has a 720-minute outer timeout. Thus the longest path has neither
graph scheduling nor per-node CPU/memory/profile data.

The experimental load guard is the counterexample: `guarded_run.py` uses a
singleton DAG around its own worker pool, verifies outer `MemoryMax`, enables
per-step cgroups and CSV metrics, and refuses to run if containment is absent.

### Benchmarks and compatibility expansion

The checked-in portable-DAG benchmark is analysis of a historical profile; its
Rust script does not launch work and should remain a pure analyzer. Future
benchmark producers should record the exact runner version/args and persist
profiles in ci-hub history.

Compatibility regression runs serial collectors. `expansion-dag.rs` correctly
generates a budgeted per-cell DAG but only prints a manual command. There is no
durable launcher/watcher, so the safest expansion architecture is aspirational
until an operator manually executes it.

### Dev-hermit tooling

- `debug/multisect` is correctly a **LEAF BOX** user: Python owns adaptive
  scheduling; the runner owns per-repetition containment and metrics.
- The guarded stress experiment is correctly a programmatic
  **ORCHESTRATOR**, albeit for a singleton outer node.
- `ci-hub/validate/aggregate.py` is an observability consumer only.
- `ci-hub/bin/agent-tool safe-ci-dag-runner` is a generic dormant adapter; no
  tracked dev-hermit caller uses it, and it supplies no default limit/profile
  policy.

## Ranked gap analysis

| Rank | Path | What happens now | Concrete cost | Required architecture |
|---:|---|---|---|---|
| **1** | GitHub portable (every PR/main) | Cross-runner matrix is parallel, but every `run-node.sh` shard executes raw `bash`; DAG timeouts, CPU budgets, hard-memory hints, cgroups, and per-node profiles are bypassed. | Largest frequency and compute spend; failures have no uniform resource classification; node timeout/memory policy is unenforceable; profiling is lost even though five jobs initialize the runner submodule. | Keep the GitHub graph, replace raw `run-node.sh` execution with runner `run --only` or generated subset DAGs; force an implementation with perf+cgroups; upload/join profiles by run ID and SHA. |
| **2** | Local full/portable/privileged validate | Already full-DAG runner orchestration, but fixed `-j2`, no memory sizing, no perf sink, no cgroups, and no CPU-time budgets. | Owner green run spends 455/499s (91%) in portable; warm concurrency is only 1.58 user/real. Profiles/resource enforcement are absent. | Derive width from CPU+memory history; persist per-node profiles to ci-hub; enable verified containment; populate CPU budgets; then safely widen `hermit_guest` and prune false dependencies as the existing speed audit proposes. |
| **3** | Nightly super + demos | Super gates are direct serial shell/Cargo; then eight demos run serially. Only outer 720m/job and 21,000s/demo-sweep wall backstops exist. | Longest single path; no CPU/peak-memory profile; one hang can consume hours; independent safe work cannot overlap. | Model super gates and demos as DAG nodes with explicit shared-build, PMU, KVM, QEMU, and scratch resources. Use CPU-time plus generous wall backstops and retain profiles/artifacts per node. |
| **4** | Narrow local profiles (`quick`, `super`, `*-compat-only`, R/R, envelope) | Direct `run_check`, Cargo, and product command sequences outside the DAG. | These frequent developer paths account for most unprofiled validation shapes; runner CPU-time/cgroup policy cannot reach them. | Incrementally add typed manifest nodes/sub-DAGs. Keep `validate.sh` as profile selection only; determinism verdict remains in `hermit --verify`. |
| **5** | Compatibility expansion/benchmarks | Expansion emits but does not launch a DAG; benchmark analyzer consumes old CSVs. | Expensive sweeps are operator-dependent and can be forgotten; no standard history join or remediation. | Add a durable ci-hub launch contract around generated DAGs, with mandatory limits, run ID, retained profiles, and completion obligation. Keep analyzers runner-free. |
| **6** | Privileged GitHub paths | Runner orchestration and perf CSVs exist, but no `--cgroups`; `-j2` is only width. | Hard-memory hints are not enforced; CPU quotas/budgets absent; wall-only timeout behavior remains load-sensitive. | Force Python/current implementation with verified cgroups or land parity in Rust; add CPU budgets; ingest uploaded perf data. |
| **No gap** | Merge gate | Exact-SHA/status/policy reducer only. | Adding tests here would duplicate CI and slow landing. | Keep runner-free; consume authoritative CI/obligation state. |
| **Good pattern** | Multisect and guarded stress | Leaf boxes or singleton DAGs with explicit ownership and resource controls. | No architectural gap in role selection. | Reuse these limit/profile contracts; do not force adaptive scheduling into a monolithic DAG. |

## Resource-control conclusions

1. **`-j` is not a CPU limit.** It bounds concurrently running nodes.
2. **`--max-mem` is not `MemoryMax`.** It selects a schedulable width from
   memory hints. Only cgroup boxing enforces a hard memory ceiling.
3. **Hermit’s normal paths never pass `--cgroups`.** Consequently the
   `hard_mem_max_bytes` values in all 47 portable and 7 privileged nodes are not
   hard enforcement in those paths.
4. **All 54 nodes have wall timeouts; zero have `cpu_timeout`.** GitHub portable
   raw shards ignore even those node wall timeouts because `run-node.sh` invokes
   `bash` directly.
5. **Implementation selection is nondeterministic locally.** `run-dag.sh`
   prefers an untracked built Rust binary when present. That pinned Rust source
   has scheduling but no cgroup/perf implementation; clean CI usually selects
   Python. Callers must select a capability-checked implementation explicitly.

## `test-architecture-epic`: corrected scope and sequencing

The epic’s current headline premise (“zero references; 36 serial full gates”) is
already corrected in its own task notes and should be corrected in the task
description when the coordinator next edits it. The source-backed work is:

1. **Unify execution semantics, not necessarily process topology.** Local full
   validation can remain one runner DAG; GitHub portable should remain a
   multi-runner matrix but use runner subset semantics within each job.
2. **Make profiling/limits mandatory capabilities.** Pick one implementation;
   refuse a requested cgroup/perf mode if unsupported; store exact version and
   arguments.
3. **Migrate only true non-DAG work.** Quick/super/compat/R/R/demo paths need
   declared dependencies and typed execution. Full portable/privileged do not
   need a big-bang orchestration rewrite.
4. **Separate product correctness from scheduling.** Test definitions live in
   manifests, execution in a typed harness, determinism in `hermit --verify`,
   orchestration/metrics/limits in the runner, and health/history in ci-hub.
5. **Do not runner-wrap policy or analysis.** Merge-gate and offline analyzers
   are intentionally runner-free.

Recommended order: GitHub portable subset runner + profile ingestion; local
width/profile/limit policy; privileged hard limits/CPU budgets; then incremental
super/compat/demo migration. Each stage must report wall, user, system,
user/real, critical path, profile coverage, and timeout/resource enforcement at
the exact tested SHA.

## Reproduction checklist

```bash
# Literal catalog.
git grep -n -I -e 'safe-ci-dag-runner'
git -C hermit grep -n -I -e 'safe-ci-dag-runner'

# Hidden indirect executions and direct bypasses.
git -C hermit grep -n -E 'run-dag[.]sh|run-node[.]sh|validate[.]sh|test_harness[.]sh' \
  -- validate.sh ci .github/workflows

# Resource flags at workflow call sites.
git -C hermit grep -n -E -- \
  '--cgroups|--max-mem|--perf-dir|-j 2|CPUQuota|MemoryMax|cpu[.]max|memory[.]max' \
  -- .github/workflows ci validate.sh

# Manifest coverage.
jq '[.steps[] | select((.hint.hard_mem_max_bytes // 0) > 0)] | length' \
  hermit/ci/dag/{portable,privileged}.json
jq '[.steps[] | select((.cpu_timeout // 0) > 0)] | length' \
  hermit/ci/dag/{portable,privileged}.json
```
