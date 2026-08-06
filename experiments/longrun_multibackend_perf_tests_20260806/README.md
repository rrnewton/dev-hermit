# Long-running, backend-portable perf test cases

**Task:** `add-long-running-multibackend-perf-tests` · **Date:** 2026-08-06
**Mode:** local authoring + native calibration. No hermit run, no validate, no egress.

## The baseline is worse than "N=2" — it is effectively N=0 portable

Measured from `compat-envelope/scorecard.csv` (618 rows): of **72** ptrace cells with a duration,
median **237 ms**, p90 **3815 ms**, only **16 > 1 s** and **3 > 5 s**. The three:

| ptrace ms | test | why it cannot serve a cross-backend ranking |
|---:|---|---|
| 56319 | `language-runtimes/python-io-subprocess-time` | **subprocess** (fork+exec) |
| 7826 | `determinism-stress/thread-contention` | **threads** |
| 5127 | `language-runtimes/bash-loop-pipe-time` | **fork + pipes** |

**Every existing long-running test is disqualified for the backends it would rank.** liteinst and
e9patch-direct are single-process/single-thread and fail closed on `clone/fork/vfork/execve`
(BACKENDS.md ground-truth audit, this session). So the long tail is exactly the set those backends
cannot run. The corpus is not merely thin at >5 s — it is thin *and* unportable.

**Design rule that follows:** a portable long-running case must be **single-process, single-thread,
no exec**. Both programs here are, by construction.

## The two cases, calibrated by measurement

| program | native wall | syscalls (measured) | predicted ptrace | axis |
|---|---:|---:|---:|---|
| `syscall_churn` | **1.75 s** | **288 040** (`strace -c`) | **~9.4 s** | syscall-dense |
| `compute_bound` | **5.71 s** | ~30 | **~5.7 s** | compute-dense **control** |

`syscall_churn` prediction = 288 040 × 26.4 µs/syscall + 1.75 s native, using the S1 measured ptrace
cost of 26 393.7 ns/syscall. **This is a prediction, not a measurement** — confirming it needs a
ptrace run, which is isolation-gated and out of scope here.

`compute_bound` clears 5 s **natively**, which is the point: ptrace taxes syscalls, not compute, so a
compute-bound case must already be long before interception. It is the **control**, and it is what
makes the pair a bracket rather than two samples:

- On `syscall_churn`, backends *should* separate — it is nearly pure interception cost.
- On `compute_bound`, backends *should not* separate. **A ranking that shows a large spread here is
  measuring something other than interception** (build variance, cgroup CPU quota, host noise) and
  the ranking should be rejected rather than reported.

Both are deterministic (fixed seeds, no clock/PID/env in output) so they are `--verify`-safe.

## Placement — shared manifest, ptrace top-of-funnel

Per the directive these go in the **shared e2e manifest**, not a backend-specific corpus:
`tests/e2e/manifests/c-programs.toml`, sources under `tests/c/`. Establish on ptrace first; other
backends are enabled per-cell only once each has its own measurement, exactly as the manifest
already does elsewhere.

```toml
[[test]]
name = "syscall_churn"
program = "tests/c/syscall_churn.c"
description = "Strict verification for tests/c/syscall_churn.c"
ci = false
backends_enabled = ["ptrace"]      # widen per-backend only with a per-cell measurement

[[test]]
name = "compute_bound"
program = "tests/c/compute_bound.c"
description = "Strict verification for tests/c/compute_bound.c"
ci = false
backends_enabled = ["ptrace"]
```

`ci = false` deliberately: these are ~6-10 s each and belong in the perf/occasional lane, not the
blocking PR gate. Adding ~16 s to every PR to serve a ranking that runs separately would be the
wrong trade — and the memory-cap audit this session showed the DAG is already admission-limited.

## Gaps and honesty

- **Neither has been run under hermit.** No validate was launched (concurrency rule). The ptrace
  numbers are predictions from a measured per-syscall cost.
- **Two cases is a bracket, not a ranking corpus.** They fix the *portability* defect and give one
  clean axis plus a control. A ranking wants more points along the syscall-density spectrum —
  natural next additions, all single-process: a SQLite bulk-insert-in-one-transaction, a
  compress/decompress round-trip, and a large-input `sort`/`sha256sum`.
- **`syscall_churn` uses `/tmp`.** Under a backend that virtualizes the filesystem differently this
  could shift cost; that is itself worth measuring, but it means the case is not purely CPU-portable.

## Reproduce

```bash
cd experiments/longrun_multibackend_perf_tests_20260806/src
gcc -O2 -o syscall_churn syscall_churn.c && /usr/bin/time -f "%es" ./syscall_churn
gcc -O2 -o compute_bound compute_bound.c && /usr/bin/time -f "%es" ./compute_bound
strace -c ./syscall_churn 2>&1 | tail -1     # syscall count
```
