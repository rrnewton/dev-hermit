# Reverie backend performance attribution

This report closes the measurement gap in `impl-backend-perf-comparison` with
an end-to-end native/ptrace/KVM/DBI comparison and extends it to every runnable
Reverie counter2 backend. It reuses the landed, correctness-gated counter2
shootout and cross-checks its syscall-cost estimate against the independent
gVisor reproduction getpid loop.

## Identity

- Host: `devbig014`, AMD EPYC 9D85, 316 logical CPUs
- Reverie framework source: `36ce950a5c4207046c62efbd2904d5c808a4238f`
- Landed by: [rrnewton/reverie#331](https://github.com/rrnewton/reverie/pull/331), merge `a9f25aa7a19faeb716b69e463e92c7160ab48c03`
- Framework run: `20260802T042358Z`, clean source, seed 1
- Configuration: 5 second native target, 1 warmup, 5 measured repetitions,
  randomized schedule, 180 second timeout
- Correctness gate: 4/4 native probes, 12/12 backend probes, and 80/80 timed
  samples exited zero, matched native stdout, and emitted nonzero exact-counter2
  totals
- Independent getpid source: Hermit `8c83575217a341af17232e6f50c7717eb17a0211`,
  Reverie consumer `2b2532314eefd90b92cca94f4294a4fed65de33f`

The copied raw framework inputs are under `raw/`. The getpid source remains in
`../gvisor-systrap-benchmark-repro-20260802/raw/getpid-summary.tsv`.

## End-to-end result

The geometric mean is over the CPU-heavy and syscall-mix workloads, after each
backend is divided by the matching native binary's median. KVM runs the same C
source as a static ELF and is normalized to the native static ELF; the other
backends use the dynamic ELF.

| Rank | Backend | Geomean slowdown | CPU-heavy | Syscall-mix |
| ---: | --- | ---: | ---: | ---: |
| 1 | LiteInst | 1.032x | 1.008x | 1.057x |
| 2 | e9patch | 1.036x | 1.006x | 1.067x |
| 3 | SaBRe | 1.056x | 1.015x | 1.098x |
| 4 | KVM | 1.597x | 1.101x | 2.318x |
| 5 | ptrace | 1.687x | 1.116x | 2.551x |
| 6 | DBI | 5.433x | 5.379x | 5.488x |

On the independent Redis SET 250,000/concurrency-5 application benchmark, the
counter2 medians preserve the same broad result: SaBRe 1.370x native, DBI
4.118x, and ptrace 14.611x. KVM could not be included because the static guest's
Redis listener was unreachable from the host client.

## Attribution

The two framework workloads execute nearly the same amount of native work but
increase exact counter2 events from about 28,300 to about 450,750. For each
backend, the paired slope is:

```text
((syscall_mix_ms - native_mix_ms) - (cpu_heavy_ms - native_cpu_ms)) * 1e6
----------------------------------------------------------------------------
                 syscall_mix_count - cpu_heavy_count
```

| Backend | CPU overhead ms | Mix overhead ms | Extra calls | Paired ns/call | getpid marginal ns | Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LiteInst | 40.147 | 286.275 | 422,451 | 582.619 | 627.111 | 0.929x |
| e9patch | 29.510 | 339.486 | 422,451 | 733.756 | 783.872 | 0.936x |
| SaBRe | 73.383 | 494.265 | 422,451 | 996.286 | 2,501.235 | 0.398x |
| DBI | 22,026.747 | 22,602.149 | 422,451 | 1,362.056 | 1,167.115 | 1.167x |
| KVM | 505.046 | 6,630.864 | 422,451 | 14,500.659 | 14,346.785 | 1.011x |
| ptrace | 585.563 | 7,810.016 | 422,451 | 17,101.280 | 17,035.385 | 1.004x |

The attribution is clear:

- ptrace and KVM are dominated by syscall interception. Their paired slopes
  reproduce the getpid marginal cost within 0.4% and 1.1%, respectively.
- LiteInst and e9patch also track getpid within 7.1% and 6.4%, but at 0.58-0.73
  microseconds per extra call. Their multi-second CPU floors are within 0.8% of
  native.
- DBI's marginal extra-syscall cost is only about 1.36 microseconds, while its
  CPU-heavy workload is already 5.38x native. Its poor overall rank is therefore
  the DynamoRIO code-stream instrumentation floor, not counter2 dispatch.
- SaBRe's paired workload slope is lower than its isolated getpid cost. Syscall
  mix and launch/runtime effects differ, so its exact per-call latency remains
  workload-sensitive; both measurements still place it in the in-process tier.

## Limits

This is a single-host, five-repetition wall-time baseline under high background
load, not a confidence-bounded population estimate. The paired slope subtracts
native work and the low-syscall backend floor, but the workloads use separately
calibrated iteration counts that differ by 0.34%. It is suitable for locating
the dominant overhead class and ranking these cells, not for claiming a
hardware-independent nanosecond constant.

