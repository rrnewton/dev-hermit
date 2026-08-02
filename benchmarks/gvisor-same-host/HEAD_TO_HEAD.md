# Same-host head-to-head

Host: `devbig014.atn7.facebook.com`. Values are slowdown versus the native
sample from the same collection. The runsc columns use local runsc release
`20260727.0`; no gVisor blog result is used.

## getpid

| Our tier | Our backend | Our result | runsc systrap | runsc KVM | runsc ptrace |
| --- | --- | ---: | ---: | ---: | ---: |
| counter2 | ptrace | 186.743x | 40.245x | 12.425x | 70.543x |
| counter2 | KVM | 157.428x | 40.245x | 12.425x | 70.543x |
| counter2 | LiteInst | 7.838x | 40.245x | 12.425x | 70.543x |
| counter2 | DBI | 13.725x | 40.245x | 12.425x | 70.543x |
| counter2 | SaBRe | 28.272x | 40.245x | 12.425x | 70.543x |
| counter2 | e9patch | 9.547x | 40.245x | 12.425x | 70.543x |
| relaxed | ptrace | 434.364x | 40.245x | 12.425x | 70.543x |
| relaxed | KVM | 232.564x | 40.245x | 12.425x | 70.543x |
| relaxed | LiteInst | 1,010.829x | 40.245x | 12.425x | 70.543x |
| relaxed | SaBRe | 641.102x | 40.245x | 12.425x | 70.543x |
| relaxed | e9patch | 421.328x | 40.245x | 12.425x | 70.543x |
| strict | ptrace | 3,697.296x | 40.245x | 12.425x | 70.543x |
| strict | KVM | 485.569x | 40.245x | 12.425x | 70.543x |
| strict | LiteInst | 2,089.840x | 40.245x | 12.425x | 70.543x |
| strict | DBI | 16.879x | 40.245x | 12.425x | 70.543x |
| strict | SaBRe | 259.142x | 40.245x | 12.425x | 70.543x |
| strict | e9patch | 1,839.454x | 40.245x | 12.425x | 70.543x |

Runsc medians are native `100.789 ns`, systrap `4,056.209 ns`, KVM
`1,252.276 ns`, and ptrace `7,109.977 ns`. Counter2/relaxed used 200,000
iterations; strict used 20,000; runsc used 1,000,000. The table compares
steady-state per-call cost, not equal-duration processes.

## Redis SET 250k/c5

| Our tier | Our backend | Our result | runsc systrap | runsc KVM | runsc ptrace |
| --- | --- | ---: | ---: | ---: | ---: |
| counter2 | ptrace | 14.611x | 11.230x | 12.490x | 20.457x |
| counter2 | DBI | 4.118x | 11.230x | 12.490x | 20.457x |
| counter2 | SaBRe | 1.370x | 11.230x | 12.490x | 20.457x |
| relaxed | ptrace | 35.273x | 11.230x | 12.490x | 20.457x |
| relaxed | SaBRe | 75.478x | 11.230x | 12.490x | 20.457x |
| relaxed | e9patch | 32.915x | 11.230x | 12.490x | 20.457x |
| strict | DBI | 80.943x | 11.230x | 12.490x | 20.457x |

Runsc medians are native `1,128.000 ms`, systrap `12,668.003 ms`, KVM
`14,089.000 ms`, and ptrace `23,075.993 ms`. All rows use 250,000 SET requests
and five concurrent clients.

## ffmpeg

| Our tier | Our backend | Our result | runsc systrap | runsc KVM | runsc ptrace |
| --- | --- | ---: | ---: | ---: | ---: |
| counter2 | ptrace | 1.116x | 1.051x | 1.041x | 1.051x |
| relaxed | ptrace | 4.672x | 1.051x | 1.041x | 1.051x |
| counter2 | DBI | timeout >37.391x | 1.051x | 1.041x | 1.051x |

Runsc medians are native `24,681.582 ms`, systrap `25,933.676 ms`, KVM
`25,686.832 ms`, and ptrace `25,941.522 ms`. All successful rows produced the
expected output file.

## Coverage-limited applications

| Workload | Our tier/backend | Our result | runsc systrap | runsc KVM | runsc ptrace | Head-to-head status |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Build ABSL | counter2 ptrace | 16.387x | 1.124x | 2.026x | 1.327x | Not ranked: our older build was unbounded; runsc is pinned to 16 Bazel jobs/loading threads. |
| TensorFlow basic five | counter2 ptrace | 8.127x | n/a | n/a | n/a | Not ranked: runsc measured the full eight-program suite. |
| TensorFlow convolutional | counter2 ptrace | timeout >34.001x | n/a | n/a | n/a | Not ranked: neither row is a completed matching aggregate. |
| TensorFlow full eight | no completed tier | n/a | 2.610x | 2.467x | timeout >5.549x | Local runsc result only. |

The comparison is same-host but not simultaneous. It compares overhead, not
semantic equivalence: counter2 only counts interceptions, relaxed adds syscall
determinization without thread scheduling, strict adds full deterministic
scheduling, and runsc provides a sandbox rather than Hermit's determinization.
Exact absolute values, repetitions, statuses, and sources are in
[`SCORECARD.tsv`](SCORECARD.tsv).
