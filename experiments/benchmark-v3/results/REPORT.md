# Counter1 syscall benchmark v3 results

Date: 2026-07-26  
Host: `devbig030`
Status: complete, 28/28 backend/workload rows

## Executive result

The corrected run used one compiled helper binary and one counter1 contract for
every instrumented backend. It retained 20 measured batches per row (560 raw
samples total), batch medians, Criterion linear-regression slopes with 95%
confidence intervals, fixed getpid anchors, exact artifacts, and all idle
gates.

Real `reverie-examples/counter1` is **not per-thread local**, despite the task
parenthetical. Its thread state is `()`; every intercepted syscall sends
`IncrMsg(Sysno)` and performs one process-tree-global SeqCst `AtomicU64`
increment. Ptrace, DBI, SaBRe, and Reverie KVM compile the same factored
`counter1_tool.rs`. The gVisor port performs the platform-API equivalent:
one platform-wide sequentially consistent atomic increment after each
successful `platform.Context.Switch`.

Native is included only as a tool-free reference. It does not claim to run
counter1.

## Revisions and artifacts

| Repository | HEAD | Depth |
| --- | --- | ---: |
| dev-hermit parent | `667fa56c0ac21f050f27bf2a41bfcdc9cfc58d4f` | 103 |
| Reverie worktree | `88fb2656059c4f41d5b9c9f6da016cef941bc317` | 565 |
| gVisor experiment | `8eb8f9e0df89e0352305057c2c08a993fe92bc03` | 11495 |

The counter1 backend work was uncommitted task-owned state on those revisions,
so commit IDs alone are insufficient. Every run also records the same Reverie
binary diff SHA-256
`8a7f2b84a8ab9724de231906e5a4e4622a1f24b8d736aff35ef985befbf7ed20`,
gVisor diff SHA-256
`e87f1970bd534a37ecbabd38e6f2eb7e2e17bb472c1c3a9e5ae94097e3474aa4`,
and individual source/binary hashes in `artifact-sha256.tsv`. Artifact hash
columns were identical across all four accepted invocations.

## What was measured

The single helper `syscall_server.c` was compiled once per invocation with
`cc -O3 -fno-plt`; its source and resulting binary hashes were identical
across invocations. The same dynamically linked ELF was used for every backend.

Each Criterion iteration sends a command to an already-running helper, waits
for it to execute N raw syscalls, and validates its response. Linear sampling
varies N. The regression slope estimates the marginal wall-clock nanoseconds
per additional syscall, separating fixed command/response cost through the
intercept. The raw sample rate and its median retain that fixed cost and are
reported as a secondary descriptive statistic.

Workloads:

- `getpid`: raw `syscall(SYS_getpid)`.
- `read-devnull`: one-byte raw `read` from an already-open `/dev/null`.
- `write-devnull`: one-byte raw `write` to an already-open `/dev/null`.
- `clock-gettime`: raw `syscall(SYS_clock_gettime, CLOCK_MONOTONIC)`, not vDSO.

Backend boundaries:

| Backend | Detailed measured path |
| --- | --- |
| native | Persistent helper directly on Linux, with no counter tool. It is the uninstrumented reference for the same batching protocol. |
| gvisor-systrap | Persistent helper under `runsc --platform=counter1-systrap`. The wrapper delegates to systrap, then increments one platform-wide Go `atomic.Uint64` after each nil-returning `Context.Switch`, gVisor's one-guest-syscall boundary. |
| gvisor-kvm | Same wrapper and global atomic contract as gVisor systrap, delegating to gVisor's KVM platform. |
| reverie-ptrace | Real shared `CounterLocal` under `TracerBuilder`: send `IncrMsg(Sysno)` to shared `CounterGlobal`, SeqCst increment, then unchanged tail injection via ptrace. |
| reverie-dbi | Same shared Rust `CounterLocal` and `CounterGlobal` dispatched by the DynamoRIO adapter before the original syscall. The helper uses a Unix control socket so benchmark protocol I/O is not confused with intercepted stdio. |
| reverie-kvm | Same shared Rust tool run through `KvmBackend`; the already-loaded helper stays alive for all samples in the row. |
| reverie-sabre | Same shared Rust tool selected as `counter1-exact` in the SaBRe plugin adapter; unchanged syscall tail injection and a Unix control socket. |

The N=0/N=16 semantic preflight ran before every accepted invocation. Every
instrumented backend's reported total increased by exactly 16. Startup totals
differ by interception boundary (gVisor/ptrace/KVM 33, DBI 32, SaBRe 8), but
the measured loop delta was identical.

## Statistical method

- 20 linear samples per row.
- 2 seconds warmup and nominal 5 seconds measurement per row.
- 50,000 bootstrap resamples and 95% slope confidence intervals.
- One persistent guest per backend/workload row; startup is excluded.
- Fixed getpid anchors at 1K, 10K, 100K, and 1M calls.
- One deterministic shuffled backend permutation:
  native, Reverie KVM, gVisor KVM, ptrace, SaBRe, gVisor systrap, DBI.
- CPU affinity inherited by every child.
- Before each accepted block: load1 <= 79.0 (0.25 per 316 logical CPUs), and
  both SMT threads >=95% idle over 10 seconds.
- Locale/timezone fixed; Rust logging disabled.
- AMD EPYC 9D85, `performance` governor, `amd-pstate-epp`, boost enabled.

The host was too bursty for one uninterrupted four-block invocation, and the
harness rejected several attempts. The final result therefore consists of four
independently gated one-workload invocations, aggregated without modifying
samples. Each run retains separate metadata. Because a one-workload invocation
resets the operation index, all four used the same seeded backend permutation,
not independently shuffled permutations. This is disclosed order risk; a
future replication should vary the per-block seed.

