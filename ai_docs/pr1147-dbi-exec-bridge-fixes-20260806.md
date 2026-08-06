# PR #1147 — the three DBI exec-bridge findings: diagnosis, patch, and two ABI blockers

**Tasks:** `fix_pr_1147_fail` · `fix_pr_1147_failed` · `fix_pr_1147_nonleader` (one cluster, one file)
**Date:** 2026-08-06 · **Author:** hermit-design
**Head audited:** hermit `5f4e24ac` (current #1147 head; findings were raised at `683fb5ca` and **all three persist**)
**Status:** finding 1 patched and ready; findings 2 and 3 blocked on a C-ABI change — §4, §5. No build, no run, no egress.

The three findings live within ~200 lines of `detcore-dbi/src/lib.rs` and two of them touch the same
call site, so they are handled as one change set. I hold all three tasks; no other agent should edit
this file meanwhile.

---

## 1. Finding 1 — the fail-open PrepareExec bridge (patched)

```rust
// detcore-dbi/src/lib.rs:1325-1341 @ 5f4e24ac
fn drive_handler_to_ready<F: Future>(future: F) -> Option<F::Output> {
    let mut future = pin!(future);
    let mut context = Context::from_waker(Waker::noop());
    for _ in 0..1_000_000 {
        match future.as_mut().poll(&mut context) {
            Poll::Ready(value) => return Some(value),
            Poll::Pending => std::thread::yield_now(),
        }
    }
    None
}
```

and at the only call site (`:1382`):

```rust
drive_handler_to_ready(prepare_exec(&mut guest, mm, BTreeSet::new()));
```

**The `Option` is discarded.** No `let`, no `match`. So if the future is ever `Pending` for 1,000,000
polls, the bridge returns `None`, nobody reads it, and the native exec proceeds — with the coordinator
never having recorded `pending_exec_states`. The post-exec image then self-registers as a *fresh*
thread at logical time 0, `update_global_time` rewinds, and the panic poisons the scheduler mutex.
That is precisely the failure #1147 exists to prevent, re-armed as a function of host load.

**Why the arbitrary bound cannot be a correctness argument.** The function's own doc comment states the
real invariant: *"the DBI coordinator RPC client resolves each RPC synchronously … so a handler that
performs only RPCs (like `prepare_exec`) is ready on the first poll."* If that is true, `1_000_000` is
dead code. If it is false, no finite number is right — the loop converts a contract violation into a
host-load-dependent coin flip. A poll count is not a proof.

### The fix: assert the invariant the comment already claims

```rust
/// Drives a Detcore handler future that is CONTRACTUALLY ready on its first poll.
///
/// The DBI coordinator RPC client resolves each RPC synchronously (`reverie_dbi`'s
/// `run_ready`), so a handler performing only RPCs — `prepare_exec`, `cancel_exec` —
/// completes on poll #1. This polls exactly once and treats `Pending` as the
/// contract violation it is.
///
/// FAIL-CLOSED, deliberately. The previous form polled 1,000,000 times and returned
/// `Option`, and the sole caller discarded it: a `Pending` future meant the exec
/// proceeded with no `pending_exec_states` recorded, so the post-exec re-registration
/// rewound the coordinator clock and panicked — the exact defect this bridge exists
/// to prevent, re-armed silently as a function of host load. A bounded retry is not a
/// weaker version of correctness here; it is a coin flip. If the synchronous-RPC
/// contract ever breaks, the run must stop loudly at the violation, not continue into
/// a corrupted clock.
fn drive_synchronous_handler<F: Future>(future: F) -> F::Output {
    let mut future = pin!(future);
    let mut context = Context::from_waker(Waker::noop());
    match future.as_mut().poll(&mut context) {
        Poll::Ready(value) => value,
        Poll::Pending => panic!(
            "detcore-dbi: a Detcore handler driven from a DBI callback returned Pending. \
             The DBI coordinator RPC client is synchronous, so this handler must complete \
             on its first poll. Continuing would run execve without the coordinator's \
             pending_exec_states, rewinding the post-exec clock. Refusing."
        ),
    }
}
```

and the call site loses its silence:

```rust
drive_synchronous_handler(prepare_exec(&mut guest, mm, BTreeSet::new()));
```

The return type change from `Option<F::Output>` to `F::Output` is what makes the discard
unrepresentable — a caller can no longer ignore a failure, because there is no failure value to
ignore. `panic!` matches the file's existing idiom for broken invariants
(`.expect("Detcore DBI runtime lock poisoned")`, and the `assert!` in `reverie_dbi_runtime_exec_failed`).

### Tests

```rust
#[test]
fn drive_synchronous_handler_returns_a_ready_value() {
    assert_eq!(drive_synchronous_handler(async { 7_i64 }), 7);
}

/// The whole point: a Pending handler must ABORT, not silently continue. Before
/// this change the bridge returned None here and the caller discarded it.
#[test]
#[should_panic(expected = "must complete on its first poll")]
fn drive_synchronous_handler_refuses_a_pending_handler() {
    drive_synchronous_handler(std::future::pending::<()>());
}

/// Anti-regression on the SHAPE, not just the behaviour: the bridge must not
/// return an ignorable Option again. This fails to compile if it does.
#[test]
fn drive_synchronous_handler_yields_the_output_directly_not_an_option() {
    let value: u8 = drive_synchronous_handler(async { 3_u8 });
    assert_eq!(value, 3);
}
```

The `should_panic` test is the plant-a-violation bracket; the first is the positive control that keeps
the guard from being vacuously "always panics".

---

## 2. Finding 2 — failed-exec leaves `pending_exec_states` stale (BLOCKED, §4)

`reverie_dbi_runtime_exec_failed` (`:1213-1222`) asserts a runtime exists and calls
`resume_paused_runtime()`. That is all. It never tells the coordinator the exec did not happen.

The golden ptrace path does two things on a kernel-rejected exec (`detcore/src/syscalls/threads.rs`):

```rust
let errno = self.record_or_replay(guest, call).await.unwrap_err();
{   let thread_state = guest.thread_state_mut();
    thread_state.file_metadata      = old_metadata;
    thread_state.memory_metadata    = old_memory_metadata;
    thread_state.mm_id              = old_mm_id;      // <-- local rollback
}
cancel_exec(guest).await;                              // <-- coordinator rollback
```

So DBI is missing **both** halves. The consequence is not cosmetic: `pending_exec_states` keyed on this
process stays populated, and `receive_rpc`'s `exec_reconnect` lookup (`tool_global.rs:616-624`) matches
any later `CreateChildThread(child==dtid==process)` against that stale entry — so a *subsequent,
unrelated* registration is silently reconciled as an exec-reconnect, inheriting a clock and scheduler
identity it never earned. A failed exec followed by a retry or a fork is the reproducer.

`cancel_exec` exists (`tool_global.rs:2031`) but **is not exported**: `detcore/src/lib.rs:120` re-exports
only `prepare_exec`. The fix needs the symmetric `pub use tool_global::cancel_exec;` — additive, exactly
like the export #1147 already added for `prepare_exec`.

---

## 3. Finding 3 — non-leader exec is unbridged (BLOCKED, §5)

The gate at `:1505` is:

```rust
if tid == pid && !scratch.runtime_state.is_null() {
```

Linux permits **any** thread to `execve`; the non-leader becomes the new thread-group leader and the old
leader is destroyed. Detcore's own `reconnect_after_exec` (`scheduler.rs:1214-1293`) explicitly
implements `caller != new_leader`, so the coordinator half already exists — DBI simply never drives it.
A non-leader exec therefore takes the unbridged path: no `pending_exec_states`, fresh re-registration,
clock rewind.

The comment above the gate justifies `tid == pid` for *forked children* (an initialized process leader),
which is correct as far as it goes; it does not address the non-leader case at all.

---

## 4. Why findings 2 and 3 are blocked — the callback ABI

Both fixes need to send an RPC, and sending one requires building a `DbiGuest`, which needs
`context`, `tid`, `pid`, `branches`, `thread_state`, `invoke_syscall`, `read_registers`,
`write_registers` (see `send_dbi_prepare_exec`, `:1352-1386`).

`reverie_dbi_runtime_exec_failed(_scratch: *mut c_void, _pid: i32)` receives **two** of those, and both
are currently unused. There is no way to construct a guest, so `cancel_exec` cannot be issued from
where the failure is observed. Options, none of them local-only:

| Option | Cost |
| --- | --- |
| **(a) Extend the C ABI** of `reverie_dbi_runtime_exec_failed` to carry the same context/callbacks as `pre_syscall` | touches the DynamoRIO client side (reverie-dbi) as well as detcore-dbi — cross-repo, cross-pin |
| **(b) Stash the guest-construction inputs** at `PrepareExec` time in a process-global, consume on failure | no ABI change, but a process-global that must survive the pause handshake and be cleared on the success path too; a stale stash is its own correctness hazard |
| **(c) Coordinator-side expiry** — have the coordinator invalidate a `pending_exec_states` entry that is never reconciled | changes shared coordinator semantics for all backends; would need its own review |

**(a) is the faithful one** — it makes DBI's failed-exec path structurally parallel to ptrace's — and it
is the reason this cannot be finished in a local-only session: it needs a reverie change, a pin bump,
and a coordinated validate.

Finding 3 has a smaller version of the same problem plus an open semantic question: for a non-leader
exec the *caller* becomes the new leader, so `send_dbi_prepare_exec`'s `pid`/`MmId::for_exec(detpid)`
mapping has to be checked against what `reconnect_after_exec` expects for `caller != new_leader`. I did
not verify that mapping, and guessing it would risk substituting one wrong reconnect for another.

---

## 5. Determinism PR summary (for the eventual PR)

**Summary.** Replace `detcore-dbi`'s fail-open `drive_handler_to_ready` with a fail-closed
`drive_synchronous_handler` that polls once and aborts on `Pending`, and make the sole caller's success
non-optional. Findings 2 (failed-exec `CancelExec` rollback) and 3 (non-leader exec bridge) are
diagnosed and specified here but not included — both require a C-ABI change to the DBI failed-exec
callback (§4).

**Determinism.** The change strictly removes a nondeterministic outcome; it adds none. Before: whether
the coordinator learned about a pending exec depended on how many polls the future needed, i.e. on host
scheduling — the same guest could take the reconnect path on one run and the fresh-registration path
(clock rewind, panic, poisoned scheduler mutex) on another. After: the outcome is a function of the
program alone. Either the RPC completes on the first poll — the contract the synchronous coordinator
client guarantees — and the exec proceeds with clock continuity intact, or the process stops at the
violation. No timing-dependent branch remains. **No virtual time is blunted, coarsened, or reset**, and
no host serialization is introduced; the patch touches only how a future is driven.

**Linux Semantics.** Unchanged for finding 1: `execve` behaviour, ordering, and the pause handshake are
untouched; only the internal bridge's failure handling changes. Findings 2 and 3 are precisely where
Linux semantics are currently *violated* — a kernel-rejected `execve` must leave the process exactly as
it was (DBI leaves coordinator state dirty), and any thread may `execve` (DBI bridges only the leader).
Both remain violations after this patch and are called out rather than quietly narrowed.

**Validation.** **Not performed.** No build, no test run, no `--verify`, no wall/CPU numbers. See §6.

**Human Review Required.** #1147 already carries `post-facto-human-review`. This patch sits on the
exec/clock-continuity path that trigger #4 (core DetCore scheduling — how threads are scheduled and how
their logical clock is carried across exec) covers, so the label stays.

