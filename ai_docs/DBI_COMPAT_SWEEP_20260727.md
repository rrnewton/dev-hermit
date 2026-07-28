# DBI Backend Compatibility Sweep — 2026-07-27

Deep compat debugging of the Reverie **DBI** backend (DynamoRIO) driving Hermit's
Detcore tool. Goal: run Hermit's guest corpus under `--backend dbi`, categorize
every failure by root cause, and identify which are fixable in-lane (hermit /
detcore) versus cross-repo (reverie-dbi).

- **Backend:** DBI (DynamoRIO); Detcore tool compiled into release-built
  `libreverie_dbi_client.so`.
- **reverie-dbi pin:** `dd15e1df0da90fa38430cef62d09e7d070676439` (Cargo.lock).
- **Hermit worktree:** `worktrees/274/hermit`, branch `codex/dbi-compat-debug`
  (based on `origin/main`). No hermit source changes were warranted (see
  Conclusion).
- **Command form:** `hermit --backend dbi run --strict -- target/release/<guest>`
  (assurance floor L1; `--verify` not reached for failing guests).
- **Corpus:** 37 built guest bins in `tests/` (`hermit-cli`'s guest programs;
  `target/release/examples/` is empty — the corpus is `[[bin]]` targets, not
  `[[example]]`s). `standalone_stacktrace_events` is a driver, not a bare guest
  (see below).

## Result summary

| Bucket | Count | Root cause |
| --- | --- | --- |
| OK (L1) | 27 | run clean under `--backend dbi run --strict` |
| HANG (timeout 124) | 7 | no DBI preemption of non-yielding threads |
| FAIL (rc 101) | 3 | 1 real signal gap, 1 no-netns, 1 false positive |

Per-guest data: `/tmp/dbi-sweep.tsv` (this host). All 10 non-passing guests
trace to **reverie-dbi architectural gaps**, not to Detcore/hermit logic.

### OK (27)
chaos_cas_sequence_bin, chaos_hello_chaos, nanosleep_threads_nocrash_rust,
network_bind_rs, rustbin_bind_connect_race, rustbin_clock_gettime,
rustbin_futex_timeout, rustbin_futex_wait_child, rustbin_heap_ptrs,
rustbin_interrogate_tty, rustbin_landlock, rustbin_mem_race, rustbin_nanosleep,
rustbin_network_hello_world, rustbin_pipe_basics, rustbin_poll,
rustbin_print_clock_nanosleep_monotonic_abs_race,
rustbin_print_clock_nanosleep_monotonic_race,
rustbin_print_clock_nanosleep_realtime_abs_race, rustbin_print_nanosleep_race,
rustbin_rdtsc, rustbin_recvmmsg, rustbin_select, rustbin_shutdown,
rustbin_socketpair, rustbin_stack_ptr, rustbin_thread_random.

### HANG (7) — no preemption of non-yielding threads
chaos_keyvalue_bin, rustbin_clock_total_order, rustbin_exit_group,
rustbin_futex_and_print, rustbin_futex_wake_some, rustbin_poll_spin,
rustbin_sched_yield.