Accepted gates:

| Workload | Load1 before -> after | CPU / sibling | Idle % |
| --- | ---: | --- | ---: |
| getpid | 48.93 -> 45.31 | 148 / 306 | 97.289 / 97.495 |
| read-devnull | 54.96 -> 49.29 | 148 / 306 | 98.395 / 98.897 |
| write-devnull | 55.66 -> 53.05 | 115 / 273 | 97.292 / 95.687 |
| clock-gettime | 31.84 -> 36.52 | 25 / 183 | 96.496 / 95.473 |

## Full results

`Slope` is the primary marginal estimate. `Median` is the median of 20 raw
batch-average ns/syscall values and includes fixed protocol cost.

| Workload | Backend | Slope ns/syscall | 95% CI | Median ns/syscall |
| --- | --- | ---: | ---: | ---: |
| clock-gettime | native | 115.610 | 113.975-116.802 | 115.299 |
| clock-gettime | gvisor-systrap | 7209.173 | 7121.121-7300.354 | 7144.599 |
| clock-gettime | gvisor-kvm | 1083.969 | 1073.389-1096.389 | 1081.300 |
| clock-gettime | reverie-ptrace | 17072.594 | 16925.207-17197.815 | 17186.113 |
| clock-gettime | reverie-dbi | 1171.914 | 1166.478-1180.948 | 1170.109 |
| clock-gettime | reverie-kvm | 11058.252 | 11034.619-11087.307 | 11042.389 |
| clock-gettime | reverie-sabre | 668.829 | 666.137-671.425 | 665.225 |
| getpid | native | 69.554 | 68.464-71.214 | 70.212 |
| getpid | gvisor-systrap | 7305.646 | 7132.485-7508.248 | 7324.574 |
| getpid | gvisor-kvm | 911.084 | 904.959-916.668 | 914.506 |
| getpid | reverie-ptrace | 16945.545 | 16880.530-17013.757 | 16953.320 |
| getpid | reverie-dbi | 1001.195 | 998.147-1004.378 | 1000.867 |
| getpid | reverie-kvm | 9965.556 | 9943.981-9996.991 | 9977.965 |
| getpid | reverie-sabre | 614.420 | 609.579-618.688 | 612.758 |
| read-devnull | native | 92.483 | 91.533-93.345 | 91.785 |
| read-devnull | gvisor-systrap | 7438.116 | 7354.679-7508.922 | 7322.020 |
| read-devnull | gvisor-kvm | 1041.539 | 1032.682-1051.104 | 1042.598 |
| read-devnull | reverie-ptrace | 17310.645 | 17142.354-17465.778 | 17106.554 |
| read-devnull | reverie-dbi | 1521.385 | 1513.080-1529.048 | 1513.086 |
| read-devnull | reverie-kvm | 13183.877 | 13172.636-13193.331 | 13188.163 |
| read-devnull | reverie-sabre | 851.538 | 848.284-856.619 | 849.843 |
| write-devnull | native | 87.741 | 87.445-88.069 | 87.716 |
| write-devnull | gvisor-systrap | 7138.565 | 7076.769-7202.960 | 7194.189 |
| write-devnull | gvisor-kvm | 1071.846 | 1066.044-1077.086 | 1076.776 |
| write-devnull | reverie-ptrace | 16760.377 | 16574.851-16922.329 | 16566.001 |
| write-devnull | reverie-dbi | 1222.492 | 1162.798-1294.752 | 1172.030 |
| write-devnull | reverie-kvm | 12705.703 | 12661.557-12802.081 | 12694.550 |
| write-devnull | reverie-sabre | 862.449 | 858.265-868.580 | 858.975 |

## Interpretation

- SaBRe was the fastest counter1-instrumented backend in all four workloads:
  614-862 ns/syscall.
- gVisor KVM was next at 911-1084 ns/syscall. DBI measured 1001-1521
  ns/syscall; its write row was noisier, with a wider confidence interval.
- gVisor systrap measured 7.14-7.44 us/syscall.
- Reverie KVM measured 9.97-13.18 us/syscall.
- Ptrace was slowest at 16.76-17.31 us/syscall.
- These are total observed paths with counter1 enabled, not an isolated
  subtraction of tool overhead. Native provides context but does not execute
  counter1.
- The results are directly comparable within this host/run contract. They are
  not universal backend rankings: CPU boost remained enabled, blocks used
  different cores, gVisor implements a semantic platform-API equivalent rather
  than Reverie's RPC machinery, and only one accepted replication exists.

The broken v2 numbers are intentionally not used as a quantitative comparison:
their tools were different, so any apparent delta would remain
apples-to-oranges.

## Result inventory

- `summary.tsv`: all 28 regression estimates and confidence intervals.
- `medians.tsv`: 28 batch-rate medians/min/max values.
- `raw-samples.tsv`: all 560 measured batches with iteration count and elapsed ns.
- `criterion-raw/`: 56 sample trees (new and base for all 28 rows).
- `runs/<workload>/`: each independently gated run and its full metadata.
- `run-metadata.tsv`, `idle-gates.tsv`, `backend-order.tsv`.
- `preflight.tsv`, `capabilities.tsv`, `fixed-counts.tsv`.
- `artifact-sha256.tsv`: exact helper, backend binaries, and tool sources.
- `*.svg`: generated backend comparison plots.

Methodology sources are listed in `../harness/README.md`.
