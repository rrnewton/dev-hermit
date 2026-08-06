# PR #1147 findings 2 and 3 — both open questions resolved, one fix is now trivial

**Tasks:** `fix_pr_1147_failed` · `fix_pr_1147_nonleader` (handled together; same file)
**Date:** 2026-08-06 · **Author:** hermit-design
**Extends:** `ai_docs/pr1147-dbi-exec-bridge-fixes-20260806.md` (finding 1 + the initial diagnosis of 2/3)
**Head audited:** hermit `5f4e24ac` · Local read-only. No build, no run, no egress.

Last session I left two questions open and refused to implement against them. Both are now answered
from source. One answer **shrinks finding 3 to a one-conjunct deletion**; the other **confirms finding
2's blocker and adds a fourth option the first analysis missed**.

---

## 1. Finding 3 (non-leader exec) — RESOLVED, and the fix is smaller than feared

Last session's caveat was: *"for a non-leader exec the caller becomes the new leader, so
`send_dbi_prepare_exec`'s `pid` / `MmId::for_exec(detpid)` mapping must be checked against what
`reconnect_after_exec` expects for `caller != new_leader`. Do not implement until verified."*

### What `prepare_exec` actually sends

```rust
// detcore/src/tool_global.rs:2020
pub async fn prepare_exec<G, T>(guest: &mut G, mm: MmId, fd_blocking: ExecFdBlockingOverrides) {
    let detpid = guest.thread_state().detpid.expect("detpid unset");
    let (_, response) =
        send_and_update_time(guest, GlobalRequest::PrepareExec(detpid, mm, fd_blocking)).await;
    ...
}
```

The payload is `(detpid, mm, fd_blocking)`. **There is no caller tid in it.** The coordinator learns the
caller from the RPC envelope — `receive_rpc(&self, from: Tid, …)` → `dtid`. And `mm` is the *process's*
pre-exec `MmId`.

### What `reconnect_after_exec` does with it

```rust
// detcore/src/scheduler.rs:1458
pub fn reconnect_after_exec(&mut self, reconnect: ExecReconnect) -> Vec<DetTid> {
    let ExecReconnect { caller, new_leader, detpid, pre_exec_mm, post_exec_mm, .. } = reconnect;
    let group = self.thread_tree.my_thread_group(&detpid);
    assert!(group.contains(&caller));
    assert!(group.contains(&new_leader));
    ...
    if caller == new_leader { /* leader exec: kill siblings, return */ }

    // caller != new_leader — the non-leader path:
    let survivor_priority = self.priorities.get(&caller).copied().or(reconnect_priority)
        .expect("exec caller must have a scheduler priority");
    for old_tid in group.into_iter().filter(|tid| *tid != caller) {
        self.logically_kill_thread(&old_tid, &detpid, pre_exec_mm);
        ...
    }
    // "The leader identity was occupied by a thread the kernel destroyed as part of this
    //  exec. This is the one intentional exception to permanent raw-TID tombstones."
    self.logically_killed_threads.remove(&new_leader);
    ...
}
```

This is a faithful model of Linux: a non-leader `execve` destroys the old leader, the execing thread
survives and takes over the leader identity, and every other group member dies.

### Therefore

Every input the coordinator needs is **process-keyed or envelope-derived**, and none of it depends on
the caller being the leader:

| Input | Source | Differs for a non-leader exec? |
| --- | --- | --- |
| `detpid` | `guest.thread_state().detpid` | no — same process |
| `mm` (pre-exec) | `thread_state.mm_id` | no — process-wide |
| `caller` | the RPC envelope's `from` Tid | correct automatically |
| `new_leader` | derived coordinator-side from `detpid` | correct automatically |

**`MmId::for_exec(detpid)` is keyed on the process, not the thread**, so the mapping I flagged as
unverified is simply *identical* in both cases. The coordinator's non-leader path already exists and is
already the one that will run.

### The fix

Delete one conjunct from the gate at `detcore-dbi/src/lib.rs:1505`:

```diff
-            if tid == pid && !scratch.runtime_state.is_null() {
+            // Linux permits ANY thread to execve: the caller survives and takes over
+            // the leader identity while the old leader is destroyed. Detcore models
+            // exactly that (`reconnect_after_exec`, caller != new_leader), and every
+            // input PrepareExec carries is process-keyed (`detpid`, the process `mm`)
+            // or derived from the RPC envelope (`caller`), so the bridge is correct
+            // for a non-leader without any mapping change. Gating on `tid == pid`
+            // left non-leader execs unbridged: no pending_exec_states, a fresh
+            // re-registration, and the epoch-reset clock rewind this bridge exists
+            // to prevent.
+            if !scratch.runtime_state.is_null() {
```

`!scratch.runtime_state.is_null()` and the inner `thread.initialized` check both stay — they establish
that this thread has a `detpid` and a scheduler identity at all, which is a genuine precondition.

**One detail to confirm when building** (I could not, §3): `send_dbi_prepare_exec` constructs its
`DbiGuest` with `Pid::from_raw(tid)` and `Pid::from_raw(pid)`. The RPC's `from` must be the *execing
thread's* DetTid, not the process's. For a leader these coincide, which is why the current code cannot
distinguish them; for a non-leader they differ and the guest's `dettid` must be the caller.

---

## 2. Finding 2 (failed-exec rollback) — blocker CONFIRMED, plus a fourth option

Last session I listed three options and recommended (a) extend the C ABI. The reason was that
`reverie_dbi_runtime_exec_failed(_scratch, _pid)` cannot construct a `DbiGuest`. That is now confirmed
at the transport level, and it is stronger than "the constructor wants more arguments":

