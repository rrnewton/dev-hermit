# Two-level validate parallelism: INNER width per DAG node class (the READ half)

Task: `parallelism-is-two-level-outer-dag-times-inner-step-width` (P0, owner-asked-twice).
Date: 2026-08-04. Author: hermit-220 (opus-4.8). **Method: STRUCTURAL** — read from
`hermit/ci/dag/{portable,privileged}.json`, `validate.sh`, `.config/nextest.toml`, and the
safe-ci-dag-runner source. Load-independent; no benchmark run (the benchmark half — fork-join
profiling + the same-core ptrace verdict — is with the background agents and lands separately).
Every figure is labelled **OUTER** (concurrent DAG nodes) or **INNER** (parallelism inside one node).

## The 54 nodes = portable.json (47) + privileged.json (7)
The owner's "of 54" reconciles here: the prior 08:42 note counted only portable's 47 and missed
the 7 privileged nodes. A full validate is both lanes.

### Counts by class (of 54)
| class | count | what |
|---|---|---|
| COMPILE-bearing (cargo build / clippy / doc / `--no-run`) | 8 | build.workspace, build.dbi_release, build.sabre_release, build.flaky_harnesses(`--no-run`), build.privileged_tests(`--no-run`), lint.clippy, doc.rustdoc, doc.doctests(compiles then runs) |
| TEST-execution | 22 | the `test.*` nodes + doc.doctests + cpuid.faulting + pmu.preemption |
| E2E-GUEST-run (prebuilt, `test_harness.sh run`) | 15 | portable e2e.manifest_* (13) + privileged (2) |
| SCRIPT / SETUP / manifest-gen (negligible cores) | ~9 | check.*×3, setup.nextest, lint.rustfmt, build.liteinst_runtime_release (native make), e2e.metadata×2, build.manifest_guests×2 |

(build.liteinst_runtime_release is a native make build — INNER = its own make `-j`, not flag-visible this pass.)

## Q1 — TEST steps: what sets INNER width, per node
| node | INNER | how set | note |
|---|---|---|---|
| test.regular_crates | **~nproc (316)** | nextest `test-threads="num-cpus"` (`.config/nextest.toml [profile.default]`; `ci` profile does NOT override it) | EMBARRASSINGLY PARALLEL; excludes hermit+detcore; **uncapped, already maxed** |
| test.detcore_unit | **~nproc (316)** | libtest default (no `--test-threads`) | embarrassingly parallel, no hermit_guest token → runs wide **and** concurrent; uncapped |
| doc.doctests | **~nproc (316)** | libtest default | parallel |
| test.detcore_parallel | **4** | `--test-threads=4` (explicit) | deliberately tests parallelism |
| test.hermit_unit, detcore_misc, hermit_integration, arbitrary_binaries, cli, liteinst_strict, sabre_examples, hermit_modes, app_strict_verify, command_strict_verify, ignored_syscall_regressions | **1** | `--test-threads=1` (explicit flag) — **11 nodes** | see GUARD below |
| test.rr_suite_contract, cpuid.faulting | **1** | single `--exact` test | one test |
| pmu.preemption | **1** | single C micro-proc (`cc … && ./pmu_skid`) | |
| test.dbi_parity, test.applications_e2e | **~1 (inferred)** | `run_matrix.py` / `run_all.sh` internal | script-driven, not flag |
| test.envelope_levels | **~1** | bash `REPS=20` sequential probes; uses **target/debug/hermit** | serial |
| test.strict_compat | **~1–2** | `validate.sh --portable-strict-compat-only`, per-utility serial loop | **600s TAIL** |

