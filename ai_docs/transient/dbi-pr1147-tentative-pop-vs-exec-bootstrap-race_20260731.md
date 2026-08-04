# DBI #1147 hang — the REAL residual: scheduler tentative-pop vs exec-child bootstrap-push race

Date: 2026-07-31
Task: `p1_fix_dbi_from` (Hermit PR #1147, branch `codex/dbi-b3-example-parity`)
Test: `run_dbi_virtualizes_process_identities` (hermit-cli/tests/cli.rs:902)
Backend: DBI (`hermit run --backend dbi --strict --verify`), determinism level L2.

## TL;DR

There are **two distinct bugs** behind the 900s `run_dbi_virtualizes_process_identities`
CI hang on the hosted "Regular tests (GitHub-managed portable)" lane:

1. **Bug A (DBI, FIXED by 683fb5ca):** a forked non-vfork child has no DynamoRIO
   background client thread, so on `execve` it busy-spins forever in the native
   pause handshake. My hermit-only fix (gate the pause on
   `RUNTIME_BACKGROUND_OWNER_PID == getpid()` + broaden `PrepareExec` to every
   initialized leader) fixes this. Verified by A/B below.
2. **Bug B (core scheduler, NOT fixed, NOT mine):** a `tentative_selection`
   assertion panic. The exec-child's post-exec `CreateChildThread` self-registration
   pushes to the run queue **concurrently with the scheduler daemon's tentative-pop
   window**, tripping `assert!(self.tentative_selection.is_none())`. This poisons the
   scheduler mutex and hangs the run.

My fix (683fb5ca) is a **real, necessary improvement but incomplete**: it takes the
base from "hangs ~always" to "hangs ~2-5% (single-core)". The residual ~2-5% is
Bug B, which on the slow single-core GitHub VM reproduces reliably → CI still red.

## Environment note: it is NOT "no PMU"

The portable lane node is labelled "no PMU or CPUID interception", but DBI does
**not** use the hardware PMU: `reverie/reverie-dbi/native/client.c` counts retired
conditional branches with DynamoRIO software instrumentation (`branch_count`
atomic), and DBI preemption ships **default-off** (reverie#294/hermit#1180). So
"no PMU" is a red herring for DBI. The real CI-vs-devbig difference is
**timing** (a constrained single-core cloud VM widens the race window), which is
why the panic reproduces almost every run on CI but only ~2-5% on a fast box.

## A/B validation (definitive)

Host: devbig (this workspace). Repro command, pinned to one core to mimic the
constrained VM and widen the race window:

```
taskset -c 0 timeout 30 ./target/debug/hermit run --backend dbi --strict --verify \
  --tmp=/tmp -- /tmp/dbi-repro/dbi_pid_virtualization
```

Guest compiled from `hermit-cli/tests/c/dbi_pid_virtualization.c`
(`cc -O0 -g -Wall -Wextra -Werror`). Backend `.so` rebuilt per side with
`cargo build --release -p detcore-dbi` (wired via
`target/install_pkg/rsrcs/libdetcore_dbi.so -> target/release/libdetcore_dbi.so`).
Leaked PPID=1 `dbi_pid_virtualization` orphans reaped between iterations.

| Build | Outcome (single-core) | Hang mode |
| --- | --- | --- |
| BASE `efae0cbc` (my change reverted) | HANG 3/3 (loop killed early) | busy-spin, empty stdout — hangs at first fork+exec |
| MY FIX `683fb5ca` | 19/20 then 59/60 pass; **1 hang each** | PANIC (see below), during "DBI Run2" |

Base hangs produced no panic (pure busy-spin / quiescence wait). My-fix hangs
produced this panic captured from stderr of a hung run:

```
:: DBI Run1...
:: DBI Run2...
thread 'tokio-rt-worker' panicked at detcore/src/scheduler/runqueue.rs:317:9:
assertion failed: self.tentative_selection.is_none()
thread 'tokio-rt-worker' panicked at detcore/src/scheduler.rs:829:22:
called `Result::unwrap()` on an `Err` value: PoisonError { .. }
```

Surviving orphan after the hang: a single-thread `dbi_pid_virtualization`
(PPID=1). The first panic poisons `sched`; the daemon's next `sched.lock().unwrap()`
(scheduler.rs:829) re-panics on `PoisonError`; every guest RPC that locks `sched`
then hangs → 900s node timeout.

## Bug B mechanism (core scheduler)

The daemon turn (`scheduler.rs` ~800-843, `do_a_turn_blocking`):

1. lock `sched`; `step2_process_blocked`; `step3_peek` → `run_queue.tentative_pop_*`
   sets `tentative_selection = Some(tid)`; **drop the lock**.
2. `req.get().await` — wait for the selected thread's request. The tentative
   selection persists across this await (commit happens later, in step4).
3. re-lock; `step4_resource_block` → `commit_tentative_pop` clears the tentative
   state.

Any run-queue **mutation** while `tentative_selection.is_some()` trips the guard:
`push_back`/`push_front`/`push_poller`/`push_eager_io_repoll` all
`assert!(self.tentative_selection.is_none())` (runqueue.rs:258/287/317/329).

The offending pushers run **outside** the daemon turn, as global-request handlers
that independently `self.sched.lock()`:

- `tool_global.rs:817` `recv_create_child_thread` → `sched.runqueue_push_front/back`
  (the new-child branch, taken when `exec_reconnect` is `None`).
- `scheduler.rs` `reconnect_after_exec` → `self.runqueue_push_back(new_leader)`
  (the exec-reconnect branch, taken when a matching `PrepareExec` was registered).

Either can execute during the daemon's step-2→step-4 await window (a different
tokio worker), so either can push while a tentative selection is live → panic.

Under ptrace this path is driven synchronously within the selecting thread's turn
(after commit), so it does not race. Under DBI the exec-child self-bootstraps via
an asynchronous `CreateChildThread` global RPC that is not bound to a scheduler
turn, so it races the daemon. **My broadened `PrepareExec` did not create this
race; it exposed a pre-existing latent one** by letting exec-children get far
enough to self-register (base busy-spun before ever registering).

## Why this is trigger #4 and not a quick patch

- It is a **core DetCore scheduling change** (always human-reviewed per
  `AGENTS.md` trigger #4).
- The naive fix — "defer the push when `tentative_selection.is_some()`" — is
  dangerous: whether the push lands mid-tentative depends on **real guest-vs-daemon
  timing**, so deferring on that condition makes the queue-insertion order depend
  on a race → risks turning a crash into a **non-deterministic schedule** (a
  `--verify` Run1/Run2 divergence) rather than a fix.
- The correct fix must make the exec-child/new-leader **registration deterministic
  w.r.t. the schedule** (a fixed admission point), in the same family as
  PR #1162 (tentative-pop deferral of `block_for_one_resource` wakes) and
  PR #1152 (`backend_defers_vfork_child_registration` vfork deferral). The
  existing deferral infra (`sigchld_deferred`, `backend_defers_vfork_child_registration`)
  is the pattern to extend, but the admission ORDER must be derived from
  deterministic state, not from whoever-won-the-lock.

## Candidate fix directions (for owner design decision)

1. **Deterministic admission point (preferred).** Have global-request handlers
   record the pending admission (tid + front/back intent + priority) in a
   scheduler-owned set instead of pushing directly; drain it into the run queue
   at a single deterministic point in `step2` (before `step3_peek`), ordered by a
   deterministic key (e.g. DetTid), never by lock-acquisition order. Must be
   proven to give identical Run1/Run2 queue states.
2. **Eliminate the cross-await tentative state.** Restructure the daemon so the
   `tentative_pop`→`commit` transaction does not span the `req.get().await` (fully
   pop in step3, re-insert deterministically on skip/abort). Larger blast radius;
   touches all backends' selection/undo logic.
3. **DBI-side quiescence (revisit the superseded cross-repo plan).** Give forked
   children their own DynamoRIO background client thread (`dr_register_fork_init`
   in `client.c`) so the exec pause/quiescence handshake runs for them and the
   post-exec reconnect happens at a scheduler-quiescent point. Needs verification
   that quiescence actually excludes the daemon tentative window (it may not —
   quiescence synchronizes guest threads, not the daemon's tentative transaction).

## Status

- Bug A fix committed on branch `codex/dbi-b3-example-parity` @ `683fb5ca` (PR #1147).
  Keep it — it is correct and necessary.
- Bug B remains OPEN. `run_dbi_virtualizes_process_identities` still hangs on CI.
- Task `p1_fix_dbi_from` is `in_progress` (implemented tag STRIPPED; the earlier
  IMPLEMENTED claim was premature — the test still hangs on CI).

## Supersedes

- `ai_docs/dbi-pr1147-second-hang-forked-child-no-background-thread_20260730.md`
  (Bug A analysis + the busy-spin fix — still correct for Bug A, but its "this is
  the whole fix" framing is wrong; Bug B remains).
- `ai_docs/dbi-pr1147-ci-hang-postexec-time-rewind_20260730.md` (time-rewind
  hypothesis — not reached).