**The guest-side RPC transport itself requires the syscall-injection callbacks.**
`detcore-dbi/src/lib.rs` contains no `send_rpc` implementation at all — the `GlobalRPC` path comes from
reverie-dbi's `sync_rpc::send_rpc_from_guest(context, invoke_syscall, …)`, which injects
`socket`/`connect`/`write`/`read` **as guest syscalls**. So sending *any* RPC from the failed-exec
callback needs `context` and `invoke_syscall`, and those are values only the syscall callbacks receive.
A process-global stash (option b) cannot help: it would have to stash per-callback pointers whose
validity is scoped to a callback that has already returned.

### The fourth option the first analysis missed

**(d) Defer the `CancelExec` to the thread's next syscall.**

A thread whose `execve` was rejected by the kernel *keeps running* and will reach `pre_syscall` again —
and `pre_syscall` has `context`, `invoke_syscall`, and the callbacks. So:

1. In `reverie_dbi_runtime_exec_failed`, set a flag on the thread state reachable through `scratch`
   (which *is* passed, currently as `_scratch`) — e.g. `thread.exec_cancel_pending = true`.
2. At the top of `reverie_dbi_runtime_pre_syscall`, if the flag is set, send `cancel_exec` first and
   clear it.

No ABI change, no process-global, no cross-repo edit.

**What (d) does not buy, stated plainly.** It is a *narrowing*, not a fix:

* If the thread never issues another syscall — it exits, or the failed exec is its last act — the
  cancel never happens and the stale `pending_exec_states` entry survives. Because that entry is keyed
  on **`detpid`**, a surviving *sibling's* later registration can still be mis-reconciled as an
  exec-reconnect.
* Between the failed exec and the next syscall there is a real window in which a sibling can register
  and hit the stale entry.

So (d) shrinks the exposure from "until the process exits" to "until this thread's next syscall", which
is a large reduction and not a guarantee. **(a) remains the complete fix**, and the honest way to ship
(d) would be as an explicitly-labelled mitigation with the residual window documented — not as a
closure of the finding.

Either way, the missing export is unchanged: `detcore/src/lib.rs:120` re-exports only `prepare_exec`;
`cancel_exec` (`tool_global.rs:2031`) needs the symmetric `pub use`.

### And the local half is missing too

Even with the RPC solved, DBI would still be missing the *other* half of the golden path. ptrace does:

```rust
thread_state.file_metadata   = old_metadata;
thread_state.memory_metadata = old_memory_metadata;
thread_state.mm_id           = old_mm_id;   // local rollback
cancel_exec(guest).await;                   // coordinator rollback
```

`reverie_dbi_runtime_exec_failed` does neither. The local restore needs no RPC and no ABI change — it
only needs the pre-exec values, which the DBI path would have to save at `PrepareExec` time. That part
**can** be done today and should not wait on the ABI decision.

---

## 3. Why neither is implemented in this session

**No slot.** Every worktree under `worktrees/*/hermit` is on another agent's branch or task.
`worktrees/coord-drain/hermit` is the interesting one — it sits on `rebase/1147-b3`, clean, with HEAD
*"hermit-cli: gate DBI-only run helpers behind the dbi feature"*, i.e. **someone has already fixed the
clippy blocker I flagged** on a rebased #1147. But its `ACTIVE.md` row reads
`coord-drain | coord-drain | detached | - | - | - | active` — registered to another agent with no task
and no purpose, and physically on a branch the registry does not mention. Building there would both
commandeer another agent's slot and share a writable `target/` (Invariant 8). Allocation is
coordinator-only, and egress is 403, so nothing could be pushed regardless.

Worth flagging separately: that registry/reality divergence (`ACTIVE.md` says detached-with-no-purpose,
the filesystem says `rebase/1147-b3`) is exactly the three-registries-no-reconciler problem, and it is
why I could not tell whether that slot is genuinely idle.

---

## 4. Ready-to-apply summary

| Finding | Change | Blocked? |
| --- | --- | --- |
| 1 (fail-open bridge) | `drive_synchronous_handler` + 3 tests — patch in the prior artifact | no — needs only a slot |
| 3 (non-leader) | delete `tid == pid` from the gate at `:1505` + comment | **no longer blocked** — mapping resolved §1 |
| 2 (failed-exec, local half) | save pre-exec `mm_id`/metadata at PrepareExec, restore in `exec_failed` | no |
| 2 (failed-exec, coordinator half) | `pub use cancel_exec` + option (a) ABI or (d) deferred-send | (a) cross-repo; (d) shippable as a labelled mitigation |

Order: finding 1 and finding 3 together (same file, both self-contained, both one hunk), then finding
2's local half, then the (a)-vs-(d) decision for the coordinator half.

---

## 5. Not established

* **Nothing built, run, or tested.** Both resolutions are source reads at `5f4e24ac`.
* **The `DbiGuest` dettid detail in §1** — that the RPC envelope carries the execing thread's DetTid
  rather than the process's — is the one thing a non-leader exec depends on that I did not verify. It
  must be checked before finding 3 lands, because if `DbiGuest::new(tid, pid, …)` derives the RPC
  identity from `pid`, the coordinator would see `caller == new_leader` and take the wrong branch.
* **Option (d) has not been prototyped.** The claim that `pre_syscall` is reached again after a failed
  exec is from reading the callback structure, not from observing it.
* I did not re-verify finding 1; it is unchanged from the prior artifact.