**GUARD on the 11 `--test-threads=1` nodes — the owner's "is the cap a free lever?" answer: NO.**
All 11 carry `resources:{hermit_guest:1}` and the lane sets `resource_caps.hermit_guest = 1`, so they
are **OUTER-serialized to one-at-a-time** regardless of inner threads. Raising `--test-threads` would
not help wall (they still can't run concurrently) and would break correctness: they run a real hermit
guest and the cap defends against PMU-counter contention + guest isolation + determinism
(see memory `pmu-exhaustion-hardfail-not-silent`). It is a **guarded** cap, not a free lever.
The genuinely-embarrassingly-parallel test nodes (regular_crates, detcore_unit, doctests) are **already
at INNER=nproc** — there is no low cap to lift there.

## Q2 — BUILD steps: what `-j` cargo actually gets
- **3 third-party build cells** (build.workspace, build.dbi_release, build.sabre_release) read
  `CARGO_BUILD_JOBS=${THIRD_PARTY_BUILD_JOBS:-$(nproc)}`. `validate.sh` (lines 491–502) exports
  `THIRD_PARTY_BUILD_JOBS = min(host_cpus, VALIDATE_THIRD_PARTY_BUILD_JOBS_CAP=32)`, and
  `host_cpus = getconf _NPROCESSORS_ONLN` = **316 (raw, NOT cgroup-aware)** → these cells get **INNER -j = 32**.
- **Every other compile-bearing node** — lint.clippy, doc.rustdoc, doc.doctests(compile), build.flaky_harnesses,
  build.privileged_tests — sets **no** CARGO_BUILD_JOBS → cargo default = logical CPUs = **INNER -j = 316**.
  (There is no `.cargo/config.toml` in hermit to change this.)
- The safe-ci-dag-runner does **not** inject a per-node cargo `-j` in normal `run` mode: its `--jobs` is
  the **OUTER** step-concurrency knob; the `--jobs=`-template rewrite exists only in the `sweep` subcommand.
- **But INNER build width is crate-DAG-bounded, not `-j`-bounded:** measured fat middle ≈ **27 concurrent
  rustc**, wall floor near j64 (j1 198.6s → j64 34.4s = 5.77×), from memory
  `per-step-j-model-already-exists-memory-is-the-gap`. Both 32 and 316 exceed the ~27 fat middle, so the
  `-j` cap is **nearly wall-neutral** — it bounds peak-RSS/OOM (fewer simultaneous rustc), not throughput.
  Narrow ends (final crate + link) serialize to INNER→1 at each build's tail. So `-j` is **not** the build lever.

## Q3 — THE PRODUCT (OUTER × INNER over time)
From the measured ASAP infinite-core sim (08:42 note, structural / scale-invariant):
- **OUTER**: ceiling **4.24×** (time-avg concurrent nodes; sweep flat at j≥5), peak **29** nodes, makespan 1265s.
- **INNER**: 1 … 316 by class (tables above).
- **PRODUCT = actual core demand**: **time-avg ≈ 35 cores = 11% of 316**; **peak ≈ 159 cores = 50%** in one
  ~3-minute burst (t≈360–540s). Shape is **bimodal**: narrow compile spine (~27 cores) → wide burst
  (compile + e2e + regular_crates, 159) → narrow serial tail (clippy 300s → strict_compat 600s at ~1–4 cores).

**Headline (correcting the OUTER-only error): the machine is NOT ~4× utilised and NOT 316× — it is ≈11%
utilised on average (≈35/316 cores), briefly 50%.** 4.24 was OUTER nodes only; total utilisation is
OUTER×INNER ≈ 35 cores.

## The real lever (not inner `-j`, not the test-threads cap)
The **serial tail** dominates: `test.strict_compat` is 600s (47% of the 1265s critical path) at INNER ~1–2
cores. Parallelising its independent per-utility probes, or splitting it into DAG nodes the way e2e
categories already are, collapses the tail — that is the only change that would make the 316-core box earn
its size. Ties: `prune-artificial-deps-on-validate-critical-path`, memory
`two-level-parallelism-outer-times-inner-and-serial-tail`,
`derive-cargo-build-parallelism-from-speedup-curve-not-a-picked-number` (that task's answer = the build
inner knee: j64 wall-floor / ~27 fat middle, per above).

## Caveats / provenance
build inner ≈27 = MEASURED; test inner = FLAG-READ (this pass, current files); dbi_parity / applications_e2e
/ strict_compat / envelope INNER = INFERRED-from-code (not bench-measured — strict_compat is the one worth
measuring since it is 47% of critical path). No fresh load-sensitive wall measured (not needed; structural).
