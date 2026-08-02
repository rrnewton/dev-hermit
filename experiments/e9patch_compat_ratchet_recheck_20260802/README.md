# e9patch compat ratchet re-check @ e8ddd925 — 6 of 7 "inherent" gaps flipped green

## Question

Last cycle's artifact `e9patch_compat_last_cells_rootcause_20260801` concluded the
e9patch compat ratchet was **SATURATED** at parity 172/179, with all 7 residual
gap cells **inherent to the backend** (1 instruction-relocation + 6 e9loader-
prologue value shifts). That conclusion was drawn from the full-corpus scorecard
at hermit **`82a8e853`** plus source-reading attribution — **not** a direct
re-measurement. Between `82a8e853` and current main **`e8ddd925`**, 37 commits
landed, including **`ee746bde` "Stabilize stdio inode identity across backends."**
Does the "7 inherent gaps" claim still hold at `e8ddd925`?

**It does not.** 6 of the 7 flipped to parity-green + L2-deterministic. This
artifact records the corrected measurement and **retracts** the SATURATED claim.

## Method

Built a featured hermit at `e8ddd925` (`--features e9patch`, external
`CARGO_TARGET_DIR`, no mutation of the primary checkout). For each of the 7
previously-flagged cells, ran the **real corpus guest** (exact C sources / exact
`.sh --run` scripts) under ptrace and e9patch with the **exact portable-lane
sweep flags** `collect-fullcorpus.sh` uses:

```
hermit [--backend e9patch] run --strict --no-virtualize-cpuid --max-timeslice=disabled -- <guest>
```

- **parity** = e9patch plain-`--strict` stdout == ptrace plain-`--strict` stdout
  (byte-compare; `proc-fd-link-aliases` piped so fd 1 is a pipe, the real case).
- **det** = e9patch `--strict --verify` exit code (0 = L2 DETLOG self-verify).

```bash
BIN=scratch/featured-e8ddd925-target/release/hermit          # built at e8ddd925
export HERMIT_E9TOOL=worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=worktrees/e9patch/reverie/third-party/e9patch/e9patch
# per-cell: run ptrace + e9patch with the flags above, diff stdout, capture --verify exit
```

## Results

| cell | parity | e9 det (--verify) | ptrace bytes | e9 bytes | disposition |
|---|---|---|---|---|---|
| `c-programs/rcx-canonicalization` | **DIVERGE** | **1 (fail)** | 48 | 0 | **inherent** (instruction relocation) |
| `c-programs/print-memaddrs` | GREEN | 0 | 99 | 99 | **flipped green** |
| `c-programs/proc-fd-link-aliases` | GREEN | 0 | 124 | 124 | **flipped green** (via `ee746bde`) |
| `system-utils/date-nanoseconds` | GREEN | 0 | 29 | 29 | **flipped green** |
| `language-runtimes/bash-loop-pipe-time` | GREEN | 0 | 244 | 244 | **flipped green** |
| `language-runtimes/perl-io-subprocess-time` | GREEN | 0 | 169 | 169 | **flipped green** |
| `language-runtimes/python-io-subprocess-time` | GREEN | 0 | 177 | 177 | **flipped green** |

**6 of 7 flipped to parity-green + L2-det; 1 remains inherent.**

## Interpretation

- **`proc-fd-link-aliases` — confirmed cause `ee746bde`.** The diff adds
  `deterministic_stdio_inode(fd)` (`detcore/src/syscalls.rs`) returning a fixed
  `DET_SPECIAL_INODE_OFFSET + fd` for stdio, applied on the `/proc/self/fd`
  `readlink` path (`detcore/src/syscalls/namespace.rs`) and on `fstat`
  (`files.rs`). fd 1 now reports `pipe:[1001]` regardless of how many files the
  backend loader opened before `_start`, so the e9loader-prologue's virtual-inode
  shift no longer surfaces. Measured `pipe:[1001]` byte-identical on both backends.
- **The 4 time cells + `print-memaddrs` — my earlier attribution was stale.** At
  `e8ddd925` the virtual clock is **backend-independent**: a single
  `clock_gettime(CLOCK_REALTIME)`, `date +%s_%N`, and a fork+exec subprocess
  duration are all byte-identical across ptrace and e9patch. The stack/heap `%p`
  addresses are identical too. So the "e9loader prologue advances the virtual
  clock / shifts absolute addresses" mechanism I inferred from source last cycle
  does **not** produce a cross-backend divergence at this SHA. (The earlier
  classification was an inference over `82a8e853` scorecard data, not a direct
  measurement — the lesson is to measure the cell, not rationalize a flag.)
- **`rcx-canonicalization` — genuinely inherent.** The guest reads its own SYSRET
  `%rcx` (the return RIP after `syscall`) and asserts it equals a lexically
  adjacent `leaq 1f(%rip)` label. e9tool **must** relocate the in-ELF `syscall`
  into a trampoline to intercept it, so the return RIP is in the trampoline, not
  the inline label → the guest exits 1 (e9 emits 0 bytes; `--verify` exit 1). No
  interception backend that relocates the syscall can pass this; it is the single
  true "impossible by construction" cell.

## Consequences

- **The "SATURATED at 172/179, 7 inherent" conclusion is RETRACTED.** Only
  `rcx-canonicalization` is a confirmed inherent e9patch parity/determinism gap.
- **No e9patch product PR is warranted for the flips** — they were fixed by
  landed detcore changes (`ee746bde` + the clock-determinization already in main).
  Nor for `rcx-canonicalization` — it is unfixable without abandoning syscall
  interception.
- **The full-corpus e9patch column is stale.** Only these 7 cells were
  re-measured. The 200-cell e9patch column in `REPORT.md` remains at `82a8e853`
  and needs a fresh `collect-fullcorpus.sh --backends e9patch` sweep at `e8ddd925`
  for an authoritative new total (other cells may have moved in either direction
  across the 37 commits).
- **e9patch is preprocessing + a plain ptrace runtime**, so these are all detcore
  determinization wins that e9patch inherits for free — consistent with "e9patch
  tracks ptrace because the runtime *is* ptrace."

## Provenance

- Hermit `e8ddd925` (origin/main), built `--features e9patch` into
  `scratch/featured-e8ddd925-target`. e9tool/e9patch AOT binaries from
  `worktrees/e9patch/reverie/third-party/e9patch/`.
- Host: 316-core devbig, release hermit binary. Guests compiled/located under
  `/var/tmp` (never host `/tmp`).
- `results.csv` holds the 7-cell measurement.
- Supersedes: `experiments/e9patch_compat_last_cells_rootcause_20260801/`
  (correct method, but its conclusion is stale as of `ee746bde` + clock fixes).
