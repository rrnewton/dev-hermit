# DBI deterministic branch-count preemption — root cause + design

Date: 2026-07-30
Author: hermit-dbi lane (impl agent, opus-4.8)
Status: **RESOLVED via safe-point delivery** — the original clean-call-turn
design assumption was WRONG for in-process DBI (see "OUTCOME / BLOCKER"), but the
corrected safe-point mechanism WORKS and is committed default-off (draft PRs
reverie#294 @c715f6ab, hermit#1180 @2cd6f602). It fixes 2 of the 6 HANGs (the
pure no-syscall busy-waits); the other 4 have independent root causes. See
"RESOLUTION" at the bottom for the working mechanism, validation, and scope.
Read the whole file top-to-bottom: the middle section documents the dead-end you
must NOT re-attempt; the end documents what actually works.

## Corpus state (evidence)

`hermit --backend dbi run --strict --verify` over the 36-guest `tests/`
corpus (script `ignored/dbi-l2-sweep.sh`, reverie pin 9216e22):

- **29/36 PASS_L2 (80.6%)** — past B3 (50% parity).
- **6 HANG** (timeout): `chaos_keyvalue_bin`, `rustbin_clock_total_order`,
  `rustbin_exit_group`, `rustbin_futex_and_print`, `rustbin_futex_wake_some`,
  `rustbin_sched_yield`.
- **1 FAIL**: `network_bind_full_rs` — `EADDRINUSE` (no network namespace;
  host port collision). Separate, environmental; not addressed here.

## Root cause of the 6 HANGs (single, confirmed)

The DBI backend disables timeslice preemption by construction. In
`hermit/detcore-dbi/src/lib.rs::load_dbi_config` (lines ~250-263):

```rust
// the backend drives the Detcore global scheduler externally on a branch count
// rather than PMU retired-conditional-branch preemption, so timeslice
// preemption (`max_timeslice`) is disabled ...
config.max_timeslice = None;
config.sequentialize_threads = true;
```

Guest threads are sequentialized (one runs at a time) and only re-enter the
deterministic scheduler at a **syscall** (each syscall is an RPC turn through
`run_tool_syscall_with_memory_reader`). There is **no mechanism to return
control from a running guest thread to the scheduler between syscalls.**

Therefore any runnable thread that does not reach a syscall starves its
siblings and deadlocks the deterministic schedule
(`sched_loop_inner` → `do_a_turn_blocking` → `step1_check_quiescence` never sees
the thread park). The three failing shapes:

- **busy-wait on a flag, no syscall**: `while flag.load() {}`
  (`clock_total_order`), `while counter < TOTAL { noopy() }` with `noopy`
  doing only atomic add/sub (`futex_and_print`), `while children_pre < TOTAL {}`
  (`futex_wake_some`). The setter/child thread is runnable but never scheduled.
- **spin with no *blocking* syscall**: `loop { print!("") }` sibling in
  `exit_group` (empty write may elide the syscall).
- **tight syscall loop that never yields the token to a co-runnable sibling**:
  `sched_yield` (main loops `sched_yield`, child loops `exit_group`).

This is the DBI analog of the ptrace backend's PMU-RCB timeslice preemption,
which is exactly what is disabled here.

Verified `exit_group` itself is NOT broken: many PASS_L2 guests
(`rustbin_futex_wait_child`, etc.) end in `SYS_exit_group` and pass — the
process teardown works once the calling thread is actually scheduled.

## Fix: deterministic branch-count preemption (reuse existing machinery)

Preempt a running guest thread at a **deterministic branch-count quantum** by
injecting a **synthetic `sched_yield` turn** through the existing, tested
dispatch. No new Detcore scheduling algorithm — Detcore's `sched_yield`
handler already deprioritizes the caller and lets the scheduler pick the next
deterministic thread.

Determinism argument: `branch_count` is incremented only at counted app
branches (cbr/ubr/call/return) and is a deterministic function of the executed
instruction stream. A fixed `QUANTUM` makes the preemption points a
deterministic function of execution, and Detcore's response to `sched_yield` is
already deterministic. Hence bitwise-identical repeats (L2) are preserved.

### Implementation (additive, flag-gated → zero risk to the 29 passing guests)

1. **`reverie/reverie-dbi/native/client.c`**
   - Add `static bool preemption_enabled;` and a `static uint64_t
     preemption_quantum;` set from a new client arg
     `-preemption-quantum N` (0 = disabled) in `dr_client_main`'s arg loop
     (mirror `-summary`).
   - Add `uint64_t last_yield_branch;` to `prototype_counters_t`
     (per-thread; init 0 in `thread_init`).
   - In `instrument_instruction`, at the first app instruction of a bb (mirror
     the existing `start_pending_thread` hook at lines ~689-695), when
     `preemption_enabled`, insert a clean call to `maybe_preempt`
     with `DR_CLEANCALL_READS_APP_CONTEXT | DR_CLEANCALL_WRITES_APP_CONTEXT`.
   - `maybe_preempt(void)`: get drcontext + counters; if
     `branch_count - counters->last_yield_branch >= preemption_quantum`,
     set `counters->last_yield_branch = branch_count` and call
     `reverie_dbi_runtime_preempt(counters, drcontext, tid, invoke_syscall,
     read_registers, write_registers)`. (Skip while a syscall is in flight and
     when `has_copied_runtime()` is false / runtime not ready, mirroring the
     guards already used around `reverie_dbi_runtime_thread_*`.)

