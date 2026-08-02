# DBI perf-leader baseline (2026-08-01)

## Question

The DBI (DynamoRIO) backend is the perf leader among Hermit's backends. This
experiment puts numbers on *why*: it isolates the two costs that matter for a
syscall-interception engine — the **per-intercepted-syscall** cost and the
**fixed startup tax** — and compares DBI against the golden ptrace reference on
the same release binary. The goal is a documented baseline so the lead is
defensible and a future regression is detectable.

## Method

- Single release binary: `target/release/hermit` built with
  `--features dbi --release`. Both `--backend ptrace` and `--backend dbi` use
  this same binary, so the comparison is backend-only.
- Portable determinism profile:
  `--strict --no-virtualize-cpuid --max-timeslice=disabled`.
- `min-of-3` wall-clock via `/usr/bin/time -o FILE -f %e`, guest output to
  `/dev/null`. The host is shared (~316 cores, load ~61-93 during the run), so
  the **ratio** between backends is the trustworthy signal, not the absolute
  numbers. Min-of-N suppresses contention spikes.
- Two workloads:
  - **startup tax**: `/bin/true` — fixed backend attach/teardown with
    essentially no interesting guest syscalls.
  - **per-syscall**: `src/syscall_loop.c` — a tight loop of raw
    `syscall(SYS_getpid)` (`100000` iterations); every iteration crosses the
    backend's interception path.

Exact SHAs, host facts, and the CPU-bound caveat are in `metadata.json`; raw
numbers are in `results.csv`.

## Results

| workload            | native | ptrace | dbi   | DBI vs ptrace |
|---------------------|--------|--------|-------|---------------|
| `/bin/true` startup | 0.00 s | 0.01 s | 0.05 s| +~40 ms tax   |
| `getpid` × 100 000  | 0.00 s | 3.30 s | 0.20 s| **~16.5× faster** |

Per-intercepted-syscall cost:

- **ptrace**: 3.30 s / 1e5 ≈ **33 µs/syscall** — dominated by the two
  context switches of a ptrace stop per syscall.
- **dbi**: 0.20 s / 1e5 ≈ **2.0 µs/syscall** — in-process interception with no
  tracer round-trip.

Native `getpid` × 1e5 completes below `/usr/bin/time`'s 10 ms resolution
(sub-millisecond), so the native cell reads 0.00 s; it is shown only to confirm
the loop itself is not the bottleneck.

## Interpretation

- **DBI's lead is per-syscall, and it is large.** At ~16.5× on a syscall-bound
  workload, DBI's advantage grows with syscall density. This is the structural
  win of in-process interception over ptrace's stop-per-syscall model, and it is
  the property to protect.
- **DBI pays a fixed startup tax (~40 ms)** to bring up DynamoRIO and translate
  the initial code. For very short-lived, syscall-light guests, ptrace can still
  finish first; the crossover is roughly where the guest issues more than
  ~1–2k intercepted syscalls (40 ms tax ÷ ~31 µs/syscall saved). Long-running or
  syscall-heavy guests are firmly DBI's territory.
- **Regression watch.** A future change that pushes per-syscall DBI cost much
  above ~2 µs, or the startup tax well past ~40 ms, would erode the lead. The
  `dbi_backend_stats_provider` work (surfacing `branches`/`syscalls`/`rewritten`
  counters) gives a per-run, in-band signal to correlate with these wall-clock
  numbers.

## Reproduction

```bash
# In a slot with the DBI toolchain env sourced (.env.dbt.slot):
gcc -O2 -o src/syscall_loop src/syscall_loop.c
BIN=.../target/release/hermit          # built --features dbi --release
PROF="--strict --no-virtualize-cpuid --max-timeslice=disabled"

# per-syscall
/usr/bin/time -f %e $BIN run --backend ptrace $PROF -- src/syscall_loop 100000
/usr/bin/time -f %e $BIN run --backend dbi    $PROF -- src/syscall_loop 100000
# startup tax
/usr/bin/time -f %e $BIN run --backend ptrace $PROF -- /bin/true
/usr/bin/time -f %e $BIN run --backend dbi    $PROF -- /bin/true
```

Take the min of several runs on a shared host and compare the ratio.
