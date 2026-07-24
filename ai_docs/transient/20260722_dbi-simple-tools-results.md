# DBI simple tools — implementation results (syscall counter + strace)

Status: implemented + validated. Author: agent `hermit-069`
(task `impl-dbi-simple-tools`). Builds on the interface audit in
`20260722_dbi-guest-trait-audit.md`.

## TL;DR

Two Reverie tools now run on the DynamoRIO (DBI) backend as proper
`impl Tool` types, dispatched through a real `DbiGuest`, and validated against
the `strace` baseline:

1. **`SyscallCounterTool`** — counts every syscall by number, prints a histogram
   at exit.
2. **`StraceTool`** — logs every syscall's name, decoded args, and **real return
   value** (via `guest.inject`).

This answers the parent question — *does the DBI Guest implement enough of the
`Guest` trait for simple tools to work?* — with **yes**, empirically.

## Where the code is

- Worktree: `worktrees_reverie/slot80` (reverie), branch
  `impl-dbi-simple-tools-slot80`, based at rev `69f47d9` (the known-good client
  rev; `e3e2c965`'s client SIGSEGVs on dynamic ELFs — see memory
  `dbi-client-rev-e3e2c965-broken`).
- New file: `reverie-dbi/src/tools.rs` — the two `impl Tool` types + the
  env-driven dispatcher `run_active_tool`.
- `reverie-dbi/src/lib.rs` — driver (`reverie_dbi_runtime_pre_syscall`) now calls
  `tools::run_active_tool` first; if an observation tool is active it handles the
  syscall via the standard `Tool` trait and supersedes the built-in policy.
- `reverie-dbi/native/client.c` — a 12-line addition: a `reverie_dbi_emit`
  callback (`dr_write_file(STDERR, …)`) threaded into `reverie_dbi_runtime_pre_syscall`,
  so tool output uses DynamoRIO's own I/O.

The tools use only implemented `Guest` methods: `tid`, `memory` (arg decode),
`thread_state`, and `inject` (execute + capture retval). They mirror
`reverie-examples/counter1.rs` and `strace_minimal.rs`, adapted to DBI:
`inject` instead of `tail_inject` (which panics on DBI), and a process-global
histogram instead of `GlobalState` RPC (DBI hardwires the global state to `()`).

## How to build and run

```bash
cd worktrees_reverie/slot80
export DYNAMORIO_HOME=/home/newton/work/dev-reverie/dynamorio   # source build
PROFILE=release bash reverie-dbi/scripts/build-client.sh        # RELEASE — see caveat
DRRUN=$DYNAMORIO_HOME/build/bin64/drrun
CLIENT=$PWD/target/reverie-dbi-native/libreverie_dbi_client.so

# syscall counter
HERMIT_DBI_SYSCALL_HISTOGRAM=1 "$DRRUN" -disable_rseq -c "$CLIENT" -- /bin/echo hi
# strace
HERMIT_DBI_STRACE=1            "$DRRUN" -disable_rseq -c "$CLIENT" -- /bin/echo hi
```

With no env var set, the client behaves exactly as before (built-in determinism
policy; `-summary` prints the branch/syscall totals).

## Validation (release; `/bin/echo hi`)

**Syscall counter** — `112 calls, 18 distinct`. Every count matches real
`strace -c`:

| syscall | DBI | strace -c | | syscall | DBI | strace -c |
|---|---|---|---|---|---|---|
| openat | 30 | 30 | | brk | 3 | 3 |
| mmap | 21 | 21 | | arch_prctl | 2 | 2 |
| close | 19 | 19 | | write | 1 | 1 |
| fstat | 18 | 18 | | munmap | 1 | 1 |
| pread64 | 4 | 4 | | access/futex/getrandom/… | 1 | 1 |
| read | 3 | 3 | | rseq/set_tid_address/set_robust_list | 1 | 1 |
| mprotect | 3 | 3 | | | | |

Fully-explained differences: `execve` (strace 1) is not seen because DynamoRIO
attaches *after* the guest is exec'd; `prlimit64` (strace 1) is consumed by the
client's C resource-virtualization *before* the Rust hook; `exit_group` (DBI 1)
is counted by DBI. Arithmetic: `113 − execve − prlimit64 + exit_group = 112`.

**Strace** — 112 lines, exit 0, no crash, with real return values:

```
[dbi strace pid …] openat(-100, … -> "/etc/ld.so.cache", OFlag(O_CLOEXEC)) = 3
[dbi strace pid …] read(3, 0x…, 832) = 832
[dbi strace pid …] arch_prctl(12289, 0x…) = -1 (EINVAL)
[dbi strace pid …] write(1, 0x…, 3) = 3
[dbi strace pid …] exit_group(0) = ?
```

This is strictly richer than upstream `strace_minimal`, which prints `= ?`
(it uses `tail_inject` and never sees the retval).

Also verified: both flags together on `/bin/ls /etc/hostname` (158 calls, 22
distinct, `openat 34` matches strace; strace emits 158 lines). Baseline
unchanged (`syscalls=112`). `cargo fmt`/`clippy` clean for `reverie-dbi`;
10/10 unit tests pass.

## Two findings worth keeping

1. **The DBI client must be RELEASE-built.** A debug build's large, un-optimized
   stack frames overflow DynamoRIO's ~56K client stack — even the baseline
   crashes. `PROFILE=release` is required, not optional.
2. **Tool output must use DynamoRIO I/O, not fd 2.** `eprintln!` (a) re-enters
   the syscall-interception path via its own `write(2)`, and (b) fails once the
   guest closes its stderr before exit. Routing output through a
   `dr_write_file(STDERR, …)` callback fixes both. (The counter also prints its
   histogram from the guest's `exit_group` prehook, on the ample app stack, not
   from the DR exit callback which runs on the tiny client stack.)

## Status of the remaining two tools in the progression

- **Time-pinning (clock_gettime/gettimeofday):** feasible via the same
  `guest.memory().write_value` + suppress pattern already used for
  `sysinfo`/`getrusage`. Not implemented here because the dominant path is the
  **vDSO**, which serves these without a syscall, so a syscall-only tool won't
  see the common case — a vehicle-level gap (vDSO neutralization), not a
  `Guest`-trait gap.
- **CPUID (via the Tool trait):** not possible today. CPUID is trapped and
  emulated entirely in C (`deterministic_cpuid` in `client.c`); `handle_cpuid_event`
  is never dispatched and `has_cpuid_interception()` returns `false`. A
  trait-based CPUID tool needs a new C→Rust dispatch at the existing
  `rewrite_cpuid`/`emulate_cpuid` site — the first tool that forces work beyond
  `handle_syscall_event`.

## SCM state

Changes are committed nowhere (the task did not authorize commits). They live
uncommitted in the isolated worktree `worktrees_reverie/slot80` on branch
`impl-dbi-simple-tools-slot80`. Files touched: `reverie-dbi/src/tools.rs` (new),
`reverie-dbi/src/lib.rs`, `reverie-dbi/native/client.c`. Rebuild with the
command block above.
