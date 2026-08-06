# Cargo build parallelism derived from a speedup curve

Date: 2026-08-05

Task: `derive-cargo-build-parallelism-from-speedup-curve-not-a-picked-number`

## Decision

For the measured historical `build.dbi_release` workload on the 316-thread AMD
EPYC 9D85 host, the available curve derives a **provisional reference
throughput knee of
`CARGO_BUILD_JOBS=32`**. It is the last measured width before marginal wall-time
improvement falls below the existing 15% advance threshold:

| jobs | wall (s) | speedup vs j8 | marginal speedup | build status |
| ---: | ---: | ---: | ---: | --- |
| 8 | 175 | 1.00x | — | pass |
| 16 | 132 | 1.33x | 1.33x | pass |
| **32** | **108** | **1.62x** | **1.22x** | **pass** |
| 64 | 104 | 1.68x | **1.04x** | pass |

The arithmetic is `marginal(j) = median_wall(previous_j) /
median_wall(j)`. Advance while `marginal >= 1.15`. The `32 -> 64` step saves
only 4 / 108 seconds (3.7%), so the derived knee is 32, not 64.

This result does **not** authorize a universal bare `32`. The value is a typed
profile:

```text
jobs:        32
machine:     devbig014; AMD EPYC 9D85; 158 physical cores / 316 threads
workload:    clean historical build.dbi_release; Cargo and third-party width coupled
curve:       results.csv sha256:e2543ff81494ee167759b5ee9b7d019f5ab40afe645e7e50d71932b7adf5df21
knee_rule:   last width before marginal speedup drops below 1.15x
status:      provisional (n=1/point; no CPU-seconds; original Hermit SHA absent)
```

For the historical node's 8.5 GiB memory ceiling, the effective safe width was
8 because the existing per-worker memory model predicted that 16 exceeded the
slice. With a roughly 24 GiB slice, 32 is the measured reference knee. Current
main has since replaced this node with larger fat-build nodes and 64 GiB slices;
that workload change requires a fresh curve before j32 is authoritative there.
Thus the executable setting must be derived as:

```text
effective_cargo_jobs = min(machine_curve_knee,
                           memory_supported_jobs,
                           online_cpu_threads)
```

The native third-party/link width is a separate correctness term and must not
be hidden in this throughput expression (§6).

## 1. Primary measurement anchor

The result above is independently recomputed from the tracked local experiment
`experiments/cargo_build_jobs_speedup_20260802`:

| Field | Value |
| --- | --- |
| raw data content commit | `d83a34b3c3e619a9778ad8a130c35afaf0116acc` |
| explanatory README commit | `5bb7c5b98da94011028669541b7c6f184eb848a4` |
| `results.csv` SHA-256 | `e2543ff81494ee167759b5ee9b7d019f5ab40afe645e7e50d71932b7adf5df21` |
| producer script SHA-256 | `0697d21847b283543dce2d43558825a85701a84c744f4cb5d7166de98ae8cd95` |
| host | short hostname `devbig014`; AMD EPYC 9D85; 316 online threads |
| CPU set | `all` |
| collection date | 2026-08-02 |
| samples | one clean target per width |

The measured command was:

```bash
CARGO_BUILD_JOBS="$jobs" THIRD_PARTY_BUILD_JOBS="$jobs" \
  CARGO_TARGET_DIR="$fresh_target" \
  cargo build --release --locked \
    -p hermit --features third-party-backends \
    -p detcore-dbi -p hermit-install
```

Each point used a fresh throwaway target directory. Rows were retained only
when `exit_code == 0`. The attempted j128 and j316 rows exited 101 after the
3pai filesystem enforcer blocked native compiler operations; their 42s and 66s
walls are failure times and contribute no speedup signal. The same exclusion
rule removes failed cpuset4 rows. Four successful full-host rows remain, and
all four appear in the table above.

## 2. What the data proves—and what it does not

It proves that, for this historical combined Cargo/DynamoRIO release build on
this machine, j8 leaves material throughput unused, j16 and j32 buy useful wall
reduction, and j64 is past the measured marginal-gain knee. This turns `32`
from a guess into a curve-derived operating point **for that profile**. It is
supporting evidence, not a workload-identity match for current main.

It is exploratory rather than benchmark-grade evidence:

- `n=1` at each width, so there is no median dispersion or order-robustness
  estimate.
