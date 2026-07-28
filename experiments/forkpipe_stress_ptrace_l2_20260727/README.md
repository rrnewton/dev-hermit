# Ptrace compat — fork+pipe stress test is L2-deterministic (run mode)

- **Date:** 2026-07-27
- **Task:** ptrace compat — hard programs. Write a fork+pipe stress test in C,
  run it under `--strict --verify`, debug from `--log info`.
- **Result:** **PASS at L2** (`hermit run --strict --verify`, bitwise-identical
  repeat), **ptrace** backend, **relaxations: none**, **run** mode.

## Why this is a "hard program"

Concurrent pipe I/O with high fork volume is a classic determinism stress
point for Hermit:

- prior notes: *record hangs on concurrent pipes*, *record hangs on concurrent
  multi-process*, *fork-heavy workloads fail L2 (cmake configure times out)*,
  *vfork nondet = BlockingExternalIO race*.

So this program deliberately combines all three pressures: many `fork()`s, many
concurrent pipe writers fanning in to one `poll()`-driven reader, and **output
that depends on the interleaving** — not just an order-independent checksum.

## The test (`forkpipe_stress.c`)

- Parent creates **16 children**, each with its own pipe.
- Each child writes **64 records × 4 bytes** (256 bytes of value `id+1`) into its
  pipe, then `_exit(id & 0x7f)`.
- Parent `poll()`s all 16 read ends and drains them, and for **every arriving
  read** appends the source child id to:
  - `CHECKSUM` — order-**independent** sum of all bytes (sanity value).
  - `ORDERDIG` — order-**dependent** FNV rolling hash of the arrival sequence.
  - `ORDERHEAD` — the first 64 arrivals, literally.
  - `STATUSHASH` — FNV over the 16 reaped child exit codes.

If the scheduler were racy, `ORDERDIG`/`ORDERHEAD`/`ARRIVALS` would perturb
between runs even while `CHECKSUM` stayed constant. That makes the test able to
*detect* nondeterminism, not just tolerate it.

Build: `gcc -O2 -static -fno-pie -no-pie -o forkpipe_stress forkpipe_stress.c`

## Results

`hermit run --strict --verify -- ./forkpipe_stress` (see `verify.err`):

```
:: Run1... / :: Run2... / :: Comparing logs...
:: Success: deterministic. Determinism verified.
```

Two **independent** `hermit run --strict` invocations are also byte-identical
(`strictA.out` == `strictB.out`), including the order-sensitive fields:

| field | native | hermit (both strict runs) |
| --- | --- | --- |
| CHECKSUM (order-independent) | 34816 | 34816 |
| ARRIVALS (scheduling-sensitive) | 17 (varies) | **63 (stable)** |
| ORDERDIG (order-dependent hash) | varies | **11853468550816639891 (stable)** |
| ORDERHEAD | varies | **`1 2 3 … 16 1 2 … 16 …` (round-robin)** |
| STATUSHASH | 615731795747517251 | 615731795747517251 |

`CHECKSUM 34816` = `256 × (1+2+…+16)` = `256 × 136`, confirming no data loss.

## Debugging from `--log info`

`hermit --log info run --strict -- ./forkpipe_stress` (excerpt in
`info_excerpt.log`) shows precisely how Hermit determinizes it:

- `DETLOG SCHEDRAND: seeding scheduler runqueue with seed 0` and
  `DETLOG CHAOSRAND: seeding chaos scheduler with seed 0` — deterministic
  scheduler seeding.
- **1192** `COMMIT turn N, dettid …` lines — the scheduler serializes all
  threads onto one logical CPU, each turn stamped with deterministic virtual
  time starting at the epoch `1_767_225_600.000_000_000s` (2026-01-01 UTC) and
  advancing in fixed increments.
- `clone(CloneFlags(0x1200011), …) = Ok(5/7/9/…)` — 16 forks returning
  **virtualized, deterministic PIDs** (plain `fork()`/`SIGCHLD`, not `vfork`, so
  it dodges the vfork BlockingExternalIO race).
- `pipe(…) = Ok(0)` ×16 — deterministic pipe fds.
- The parent's fan-in `poll()` appears as the scheduler resource
  `InternalIOPolling: W`, committed in a fixed order — this is what turns the
  natively-racy arrival order into the stable `1..16` round-robin above.

Syscall counts in the DETLOG: clone 16, pipe 16, poll 5, wait4 16.

## Interpretation

A high-fork-volume concurrent-pipe fan-in — one of the historically-hard cases
— is **L2-deterministic in run mode on ptrace**, and the determinism is *real*
(the order-dependent digest is stable), not an artifact of an order-insensitive
checksum. This is a positive counterpoint to the fork/pipe **record-mode**
hangs: the failure mode there is specific to record finalization, not to
strict-mode `run`/`verify`.

## Scope / caveats

- **ptrace, run mode only.** `record --verify` is separately known to hang on
  concurrent pipes and was not retested here.
- Uses `fork()` (clone + SIGCHLD), not `vfork()`.
- KVM/DBI parity not claimed.

## Reproduction

```bash
cd ~/work/dev-hermit/scratch/forkpipe-stress-20260727   # or copy forkpipe_stress.c anywhere
gcc -O2 -static -fno-pie -no-pie -o forkpipe_stress forkpipe_stress.c
HERMIT=~/work/dev-hermit/worktrees/275/hermit/target/release/hermit
"$HERMIT" run --strict --verify -- ./forkpipe_stress          # L2
"$HERMIT" --log info run --strict -- ./forkpipe_stress        # debug event stream
```

See `metadata.json` for SHAs/host.