2. **`reverie/reverie-dbi/src/lib.rs`**
   - Add `reverie_dbi_runtime_preempt(...)` extern "C": build the same
     `DbiGuest`/thread context the syscall path builds and call a thin helper
     that runs the tool over a **synthetic `Syscall::SchedYield`**, discarding
     the outcome (a yield returns 0 and mutates no guest registers). Reuse
     `run_tool_syscall_with_memory_reader` so the cooperative park/idle and the
     scheduler turn are identical to a real `sched_yield`.

3. **`hermit/detcore-dbi/src/lib.rs`**
   - Pass the quantum to the client. The client args are assembled by the
     `DbiRunner`; thread `-preemption-quantum <N>` when a
     `HERMIT_DBI_PREEMPT_QUANTUM` env (or a Detcore config field) is set.
   - Do **not** unconditionally clear `max_timeslice` semantics; the synthetic
     yield does not depend on `max_timeslice`. Leave `sequentialize_threads =
     true`.

4. **`hermit/hermit-cli`** (optional, follow-up): surface a
   `--dbi-preempt-quantum` or reuse `--preemptions`/vtime knobs. For the first
   landing, env-gated is sufficient.

### Validation plan

- Build release (detached; DynamoRIO builds `-j1` to avoid the known parallel
  cmake race): `cargo build --release -j1 -p hermit-install -p detcore-dbi -p
  hermit --bin hermit`.
- With quantum unset (default): re-run the full sweep → must stay 29/36 (no
  regressions).
- With `HERMIT_DBI_PREEMPT_QUANTUM` set (tune, e.g. 1000–100000): re-run the 5
  busy-wait/yield HANGs under `--strict --verify`; expect PASS_L2. `exit_group`
  (spinning-sibling teardown) may need additional lifecycle work and can remain
  a documented follow-up.
- Confirm L2 (bitwise-identical) on each newly-passing guest.

### Review classification

Trigger #4 (core DetCore scheduling change: introduces preemption). PR gets
`post-facto-human-review` + a `Human Review Required` section naming trigger 4.
Additive/flag-gated, so it is safe to land green with preemption default-off
and enable after broader (L4) validation.

## OUTCOME / BLOCKER (2026-07-30, after implementation)

The design was implemented faithfully and committed **default-off** (draft PRs
reverie#294 @`22d9d742`, hermit#1180 @`8e106752`, coordinated branch
`codex/dbi-branch-count-preemption`). **Enabling it fixes 0 of 6 HANGs** — it
hits a fundamental re-entrancy hazard.

### What is wrong with the design's core assumption

The design assumed "a clean call can safely inject a scheduler turn
mid-instruction." **False for in-process DBI.** Running the Detcore scheduler
turn synchronously from an *arbitrary clean-call instruction boundary*
re-enters non-reentrant libc that is **shared with the guest**:

- glibc lazy PLT resolver fires inside the turn → `undefined symbol: getcwd`,
  guest exits 127.
- `malloc`/heap re-entrancy → guest dies before its first write, exit 1.

This is exactly the async-signal-safety hazard of doing allocating work in a
signal handler. The ptrace backend is immune only because Detcore runs
**out-of-process**; the DBI Detcore lib runs **inside** the guest address space
and shares its allocator/loader locks.

### Isolation proofs (why the diagnosis is solid, not a tuning problem)

- **(a)** A clean call that runs only the branch-count check *without* the yield
  is fully transparent — a normally-passing guest still PASS_L2 at q=1000. The
  fault is entirely in *running the scheduler turn*, not in the instrumentation.
- **(b)** Reproduces on a single-threaded normally-passing guest → not the
  cross-thread handoff, not a scheduler-logic bug.
- **(c)** Persists under `LD_BIND_NOW` → lazy-PLT is only ONE of ≥2 re-entrancy
  modes (malloc is another); eager binding is not a fix.
- **(d)** `save_fpstate=true` and dropping `DR_CLEANCALL_WRITES_APP_CONTEXT`
  did not help → not a register/FP-state clobber.
- No robust working quantum exists; coarse q=100000 "passed" only by luck of not
  landing on a critical section.

### What IS sound