- The harness recorded wall and sampled RSS but not user/system CPU-seconds.
- The original Hermit source SHA was not recorded.
- Cargo and native third-party parallelism were set to the same value, so the
  curve cannot attribute flattening to Rust compilation versus C/C++ compile or
  link stages.
- The process tree was sampled but not launched in a recorded dedicated cgroup;
  ambient competition was described, not quantified per sample.

Those defects are carried with the value. They forbid presenting j32 as a
portable machine-independent constant, but they do not erase the observed
108s-to-104s flattening.

## 3. Present code state

At locally inspected Hermit `b64d893ae9ea6404472eae9cb86102d91ec642ef`,
the stale-premise check matters:

- `validate.sh` defaults `VALIDATE_THIRD_PARTY_BUILD_JOBS_CAP` to 32 and passes
  that value through `CARGO_BUILD_JOBS` into the DynamoRIO CMake build.
- `Makefile` defaults `THIRD_PARTY_BUILD_JOBS` to 64 and likewise exports it as
  `CARGO_BUILD_JOBS`.
- The old `build.dbi_release` node no longer exists. The portable DAG now has
  `build.workspace` and `build.runtime_release`, both with a declared
  `preferred_inner_jobs=32` and a 64 GiB memory slice. Their descriptions bind
  absolute cold j32 observations to Hermit `846baeca` (130.35s / 11.66 GB and
  185.423s / 9.01 GB respectively), but provide no surrounding speedup curve.
- Current Reverie `reverie-dbi/build.rs` clamps native CMake parallelism to 16.
  The recorded observations at native j16 and j4 calibrate duration, not the
  first race-producing width, so 16 is not yet a reproduced correctness
  ceiling.

The validate and DAG values now happen to equal the historical provisional
knee, but the validate comment derives 32 only from avoiding a high-width race
and each DAG description gives only one absolute point. A single j32 build
cannot establish a knee. The Makefile's 64 is past the historical knee and
saved 4 seconds there. None of these consumers carries the full machine/curve
record, so the current workload still lacks a curve-derived default even when
the number happens to be defensible.

The fix is not to replace one comment with “measured.” The resolved build
record must carry `{jobs, machine_profile, curve_id, memory_slice,
third_party_link_cap}` and print it before the build.

## 4. Authoritative sweep method

The owner-requested `1,2,4,8,16` sweep is the mandatory low-width segment. It
cannot by itself locate the knee when j16 is still improving, so continue
doubling through `32,64` and then one confirmation point after the first
sub-threshold step. The complete bounded algorithm is:

1. Allocate a clean registered Hermit slot at one exact 40-hex source SHA.
   Record clean status, submodule SHAs, Cargo.lock digest, compiler/linker/CMake
   versions, kernel, short hostname, CPU topology, memory, and the harness
   digest. Never use a product primary.
2. Run solo. Record ambient load and competing cgroups before and after every
   sample. Do not overlap a validate, another build sweep, or a wide Hermit run.
3. Give every sample a new `mktemp -d` `CARGO_TARGET_DIR`; do not use `cargo
   clean` on a shared cache. Use `--locked --offline`, `CARGO_INCREMENTAL=0`,
   and a fixed `RUSTC_WRAPPER` policy.
4. Launch the complete process tree through a dedicated `systemd-run --user`
   service/cgroup with one fixed, recorded CPU set and a generously nonbinding
   memory slice. This avoids the known native-build BPFJailer denial and makes
   `cpu.stat` and `memory.peak` authoritative. Do not claim exclusive hardware
   unless the CPU set was actually made exclusive.
5. Carry the known runtime workaround in every environment:
   `LD_LIBRARY_PATH=/tmp/lu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}`. The build
   normally does not execute Hermit, but any post-build correctness probe then
   observes the same declared environment.
6. For each width, run one clean warmup target and at least three clean measured
   targets. Rotate order across repetitions, for example
   `1,4,16,2,8`, then reverse, then a different rotation. Continue with
   `16,32,64` and a confirmation width if j16 still advances.
7. Wrap Cargo with GNU time and also read the cgroup counters. Persist, per
   sample: elapsed seconds, user seconds, system seconds,
   `cpu_seconds=user+system`, cgroup `usage_usec`, `memory.peak`, exit status,
   start/end UTC, load, jobs, target identity, and log digest. A failed or timed
   out sample remains a row with status but contributes no timing median.
8. Report median wall, median CPU-seconds, IQR, valid/attempted sample counts,
   speedup versus j1, marginal speedup, and CPU efficiency
   `T1_cpu / cpu_seconds(j)`. Absolute seconds accompany every ratio.
