# CounterLocal syscall benchmark v3

This harness measures marginal syscall cost with one guest program and one tool
contract across every instrumented backend. It replaces the v2 comparison,
which used unrelated DBI, SaBRe, and KVM tools.

## Common contract

`fixtures/syscall_server.c` is compiled once and the identical binary is used by
every row. It runs raw `syscall(2)` loops for `getpid`, one-byte reads and writes
on `/dev/null`, and `clock_gettime(CLOCK_MONOTONIC)` (not the vDSO).

Every instrumented backend runs the exact `CounterLocal` hot path factored from
`reverie-examples/counter2.rs`: one thread-local `u64` increment at syscall
entry, unchanged tail injection, and one per-thread report at teardown. gVisor's
faithful platform wrapper increments once when `platform.Context.Switch`
returns nil, which that API defines as one guest syscall. Native is explicitly
the tool-free subtraction baseline.

`preflight.sh` runs the same binary at N=0 and N=16 and rejects any backend
whose reported count delta is not exactly 16. Startup totals may differ because
backend interception boundaries differ; the measured loop delta must not.

## Statistical controls

- Criterion linear sampling: 20 samples, 2 s warmup, nominal 5 s measurement,
  50,000 bootstrap resamples, 95% slope confidence intervals.
- One persistent guest per backend/syscall row excludes process startup from
  the fitted marginal slope. Fixed 1K/10K/100K/1M anchors expose nonlinearity.
- Backend block order is deterministically shuffled independently per syscall
  with seed `20260726`. Raw samples and detected outliers are retained.
- The runner refuses each syscall group unless load1 is at most 0.25 per logical
  CPU and both the pinned CPU and its SMT sibling average at least 95% idle for
  10 seconds. Affinity is inherited by all backend children.
- Locale, timezone, logging, helper binary, artifacts, tool source, and command
  protocol are fixed. Artifact SHA-256 values and source revisions are emitted.

Block randomization is used rather than per-sample interleaving because each
backend owns a long-lived guest and Criterion calibrates each slope separately.
This is disclosed in `backend-order.tsv`; independent replication is still
needed before treating small differences as universal rankings.

The controls follow Criterion's analysis model and the variance guidance from
Google Benchmark, LLVM, and SPEC:

- https://github.com/bheisler/criterion.rs/blob/master/book/src/analysis.md
- https://google.github.io/benchmark/random_interleaving.html
- https://google.github.io/benchmark/reducing_variance.html
- https://llvm.org/docs/Benchmarking.html
- https://www.spec.org/cpu2017/Docs/runrules.html

## Reproduce

Build the task's Reverie release artifacts, SaBRe loader, and patched `runsc`,
then choose a genuinely idle logical CPU:

```bash
cd /home/newton/work/dev-hermit
CRITERION_HOME=/tmp/criterion-syscall-v3 \
SYSCALL_BENCH_CPU=18 \
./experiments/gvisor-reverie-benchmark_20260725/v3-criterion/run.sh \
  ./experiments/gvisor-reverie-benchmark_20260725/v3-results
```

The runner intentionally fails instead of weakening its idle or all-backend
gates. See `../v3-results/REPORT.md` for the completed run.
