# Benchmark v3: same program, same CounterLocal tool

## Verdict

The v3 run is methodologically admissible for this host: all six instrumented
backends ran the same optimized dynamic C helper and the same per-thread
CounterLocal contract. The N=0/N=16 preflight produced an exact +16 count delta
for every backend. Native is labeled separately as the tool-free baseline.

The full 28-row run completed on 2026-07-26. Four independent 10-second gates
observed load1 26.47-50.81 (limit 79.00) and CPU 18 / SMT sibling 176 at
97.99-99.20% / 98.60-98.69% idle (minimum 95%). Two earlier attempts were
rejected before timing when their selected pairs became busy.

## Results

Values are Criterion marginal slope in ns per additional syscall, followed by
the bootstrap 95% confidence interval. These are total syscall costs inside
each execution environment, not native-subtracted overheads.

| Backend | getpid | read /dev/null | write /dev/null | clock_gettime |
| --- | ---: | ---: | ---: | ---: |
| native | 68.41 [66.94, 69.45] | 89.02 [88.37, 89.59] | 92.70 [92.60, 92.79] | 116.69 [114.77, 118.64] |
| gvisor-systrap | 6,752.98 [6,726.80, 6,785.33] | 7,014.59 [6,839.73, 7,165.92] | 7,190.50 [7,040.40, 7,310.28] | 7,306.85 [7,285.32, 7,338.82] |
| gvisor-kvm | 895.90 [892.08, 899.20] | 1,026.96 [1,016.59, 1,037.11] | 1,070.46 [1,065.54, 1,075.27] | 1,072.54 [1,062.23, 1,082.69] |
| reverie-ptrace | 16,714.11 [16,535.26, 16,866.89] | 16,035.74 [15,937.78, 16,157.00] | 16,225.86 [16,132.41, 16,320.71] | 17,028.62 [16,932.73, 17,104.54] |
| reverie-dbi | 902.18 [900.86, 903.56] | 1,418.70 [1,409.46, 1,430.94] | 1,051.48 [1,048.33, 1,054.20] | 1,055.01 [1,053.84, 1,056.45] |
| reverie-kvm | 9,522.03 [9,516.10, 9,530.88] | 12,824.33 [12,799.45, 12,868.74] | 12,353.22 [12,319.73, 12,388.69] | 10,456.31 [10,431.37, 10,477.92] |
| reverie-sabre | 583.35 [581.77, 584.85] | 797.96 [790.69, 805.47] | 821.09 [817.43, 823.54] | 637.92 [636.94, 638.72] |

Native-subtracted marginal overhead (ns/syscall):

| Backend | getpid | read | write | clock_gettime |
| --- | ---: | ---: | ---: | ---: |
| gvisor-systrap | 6,684.56 | 6,925.58 | 7,097.80 | 7,190.16 |
| gvisor-kvm | 827.49 | 937.94 | 977.76 | 955.86 |
| reverie-ptrace | 16,645.70 | 15,946.72 | 16,133.17 | 16,911.93 |
| reverie-dbi | 833.77 | 1,329.68 | 958.78 | 938.33 |
| reverie-kvm | 9,453.62 | 12,735.31 | 12,260.52 | 10,339.62 |
| reverie-sabre | 514.93 | 708.94 | 728.39 | 521.24 |

On this run, SaBRe had the lowest instrumented marginal cost for all four
loops. gVisor-KVM and DBI were the next tier; DBI was notably higher on read.
gVisor-systrap cost about 6.75-7.31 us/call, Reverie-KVM 9.52-12.82 us/call,
and ptrace 16.04-17.03 us/call. This is a host- and revision-specific ordering,
not a universal backend ranking.

## What Each Row Measures

- **native:** the same `syscall_server.c` binary runs directly and stays alive
  for all samples in a row. It has no counter tool and is only the subtraction
  baseline. The timed interval contains the requested raw syscall loop plus one
  constant-size control request/response; Criterion's slope removes that fixed
  protocol cost.
- **gvisor-systrap:** the helper runs inside `runsc` on a wrapper around the real
  systrap platform. Each `platform.Context.Switch` returning nil increments one
  context-local `u64`; gVisor documents that return as exactly one guest
  syscall. Teardown emits one `counter2-local` report. The result includes
  gVisor sentry syscall emulation and sandbox overhead, not just the increment.
- **gvisor-kvm:** identical wrapper, helper, report, and Criterion protocol, but
  around gVisor's KVM platform. The only intentional experimental variable
  relative to gvisor-systrap is the base platform.