9. Select the last width before marginal median wall speedup falls below 1.15x.
   Run the next doubling once to confirm the curve does not rebound. If IQR
   crosses the decision boundary, collect more samples rather than choosing a
   side.

A concrete timed command inside the transient service is:

```bash
env CARGO_BUILD_JOBS="$jobs" THIRD_PARTY_BUILD_JOBS="$fixed_native_jobs" \
    CARGO_TARGET_DIR="$fresh_target" CARGO_INCREMENTAL=0 \
    LD_LIBRARY_PATH="/tmp/lu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  /usr/bin/time -f '%e\t%U\t%S\t%M\t%x' -o "$sample_time" \
  ./ci/run-with-reverie-dbi-budget.sh cargo build --release --locked --offline \
    -p hermit --features third-party-backends \
    -p detcore-dbi -p detcore-sabre -p hermit-install
```

The producer copies only small text/CSV/JSON summaries into
`experiments/cargo_build_jobs_speedup_<date>/`; build trees and full compiler
logs remain ignored.

## 5. Why no new bounded sweep ran in this task

A safe new sweep was not available in this turn. The product primaries are
forbidden measurement surfaces; this coordinator's registered slots are active
for other named tasks; and the workspace contains extensive concurrent slot
ownership. The native build must also be launched through the out-of-sandbox
systemd producer path. A benchmark-grade low-width run requires at least 15
clean builds (five widths times three measured repetitions, plus warmups), with
j1 expected to dominate elapsed time. Starting it in another owner's checkout
or reducing it to one contaminated sample would violate the method it is meant
to establish.

The prior local curve was therefore audited and recomputed, and the missing
authoritative sweep is specified exactly rather than fabricated. No Hermit
binary was run, so the `/tmp/lu` workaround was not exercised.

## 6. Cargo throughput and native link correctness are two caps

The existing wiring partially separates, but does not independently derive,
two different questions:

1. **Cargo compile throughput:** where does additional Rust build width stop
   reducing wall time? The provisional answer on this machine is j32.
2. **DynamoRIO/elfutils native correctness:** at what concurrent native
   compile/link width does the debug-symbol race begin? Current Reverie clamps
   this path to 16, but a passing j16 sample is not a derived failure boundary.

The authoritative experiment first holds native width at a proven-safe value
while sweeping Cargo width. Below the current native clamp, Cargo's `NUM_JOBS`
still controls CMake width, so this requires either a prebuilt content-identical
DynamoRIO artifact or a measurement-only explicit native-width input; merely
setting the two existing environment variables differently does not prove
separation. It then holds Cargo at its derived knee and sweeps native width
separately.

The native sweep records every success and the exact `_dwelf`/`pt_iscache`
signature for failures, repeats widths around the first reproduced failure,
and selects a link cap from that bracket. It must also read `memory.events` and
compiler exit status: an OOM-killed `cc1plus` followed by a missing/truncated
object and undefined references is a memory failure, not evidence of a linker
race. Publish `{width, attempts, successes, race_failures, oom_failures,
other_failures}`; “16 worked once” is not a correctness argument.

The final resolved setting is consequently:

```text
cargo_jobs = min(machine_curve_knee, memory_supported_jobs, online_threads)
native_jobs = min(native_throughput_knee,
                  reproduced_link_correctness_ceiling,
                  native_memory_supported_jobs)
```

If the build system cannot expose separate widths, that coupling is the next
mechanism defect. It is not a reason to pretend one number proves both facts.

## 7. Acceptance brackets

The mechanism that consumes this result must demonstrate:

1. A matching machine/workload/curve profile resolves to 32 when memory and
   native-link caps allow it.
2. The same profile under an 8.5 GiB node slice resolves to the measured
   memory-supported width, currently 8, rather than OOMing at 32.
3. A different or unknown machine profile refuses to call bare 32 “derived” and
   requests a sweep or uses an explicitly labeled conservative fallback.
4. A malformed curve, zero successful rows, or missing provenance cannot
   update the default.
5. A j64 row that saves only 3.7% does not move the knee past j32 under the
   1.15x rule.
6. A reproduced native-link failure lowers only the native cap; it does not
   rewrite the Cargo throughput curve.

This artifact is local analysis. It changes no build default, runs no validate,
uses no egress, and does not claim that the independent native-link ceiling has
been measured.
