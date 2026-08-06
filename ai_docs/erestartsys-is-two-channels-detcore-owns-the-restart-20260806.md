# ERESTARTSYS is two different things wearing one errno

**Task:** `erestartsys-retry-is-a-detcore-correctness-property-not-a-backend-one` (P1, OWNER RULING)
**Date:** 2026-08-06 · **Author:** hermit-design · **Class:** CORE-CHANGE (see §6)
**Bound to:** hermit `f89c69766` · reverie `025d3780` · Local source audit. No build, no run, no egress.

> **Owner:** *"Retrying those is an ESSENTIAL PART OF DETCORE. But if the local detcore tool retries them
> correctly then IT SHOULD BE ROBUST TO THEM OCCURRING NONDETERMINISTICALLY, IRRESPECTIVE OF REVERIE
> BACKEND. Sounds like a DETCORE correctness thing not a REVERIE BACKEND correctness thing."*

The ruling is right, and the reason it is right turns out to be sharper than "fix it once instead of N
times". **`ERESTARTSYS` is currently carrying two semantically different messages on the same wire**,
and that overload is what forces every backend to re-derive the distinction.

---

## 1. WANTED #1 — the enumeration. Two channels, not one site

`retry_nonblocking_syscall_helper` is **not** the single choke point. There are two structurally
different producers, and they mean opposite things.

### Channel A — ERESTARTSYS as a RESULT (Linux-faithful; must reach the guest)

`detcore/src/syscalls/helpers.rs:1121` `retry_nonblocking_syscall_helper`, on interruption:

```rust
if resource_request(guest, rsrc.clone()).await == ResumeStatus::Signaled {
    let errno = call.signal_interrupt_errno();     // :1140
    return Err(errno.into());                      // :1146
}
```

`signal_interrupt_errno()` is **per-syscall and already Linux-faithful** — default `ERESTARTSYS`
(`helpers.rs:673`, *"Most blocking I/O is restartable when its handler uses SA_RESTART"*), overridden
to `EINTR` for the syscalls Linux never restarts. Pinned by an existing test,
`signal_interruption_errno_matches_linux_restart_policy` (`helpers.rs:1298`):

| Syscall | `signal_interrupt_errno()` |
| --- | --- |
| `Read`, `Futex` | `ERESTARTSYS` |
| `Poll`, `Ppoll`, `EpollWait`, `RtSigtimedwait` | `EINTR` |

This channel is **correct as it stands**. Its `ERESTARTSYS` is a genuine guest-visible result with real
Linux restart semantics attached.

### Channel B — ERESTARTSYS as a CONTROL SIGNAL (no Linux meaning; must never reach the guest)

`detcore/src/syscalls/threads.rs:966`, inside `handle_waitid`'s internal poll loop:

```rust
if signaled {
    return Err(Errno::ERESTARTSYS.into());
}
```

This is Detcore's **scheduler poll**. It means *"re-invoke my handler once a sibling has made
progress"*. It is not a Linux errno the guest is meant to observe. Under ptrace the kernel's syscall
restart frame consumes it transparently, so the backend appears to need no cooperation. Under any
in-guest dispatcher there is no restart frame, so each host has to implement the protocol itself —
which is exactly what happened:

* `reverie-preload/src/tool_host.rs` carries an *"ERESTARTSYS restart protocol (Reverie #362)"* whose
  `classify_outcome` reads `Ok(Errno::ERESTARTSYS) if number == Sysno::wait4 => None` (restart);
* `reverie-liteinst/src/bin/lifecycle_guest.rs:98` emits one of its own;
* SaBRe's `ReverieAdapter` has no such handling at all, so the private errno leaks to the guest as
  errno 512 — the `e9-3` defect class, still live there.

**Note what that consumer had to write: `number == Sysno::wait4`.** A *syscall-number* proxy standing in
for a semantic distinction the value itself cannot express. That is the tell.

### Two further sites, inventoried because any change to the channel touches them

* `detcore/src/syscalls/files.rs:302` — `close()` computes
  `fd_was_released = !matches!(res, Err(EBADF) | Err(ERESTARTSYS))`. Channel-A-typed and correct; it
  would silently change meaning if channel B's representation were reused here.
* `detcore/src/syscalls/signal.rs:276` — *"do not expose a tracer preemption as ERESTARTSYS to the
  guest."* This is **precedent for exactly the rule proposed below**, already applied at one site.

---

## 2. WANTED #2 — reconciling with the Chaos Tool break

The evidence in the graph: *"CI on the composite branch proved GENERIC ERESTARTSYS RETRY BREAKS CHAOS
TOOL READ."* That result is not a puzzle once the two channels are separated:

* A **generic** retry retries **channel A**. Channel A is a *result*. Retrying it swallows an
  interruption the guest is entitled to observe — and the Chaos Tool's whole job is to inject exactly
  those interruptions into `read`. So a generic retry does not merely bend Chaos; it deletes the
  behaviour Chaos exists to produce.
* Channel B is the one that must be retried, and it is a *control signal* with no Linux meaning.

**So the correct predicate is not a new invention and must not be a syscall allow-list.** It already
exists as `signal_interrupt_errno()`, and it is Linux's own SA_RESTART table. The actual defect is one
level up:

> **Channel B overloads a guest-visible errno as an internal control signal.** Any rule keyed on the
> *value* `ERESTARTSYS` therefore cannot distinguish "the guest must see this" from "detcore wants to
> be re-invoked" — which is why the one consumer that got it right had to key on `Sysno::wait4`
> instead, and why every new backend re-inherits the hazard.

---

## 3. The fix — detcore owns the restart, and the channel stops being an errno

