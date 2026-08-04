# DBI #1147 non-leader-thread exec continuity — premise CONFIRMED, fix + test WRITTEN & compile/native-verified, land BLOCKED on #1200

- **Task:** `fix_pr_1147_nonleader` (P0, owner hermit-dbi)
- **Date:** 2026-08-04 (updated 2026-08-04 second session: designs upgraded to written, ready-to-apply, compile+native-verified artifacts — see "Written artifacts" at end)
- **Verified against:** hermit `683fb5ca` (PR #1147 head), static source trace only (no runtime repro yet — see Blocker).
- **Origin:** adversarial-review finding on PR #1147 (comment 5170042181). Premise came from a
  review note; per *Establish What You Have* it was treated as UNVERIFIED and the first step was to
  verify it. **Outcome: CONFIRMED** (a refutation would have been an equally valid deliverable).

## The finding, in one line

DBI sends `PrepareExec` **only when `tid == pid`** (the thread-group leader). Linux permits *any*
thread to `execve`, and Detcore's coordinator already implements the non-leader case end-to-end. So a
**non-leader DBI exec bypasses the coordinator's continuity path and silently re-registers with a
fresh epoch** (logical clock reset to the container epoch) plus an orphaned caller identity — the
exact "time-blunting on exec" the owner flags as a major red flag.

## Static verification (both halves proven)

### Half 1 — DBI structurally excludes non-leader execs
`detcore-dbi/src/lib.rs:1491`, inside the `SYS_execve` arm of `reverie_dbi_runtime_pre_syscall`:

```rust
if tid == pid && !scratch.runtime_state.is_null() {
    let thread = unsafe { &mut *scratch.runtime_state };
    if thread.initialized {
        send_dbi_prepare_exec(context, tid, pid, branches, &mut thread.state, ...);
    }
}
```

A non-leader thread has `tid != pid` (its TID differs from the process's TGID), so the whole block is
skipped — **no `PrepareExec` is sent**. The comment even scopes this to "EVERY initialized process
leader (`tid == pid`)", confirming the leader-only intent.

### Half 2 — the coordinator fully supports the non-leader case
`detcore/src/scheduler.rs` `reconnect_after_exec` has a dedicated, documented `caller != new_leader`
branch:

> "Every sibling disappears. If a non-leader called exec, Linux also changes that surviving task's TID
> to the process leader's TID. In that case the old caller registration is retired and a fresh leader
> registration is installed before it is removed…"

And `detcore/src/tool_global.rs`:
- `:727` `PrepareExec` recv records `PendingExecState { caller: dtid, process, mm, fd_blocking }`
  keyed by `process` (detpid). **`caller = dtid` = whichever thread sent PrepareExec.** A PrepareExec
  from non-leader T would record `caller = T`.
- `:774` post-exec `CreateChildThread(new_leader==dtid==process)` self-registration matches the pending
  record → `exec_reconnect = Some` → calls `reconnect_after_exec(ExecReconnect{ caller: pending.caller,
  new_leader: dettid, ... })`.
- `:796` **`if pending.caller != dettid { global_time.reassign_thread(pending.caller, dettid) }`** —
  transfers the non-leader caller's *accumulated logical clock* to the new leader identity. This is
  precisely the continuous-time preservation for a non-leader exec.

**The non-leader continuity path is complete and correct — it is gated entirely behind DBI sending a
`PrepareExec`, which the `tid == pid` check suppresses.**

## Exact runtime consequence (static prediction; runtime disambiguation pending)

With no `PrepareExec`, `pending_exec_states` has no entry for the process, so at the post-exec
self-registration (`tool_global.rs:606-684`):
- `exec_reconnect = None` and `is_exec_caller_after_local_mm_swap = false`.
- The self-registration therefore falls to one of:
  1. **retired-incarnation rejection** (`:628` tombstone check, if `rpc_incarnation_matches` fails on
     the post-exec mm) → `R::ThreadExited` → the exec'd program is told to exit (loud-ish, wrong program
     never runs); or
  2. **`update_global_time(dtid, 0)` panic** (Bug-A class) if the incarnation happens to match with an
     accumulated-time tid; or
  3. **silent fresh registration** via `recv_create_child_thread` → epoch-reset clock, orphaned caller
     identity — the outcome the finding names.
- In all three, `reassign_thread(caller, new_leader)` **never runs** → the non-leader's accumulated
  time is lost. Which of 1/2/3 fires depends on runtime mm-incarnation details — hence a *first-sample*
  check would miss the silent case; you must compare the *continuous* accumulated clock across the exec
  boundary, repeatedly.

## Fix design (hermit-only, detcore-dbi)

Broaden the gate at `detcore-dbi/src/lib.rs:1491` so `send_dbi_prepare_exec` also fires for a non-leader
exec (`tid != pid`, still requiring `!runtime_state.is_null()` and `thread.initialized`). The pre-exec
non-leader thread state is live at the syscall, so `prepare_exec` records `caller = T`, and post-exec
reconciliation runs `reassign_thread(T, pid)` + the `caller != new_leader` branch → clock + scheduler
identity continuity, matching ptrace/SaBRe. `send_dbi_prepare_exec` already builds the guest with
`(tid, pid)`, so it needs the non-leader `(T, pid)` unchanged.

**Non-trivial parts requiring runtime validation (why this is not a one-liner):**
1. **Bug-A background-thread ownership.** A non-leader exec kills the leader + siblings; the surviving
   task adopts the leader TID. Whether the survivor owns a DynamoRIO background client thread
   (`RUNTIME_BACKGROUND_OWNER_PID` gate, added by Bug-A fix) for the pause handshake must be verified —
   a non-leader exec is a *different* lifecycle from the forked-child exec Bug-A handled.
2. **Coupling to #1200 (the real land blocker).** `reconnect_after_exec` → `logically_kill_thread` →
   `remove_tid` hits the unconditional assert `runqueue.rs:392` — this is exactly `fix_1200_codex_review`
   finding #3 ("in the very race the PR claims to harden"). The non-leader path retires *more* tids
   (all siblings + caller) so it exercises that assert *harder*. Landing the non-leader fix on top of
   an un-hardened `reconnect_after_exec` would trade a silent epoch reset for a panic. **#1200's reconnect
   fix must land first.**

## Repeated continuous-time + identity test design (deliverable 2)

New C guest (`hermit/tests/c/dbi_nonleader_exec_continuity.c` + a manifest entry, or a
`run_dbi_verifies_*` CLI test):
- Parent spawns a second pthread. The **non-leader** thread (`gettid() != getpid()`) calls `execve`
  to re-exec a small reporter.
- Reporter emits, **before and after** the exec, an identity+time bracket: `getpid()` (must be stable
  across the non-leader exec — kernel makes the survivor's TID = pid) and a monotonic
  logical-time-sensitive probe (`clock_gettime(CLOCK_MONOTONIC)` and/or `times()`/`getrusage`, which
  read Detcore accumulated time).
- **Assertions (bracket from both sides):**
  - *Negative:* on the buggy (leader-only) build the post-exec accumulated time is **< pre-exec**
    (epoch rewind) and/or pid changes — the test must FAIL, proving the mechanism is not inert.
  - *Positive:* on the fixed build post-exec time is **≥ pre-exec** (non-decreasing) and pid is
    stable — the test PASSES, proving the fix fires.
  - Run under `--strict --verify` (L2) and require byte-identical determinism across runs.
- **Repeated** samples across the boundary, not one: a single post-exec read can look plausible while
  the *continuous* clock has silently reset.

## Disposition

- **Premise: CONFIRMED** (static). Precise silent-vs-panic-vs-reject runtime disambiguation and the
  fix's runtime validation are **BLOCKED on `fix_1200_codex_review`** (reconnect_after_exec assert
  hardening) landing, plus a canonical DBI env with the combined #1147+#1200 stack.
- Task stays `in_progress`, NOT `implemented` (policy: blocked tasks never tag implemented). Land order:
  #1200 → rebase #1147 → this non-leader fix on top.
- Siblings `fix_pr_1147_fail` (fail-open 1e6-iter poll bridge) and `fix_pr_1147_failed` (no CancelExec
  rollback) share the same #1200 blocker and the same exec bridge; fixing all three together on the
  rebased stack is the efficient path.

Related: `dbi-pr1147-ci-hang-postexec-time-rewind_20260730.md`,
`dbi-pr1147-second-hang-forked-child-no-background-thread_20260730.md`,
`dbi-pr1147-tentative-pop-vs-exec-bootstrap-race_20260731.md`.
