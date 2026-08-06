# DBI teardown exit RPC — what actually hangs, what main already fixes, and the one gap left

**Task:** `p1_fix_dbi_from` (#1147's sole red) · **Date:** 2026-08-06 · **Author:** hermit-design
**Status:** source audit + design. **No DBI build, no run, no egress** — see §6.
**Bound to:** hermit `f89c69766` (primary, `main`) · reverie `025d3780`

---

## 1. The premise, corrected before designing against it

The task is scoped as *"the guest's exit-RPC to the coordinator deadlocks; design a teardown-safe exit
notification."* Two things have to be said before building on that, both established in this task's own
history and re-verified against current source here.

**(a) The exit RPC is a victim, not the cause of the CI hang.** The canonical reproduction
(2026-07-30, `worktrees/dbi` on the #1147 head with a real git-dep reverie, no `[patch]`) produced a
coordinator-side **panic**, not an exit-path stall:

```
thread tokio-rt-worker panicked at detcore-model/src/time.rs:757:
  Attempted to update tid 2381755 time to 0ns, but was already 801_500_000ns
… Could not acquire scheduler lock during forced shutdown (poisoned lock)
```

A guest-initiated second `execve` re-registers the DBI thread with a fresh state at logical time 0;
`update_global_time` rewinds and panics; the panic **poisons the scheduler mutex**; and then *every*
in-flight guest RPC hangs — the exit RPC among them. The RPC trace showed the exit RPC was never even
reached. So "the exit RPC deadlocks" describes the symptom of a poisoned lock.

**(b) The remaining work on that mechanism is landing-order, not un-written code.** Bug A (post-exec
clock rewind) was fixed in #1147 `@683fb5ca` by sending `PrepareExec` from the still-live pre-exec DBI
thread; Bug B (scheduler tentative-pop admission) is #1200. Neither has landed:

| | On `main` `f89c6976`? | Evidence |
| --- | --- | --- |
| Bug A — DBI sends `PrepareExec` | **NO** | `grep -rn "prepare_exec\|PrepareExec" detcore-dbi/src/` → no hits |
| Bug B — #1200 scheduler fix | **NO** | round-3 adversarial review 2026-08-05 04:46 = **FAIL** (async DBI registration still host-timed across drain snapshots) |

None of that is re-derived here; it is cited so the design below does not silently claim to replace it.

---

## 2. What `main` already does right — the coordinator answers during teardown

This is the part worth knowing before designing a "teardown-safe coordinator exit notification": a
substantial one already exists on `main`, in `detcore/src/tool_global.rs::receive_rpc`.

`is_deregister` is computed up front (`:614`), and **two teardown races already resolve to an immediate
answer rather than a wait**:

| Situation | Line | Coordinator's response |
| --- | --- | --- |
| RPC from a **retired exec incarnation** (`rpc_incarnation_matches` false) | `:645-650` | `DeregisterThread(())` if it is a deregister; `ThreadExited` otherwise |
| Thread already **logically killed** (tombstoned) | `:662-699` | records `tombstoned_deregistration`, runs `recv_deregister_thread`, returns `DeregisterThread(())` |

Note the asymmetry, which is deliberate and correct: for any *other* request a dead thread gets
`ThreadExited`, but a **deregister is still honoured** — the coordinator will not leave an exiting
thread hanging just because it already tombstoned it. `update_global_time` is also skipped on those
paths (`:690-697`), so a late deregister cannot rewind the clock.

**So "the coordinator won't answer the exit RPC" is not true of an alive coordinator on current main.**
The ordinary teardown races are handled.

---

## 3. The gap that is left: the guest's wait is unbounded

The coordinator-side fix only helps if the coordinator is *alive and able to run*. The guest side has
no protection for the case where it is not.

`reverie/reverie-dbi/src/sync_rpc.rs:389` `read_exact_from_guest` loops on an **injected `SYS_read`**
with no deadline:

```rust
match invoke(context, invoke_syscall, libc::SYS_read, [fd, ptr, len, 0, 0, 0]) {
    Ok(0)   => return Err(UnexpectedEof),      // socket CLOSED -> bounded, fine
    Ok(n)   => { … }
    Err(e) if e.kind() == Interrupted => {}    // retry forever
    Err(e)  => return Err(e),
}
```

And no timeout is ever configured: `grep -n "SO_RCVTIMEO\|setsockopt\|timeout" sync_rpc.rs` → **no
hits**. So the three teardown outcomes are:

| Coordinator state | Guest outcome |
| --- | --- |
| answers (incl. every §2 tombstone path) | returns — fine |
| **closed the socket** | `read` → 0 → `UnexpectedEof` — bounded, fine |
| **alive but wedged** (poisoned sched mutex; or holding the sched lock through its own teardown) | socket stays open, nothing is written, guest blocks **forever** inside a DBI callback |

That third row is the 900 s `test.cli` timeout, and it is precisely the shape the task names: *blocking
on an RPC that the tearing-down coordinator won't answer.* A wedged coordinator is, from the guest's
side, indistinguishable from a slow one — and the guest has chosen to wait indefinitely for the
difference to resolve.

---

## 4. Why the exit RPC specifically may be made non-blocking

The exit notification is the one request whose response carries no information, which is what makes a
bounded wait safe here and not elsewhere. `detcore/src/tool_global.rs:2332` `deregister_thread`:

```rust
// TODO: void_send_rpc
let resp = reverie.send_rpc((threads_time, mm, GlobalRequest::DeregisterThread(thread))).await;
// We can't update the thread time here.  But it's dead anyway!
match resp.1 { GlobalResponse::DeregisterThread(x) => x, _ => unreachable!() }
```

`GlobalResponse::DeregisterThread` payload is `()`. The caller destructures it and discards it, and the
comment says the thread's time cannot be updated anyway. **The round-trip exists solely for ordering** —
to make the thread's logical removal happen-before whatever the coordinator does next. The pre-existing
`// TODO: void_send_rpc` marker is the same conclusion reached by whoever wrote it.

---

## 5. The design

**Bounded, observable, and honest about what it is.**

1. **Bound the exit wait.** Set `SO_RCVTIMEO` on the guest connection before the exit frame's read (or
   carry an explicit deadline through `read_exact_from_guest` for this request only). On expiry, stop
   waiting and continue teardown. Ordering is preserved in every case where the coordinator can run —
   which, per §2, is every ordinary teardown race. In the pathological case we give up a strict
   ordering guarantee that is *unobtainable anyway* (the coordinator is wedged) in exchange for a
   bounded exit.

2. **The timeout must be OBSERVABLE, never silent.** A quiet fallback here would be the
   silent-fastpath defect in its most damaging form: it would convert a wedged coordinator from a loud
   900 s timeout into a *clean-looking guest exit*, and the determinism failure underneath would stop
   being visible at all. So:
   * increment a named counter on the DBI slow-path taxonomy (`exit_rpc_timeout`), so it appears in
     backend stats and can be asserted on;
   * emit a diagnostic naming the tid and the elapsed wait;
   * **a run in which any exit RPC timed out must not report a clean exit** — the timeout is a
     degraded outcome and has to travel with the result, the same way `executed_tests` travels with a
     green.

3. **Do not make the deadline a bare constant.** It should be derived from the same budget the caller
   already has (the gate/test timeout), so the guest gives up strictly *before* the harness kills it —
   the point is to convert an unattributable 900 s kill into an attributable, counted, in-process
   failure. A fixed number picked here would be a second underived constant.

4. **Keep `void_send_rpc` as the eventual shape.** Once the ordering requirement is expressed
   explicitly (the coordinator's tombstone path already makes the deregister effect synchronous), the
   exit notification can become a genuine one-way send with no read at all — at which point the bound
   in (1) is unnecessary. The bounded read is the smaller, reviewable step toward it.

**What this is and is not.** It is a *containment* change: it converts an unbounded hang into a
bounded, counted, attributable failure. It is **not** a fix for Bug A or Bug B, and it must not be
allowed to make either look fixed. If it lands before them, the DBI post-exec panic will still poison
the scheduler — the difference is that the test will fail in seconds with `exit_rpc_timeout > 0`
pointing at the coordinator, instead of burning a 900 s budget and reporting a victim test.

### Classifying the failure when it fires

The task's own discriminator applies and should be recorded on both sides:

* `600.013 wall / 599.986 CPU` — a full core burned ⇒ **livelock**.
* **low CPU against high wall** ⇒ a **blocked wait** — which is what the exit-read hang is (the guest
  is parked in `read`, burning nothing).

That distinction is what separates "the guest is stuck on the exit RPC" from "the scheduler is
spinning", and it is cheap to capture. Record both numbers at each observation rather than inferring
the class from duration.

---

## 6. What I could not do, and why

**No DBI build, no reproduction, no verification of the fix.** The task asks to verify a clean DBI
teardown, and I did not:

* Building/testing hermit requires a slot. Allocation is coordinator-only; the one slot I hold
  (`worktrees/coord/hermit`) is on an unrelated branch for `port_validate_sh_to`, and feature work in a
  primary checkout is forbidden (Hard Invariant 1).
* A DBI build is not incidental — the prior canonical repro needed a full release build plus an
  activated DynamoRIO submodule.

**Correction to something I reported earlier today:** I previously recorded that no hermit binary can
run on this box because `libunwind-x86_64.so.8` is missing and `ldconfig -p` shows nothing. That is
true of the *system* loader path, but `/tmp/lu` contains `libunwind-1.8.0-4.el9` and
`libunwind-devel-1.8.0-4.el9` RPMs plus an extracted `usr/` tree — so a run is possible with
`LD_LIBRARY_PATH` pointed at it. The earlier "cannot run at all" claim was too strong; the correct
statement is that the system lacks libunwind and the workaround is `/tmp/lu`.

So this document is a **source audit plus a design**, and the verification bar the task sets — "show
the deadlock reproduced, the fix applied, and the teardown path completing, with wall AND cpu at each"
— is **not met here**.

---

## 7. Recommended order

1. **Land Bug A** (#1147 `@683fb5ca`, DBI sends `PrepareExec`) — it removes the panic that poisons the
   mutex, which is what actually wedges the coordinator.
2. **Resolve #1200 round-3** (async DBI registration still host-timed across drain snapshots).
3. **Then** add the bounded exit wait (§5) as defence in depth, with the counter and the
   not-a-clean-exit rule. Adding it *before* (1) risks masking the panic behind a tidy timeout.
4. Retire the round-trip entirely (`void_send_rpc`) once ordering is expressed explicitly.

---

## 8. Not established

* No build, no run, no `--verify`, no wall/CPU measurements of my own. Every claim above is a read of
  hermit `f89c69766` and reverie `025d3780`.
* The "alive but wedged ⇒ guest blocks forever" row in §3 follows from the absence of any timeout in
  `sync_rpc.rs` and the unbounded retry loop; I did not observe it in a live run this session (it was
  observed by others on 2026-07-30, on the #1147 head, not on current main).
* Whether the §2 tombstone paths fully cover the teardown races **for DBI specifically** was not
  exercised — they are coordinator-side and backend-agnostic by construction, but DBI's re-registration
  behaviour is exactly the thing that has surprised this codebase before.
* The `exit_rpc_timeout` counter name is proposed, not reconciled with the existing DBI slow-path
  taxonomy (`SabrePatchRoute`/`LiteinstDispatchPath` have their own enums; DBI's `FALLBACK_*` atomics
  live in `reverie-e9patch`, and reverie-dbi has `backend_stats.rs` — the right home was not confirmed).