Make channel B a distinct internal signal that never crosses the `Tool` boundary:

1. **Represent the scheduler-poll restart as its own thing**, not `Errno::ERESTARTSYS`. It has no Linux
   meaning, so it should not borrow a Linux errno's representation.
2. **Detcore re-enters its own poll.** `handle_waitid` is already a `loop`; the restart becomes a
   `continue` after signal delivery is allowed, rather than a return value the host must interpret.
3. **Consequently every backend becomes robust with no backend change** — there is nothing left for a
   host to recognize.
4. **`classify_outcome`'s `wait4` special case becomes dead** and should be deleted *with* a test
   asserting that a bare `ERESTARTSYS` from `wait4` now surfaces to the guest as a result — because
   after the split, that is the only thing it can mean.

**The one hard part, stated rather than glossed:** detcore cannot simply spin. When `signaled` is true a
signal is pending, and Linux's own behaviour is that `wait4` returns `ERESTARTSYS`, the handler runs,
*then* the syscall restarts. So the internal loop must still yield for signal delivery before
re-polling; the restart must be "re-invoke after the guest's signal machinery has run", not "poll
again immediately". Getting that wrong would either (a) starve signal delivery — a hang — or
(b) re-poll before the handler runs, changing observable ordering. This is the part that needs the
dual review, not the representation change.

---

## 4. WANTED #3 — the test

**Property:** detcore's output is unchanged when `ERESTARTSYS` occurs nondeterministically.

* **Injection point.** Force `ResumeStatus::Signaled` at randomized points across a run — i.e. inject
  at the *channel* rather than by sending real signals, so the injection is controllable and does not
  itself change scheduling.
* **Positive assertion.** DETLOG + stdout byte-identical between an injected run and an uninjected one,
  at L2 (`--strict --verify --verify-strict --verify-json`, `bitwise_parity: true`).
* **The negative control that must not regress — the Chaos read.** A Chaos Tool `read` interruption
  must *still surface* to the guest. Without this bracket the test is satisfiable by the very generic
  retry that broke Chaos, which makes it worse than no test.
* **Cross-backend.** ptrace plus at least one in-guest backend, since the whole point is that the
  property must not depend on the kernel restart frame. LiteInst is the natural second: it already has
  an in-guest host and already emits a channel-B ERESTARTSYS of its own.
* **Anti-vacuity.** Assert the injection actually fired (a nonzero injected-interrupt count). An
  injection test that injected nothing passes trivially — the same zero-executed-tests trap as
  elsewhere in this codebase.

---

## 5. Determinism and Linux Semantics (mandatory PR sections)

**Determinism.** The change removes a nondeterminism rather than adding one, and the argument is
structural, not empirical. Today whether a guest observes errno 512 depends on *which backend* is
running: ptrace's kernel restart frame consumes the private value, reverie-preload's driver loops on
it, SaBRe's does not and leaks it. Identical guest, identical inputs, different observable results —
i.e. the backend is an input to the program's output, which is precisely what Detcore exists to
eliminate. After the split, channel B never leaves detcore, so no backend can observe or mishandle it,
and channel A's per-syscall policy is unchanged. **No virtual time is blunted, coarsened, or reset; no
host serialization is introduced.**

**Linux Semantics.** Channel A already implements Linux's SA_RESTART policy per syscall and is
untouched: `read`/`futex` keep returning `ERESTARTSYS`, `poll`/`ppoll`/`epoll_wait`/`rt_sigtimedwait`
keep returning `EINTR`, and the guest's restart behaviour is unchanged. Channel B is *not* a Linux
concept — no Linux program can observe a `wait4` that returns 512 — so removing it from the guest-visible
surface moves the implementation *toward* Linux fidelity. The interaction to preserve is signal-delivery
ordering (§3): the guest's handler must still run between the interruption and the retry, exactly as
Linux does.

---

## 6. Status and gating

**CORE-CHANGE.** This is post-facto-human-review trigger #4 territory (it changes how a thread's
scheduler poll is restarted, which is how threads get scheduled) and the task states it is dual-review
gated with mandatory Determinism + Linux Semantics sections. §5 is drafted to that bar.

**Not implemented.** Editing `detcore/` requires a slot; allocation is coordinator-only and every
worktree is on another agent's branch or task. Egress is 403. This session produced the enumeration,
the predicate reconciliation, the fix design, and the test design — the three WANTED items — not the
code.

**Recommended order:** (1) split the channel and make detcore re-enter its own poll, with the
signal-delivery ordering handled explicitly; (2) add the injected-`ERESTARTSYS` test with the Chaos
negative control; (3) only then delete `classify_outcome`'s `wait4` case and SaBRe's missing-handling
becomes a non-issue rather than a fix.

---

## 7. Not established

* **Nothing built, run, or tested.** Every claim is a read of hermit `f89c69766` / reverie `025d3780`.
* **The enumeration is grep-scoped** to `ERESTARTSYS` in `detcore`/`detcore-model` plus the three
  in-guest reverie hosts. A path that produces the value indirectly — via a variable, or a
  `signal_interrupt_errno()` override I did not enumerate — would not have been caught. The two-channel
  *distinction* is what I am confident in; I would not claim the site list is exhaustive.
* **The Chaos-break claim is quoted from the task graph** (`reverie_337_fix_liteinst`), not reproduced.
  My reconciliation explains it consistently, which is weaker than confirming it.
* **§3's "detcore can re-enter its own poll" is a design assertion.** `handle_waitid` is already a
  loop, but whether signal delivery can be sequenced correctly inside it — without starving the handler
  or reordering it — is exactly what I could not verify without building and running. Treat it as the
  proposal's main risk, not a settled point.
