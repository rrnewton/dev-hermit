# DBI corpus HANGs: safe-point preemption ceiling (2/6) and the exit_group teardown contract

- **Date:** 2026-07-31
- **Task:** `dbi_preemption_via_safe` (DBI lane)
- **SHAs:** hermit `99dd88a2a7bc41a616727c90cdb541c4312c2c42` (PR #1180) + reverie
  `048c250e8588c452a21ab8983b38808069baf0fe` (PR #294). Behavioral runs below
  use the built `target/release/hermit` at this pair with the client `.so`
  rebuilt from the pinned reverie checkout.
- **Backend:** DBI (DynamoRIO), sequentialized threads, `--strict [--verify]`.

## Question

Safe-point branch-count preemption (PR #294/#1180) flips exactly **2 of the 6**
DBI corpus HANGs to PASS_L2. Why only 2? What does each of the remaining 4
actually need, and which are in-scope for the DBI backend vs. owner-design core
scheduler / lifecycle changes (trigger #4)?

## Method

Each guest run under `--backend dbi --strict --verify --tmp=/tmp`, bounded by
`timeout`, at three preemption settings: off, `HERMIT_DBI_PREEMPT_QUANTUM=100000`,
and (for the futex case) `=1000`. `rc=124` = HANG, `rc=0` = PASS_L2. One live
`/proc` thread-state snapshot was taken during the `sched_yield` HANG to observe
where the launcher, the Detcore coordinator, and the guest threads are parked.
The Detcore scheduler trace itself is **not capturable** on this path: in DBI
mode `tool_global` runs in an in-guest DynamoRIO client thread and its
diagnostics do not reach the launcher stderr through either `--log` or
`HERMIT_LOG`; diagnosis therefore relies on behavioral + live `/proc` + source
evidence.

## Findings

### 1. The safe-point mechanism's ceiling is 2/6, and it is a real ceiling

| Guest | off | q=100000 | q=1000 | Root-cause class |
| --- | --- | --- | --- | --- |
| `rustbin_clock_total_order` | HANG | **PASS_L2** | — | no-syscall busy-wait only |
| `rustbin_futex_and_print` | HANG | **PASS_L2** | — | no-syscall busy-wait only |
| `rustbin_exit_group` | HANG | HANG | — | exit_group teardown |
| `rustbin_sched_yield` | HANG | HANG | — | exit_group teardown |
| `rustbin_futex_wake_some` | HANG | HANG | HANG | exit_group teardown w/ blocked futex waiters (+ nanosleep timed-waiter) |
| `chaos_keyvalue_bin` | HANG | HANG | — | chaos (not characterized here) |

The two guests whose *sole* blocker is a thread that tight-loops without ever
reaching a syscall are fixed: preemption injects a real `sched_yield` at a
branch-count boundary, the thread re-enters the deterministic scheduler, and its
sibling gets a turn. Every other guest **HANGs regardless of quantum** — even at
an aggressive `q=1000` for `futex_wake_some`. Preemption cannot help them
because in each the relevant thread **already reaches a syscall**; the deadlock
is downstream of thread selection, so making threads yield more often changes
nothing. This confirms the PR's scoping claim empirically: 2/6 is the mechanism's
ceiling, not a tuning artifact.

### 2. Three of the four remaining HANGs converge on one contract: exit_group teardown

`Detcore::handle_exit_group` (`detcore/src/syscalls/threads.rs:422`) requests
`ResourceID::Exit { group: true }` and then `tail_inject`s the real
`exit_group`. When the scheduler authorizes it
(`detcore/src/tool_global.rs:1093-1118`) it logically-kills the other threads in
the group and then relies on this contract:

```
// Before allowing an `exit_group` to physically proceed, we
// deregister the other threads in the thread group ...
// We trust the kernel to physically kill them irrespective of what they're
// blocked on, including us having blocked them in the `futex_waiters` list.
```

That trust holds for the **ptrace** backend: the guest threads are ordinary OS
threads in one kernel thread group, so the leader's real `exit_group` (plus the
tracer's force-kill) terminates every sibling, the scheduler observes "zero
threads left anywhere, fizzling", and the process exits cleanly. (Captured
ptrace reference trace for `exit_group`: leader `exit_group` →
`logically_kill: Scheduler removing all knowledge of tid 5 in pid 3` → clean
fizzle.)

Under **DBI** the contract does not converge. Live `/proc` snapshot during the
`rustbin_sched_yield` HANG (leader child loops `exit_group(0)`, parent loops
`sched_yield`):

```
launcher  hermit  2654266  do_wait                       # waiting for guest to exit
          hermit  2654269  unix_stream_read_generic      # coordinator: waiting for next RPC
guest  rustbin_sched_y 2654268  __futex_wait             # app thread parked awaiting its turn
       rustbin_sched_y 2654348  __se_sys_nanosleep       # DR-managed thread
guest  rustbin_sched_y 2654300  __se_sys_nanosleep       # still-live child
```

The guest thread group is **still fully alive** after the child has looped
`exit_group(0)` — the whole group was never physically torn down. The Detcore
coordinator (`2654269`) is parked in `unix_stream_read_generic` waiting for an
RPC, while the guest app thread (`2654268`) is parked in `__futex_wait` waiting
for its scheduler "go" — a coordinator↔guest mutual wait, and the launcher
(`2654266`) sits in `do_wait` forever. The DynamoRIO-managed threads are not
plain kernel threads blocked on a syscall the leader's `exit_group` will clear;
they are parked inside the DBI coordinator's own synchronization, so "trust the
kernel to physically kill them" does not fire.

- `rustbin_exit_group` and `rustbin_sched_yield` are this failure directly.
- `rustbin_futex_wake_some` reaches the same path: its leader calls `exit_group`
  while 2 of 4 children remain in a **blocking `FUTEX_WAIT`** — precisely the
  "blocked on the `futex_waiters` list" case the comment names — and it HANGs at
  every quantum, so its blocker is the teardown of blocked siblings (with a
  possible additional `nanosleep(300ms)` timed-waiter / vtime-jump component;
  see `[[scheduler-vtime-jump-unproductive-pollers]]`), not the no-syscall
  busy-spin that preemption addresses.

### 3. `chaos_keyvalue_bin` is a distinct, uncharacterized chaos-mode hang

Not reduced here; keep it separate from the teardown cluster.

## Interpretation and routing

- **In this task / done:** safe-point preemption fixes exactly the 2 busy-wait
  guests; that is its correct, verified scope. No further tuning will extend it.
- **Owner-design, do NOT freelance (trigger #4 — core scheduler + cross-backend
  lifecycle contract):** the exit_group teardown convergence for the DBI backend.
  The core assumption "trust the kernel to physically kill" sibling threads
  (`tool_global.rs:1098-1118`) is a cross-backend contract that ptrace satisfies
  and DBI does not. A fix requires a deterministic mechanism for the coordinator
  to *explicitly* drive DR-managed sibling threads to termination on an
  authorized group exit (and to detect "all threads gone" without relying on the
  kernel to have killed them), mirroring what the ptrace tracer's force-kill
  provides. This affects how/when threads leave the schedule, so it must go
  through owner design, not a lane freelance. Likely fixes 3 of the 4 remaining
  hangs at once (`exit_group`, `sched_yield`, `futex_wake_some`).
- **Separate investigation:** `chaos_keyvalue_bin`.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/dbi/hermit
source ../.env.dbi.slot          # HERMIT_BIN, HERMIT_DRRUN, DYNAMORIO_HOME, HERMIT_DBI_CLIENT
g=target/release/rustbin_sched_yield
# HANG at any quantum:
HERMIT_DBI_PREEMPT_QUANTUM=100000 timeout 25 "$HERMIT_BIN" \
  run --backend dbi --strict --verify --tmp=/tmp -- "$g"; echo rc=$?   # 124
# Live teardown evidence: launch under `timeout ... &`, sleep 6, then read
# /proc/<guest-pid>/task/*/{stat,wchan} — guest thread group still alive.
```

## Related memory

`[[dbi-l2-corpus-baseline]]`, `[[dbi-preemption-in-process-reentrancy-blocker]]`,
`[[detcore-runqueue-tentative-pop-constraint]]`,
`[[scheduler-vtime-jump-unproductive-pollers]]`,
`[[dbi-from-guest-exit-rpc-deadlock-pr1147]]`.
