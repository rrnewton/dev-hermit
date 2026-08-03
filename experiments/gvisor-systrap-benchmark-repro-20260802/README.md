# gVisor systrap benchmark reproduction

## Scope

This manual experiment compares the April 2023 gVisor systrap blog numbers
with three Hermit/Reverie tiers:

1. exact Reverie `counter2`, which measures interception plus one shared Tool
   RPC/count per syscall;
2. Hermit relaxed, with deterministic syscall handling and I/O but
   `--no-sequentialize-threads`; and
3. Hermit strict, with fail-closed syscall handling and deterministic
   scheduling.

Cells are reported only when the unchanged guest completed and passed its
workload-specific output gate. A backend failure or 900-second timeout remains
visible rather than silently reducing the comparison.

The gVisor source definitions were recovered from blog commit
`1aa75b6714eeac860e52e4fb492b16964ff58fce`. Current public benchmark images
were pulled through `with-proxy`; image digests and exact binary SHAs are in
[`metadata.json`](metadata.json).

## Blog baseline

| Workload | Native | gVisor ptrace | Optimized systrap |
| --- | ---: | ---: | ---: |
| getpid | 239 ns | 15,400 ns | 1,017 ns |
| Build ABSL | 21,744 ms | 49,316 ms | 33,277 ms |
| ffmpeg | 74,331 ms | 82,084 ms | 78,970 ms |
| TensorFlow | 86,056 ms | 110,461 ms | 100,233 ms |
| Redis SET, 250k requests, 5 clients | 17,250 ms | 58,750 ms | 24,750 ms |

Absolute times are not cross-host rankings. The blog getpid run used a
four-vCPU GCE VM; this run used a 316-logical-CPU AMD EPYC host. Slowdown
against each run's own native baseline is the more defensible application
comparison.

## getpid

The fixed-count guest executes a raw getpid on every iteration and preserves a
checksum. Counter2 and relaxed use 200,000 iterations; strict uses 20,000
because its deterministic syscall path is much slower. Values are medians of
three backend samples. Native medians use five samples.

| Tier | Backend | ns/iteration |
| --- | --- | ---: |
| native | native | 91.7 |
| counter2 | ptrace | 17,127.1 |
| counter2 | KVM | 14,438.5 |
| counter2 | LiteInst | **718.8** |
| counter2 | DBI | 1,258.8 |
| counter2 | SaBRe | 2,593.0 |
| counter2 | e9patch | **875.6** |
| relaxed | ptrace | 39,837.7 |
| relaxed | KVM | 21,329.6 |
| relaxed | LiteInst | 92,708.2 |
| relaxed | SaBRe | 58,798.7 |
| relaxed | e9patch | 38,642.1 |
| strict | ptrace | 1,020,620 |
| strict | KVM | 134,039 |
| strict | LiteInst | 576,890 |
| strict | DBI | 4,659.5 |
| strict | SaBRe | 71,534.9 |
| strict | e9patch | 507,772 |

The earlier claim that LiteInst and e9patch beat optimized systrap is retracted.
Those absolute latencies came from different hosts, so they cannot establish a
performance ordering. The corrected local runsc matrix is published under
`benchmarks/gvisor-same-host/`. Relaxed DBI is absent because the DBI backend
requires sequentialized threads.

## Applications

| Workload | Tier/backend | Measured | Local slowdown |
| --- | --- | ---: | ---: |
| Build ABSL | counter2 ptrace | 198,450 ms | 16.387x |
| ffmpeg | counter2 ptrace | 26,860 ms | 1.116x |
| ffmpeg | relaxed ptrace | 112,460 ms | 4.672x |
| ffmpeg | counter2 DBI | >900,000 ms | >37.391x |
| TensorFlow, matching basic five | counter2 ptrace | 853,370 ms | 8.127x |
| TensorFlow convolutional | counter2 ptrace | >900,000 ms | >34.001x |

ABSL native was 12.11 seconds. Counter2 ptrace completed all 61 targets and
843 actions in 198.45 seconds while counting 3,844,078 syscalls from 2,880
processes and 5,521 threads. This explains its high local overhead but does not
establish an ordering against gVisor on a different host.

Ffmpeg is compute-heavy. Native, counter2-ptrace, and relaxed-ptrace all
produced the expected 11.3 MB output. The blog slowdown is historical context,
not a cross-host performance comparison.

The eight-program TensorFlow 1.13.2 native suite passed in 146.50 seconds. The
five completed counter2 cells took 853.37 seconds versus 105.01 seconds for
their matching native subset. Convolutional training hit the fixed timeout, so
there is no valid full-suite counter2 aggregate.

## Redis

Redis uses `redis-benchmark --csv -t set -n 250000 -c 5`. The reported value
is `250000 / QPS`, matching the blog's scaled-time presentation.

| Tier | Backend | Median ms | Local slowdown |
| --- | --- | ---: | ---: |
| native | native | 1,158 | 1.000x |
| counter2 | SaBRe | **1,586** | **1.370x** |
| counter2 | DBI | 4,769 | 4.118x |
| counter2 | ptrace | 16,920 | 14.611x |
| relaxed | e9patch | 38,115 | 32.915x |
| relaxed | ptrace | 40,846 | 35.273x |
| relaxed | SaBRe | 87,403 | 75.478x |
| strict | DBI | 93,742 | 80.943x |

Counter2 SaBRe has a 1.370x local slowdown. The withdrawn cross-host ranking is
replaced by the local runsc matrix under `benchmarks/gvisor-same-host/`.

## Coverage limits

- Counter2 LiteInst cannot create Redis's event loop (`ENOTSUPP`); e9patch
  finds no recoverable direct syscall site in the Redis executable; Reverie KVM
  requires a static guest.
- Hermit relaxed LiteInst reaches Redis initialization but returns
  `ENOTSUPP`. Hermit KVM starts Redis, but its guest network is unreachable
  from the native benchmark client and reports `accept: Bad address`.
- The public ABSL/TensorFlow roots require bubblewrap for their complete
  filesystem environment. Hermit rejects bubblewrap's
  `PR_SET_NO_NEW_PRIVS` with `ENOSYS`, so those Hermit application tiers
  are not runnable without changing the workload boundary.
- Loader/preload backends cannot all cross the exported-image root transition:
  LiteInst exits before coordinator connection, SaBRe/DBI fail the post-bwrap
  exec boundary, and e9patch finds no site in bubblewrap. These are excluded
  rather than timed as if only bubblewrap had been instrumented.
- Ffmpeg counter2 DBI made progress but timed out at 900 seconds. The partial
  media file is not a passing result.

## Artifacts

- [`raw/getpid-summary.tsv`](raw/getpid-summary.tsv): complete three-tier
  backend matrix.
- [`raw/application-summary.tsv`](raw/application-summary.tsv): application
  timings and blog values.
- [`raw/redis-medians.tsv`](raw/redis-medians.tsv): corrected Redis medians.
- [`raw/tensorflow-counter2-counts.tsv`](raw/tensorflow-counter2-counts.tsv):
  exact completed TensorFlow counter totals.
- [`logs/`](logs/): selected failure and correctness diagnostics.
- [`scripts/`](scripts/): text-only workload runners used for the experiment.

No media outputs, container roots, downloaded images, build outputs, or
generated binaries are tracked.
