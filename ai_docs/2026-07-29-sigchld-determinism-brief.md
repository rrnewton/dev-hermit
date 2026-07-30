# SIGCHLD determinism: owner decision brief

**Decision requested.** Keep [PR #1160](https://github.com/rrnewton/hermit/pull/1160)'s deterministic `make -jN` fix, and choose how a blocking `wait4` learns that a child is physically reapable: **A, await host-zombie within the deterministic exit sequence**, or **B, retain the `timed_waiters` event until a swallowed real `SIGCHLD` sets `reapable=true`**. The feasibility gate for A is whether waiting can deadlock the child before it completes its injected `exit_group`.

## Symptom

Under ptrace `--strict`, parallel `make -j8` produced reproducible build bytes but non-identical execution logs. The first pinned divergence was a scheduler choice between make's jobserver `BlockedExternalContinue` and a gcc child's `InboundSignal(SIGCHLD)`; the signal appeared at different virtual times across runs. PR #1157 improved the workload from 2/6 to 5/6 clean by deferring `SIGCHLD`, but its “ordinary work must quiesce” gate starved reapers when Redis or an HTTP server remained runnable.

The original workstream also referred to GitHub #1147. That reference must not be read as a claim that #1160 fixes #1147: the PR explicitly records #1147 as a separate DBI issue and does not close it.

## Root cause

The child's `ResourceID::Exit` grant is scheduler-ordered at deterministic logical time `t_exit`, but the kernel's real `SIGCHLD` reaches the parent at a host-chosen wall-clock time. That host signal races make's jobserver pipe completion and perturbs later sibling admission.

Two scheduler structures matter:

- `run_queue` is priority-then-round-robin FIFO (plain FIFO in non-chaos mode), **not** a min-vtime heap.
- `timed_waiters` is the real min-vtime structure, ordered by `LogicalTime` and already used for deterministic alarms and POSIX timers.

PR #1157 parked the parent in bespoke `sigchld_deferred` state and released it only when the run queue was empty or contained only pollers. A continuously runnable sibling can keep that predicate false forever. Determinism improved, but liveness was conditional on sibling behavior.

## What PR #1160 fixes

At head [`91bf2208`](https://github.com/rrnewton/hermit/commit/91bf22088c5665ffe632768136ea8f16cbcab90b), #1160 registers a one-shot, child-keyed `ChildExit` event in `timed_waiters` when the scheduler grants the child's exit. Its deadline is `t_exit + 1ns`. `step2b_process_timed` then either releases the already-deferred real signal or synthesizes delivery at that logical instant. A reverse `process_parent` map identifies the reaper; distinct child keys avoid timer collisions and coalesce duplicate reports.

This removes host signal latency from admission and removes #1157's run-queue-quiescence dependency. In the four-channel make investigation, it also removed the residual equal-vtime sibling perturbation (“channel 4”): normalized DETLOGs and stdout were identical in **11/11 independent `make -j8` strict runs**, versus 5/6 at #1157. These were pristine-filesystem, same-guest-path independent runs—not literal in-process `--verify`, whose second pass observes first-pass `.o` files. The exact validation is recorded in the [independent review comment](https://github.com/rrnewton/hermit/pull/1160#issuecomment-5126229620).

## What remains: Redis/HTTP blocking reap

Redis and HTTP expose a different, pre-existing defect: the deterministic exit grant precedes the child's physical `tail_inject(exit_group)`, so at `t_exit` the child may not yet be a host zombie. A parent blocked in `wait4(-1)` is awoken once through `fire_alarm`/`Signaled`, returns `ERESTARTSYS` (often with `SIGCHLD` blocked), and may fail to reap. The one-shot event is gone, so it is never reawakened after the child becomes reapable. Baseline and #1160 both time out with this symptom; it is not a #1160 regression.

### Option A — await-zombie

Make physical exit-to-zombie completion part of the deterministic child-exit sequence; only then make the `ChildExit` deadline eligible. This gives the cleanest invariant: when deterministic SIGCHLD admission occurs, host `wait4` must succeed. **Feasibility question:** can DetCore await zombie without deadlocking when the child still needs a scheduler turn to execute the injected `exit_group`? Prototype that lifecycle first. A is viable only if the awaited completion is driven outside the blocked scheduler turn (or otherwise proven not to require that turn).

### Option B — reapable flag plus `timed_waiters`

Register the pending `ChildExit` at `t_exit`, but use the real host `SIGCHLD` only to set `reapable=true`; swallow it so it never admits the parent directly. Fire the timed event only when both its deterministic deadline has arrived and reapability is true. If the run queue empties first, block for the already-in-flight physical exit instead of advancing virtual time past an unready event. This avoids A's in-turn await, but requires per-child accounting, correct coalescing/disposition handling, and proof that host timing cannot change order when reapability crosses the deadline.

**Recommended decision procedure:** prototype A's deadlock question narrowly. Choose A if zombie completion can be awaited without consuming a child scheduler turn; otherwise choose B and make “deadline + reapable” a scheduler invariant, not a polling heuristic. A later logical-zombie table could eliminate host reapability from guest `wait4` entirely, but that is a broader `wait4`/`waitid` semantics change than this A/B decision.

## Source record

This brief distills TaskGraph notes `fix-execd-sibling-admission-quiescence` (pinned divergence, queue model, starvation, A/B mechanics and 11/11 handoff), `logical-child-reap-model` (blocking-`wait4` lifecycle and design boundary), and the [#1160 description and discussion](https://github.com/rrnewton/hermit/pull/1160).