---

## 6. Status — patch specified, not applied, not validated

**No slot.** Editing `hermit/` requires one; allocation is coordinator-only. `worktrees/dbi/hermit` is on
another agent's branch (`hermit-dbi/relocate-backend-parity-matrix`) and `worktrees/val1147/hermit` is an
active validate-producer slot — taking either is the concurrent-writer hazard this cluster's own notes
warn about. Egress is 403, so nothing could be pushed.

Also outstanding on this head, from the validate lane and **not** one of the three findings: #1147 at
`5f4e24ac` fails full validation on a deterministic clippy error — `dbi_coordinator_connect_failed`
(`hermit-cli/src/lib.rs:1323`) is compiled unconditionally but used only under
`#[cfg(feature = "dbi")]`. Fix: gate the helper `#[cfg(feature = "dbi")]` and its test callers
`#[cfg(all(test, feature = "dbi"))]`. It will not flip on a re-run, and it will mask any validation of
these fixes until it is fixed.

**To land, in order:** allocate a slot on the #1147 branch → apply the §1 patch + tests → fix the clippy
gate → `cargo test -p detcore-dbi` → decide §4(a) vs (b) for finding 2 → verify the non-leader mapping
for finding 3 → full validate at the exact head.

---

## 7. Not established

* Nothing was built, run, or validated. All three findings were re-verified by **reading `5f4e24ac`**,
  not by reproducing them.
* The claim that a stale `pending_exec_states` entry mis-reconciles a *later* registration follows from
  reading `receive_rpc`'s `exec_reconnect` lookup; I did not construct the failing sequence.
* §4's option table is a design analysis, not a decision — (a) is recommended, not chosen.
* The non-leader `MmId::for_exec` / new-leader mapping (§4, last paragraph) is **unverified**; finding 3
  should not be implemented until it is.
* I did not confirm whether `1_000_000` polls has *ever* been exceeded in practice. The argument here is
  that the bound cannot be a correctness argument either way — not that the fail-open path has been
  observed firing.
