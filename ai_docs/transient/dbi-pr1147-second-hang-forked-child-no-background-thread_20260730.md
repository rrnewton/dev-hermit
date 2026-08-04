# DBI #1147 second hang — DEFINITIVE root cause: forked exec-child has no background client thread

Date: 2026-07-30
Task: `p1_fix_dbi_from` (Hermit PR #1147, branch `codex/dbi-b3-example-parity`)
Test: `run_dbi_virtualizes_process_identities` (hermit-cli/tests/cli.rs:902) hangs to the 900s
CI node timeout; sole red on hosted "Regular tests (GitHub-managed portable)".

## TL;DR

A **forked** DBI guest child has **no background client thread**, so when that
child later `execve`s it busy-spins forever in the native-exec pause handshake
(`while !RUNTIME_PAUSED { yield_now() }`) with nothing to set `RUNTIME_PAUSED`.
The out-of-process Detcore scheduler then waits forever for that child to
quiesce/re-register. This is a **cross-repo Reverie(`client.c`) + Hermit(pin)**
fix, not a Hermit-only change.

The owner's two suggested fixes are both **refuted**:
- (a) bump coordinator tokio `worker_threads(2→N)` — the wedge is a *guest-side
  busy-spin in a forked child*, not host tokio worker starvation.
- (b) make the `DeregisterThread` exit-RPC non-blocking/void-send — the stuck
  RPC is not `DeregisterThread`; the child never reaches exit. It dies mid-exec.

## Evidence

Repro (debug binary, in-tree guest, `--tmp=/tmp` so it is not rejected as a
host-`/tmp` program):

```
timeout 60 ./target/debug/hermit run --backend dbi --strict --verify --tmp=/tmp \
  -- $(pwd)/scratch/dbi_pid_virt        # EXIT=124 (hang)
```

Coordinator DBGRPC+DBGSCHED trace (exec-child = 1672789, parent = 1672718):

```
step3 turn=43 selected=1672789 run_queue=[1672789,1672718]  # exec-child self-bootstraps (push_front)
recv from=1672789 req=StartNewThread(1672789,1672789)       # fills its req (turn 44)
step3 turn=45 selected=1672789 ...                          # MemAddrSpace go-ahead
quiescence-wait turn=46 run_queue=[1672789,1672718] unparked=[1672789]
recv from=1672789 req=PrepareExec(1672789, mm, {})          # <-- with broadened scope, exec-child DOES send PrepareExec
                                                            # ...then NO further RPC ever. Scheduler waits forever.
```

Process-state probe of a hung run: forked exec-child guest processes remain as
**single-thread, busy-spinning (`<running>`) orphans** even after the parent
`hermit` process dies — the signature of an app thread stuck in
`while !RUNTIME_PAUSED { std::thread::yield_now() }`. Each hung run leaks one
such spinning orphan pair.

## Mechanism (definitive)

1. `reverie/reverie-dbi/native/client.c`:
   - `ensure_runtime_background()` (~2366) CAS-es `runtime_background_state`
     0→1 and calls `dr_create_client_thread(runtime_background_init, ...)`
     (~2374). `runtime_background_init` (~2360) stores state=2 and calls the
     Rust `reverie_dbi_runtime_background_init`.
   - The background thread runs `run_cooperative(run_external_scheduler, idle)`
     (hermit `detcore-dbi/src/lib.rs:872`). `run_cooperative` (lib.rs:317)
     checks `RUNTIME_PAUSE_REQUESTED` at the **top of each loop iteration** and
     sets `RUNTIME_PAUSED=true` — this is what acknowledges an execve pause.
2. On `fork()`, only the forking (app) thread survives — the DynamoRIO-created
   background client thread does **not** exist in the child. But
   `runtime_background_state` is **fork-copied as 2** ("already started"), so
   the child's next `ensure_runtime_background()` CAS(0→1) **fails and returns
   early** (client.c:2368-2371). The child therefore has **no** background
   thread.
   - There is **no** `dr_register_fork_init` handler in `client.c` to reset the
     state / restart the thread in children. (Confirmed absent.)
3. When the forked child hits `execve`, hermit `detcore-dbi/src/lib.rs`'s exec
   path sets `RUNTIME_PAUSE_REQUESTED=true` and busy-spins
   `while !RUNTIME_PAUSED`. With no background thread, `RUNTIME_PAUSED` is never
   set → the child spins forever, never performs the native execve, never sends
   the post-exec re-bootstrap `CreateChildThread(exec_reconnect)`.
4. The out-of-process scheduler (`do_a_turn_blocking`, detcore/src/scheduler.rs:769)
   holds the child in the run_queue and loops in `step1_check_quiescence`
   forever, waiting for the child's req-ivar it will never fill. 900s node
   timeout.

Why the *root* process can exec but forked children cannot: the root owns the
one background thread that `dr_client_main` bootstrapped; forked children never
get their own.

## The two-part fix

### Part 1 (Reverie, the actual fix) — `reverie/reverie-dbi/native/client.c`

Register a DynamoRIO fork-init handler that redoes the per-process bootstrap in
each forked child so it gets its own background client thread:

```c
static void fork_init_event(void *drcontext) {
  (void)drcontext;
  // A fork does not copy the DR-created background client thread; only the
  // forking thread survives. runtime_background_state was inherited as
  // "started" (2), so reset it and refresh per-process identity/image state,
  // letting the next ensure_runtime_background() recreate this child's
  // background scheduler thread (which services the native-execve pause).
  atomic_store_explicit(&runtime_background_state, 0, memory_order_release);
  runtime_owner_pid = dr_get_process_id();
  // re-establish virtual identity + image_generation for the new pid,
  // mirroring the relevant subset of dr_client_main (lines ~2393-2406).
}
...
dr_register_fork_init(fork_init_event);   // in dr_client_main
```

Ordering is safe: the child performs several pre-syscall RPCs (thread_init →
CreateChildThread/StartNewThread) *before* execve, so
`ensure_runtime_background()` (called from pre_syscall, client.c:2047) will have
restarted the thread and entered `run_cooperative`'s loop before the app thread
requests the pause. `reverie_dbi_runtime_background_init` resets the pause
atomics (lib.rs:798-800) at start, before the app thread sets them.

Concurrency notes to validate (reverie guide flags clone/exec as
concurrency-sensitive): fork-copied `resource_lock` (a `dr_mutex`) and the
fork-copied Rust `RUNTIME`/`GlobalState` Mutexes. `run_cooperative` checks the
pause **before** polling the (possibly-blocked) scheduler future, so the pause
is serviced even if the child's rogue idle scheduler future would block on a
fork-copied lock. Still, prefer resetting/reinitializing what a child must not
share.

### Part 2 (Hermit, necessary companion) — `detcore-dbi/src/lib.rs` exec scope

Commit 5bbaf681 scoped `PrepareExec` to the tree-root leader
(`pid == ROOT_HOST_PID`), so **forked exec-children skip PrepareExec** and
cannot exec-reconnect. Broaden to every initialized process leader:

```rust
// was: if tid == pid && pid == ROOT_HOST_PID.load(Acquire) && !runtime_state.is_null()
if tid == pid && !scratch.runtime_state.is_null() {
    let thread = unsafe { &mut *scratch.runtime_state };
    if thread.initialized { send_dbi_prepare_exec(...); }   // keep inner guard
}
```

Keeping `tid == pid` still excludes the non-leader-thread exec that the
fully-broad `efae0cbc` variant hit; keeping `thread.initialized` excludes a
thread that execs before its first dispatched syscall. **Verified locally**:
this makes the forked exec-child emit `PrepareExec` (trace line above) — a
necessary precondition, but insufficient without Part 1 (the child still
busy-spins in the pause because it has no background thread).

## Landing plan

1. Reverie PR (client.c fork-init) → `rrnewton/reverie`; needs its own clean
   branch from the pinned base `2afd1ecc` (the reverie slot is currently on an
   unrelated task branch `codex/dbi-branch-count-preemption` — do NOT hijack).
2. Local end-to-end validation via a `[patch]` override in hermit's workspace
   `Cargo.toml` pointing `reverie-dbi`/`reverie*` at the local slot path;
   rebuild the client `.so` (DynamoRIO is cached; only client.c recompiles),
   run the repro to confirm no hang + exact-stdout L2 determinism, plus the
   `run_dbi_verifies_*` set + fmt/clippy.
3. Land reverie, bump `hermit/detcore-dbi/Cargo.toml` `rev` to the landed SHA,
   land the Part-2 hermit change on #1147.
4. Trigger: labeled `post-facto-human-review` (core clone/exec lifecycle +
   scheduler quiescence interaction). PR sections: Summary / Determinism /
   Validation / Human Review Required.

## Supersedes

This supersedes the earlier "post-exec time-rewind panic" hypothesis
(ai_docs/dbi-pr1147-ci-hang-postexec-time-rewind_20260730.md and the
`dbi-from-guest-exit-rpc-deadlock-pr1147` memory): that panic path is NOT
reached because the child dies busy-spinning in the pre-execve pause, before any
post-exec `CreateChildThread`/`update_global_time`. The scheduler quiescence
wedge is the observed terminal state.