**Root cause.** The DBI backend has no RCB/PMU logical clock and no
timer-preemption (`Guest::set_timer` is effectively unsupported — see the
`adding-a-backend` skill: "back these with your clock source … if unsupported,
be explicit"). Detcore serializes guest threads onto one logical CPU and only
switches at scheduling points. A thread that busy-loops without issuing a
blocking/yielding syscall (`loop { print!("") }` in `exit_group.rs`; spin loops
in `poll_spin`, `futex_wake_some`, `sched_yield`) never yields, so Detcore's
sequentializing scheduler cannot preempt it and the run deadlocks. Under ptrace
these same guests are preempted by the RCB timer and pass.

**Fix owner:** reverie-dbi (add a preemption/clock source to `DbiGuest`).
Substantial; not an easy fix.

### FAIL (3)

1. **rustbin_tkill — `rt_tgsigqueueinfo` returns -1 (EPERM).** REAL gap.
   - ptrace baseline: **passes at L1** ("tkill + rt_tgsigqueueinfo +
     rt_sigqueueinfo delivery OK. Test complete.", exit 0).
   - DBI: panics at `tests/rust/tkill.rs:104`, `rt_tgsigqueueinfo returned -1`.
   - `strace -f -e trace=tkill,rt_tgsigqueueinfo` of the DBI run:
     ```
     tkill(888202, SIGUSR1)                                   = 0
     rt_tgsigqueueinfo(3, 3, SIGUSR1, {si_code=SI_QUEUE, ..}) = -1 EPERM
     ```
   - **Root cause.** DBI has **no PID namespace** (unlike ptrace). Detcore
     virtualizes `getpid`/`gettid` (`syscall_classification.rs:2104`
     "getpid // virtualized"), so the guest reads `pid=3, tid=3` (deterministic
     DetPid). DynamoRIO intercepts `tkill`/`tgkill` for its own thread
     management and translates the virtual tid → real host tid (`3 → 888202`),
     so `tkill` succeeds. It does **not** intercept the sigqueue family, so
     `rt_tgsigqueueinfo(3, 3, ..)` reaches the kernel verbatim; real host pid 3
     is an unrelated process → **EPERM**.
   - **Why not a hermit-only fix.** `detcore/src/record_or_replay.rs` and the
     signal handlers (`detcore/src/syscalls/signal.rs`) are backend-agnostic
     (no `backend`/`dbi`/`namespace` awareness — confirmed by grep).
     `handle_tkill` and `handle_rt_tgsigqueueinfo` use the *identical*
     `record_or_replay(guest, call)` forward; the divergence is entirely below
     Detcore. Making Detcore translate virtual→real pid/tid would be correct
     only under DBI and would **break ptrace**, whose in-namespace kernel
     requires the virtual ids verbatim. So the translation must live in the
     backend.
   - **Fix owner:** reverie-dbi — translate the pid/tid arguments of
     `rt_tgsigqueueinfo`/`rt_sigqueueinfo` (and audit `kill`) to real host ids
     the same way tkill/tgkill are handled, **or** run the DBI guest in a PID
     namespace so virtual==real. Recommend the latter for general correctness;
     it would also fix any other id-carrying syscall. This is the parallel
     `dbi` agent's lane (reverie branch `codex/verify-dbi-b15`); it is **not**
     currently addressed by their in-flight work (env-shebang + KVM/sabre).

2. **network_bind_full_rs — bind fails EADDRINUSE.** Architectural.
   - The guest binds every ephemeral port 32768..65535 and expects a private
     port space. DBI has **no network namespace container**, so ports already
     in use on the host make `TcpListener::bind` panic at
     `tests/standalone/network_bind_full.rs:17`. `network_bind_rs` (which does
     not require an empty ephemeral range) passes.
   - **Fix owner:** reverie-dbi (network namespace / container isolation).

3. **standalone_stacktrace_events — FALSE POSITIVE.** Not a DBI failure.
   - It is a *driver* program: `let hermit = &allargs[1]; let prog =
     &allargs[2];` (`tests/standalone/stacktrace_events.rs:22-23`). Run as a
     bare guest with no args it panics "index out of bounds: len 1 index 1".
     Exclude from the bare-guest sweep.

## Conclusion & handoff

- **No easy hermit-only (detcore) fix exists** among the 10 non-passing guests.
  All real failures are reverie-dbi architectural gaps: (a) no preemption/clock
  for non-yielding threads (7 HANG), (b) no PID-namespace / incomplete
  virtual→real id translation for the sigqueue family (tkill guest), (c) no
  network namespace (bind_full). One "failure" is a false positive.
- **Recommended next fix (highest value / lowest effort, reverie-dbi lane):**
  translate the `rt_tgsigqueueinfo`/`rt_sigqueueinfo` pid/tid args to real host
  ids in reverie-dbi, mirroring DynamoRIO's tkill/tgkill handling — or, more
  robustly, launch the DBI guest inside a PID namespace. This turns
  rustbin_tkill green under DBI and generalizes to other id-carrying syscalls.
- **Registry note for the coordinator:** slot 274 is registered in `ACTIVE.md`
  to `impl-landing-rebase-chain-b`, but this DBI-compat investigation ran there;
  the `dbi-deep-compat-debugging` task named for slot `dbi` does not exist in
  `tg`. Reconcile ownership before any reverie-dbi implementation is dispatched,
  to avoid colliding with the parallel `dbi` agent
  (`codex/dbi-env-shebang-compat` / `codex/verify-dbi-b15`).
