# LiteInst instrumentation-side stats — is the 14.5x per-syscall ptrace, from the patch-site side?

**Task:** `liteinst-instrumentation-stats` (owner hermit-liteinst, research-only).
**Date:** 2026-08-04. **Host:** devbig014 (316 CPU, kernel 6.18.39).

## Question

hermit-perf attributed LiteInst's "14.5x slowdown" to **per-syscall ptrace host
round-trips** — a **timing** argument (ns/call, instruction counts). This experiment
attacks the same claim from the **instrumentation** side: patch-site accounting, not a
stopwatch. Two independent methods agreeing is a measurement; one is a claim. If they
disagree, the disagreement is the finding.

## Method

- **Binary:** hermit `rrnewton/hermit#1451` @ `02a47ef718b4fdb40378654d0dd75e4f4d773ae6`
  (DEBUG build; the reported stats are **exact integer event counters** — properties of
  the guest's patch sites and dispatch paths, independent of build profile and of host
  load; debug was chosen only for fast compile).
- **Backend:** `liteinst` (in-guest Detcore Tool; the ptrace supervisor is
  lifecycle-only, `TracerBuilder<()>` with **zero** host syscall subscriptions).
- **reverie pin** `3eda4286…` (stats-API source behavior; landed on reverie main as
  squash `9e7af7df…` via reverie PR #362). **liteinst2 pin** `95ee5e69…`.
- **Stats emission:** `RUST_LOG=hermit::backend_stats=info` (INFO for that target only;
  `--log info` floods DETLOG). Report fires once at run end,
  `hermit-cli/src/backend_stats.rs:37 report()` from `hermit-cli/src/lib.rs:1538-1556`.
- **Runtime staged** via `scripts/stage-liteinst-runtime.sh dev`.
- **Runs:** 3 per config (2 for python3 and for strict_verify). **Every repeat was
  bitwise-identical** across counts — the counters are deterministic.

### Workloads (guest + exact command)

| guest | workload | command | regime |
|---|---|---|---|
| `syscall_loop` | getpid N∈{1e4,1e5,1e6} | `syscall_loop` (gcc -O2, `dbi_perf_leader_baseline_20260801/src`); tight `syscall(SYS_getpid)` loop | per-syscall |
| `branch_heavy` | collatz 2e6 | `branch_heavy 2000000` (gcc -O2); ~745M branches, ~37 syscalls | compute-bound / syscall-sparse |
| `bin_echo` | hello | `/bin/echo hello` | short real binary |
| `bin_ls` | slash | `/bin/ls /` | short real binary |
| `python3` | print sum | `/usr/bin/python3 -c 'print(sum(range(1000)))'` | interpreter |

Modes: `run` = `hermit run --backend liteinst` (L1); `strict_verify` =
`--strict --verify` (L2).

## Results (see `results.csv`, raw in `raw-stats.txt`)

Every number below is qualified by (guest, workload, mode, runs) in `results.csv`.

- **syscall_loop getpid, mode=run:** `distinct_rips_patched=2` **CONSTANT** at
  N=10k / 100k / 1M, while `direct_hook = N+1` (10001 / 100001 / 1000001) and
  **`ptrace_installation=0`** at every N. → Patch work is one-time, O(#sites),
  invocation-**independent**; per-invocation dispatch is **100% in-guest** (`direct_hook`)
  with **zero** ptrace host round-trips.
- **strict_verify N=100k:** counts **identical** to `run` (distinct=2, direct_hook=100001,
  ptrace_installation=0). → `--strict` adds **no instrumentation**; its cost is elsewhere
  (Detcore coordinator RPC — corroborates the timing lane).
- **branch_heavy 2e6** (~745M branches, ~37 syscalls): `distinct=4`, `direct_hook=5`,
  `ptrace_installation=0`. → LiteInst patches **syscalls only**, no per-branch cost
  (opposite of DBI whole-program translation).
- **bin_echo:** distinct=5, direct_hook=7. **bin_ls:** distinct=7, direct_hook=11.
  **python3:** distinct=6, direct_hook=111, unpatchable_or_other=49. `ptrace_installation=0`
  for all.
- **All workloads:** `cacheline_straddlers=0`; every patch site `instruction_lengths[2]`
  only (2-byte, `0F 05` = `syscall` opcode); `straddle_prefix[*]=0`. → The **straddler
  ptrace-bail slow path is never exercised** on these workloads — even the fallback that
  *would* cost host round-trips does not fire.

## Interpretation — the two methods AGREE

hermit-perf's timing lane (memory `liteinst-perf-fastpath-is-leader-not-broken`) found the
genuine 14.5x is the **retired legacy host hybrid** (SIGTRAP→ptrace host per syscall +
`/proc/maps` parse, >450µs/call), while the **current in-guest backend is the perf leader**
(0.58µs/call, 1.032x native). The blamed mechanism was per-syscall ptrace host round-trips.

The instrumentation lane confirms the same conclusion by a wholly different observable: on
the **current** in-guest backend, `ptrace_installation=0` and `direct_hook=N+1` on every
workload — including a 1,000,000-iteration getpid loop — while distinct patch sites stay
constant. The current backend **cannot** be paying per-syscall ptrace round-trips, because
it performs **zero** of them.

**Convergence:** the 14.5x belongs to the retired legacy host hybrid; the current in-guest
LiteInst backend pays no per-syscall ptrace host round-trips. The candidate disagreement —
"is the *current* backend the thing that's 14.5x?" — is refuted from **both** the timing and
the instrumentation side. This is a measurement, not a claim.

## Reproduction

```
# in a hermit worktree at 02a47ef7 (or rebased equivalent), backend=liteinst staged:
scripts/stage-liteinst-runtime.sh dev
cargo build -p hermit --bin hermit          # package is 'hermit', not 'hermit-cli'
RUST_LOG=hermit::backend_stats=info \
  ./target/debug/hermit run --backend liteinst -- <workload>
# stats line printed once at run end; see harness.sh for the full sweep.
```

See `harness.sh` for the exact sweep and `metadata.json` for full pins/field glossary.