The **scheduling mechanism itself works**: with preemption on, busy-wait guests
*do* make progress past the hang (both threads' work executes) before the
corruption kills them. The synthetic-`sched_yield` handoff through Detcore's
existing deterministic handler is correct. The broken part is the **safety of
where/how the turn runs**, not the turn's logic. The arg plumbing
(`-preemption-quantum`, `HERMIT_DBI_PREEMPT_QUANTUM`) and the per-thread
`last_yield_branch` counter are reusable by the corrected approach.

### Path forward (new task `dbi_preempt_safepoint`)

Do NOT re-attempt "run the scheduler turn in the clean call." Instead:

1. **Safe-point delivery.** In the clean call do only a cheap, libc-free
   "preempt pending" flag set (branch-count compare + atomic store). Deliver the
   actual scheduler turn only at a point where the guest provably holds no libc
   lock. The hard part: a pure busy-wait has no syscall boundary — need a
   DynamoRIO-mediated safe suspension (e.g. `dr_suspend_all_other_threads`, or
   flushing the fragment and re-entering at a controlled point) so the turn runs
   in DR state on a DR-private stack/allocator, not the guest's.
2. **Allocation-free / guest-libc-free scheduler-turn path**, or run the turn
   out-of-process (large change, mirrors ptrace).

Trigger #4 still applies to any real enable.

## RESOLUTION (2026-07-30, task `dbi_preemption_via_safe`) — safe-point delivery WORKS

The corrected safe-point approach is implemented, validated, and committed
default-OFF: reverie PR #294 @`c715f6ab440d0c63786f468780a1ae2e3bbf6c81`,
hermit PR #1180 @`2cd6f6029d38af46af26a0e42066f8b953d6cfda` (pins that reverie
SHA), branch `codex/dbi-branch-count-preemption`, both DRAFT +
`post-facto-human-review` (trigger #4).

### Mechanism (a hybrid of "safe suspension" that needs no thread suspension)

The insight: don't try to make the in-guest scheduler turn safe at an arbitrary
PC — instead make the preempted thread reach the **already-safe syscall
boundary** by injecting a *real* syscall into its own execution:

1. The DynamoRIO clean call does **no** scheduler work. It captures the
   interrupted thread's full mcontext (integer + control + FP/SIMD) and
   `dr_redirect_execution`s to a generated `syscall; ud2` stub with
   `rax = SYS_sched_yield`.
2. The stub executes a **real** `sched_yield`, which fires the existing
   `pre_syscall` handler → Detcore's ordinary deterministic `sched_yield` turn
   (the out-of-process RPC boundary that the 29 passing guests already use, and
   the proven-safe site — the guest holds no libc lock at a syscall).
3. A clean call on the block after the stub (`preempt_return`) redirects back to
   the captured context, restoring `rax`/`rip`/FP — fully transparent to the
   guest.
4. `dr_redirect_execution` is legal only from a clean call, not from
   `pre_syscall`; hence the return trip via the `ud2`-block clean call.

This sidesteps the re-entrancy hazard entirely: the scheduler turn never runs
from the clean call. No `dr_suspend_all_other_threads`, no private allocator, no
out-of-process port of Detcore was needed — the syscall the guest "makes" *is*
the safe hand-off.

### Empirical checks (answered)

- **Does an injected code-cache syscall raise `pre_syscall`?** Yes —
  `filter_syscall` returns `true` for all syscalls, so the injected
  `sched_yield` (nr 24) reaches Detcore.
- **Does injection perturb `--verify`?** No — every quantum-on PASS is a
  matching-hash `--verify` pass; default-off produced zero divergences.
- **rax clobber?** Avoided — the original `rax` is restored from the captured
  mcontext.

### Validation (backend=dbi, `--strict --verify`, full 36-guest sweep)

- **Default-off: 29/36 PASS_L2, 6 HANG, 1 FAIL** — identical to baseline, zero
  regressions, zero divergences.
- **`HERMIT_DBI_PREEMPT_QUANTUM=100000`: 30/36 PASS_L2** — flips
  `rustbin_clock_total_order` + `rustbin_futex_and_print` (the two pure
  no-syscall busy-waits) to PASS_L2. `chaos_hello_chaos` becomes a deterministic
  self-`exit(1)` under the different-but-deterministic interleaving (stable
  hashes → not a determinism/corruption failure, just a genuinely different
  schedule). q=100000 is the sweet spot; at q=1000 `rustbin_mem_race` regresses.
- `fmt --all --check` clean in both repos; `clippy -p reverie-dbi` clean.
- Sweep TSVs: `ignored/sweep-defaultoff.tsv`, `sweep-q1000.tsv`,
  `sweep-q100000.tsv`.

### Honest scope: 2 of 6, not all 6

Only the two pure no-syscall busy-waits flip. The other four have INDEPENDENT
root causes that branch-count preemption cannot address, and were diagnosed
during this work:

- `rustbin_sched_yield`, `rustbin_exit_group` — thread admission/selection: both
  threads already syscall every iteration, yet the sibling is never scheduled;
  relaxing the delivery gate to any-PC did not help.
- `rustbin_futex_wake_some` — blocks on `FUTEX_WAIT` + a 300ms `nanosleep` and
  never reaches the wake.
- `chaos_keyvalue_bin` — the documented stretch.

These need separate scheduling/futex work, tracked apart from this preemption
mechanism. The mechanism itself is correct, safe, and default-off preserves the
29/36 baseline exactly.
