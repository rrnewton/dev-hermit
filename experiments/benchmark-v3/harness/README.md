# Counter1 syscall benchmark v3

This harness measures marginal syscall cost with one guest binary and one
counter tool contract across every instrumented backend. It supersedes the
invalid v2 comparison, which ran different tools on different backends.

## Common workload and tool contract

`fixtures/syscall_server.c` is compiled once. The identical dynamically linked
ELF runs on native Linux and under gVisor systrap, gVisor KVM, Reverie ptrace,
DynamoRIO, Reverie KVM, and SaBRe. It executes raw `syscall(2)` loops for
`getpid`, one-byte reads and writes on `/dev/null`, and
`clock_gettime(CLOCK_MONOTONIC)` rather than the vDSO.

The literal `reverie-examples/counter1` source is authoritative. Contrary to
the task's parenthetical description, real counter1 is not per-thread local:
its thread state is `()`, every syscall sends `IncrMsg(Sysno)` to global state,
and the global state performs one sequentially consistent `AtomicU64`
increment. Ptrace, DBI, SaBRe, and Reverie KVM compile the same factored
`counter1_tool.rs`. gVisor uses the platform-API equivalent: one platform-wide
sequentially consistent atomic increment after each successful
`platform.Context.Switch`, the API boundary for one guest syscall. Native is
only a tool-free reference, not a counter1 backend.

`preflight.sh` runs N=0 and N=16 with the same helper and rejects any
instrumented backend unless the reported counter1 total increases by exactly
16. Startup totals may differ because interception boundaries differ.

## Statistical controls

- Criterion linear sampling: 20 measured samples per row, 2 seconds warmup,
  nominal 5 seconds measurement, 50,000 bootstrap resamples, and 95% confidence
  intervals.
- A persistent guest per backend and syscall keeps startup outside the
  regression. Fixed getpid anchors at 1K, 10K, 100K, and 1M calls expose
  nonlinearity.
- Backend block order is deterministically shuffled independently per syscall
  with seed `20260726`.
- Before each syscall block, the run aborts unless load1 is at most 0.25 per
  logical CPU and the pinned CPU plus its SMT sibling average at least 95% idle
  for 10 seconds.
- Every backend inherits the same CPU affinity, locale, timezone, disabled Rust
  logging, helper binary, and command protocol.
- `summary.tsv` contains Criterion regression slopes and confidence intervals.
  `raw-samples.tsv` records every measured batch. `medians.tsv` is the median
  of each row's batch-average ns/syscall values. Batch medians include the
  fixed request/response cost; slopes are the primary marginal-cost statistic.
- Raw Criterion JSON, fixed-count anchors, idle samples, backend order, exact
  repository revisions and depths, dirty-diff hashes, and artifact SHA-256
  values are retained.

Block randomization is used instead of per-sample interleaving because each
backend owns a long-lived guest and Criterion calibrates each regression
separately. Independent replication is needed before treating close results as
a universal ranking.

Methodology references:

- Criterion analysis: https://bheisler.github.io/criterion.rs/book/analysis.html
- Google Benchmark random interleaving: https://google.github.io/benchmark/random_interleaving.html
- Google Benchmark variance: https://google.github.io/benchmark/reducing_variance.html
- LLVM benchmarking: https://llvm.org/docs/Benchmarking.html
- SPEC CPU run rules: https://www.spec.org/cpu2017/Docs/runrules.html

## Reproduce

Build the task's Reverie release artifacts, SaBRe loader, and patched `runsc`,
then select a genuinely idle logical CPU:

```bash
cd /home/newton/work/dev-hermit
CRITERION_HOME=/tmp/criterion-counter1-v3 \
SYSCALL_BENCH_CPU=18 \
./experiments/benchmark-v3/harness/run.sh \
  ./experiments/benchmark-v3/results
```

The runner fails instead of weakening its all-backend or idle gates.