- **reverie-ptrace:** the `counter2` launcher runs the exact factored
  `CounterLocal` from `reverie-examples`. Its hot path increments the Reverie
  `ThreadState` `u64` once and calls `tail_inject` unchanged. Per-thread and
  process-tree aggregation occurs only at teardown, outside measured samples.
- **reverie-dbi:** DynamoRIO invokes that same `CounterLocal` Tool implementation
  at each observed syscall. The persistent thread state lives in DynamoRIO TLS;
  `tail_inject` maps back to the original syscall. This total also includes
  DynamoRIO basic-block instrumentation, so it is not a pure trap-only cost.
- **reverie-kvm:** Reverie's KVM backend loads the exact same dynamic helper and
  drives the exact same `CounterLocal` through its generic Tool adapter. The
  guest has 1 GiB virtual memory and stays alive for the complete row. The total
  includes Reverie's KVM executor and syscall implementation.
- **reverie-sabre:** the SaBRe plugin includes the exact same CounterLocal source
  and keeps its per-thread `u64` across all samples. Its local adapter runs the
  Tool destructor before nonreturning `exit_group`, then prints the same
  per-thread report. The result includes SaBRe rewriting/trampoline overhead.

The startup totals are not expected to match because adapters begin observing
at different loader/runtime boundaries (N=0: DBI 32, SaBRe 7, other tools 33).
The controlled property is the measured loop: every N=16 total increased by
exactly 16.

## Workload And Analysis

The one compiled helper performs four loops with raw Linux syscalls:

1. `getpid` with the result accumulated.
2. One-byte `read` from a persistent `/dev/null` descriptor.
3. One-byte `write` to a persistent `/dev/null` descriptor.
4. `clock_gettime(CLOCK_MONOTONIC)` through `syscall`, bypassing the vDSO.

Each row uses 20 linear samples, a 2-second warmup, a nominal 5-second
measurement period, 50,000 bootstrap resamples, a 95% confidence interval, and
a 2% noise threshold. Criterion retains and reports outliers. Fixed 1K, 10K,
100K, and 1M getpid batches are in `fixed-counts.tsv`; their per-call values
converge on the reported slopes. Backend block order is seeded and differs by
syscall (`backend-order.tsv`). All raw Criterion JSON is retained under
`criterion-raw/`.

## V2 Comparison

`hermit/experiments/criterion-syscall/RESULTS_20260725.md` exists, but v2 is not
a valid regression baseline: DBI used PrototypeTool, SaBRe used riptrace,
Reverie-KVM used a custom one-shot counter, and its host caveat reported load
around 300-400. V3 uses CounterLocal everywhere and passed load/SMT gates.

For transparency, these are descriptive changes in total marginal slope from
v2 to v3; they must not be attributed to backend improvements:

| Backend | getpid | read | write | clock_gettime |
| --- | ---: | ---: | ---: | ---: |
| native | -24.9% | -23.5% | -17.1% | -19.8% |
| gvisor-systrap | -15.1% | -5.8% | +0.1% | -6.9% |
| gvisor-kvm | -18.1% | -10.5% | -15.7% | -19.8% |
| reverie-ptrace | -32.9% | -34.1% | -30.8% | -32.3% |
| reverie-dbi | -30.4% | -49.6% | -31.4% | +3.5% |
| reverie-kvm | -80.5% | -70.7% | -72.6% | -80.1% |
| reverie-sabre | -30.7% | -38.7% | -52.2% | -37.0% |

The roughly 17-25% faster native slopes alone show the host-condition effect.
The very large Reverie-KVM change additionally reflects removal of v2's custom
one-shot methodology. V2 numbers should therefore be archived as historical,
not combined with v3.

## Provenance

- Parent workspace: `667fa56c0ac21f050f27bf2a41bfcdc9cfc58d4f`, depth 103.
- Reverie base: `88fb2656059c4f41d5b9c9f6da016cef941bc317`, depth 565;
  measured source diff SHA-256 is in `metadata.tsv`.
- gVisor base: `8eb8f9e0df89e0352305057c2c08a993fe92bc03`, depth 11,495;
  measured source diff SHA-256 is in `metadata.tsv`.
- Host: AMD EPYC 9D85, 316 logical CPUs, Linux 6.17.13; pinned CPU 18,
  sibling 176.
- Exact binaries and tool-source hashes: `artifact-sha256.tsv`.
- Machine-readable estimates: `summary.tsv`; semantic evidence:
  `preflight.tsv`; load evidence: `idle-gates.tsv`.
