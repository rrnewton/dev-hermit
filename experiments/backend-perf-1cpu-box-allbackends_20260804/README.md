# All-backend single-core CPU-time instrumentation cost (fills the DBI/SaBRe/e9patch box gap)

**Date:** 2026-08-04 · **Lane:** hermit-perf · **Task:** `backend-rb-readiness-assessment` (single-core CPU-time extension)

## Why this run exists

The prior 1-CPU-box run (`experiments/liteinst-perf-1cpu-box-confirmation_20260804/`,
parent `000946b`) boxed only **liteinst / ptrace / kvm**. DBI, SaBRe, and e9patch
had only the *unboxed* (under-load) paired-slope ranking from the landed counter2
shootout. This run puts **all backends on the contamination-proof single-core
instrument in one session**, so the previously-unboxed three are directly
comparable to the known-clean ptrace/liteinst anchors.

## Box mechanism (answers the "fixed core / contention" concern)

- **Mechanism: `sched_setaffinity`** onto a **discovered least-busy free core**, via
  `scratch/run-on-k-free-cores.py 1` (reads the allowed set, samples `/proc/stat`
  idle fractions 0.3s, pins the whole tree to the most-idle allowed core).
- **NOT `systemd-run --property=AllowedCPUs=<n>`.** `AllowedCPUs` takes a CPU *set*,
  not a count — `AllowedCPUs=1` means CPU **#1 specifically**, so two agents using
  it both land on CPU #1 and contend while believing they are isolated. The
  discovered-free-core recipe avoids that by picking dynamically.
- **Not a fixed index / dynamic per run:** this run picked **core 101**; the prior
  anchor run picked **core 9**.
- **Honest gap: the recipe is stateless — no cross-run allocator.** No lock, no
  state file, no systemd; it only reads `/proc/stat`. Two invocations that sample
  simultaneously before either warms up could collide on the same "least-busy"
  core. Mitigation is only that an already-active box lowers that core's idle
  fraction, so a later starter avoids it. A future runner feature should track
  allocation (or use a delegated cpuset partition where available).

## Contamination guard (why these numbers stand)

The **ptrace anchor** is the tell — the out-of-process backend most sensitive to
core-sharing (the core-0 artifact inflated ptrace 10x to 22.6x). This run's ptrace
marginal = **14,018 ns/syscall vs the prior known-clean core-9 run's 14,032**
(0.1% apart); liteinst geomean 1.027x vs prior 1.030x. Had core 101 been shared,
ptrace would have blown up — it didn't. Core 101 was uncontended during this run.

## Provenance caveat (state it for any future clean-box comparison)

Measured while **6 orphaned `hermit` processes** (ppid=1, 99.9% CPU, ~4.1h
elapsed) consumed 6 cores continuously — detcore_misc **livelock victims**
(reverie#355; symptoms not causes, correctly not killed). Single-core CPU-time
(everything serializes on the boxed core; wall ≈ CPU-time there) is
contamination-proof against this. **No wall-clock figure was taken** — it would be
doubly contaminated.

## Identity

- Host: `devbig014.atn7.facebook.com`, AMD EPYC 9D85, 316 logical CPUs
- Reverie: `bfea4d5aa7d662cacf21f41ff2df5b60925dff2d` (main; landed shootout
  `a9f25aa7` is an ancestor)
- Harness: `reverie/benchmarks/counter2-shootout/run.py`, **release**, seed 1,
  `--target-seconds 3 --repetitions 3 --warmups 1`, boxed core 101
- Fixtures (single-threaded, direct x86-64 syscall sites in the main ELF):
  - `counter2-cpu-heavy` — one `getpid` per 65,536 iters (~16.9k calls)
  - `counter2-syscall-mix` — one `getpid` per 4,096 iters (~269.6k calls)
- Correctness gate (harness-enforced before timing): every cell exited 0, matched
  native stdout, emitted a nonzero exact-counter2 total.

## Result — INSTRUMENTATION cost (boxed K=1, core 101), native-normalized

| Backend | Geomean slowdown | cpu-heavy | syscall-mix | Marginal ns/syscall | Mechanism |
| --- | ---: | ---: | ---: | ---: | --- |
| **liteinst** | **1.027x** | 1.003x | 1.052x | 582 | in-guest patch |
| **e9patch** | **1.044x** | 1.017x | 1.071x | 643 | AOT static rewrite (in-process) |
| **sabre** | **1.049x** | 1.023x | 1.076x | 623 | in-guest rewrite |
| ptrace | 1.569x | 1.086x | 2.266x | 14,018 | host round-trip |
| dbi | 5.549x | 5.428x | 5.674x | 2,896 | DynamoRIO translation |

Marginal ns/syscall = paired slope: `[(t_mix - t_cpuheavy)_backend - (t_mix -
t_cpuheavy)_native] / (calls_mix - calls_cpuheavy)` — compute cancels, isolating
per-syscall interception cost.

## Two caveats for reading the table

1. **DBI's cost is compute translation, not syscalls.** cpu-heavy 5.43x ≈
   syscall-mix 5.67x — nearly flat across 16x more syscalls — so its penalty is
   per-branch DynamoRIO translation, not interception. Confirms the prior "~11x
   slower on branch-bound compute, structural" finding. Its low marginal
   per-syscall does **not** rescue a compile-heavy reproducible build.
2. **These are plain-tool interception-mechanism costs, NOT `--strict` Detcore
   costs.** The deterministic path adds the ~14µs/syscall coordinator RPC on top
   (see `liteinst-perf-fastpath-is-leader-not-broken`). The e9patch 1.044x is the
   AOT patch fastpath in-process, **not** the hermit e9patch runtime (which maps
   to ptrace for determinism).

## Bottom line

The patching backends (liteinst ≈ e9patch ≈ sabre, all within ~5% of native) are
the interception-mechanism perf-leader tier; ptrace is ~1.57x; DBI is a separate,
compute-bound 5.5x. Ranking preserved and now contamination-controlled for all six
backends on one instrument. For RB-perf-graduation the mechanism cost favors a
patching backend, but the deciding factor remains process-model coverage
(fork/exec/wait/threads) — the `--strict` RPC and DBI's compute tax are the real
levers, not the patch.

## Files

- `results.csv` — the summary table above
- `raw/summary.csv`, `raw/overall.csv` — per-cell medians / geomeans (harness)
- `raw/boxed-k1.json/` — full harness output (samples.jsonl, probes.jsonl, report.md, metadata.json)
- `metadata.json` — experiment identity block
- `ignored/` — build + run logs (not committed)
