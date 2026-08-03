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
- Three workloads:
  - **startup tax**: `/bin/true` — fixed backend attach/teardown with
    essentially no interesting guest syscalls.
  - **per-syscall**: `src/syscall_loop.c` — a tight loop of raw
    `syscall(SYS_getpid)` (`100000` iterations); every iteration crosses the
    backend's interception path.
  - **branch-bound**: `src/branch_heavy.c` — sums Collatz step counts over
    `1..2000000` (~745M counted branches, ~37 syscalls). Syscall-free hot loop;
    isolates DBI's per-branch instrumentation from its per-syscall cost.

Exact SHAs, host facts, and the CPU-bound caveat are in `metadata.json`; raw
numbers are in `results.csv`.

## Results

| workload            | native | ptrace | dbi   | DBI vs ptrace |
|---------------------|--------|--------|-------|---------------|
| `/bin/true` startup | 0.00 s | 0.01 s | 0.05 s| +~40 ms tax   |
| `getpid` × 100 000  | 0.00 s | 3.30 s | 0.20 s| **~16.5× faster** |
| `branch_heavy` 2e6  | 0.48 s | 0.49 s | 5.35 s| **~11× slower** |

### The other side of the ledger: branch-bound code

The `getpid` win is the syscall-density regime. The `branch_heavy` row is the
opposite regime and it is where DBI *loses*: a syscall-free, branch-dense loop
runs at native speed under ptrace (ptrace only traps syscalls, so it is
transparent to pure compute) but **~11× slower under DBI**, because DynamoRIO
translates and dispatches every basic block through its code cache.

### A/B: is the per-branch counter the "dumb slowness"? No.

A prior hypothesis was that the unconditional, *locked* per-branch counter in
`reverie-dbi/native/client.c` (`drx_insert_counter_update(..., DRX_COUNTER_64BIT
| DRX_COUNTER_LOCK)` on every counted branch) was wasted work under DBI's
single-writer, cooperatively-serialized execution model, and that dropping the
`DRX_COUNTER_LOCK` would recover time. Three client `.so`s were built from the
same source differing only at the counter site, and selected at runtime with
`REVERIE_DBI_CLIENT`:

| variant   | counter site                              | branch_heavy 2e6 (min of 5) |
|-----------|-------------------------------------------|-----------------------------|
| baseline  | `DRX_COUNTER_64BIT \| DRX_COUNTER_LOCK`    | 5.35 s                      |
| patched   | `DRX_COUNTER_64BIT` (lock removed)        | 5.36 s                      |
| no-count  | counter update removed **entirely**       | 5.37 s                      |

**Removing the lock buys nothing, and removing the whole counter update buys
nothing** — all three are within run-to-run noise. On x86 an uncontended `lock
add` to a 64-byte-aligned, single-writer location keeps the line exclusive in the
writer's L1, so the lock prefix costs a handful of cycles, and even the entire
increment is negligible against DynamoRIO's per-branch dispatch. The counter is
*not* the bottleneck; the DBI execution model is. There is no cheap C-level win
here, and no `reverie-dbi` PR to remove the lock is warranted on perf grounds.
(The methodology first used a nonexistent `HERMIT_DBI_CLIENT` var, which the
launcher ignored, silently running the default client for all three variants; the
real knob is `REVERIE_DBI_CLIENT` — see `reverie-dbi/src/launcher.rs`
`CLIENT_ENV`.)

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
- **DBI loses on branch-bound compute (~11×).** The advantage is entirely a
  function of syscall density: replace syscalls with pure computation and the
  in-process translation cost that DBI pays on every basic block, from which
  ptrace is exempt, dominates. This is structural to DynamoRIO, not a fixable
  inefficiency in the reverie-dbi client (the A/B above rules out the counter),
  so the lever for DBI on compute-heavy guests is reducing *translated* work
  (code-cache reuse, trace formation), not shaving the counter.
- **Regression watch.** A future change that pushes per-syscall DBI cost much
  above ~2 µs, or the startup tax well past ~40 ms, would erode the lead. The
  `dbi_backend_stats_provider` work (surfacing `branches`/`syscalls`/`rewritten`
  counters) gives a per-run, in-band signal to correlate with these wall-clock
  numbers.

## Reproduction

```bash
# In a slot with the DBI toolchain env sourced (.env.dbt.slot):
gcc -O2 -o src/syscall_loop src/syscall_loop.c
gcc -O2 -o src/branch_heavy src/branch_heavy.c
BIN=.../target/release/hermit          # built --features dbi --release
PROF="--strict --no-virtualize-cpuid --max-timeslice=disabled"

# per-syscall (DBI wins ~16.5x)
/usr/bin/time -f %e $BIN run --backend ptrace $PROF -- src/syscall_loop 100000
/usr/bin/time -f %e $BIN run --backend dbi    $PROF -- src/syscall_loop 100000
# branch-bound (DBI loses ~11x)
/usr/bin/time -f %e $BIN run --backend ptrace $PROF -- src/branch_heavy 2000000
/usr/bin/time -f %e $BIN run --backend dbi    $PROF -- src/branch_heavy 2000000
# startup tax
/usr/bin/time -f %e $BIN run --backend ptrace $PROF -- /bin/true
/usr/bin/time -f %e $BIN run --backend dbi    $PROF -- /bin/true
```

Take the min of several runs on a shared host and compare the ratio.

### A/B counter variants (the negative result)

Build three clients from the same source differing only at the counter site
(`client.c` ~line 815), and select each at runtime with `REVERIE_DBI_CLIENT`
(the launcher's `CLIENT_ENV`; note it is *not* `HERMIT_DBI_CLIENT`):

- **baseline**: `DRX_COUNTER_64BIT | DRX_COUNTER_LOCK` (upstream).
- **patched**: `DRX_COUNTER_64BIT` (lock dropped).
- **no-count**: replace the `drx_insert_counter_update(...)` block with a no-op
  (diagnostic only — not determinism-valid; branch clock stops advancing in C).

Compile each with the hermit CMake wrapper
(`hermit-install/native-client/CMakeLists.txt`) passing `-DDynamoRIO_DIR`,
`-DREVERIE_DBI_NATIVE_SOURCE=<variant>.c`, `-DHERMIT_RESOURCE_DIR=<out>`, then:

```bash
REVERIE_DBI_CLIENT=<out>/libreverie_dbi_client.so \
  /usr/bin/time -f %e $BIN run --backend dbi $PROF -- src/branch_heavy 2000000
```

All three land at ~5.35 s: the counter and its lock are not the cost.
