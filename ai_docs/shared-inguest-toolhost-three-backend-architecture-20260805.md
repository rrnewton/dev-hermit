# Shared in-guest tool host across all three patching backends — architecture proposal

**For owner review.** Task `shared_inguest_toolhost_family`.
**Date:** 2026-08-05 · **Author:** hermit-design · **Status:** design only — no code written, no build, no validate, no egress.
**Bound to:** reverie `025d37800d347c32711038bd0a3889e8e4774c2b` (primary, `main`) · hermit `b64d893ae9ea6404472eae9cb86102d91ec642ef`.
Every claim below carries a `file:line` at those SHAs.

**Builds on, does not fork:**
`ai_docs/shared-inguest-toolhost-build-spec-20260804.md` (Family-A seam, Path A),
`ai_docs/patching-backends-failclosed-inguest-sigsys-spec-20260805.md` (fail-closed rule + §5 conformance predicate),
`ai_docs/patching-backends-zero-ptracer-three-bucket-classification-20260804.md` (per-backend gap enumeration).
This document adds the third backend and the single statement of the shared interface.

---

## 0. The gate, and where we actually stand

The owner's architecture gate has two properties:

1. **SHARED CODE across all three** patching backends (sabre, e9patch, liteinst).
2. **ZERO PTRACER** — a patching backend whose *syscall path* needs a ptrace round trip is not
   architecturally correct. The fail-closed path is a systrap-style **in-guest signal handler**,
   never a ptrace trap.

| Backend | Gate 1 — shared host | Gate 2 — zero ptracer on the syscall path |
| --- | --- | --- |
| **liteinst** | **YES** — calls `reverie_preload::tool_host::drive_tool_syscall` (`reverie-liteinst/src/tool_host.rs:309`) | fail-closed **already in-guest SIGSYS**; residual is the CLI flip, not the syscall path |
| **e9patch** | **YES** — same shared driver (`reverie-e9patch/src/tool_host.rs:343`) | **NO today** — `install_hybrid_runtime` returns `Unsupported` (`reverie-e9patch/src/runtime.rs:259`; the comment at `:254` says "ptrace performs all event handling"). This is the owner's cited defect. |
| **sabre** | **NO — and this is the finding that changes the plan (§1)** | **NO** — a persistent `PTRACE_SYSCALL` supervisor stops **every** syscall entry *and* exit (`hermit/hermit-cli/src/sabre_ptrace.rs:301,582,610`), entered unconditionally on every SaBRe run (`hermit/hermit-cli/src/lib.rs:1056`) |

The prior planning documents record SaBRe as **out of scope** for the shared host — the
three-bucket table's SaBRe row reads "— (independent of shared host)", and a task note states
"SaBRe sb-3 … is INDEPENDENT of this host — do not fold it in." Under the owner's gate 1 that
disposition is no longer available, and §1 shows the code does not support it either.

---

## 1. The finding: SaBRe carries two *more* copies of the in-guest host, with a weaker policy

The Family-A work removed the second copy of the in-guest tool host. It did not remove the third
and fourth. `reverie/experimental/reverie-sabre/src/reverie_adapter.rs` (2211 lines) contains two
independent hosts for the same job:

* `ReverieAdapter<T>` (`:77`) — in-process `GlobalState`, dispatch at `:128`.
* `RemoteReverieAdapter<T>` (`:342`) — coordinator-RPC `GlobalState`, dispatch at `:517`.

Both re-implement, in their own way, exactly what `reverie-preload/src/tool_host.rs` now owns
once: the poll driver, the tail-inject rendezvous, the subscription filter, and the per-thread
state map. **And they implement it with a materially weaker policy in three places.**

### 1.1 `poll_once`, not poll-to-completion

```rust
fn poll_once<F: Future>(future: F) -> Poll<F::Output> {   // reverie_adapter.rs:959
    let mut context = Context::from_waker(Waker::noop());
    pin!(future).as_mut().poll(&mut context)
}
```

One poll. The shared driver loops (`drive_ready` `tool_host.rs:86`, `drive_syscall` `:140`). A Tool
handler that needs a second poll for any reason other than a staged tail-inject is failed with
`EIO` and an `eprintln` (`reverie_adapter.rs:163-171`). The same handler under liteinst/e9patch
completes.

### 1.2 No `ERESTARTSYS` restart protocol — the e9-3 defect, still live, in a third place

`shared_result` (`:796`) maps a Tool error through `error.into_errno()`. Detcore's `wait4`
scheduler poll returns the **private** `ERESTARTSYS` (512) to mean "re-run me once a sibling has
made progress". There is no restart loop here, so that private errno is returned to the guest as
an application-visible errno — the precise defect (`e9-3`, Reverie #362) that e9patch had before it
inherited `classify_outcome`. Non-errno Tool errors additionally
collapse to `EIO` (`:799`).

The shared driver already encodes the correct policy in one pure, tested function:

```rust
Ok(Errno::ERESTARTSYS) if number == Sysno::wait4 => None,   // tool_host.rs:257  (restart)
```

SaBRe does not call it. Fixing SaBRe by hand would be writing the protocol a **third** time — which
is the drift the shared host exists to prevent.

### 1.3 Allocation and blocking locks on the dispatch path

The SaBRe adapters use `parking_lot::Mutex` + `HashMap` for thread state (`:80`, `:142`) and the
crate links `mimalloc`. The preload host deliberately uses a non-allocating `SpinMutex`
(`reverie-preload/src/sync.rs`) and lock-free atomics for the tail rendezvous
(`TailResult`, `tool_host.rs:284`) because the in-guest host runs in async-signal context. This
difference is latent today (SaBRe dispatch is a plain plugin callback, not a signal handler) and
becomes **live** the moment SaBRe adopts the in-guest SIGSYS fail-closed path in §5 — see the risk
in §6.2.

### 1.4 Why SaBRe is nonetheless eligible for the shared host — the Path A precondition holds

Path A (the Family-A design of record) works because the shared driver never touches registers:
register access lives entirely inside each backend's `Guest<T>` impl. That was verified for
e9patch (`aot::current_regs()`, a thread-local) and liteinst (`HookContext*` carried on its event).

**SaBRe satisfies the same precondition.** `SabreGuest::regs()` (`reverie_adapter.rs:1132`) builds
`user_regs_struct` from `crate::callbacks::current_syscall_frame()` — a **thread-local frame
pointer**, exactly e9patch's shape, not a field of any syscall event. And `SabreGuest` (`:991`)
already implements `reverie::Guest<T>`.

The shared driver's signature is
`drive_tool_syscall<T: Tool, G: Guest<T>>(tool, guest, syscall, number, tail)` (`tool_host.rs:222`).
**SaBRe can call it as-is.** No new trait is required for SaBRe to adopt the driver, and no shared
Reverie public type changes — so this stays inside the additive-API policy and does not touch the
`Tool`/`Guest`/`Backend` contract.

### 1.5 The dependency is available and inert

`reverie-preload` is `crate-type = ["cdylib", "rlib"]`, and its `.init_array` constructor is behind
`#[cfg(feature = "preload-constructor")]` (`reverie-preload/src/lib.rs:258-260`), which is a
**default** feature. Depending on it as an rlib with `default-features = false, features =
["coordinator-rpc"]` links the host and installs **nothing** — the constructor, the seccomp filter
and the SIGSYS handler are all opt-in. Its dependency set is `libc` + optional
`serde`/`bincode`/`reverie-core`: no tokio, no nix, no ptrace. A SaBRe plugin can link it.

> **Naming consequence.** Once a non-`LD_PRELOAD` backend depends on it, the crate name
> `reverie-preload` misdescribes its role. Recommendation in §7.

---

## 2. The shared toolhost interface

One sentence: **the tool host is the thing that turns a synchronous in-guest trap into a driven
Reverie `Tool` callback and back into a syscall result.** Everything in that sentence is shared.
Everything about *how the trap arrived* and *how registers are reached* is per-backend.

### 2.1 What is shared (one implementation, in the shared crate)

| Element | Today | Status |
| --- | --- | --- |
| No-executor poll driver | `drive_ready` `tool_host.rs:86` | **shared, landed** |
| Tail-inject rendezvous | `TailResult` / `drive_syscall` `:284` / `:140` | **shared, landed** |
| Per-syscall restart loop | `drive_tool_syscall` `:222` | **shared, landed** |
| Restart **policy** (`ERESTARTSYS`/`wait4`) | `classify_outcome` `:249`, 6 unit tests | **shared, landed** |
| Terminal-outcome vocabulary | `DrivenSyscall{Result,Exit,ForkChild,Fatal}` `:189` | **shared, landed** |
| Non-allocating mutex | `SpinMutex` `sync.rs` | **shared, landed** |
| Fail-closed dispatch guards | `PassthroughDispatcher::apply_guards` `dispatch.rs:208` | **shared, landed** |
| Trusted-gate seccomp filter | `SeccompFilter::for_trusted_gate` `seccomp.rs:78` | **shared, landed** |
| SIGSYS handler + reserved-signal rule | `signal.rs:42` / `is_reserved` `:28` | **shared, landed** |
| Lifecycle-controller seam | `LifecycleController` `lifecycle.rs:50` | **shared, landed** |
| Subscription filter | duplicated per backend | **to hoist** |
| Thread-state map + first-poll lifecycle | duplicated per backend | **to hoist** |
| Slow-path counter accessor | **absent** (`tool_host.rs:44-54` records it as deferred) | **to add, non-`Option`** |
| Exit-time stats submission | liteinst-only (`runtime::submit_process_stats`) | **to hoist** |

### 2.2 What stays per-backend (the seam)

Exactly four things. A backend that needs a fifth is a signal that the seam is wrong.

1. **Trap installation / lifecycle** — how syscalls start arriving. Already a trait:
   `LifecycleController::install` (`lifecycle.rs:50`), with `InProcessSeccomp` (`:66`) and the
   `HybridPtrace` skeleton (`:95`).
2. **The `Guest<T>` impl** — registers, memory, `inject`/`tail_inject`, stack. Per-backend by
   necessity (§1.4): `E9patchGuest`, `LiteinstGuest`, `SabreGuest`.
3. **The concrete syscall-event type** — liteinst's `runtime::SyscallEvent` (carries `HookContext*`)
   vs. `reverie_preload::dispatch::SyscallEvent` vs. SaBRe's callback frame.
4. **The fatal path and the slow-path taxonomy values** — the *message* is backend-specific and the
   path enums genuinely differ (`LiteinstDispatchPath` vs. `SabrePatchRoute`/`SabreSlowPath`); they
   unify at the `CounterSnapshot<K>` **contract**, not at one enum. (This corrects the earlier
   "SaBRe conforms to the same taxonomy" claim, per the post-#373 stats-transport map.)

### 2.3 The two seam traits, and the one invariant that must not be optional

```rust
/// What the shared driver needs from a backend's concrete syscall event.
/// Deliberately does NOT expose registers: register access is Guest<T>'s job (§1.4),
/// which is what lets all three backends share the driver without a shared-type change.
pub trait HostSyscallEvent {
    fn number(&self) -> Sysno;
    fn args(&self) -> [u64; 6];
    fn set_result(&mut self, value: i64);
    fn source(&self) -> EventSource;   // DirectHook | SignalTrap | ...
}

/// What the shared host needs from a backend, beyond its Guest<T>.
pub trait HostBackend {
    type Event: HostSyscallEvent;
    type Guest<'a>: Guest<Self::Tool>;
    type Tool: Tool;

    fn fatal(&self, error: Error) -> !;      // backend-specific message + abort path

    /// NON-Option, deliberately. A converging backend must not be able to
    /// compile without declaring where its per-path counts go.
    fn slowpath_counter(&self) -> &dyn SlowpathCounter;
}
```

**The counter invariant.** `slowpath_counter` is not an `Option` and has no default. This is the one
piece of the interface that exists for a *process* reason rather than a mechanical one: LiteInst's
retired 14.5× slow path was invisible because a counter was quietly absent. A non-`Option` accessor
converts "forgot to count" from a silent measurement hole into a compile error. The shared host
owns **submission** at process exit; each backend owns its own path enum and supplies the values.

*Scheduling note, unchanged:* wiring the counter seam is measurement-adjacent, and the owner's order
is ptracer-out first. This document specifies the seam so it is designed against three real
implementors; it does not ask for it to be built ahead of the ptracer work.

---

## 3. How each backend plugs in

### 3.1 liteinst — already plugged in; the residue is coverage, not architecture

Calls the shared driver at `reverie-liteinst/src/tool_host.rs:309` and the shared `drive_ready` for
lifecycle callbacks (`:225`, `:243`, `:402`, `:434`). Keeps: `LiteinstGuest` (regs via `HookContext*`
on its own event), fork support (`finish_fork_child`), its own trap install, its `LiteinstDispatchPath`
enum and `submit_process_stats`.

Remaining, from the three-bucket enumeration: the CLI flip, and the in-guest event gaps (timer/PMU
`RCB=0`, CPUID/RDTSC, `clone3`/`vfork`/`exec`/vDSO). None of these change the host interface.

### 3.2 e9patch — plugged into the host; **not** plugged into an in-guest trap

Calls the shared driver at `reverie-e9patch/src/tool_host.rs:343` and installs the shared preload
runtime at `:146`. Its gate-1 obligation is met.

Its gate-2 obligation is not: `install_hybrid_runtime` returns `Unsupported`
(`runtime.rs:259`), so on `main` the whole event path is a ptrace round trip — **including DETLOG,
which is produced by the ptrace host rather than in-guest.** That is the specific defect the owner
named. The host being shared is what makes the fix tractable: the in-guest side already exists and
is exercised by liteinst; what is missing is a Detcore-embedding preload DSO so the Tool lives in
the guest at all (`ai_docs/e9patch-converge-remaining-lift-detcore-preload-cdylib-20260805.md`), plus
the `HybridPtrace` lifecycle owner or an `InProcessSeccomp` equivalent. Owned by
`e9patch_hybridptrace_inguest_converge`; that work has already demonstrated the end-to-end zero-ptracer
path on a branch (reverie #377 / hermit #1638) but it is not on `main` at `025d3780`.

### 3.3 sabre — the proposal

**Delta, in dependency order:**

| Step | Change | Removes |
| --- | --- | --- |
| S1 | `reverie-sabre` depends on `reverie-preload` (`default-features = false`, `features = ["coordinator-rpc"]`) | — |
| S2 | Replace `TAIL_INJECT_RESULT` (`:56`, a `thread_local Cell`) with the shared `TailResult` | one rendezvous copy |
| S3 | Replace `poll_once` (`:959`) at the *lifecycle* call sites with `drive_ready` | single-poll `EIO` failures on thread-start/post-exec/exit (`:205,:244,:273,:313`) |
| S4 | Replace both `dispatch_syscall` bodies (`:128`, `:517`) with `drive_tool_syscall(tool, guest, syscall, number, tail)` + a `match` on `DrivenSyscall` | the second and third host copies; **and fixes the `ERESTARTSYS` leak for free** |
| S5 | Hoist the subscription filter and thread-state map into the shared host | the last duplicated structure |
| S6 | `SpinMutex` in place of `parking_lot::Mutex` on the dispatch path | the allocation/blocking hazard that §5 makes live |

S4 is the load-bearing step and is small: `SabreGuest` already implements `Guest<T>`, so the driver
takes it unmodified. S1–S4 alone collapse three host implementations into one and eliminate a live
defect class without touching SaBRe's rewriting engine, its C plugin ABI, or any Reverie public type.

**What SaBRe keeps:** `SabreGuest` (regs from the callback frame), the SaBRe rewriting engine and
plugin ABI, its `SabrePatchRoute`/`SabreSlowPath` enums and shmem stats engine, and its
guest-sigaction virtualization (`src/signal.rs`).

---

## 4. The zero-ptracer fail-closed path, stated once for all three

**The rule** (from the landed fail-closed spec, restated because it is the gate): when a syscall
reaches the kernel from a site the backend did **not** instrument, the recovery path is an
**in-guest SIGSYS handler**, never a ptrace stop. A ptrace round trip on the syscall path is a
defect regardless of how rare it is claimed to be.

**The shared mechanism already exists in one place**, and all three backends can use the same copy:

1. `SeccompFilter::for_trusted_gate` (`reverie-preload/src/seccomp.rs:78`) installs a BPF program
   that compares `SECCOMP_DATA_IP` against the runtime's trusted gate:

   ```
   rt_sigreturn                       -> ALLOW      (:89, unwind the signal frame)
   IP == gate.syscall_ip | return_ip  -> ALLOW      (:94-95)
   everything else                    -> RET_TRAP   (:97)
   ```

2. `RET_TRAP` raises **SIGSYS in the faulting thread**; the handler is installed by
   `install_sigsys_handler` with `SA_SIGINFO | SA_ONSTACK` (`signal.rs:42`), on an alternate stack
   so a trap near the guest's stack limit still has frame room (`:68`).
3. The handler routes to the registered dispatcher, whose `apply_guards` (`dispatch.rs:208`) applies the fail-closed
   policy and tags the event `source() == SignalTrap`.
4. `InProcessSeccomp::install` (`lifecycle.rs:66`) does 1+2 in the required order: handler first,
   then filter.
5. `SIGSYS` is runtime-reserved (`signal.rs:28`); the dispatcher refuses guest attempts to change
   its disposition, mask, or alternate stack, so the guest cannot displace the trap.

**The key property for this proposal:** the filter is keyed on the **instruction pointer**, not on
the syscall number and not on any record of what was rewritten. It therefore traps precisely the
case "a syscall executed from a site we did not instrument" — with no per-syscall supervisor, no
stop, and no context switch to another process.

---

## 5. sb-3: the same shared mechanism replaces SaBRe's persistent ptracer

This is the largest single zero-ptracer violation in the tree, and it is the one that has been
treated as needing a SaBRe-specific answer. It does not.

**What sb-3 does today.** `hermit-cli/src/sabre_ptrace.rs` (1843 lines) attaches a supervisor and
resumes with `PTRACE_SYSCALL` (`:301`, and again after every stop at `:582`, `:610`). On every
syscall-entry stop it reads registers, computes `site = rip - 2`, reads two bytes, and only then
tests `bytes == [0x0f, 0x05] && fallback_ready` (`:511-530`). Every SaBRe run enters this
unconditionally (`hermit-cli/src/lib.rs:1056`).

So the mechanism **cannot** fire only on missed sites: the stop must already have happened before
the miss can be detected. Every syscall entry *and* exit pays a ptrace round trip, forever. As the
sb-3 audit put it, this is "architecturally a persistent ptracer, not a rare fallback".

**What it is trying to detect:** a raw `0f 05` executing from an address SaBRe failed to rewrite.

**What the shared filter detects:** a syscall executing from an IP that is not the trusted gate —
which is the same set, by construction, and it is detected *by the kernel at the trap*, with no
supervisor, no stop, and no second process.

**Proposal (A6):** SaBRe adopts `InProcessSeccomp` as its `LifecycleController`, and
`hermit-cli/src/sabre_ptrace.rs` is deleted rather than made selective. The miss is then handled the
same way liteinst already handles its `UnpatchableOrOther`/`CachelineStraddler` class: SIGSYS →
in-guest dispatcher → the shared driver → the Tool. Same code, same counters, one fail-closed path
across all three backends.

This is the move that satisfies **both** gates for SaBRe at once, which is why it belongs in this
document rather than in a separate sb-3 task: gate 1 (adopt the shared host, §3.3) and gate 2
(adopt the shared trap, here) are the same integration.

---

## 6. Risks, honestly stated

### 6.1 The trusted-gate filter traps the plugin's *own* syscalls (main integration cost)

The filter allows exactly two IPs. The SaBRe plugin issues syscalls from many sites of its own —
`mimalloc`, its RPC client, `nostd-print` — and every one of those will `RET_TRAP` into SIGSYS and
must be routed by the dispatcher, some of them re-entrantly. liteinst already lives with this
(it has a counted `InGuestNestedSigsys` class), so the pattern is proven, but for SaBRe this is
real work and is where the effort in §5 actually is. It is not a flag flip.

### 6.2 §1.3 becomes live under §5

`parking_lot::Mutex` + `HashMap` + `mimalloc` on the dispatch path are tolerable while dispatch is a
plain callback. Under a SIGSYS handler they are async-signal-unsafe: an allocation that takes an
allocator lock the interrupted thread already holds is a self-deadlock. **S6 is therefore not
optional once §5 lands** — it is a precondition, and this ordering should be explicit in the task.

### 6.3 SIGSYS ownership collides with SaBRe's sigaction virtualization

SaBRe maintains guest-facing vs. internal `sigaction` pairs (`experimental/reverie-sabre/src/signal.rs:36-39`).
`SIGSYS` must move to the reserved set (`reverie-preload/src/signal.rs:28`) and SaBRe's
virtualization must refuse guest changes to it. Two mechanisms currently believe they own signal
disposition; they must be reconciled before §5, not during.

### 6.4 The unproven premise in §5

"Trapped-by-IP catches exactly the set sb-3 catches" is argued from the filter's construction
(`seccomp.rs:78-99`) and from what `sabre_ptrace.rs:511-530` tests. It has **not** been demonstrated
by running a fixture with a deliberately un-rewritten site under both mechanisms and comparing
counts. That demonstration is the §8 predicate, and it must run before sb-3 is deleted. **Do not
delete the ptracer on the strength of this document.**

### 6.5 Scope

Nothing here changes `Tool`, `Guest`, `Backend`, or the syscall-interception model — the additions
are a new trait pair in `reverie-preload` and a new dependency edge. It stays inside the additive
Reverie API policy and does not trigger the core-abstraction review. §7's rename would be a
mechanical, separately-reviewable change.

---

## 7. Two decisions for the owner

**D1 — Does SaBRe join the shared host?** This document argues yes: it is a fourth and fifth copy of
the host (§1), it carries a weaker policy including a live `ERESTARTSYS` leak (§1.2), and it meets
the Path A precondition so adoption needs no new abstraction (§1.4). The counter-argument on record
is that SaBRe is `experimental/` and outside the main workspace. Under gate 1 as written, "shared
across all three" is not satisfied while two of the five host implementations are SaBRe's.

**D2 — Naming.** Once a non-`LD_PRELOAD` backend depends on it, `reverie-preload` misdescribes its
contents: it holds the backend-neutral in-guest host, the trusted-gate filter, and the SIGSYS trap.
Options: (a) leave the name and document the role — zero churn, permanently misleading; (b) split
the neutral parts into `reverie-inguest` and leave the `LD_PRELOAD` cdylib + constructor in
`reverie-preload` — one mechanical move, name matches contents. Recommend (b), sequenced **after**
the SaBRe adoption so the split is informed by a third real consumer.

---

## 8. Increments and the conformance predicate

Ordered so each step is independently compilable and testable, and so nothing lands ahead of the
owner's "ptracer out first, measure after".

| # | Step | Gate | Depends on |
| --- | --- | --- | --- |
| 1 | SaBRe S1–S4: adopt the shared driver + `TailResult` (§3.3) | 1 | — |
| 2 | SaBRe S6: `SpinMutex`, no allocation on dispatch (§6.2) | precondition for 4 | 1 |
| 3 | SaBRe S5: hoist subscription filter + thread-state map | 1 | 1 |
| 4 | SaBRe A6: `InProcessSeccomp` controller; reconcile SIGSYS ownership (§5, §6.1, §6.3) | 2 | 2 |
| 5 | Delete `hermit-cli/src/sabre_ptrace.rs` — **only after the §8 predicate passes** | 2 | 4 |
| 6 | e9patch: Detcore-embedding preload DSO + lifecycle owner (separate task) | 2 | — |
| 7 | liteinst: CLI flip + in-guest event gaps (separate task) | 2 | — |
| 8 | Non-`Option` `slowpath_counter` seam + shared exit submission (§2.3) | 1 | 1, 6 — **measure-after** |

Step 1 is the highest-value single step: it removes two host copies and one live defect class, and
it needs no seccomp work, no signal reconciliation, and no change outside `reverie-sabre`.

**Verification is the existing predicate, not a new one.** Use
`ai_docs/patching-backends-failclosed-inguest-sigsys-spec-20260805.md` §5 verbatim — plant an
un-instrumented syscall; require **both** sides counted at an exact SHA: in-guest SIGSYS dispatch
count **> 0** (positive: the mechanism fired, not inert) *and* per-syscall ptrace-stop count **== 0**
(negative: nothing round-tripped). A run that never executes the un-instrumented syscall is a
no-result, not a pass. For SaBRe specifically the negative bracket is
`SabreSlowPath::{PtraceSyscallEntry, PtraceSyscallExit, RawSyscallRedirect} == 0`.

Additionally, for step 1, a bracket that is cheap and worth having: a unit test asserting SaBRe's
dispatch **restarts** `wait4` on a private `ERESTARTSYS` rather than returning 512. Today that test
would fail; after step 1 it passes because `classify_outcome` is shared. It is the smallest possible
proof that the sharing is real rather than nominal.

---

## 9. What this document does **not** establish

* **No code was written, built, or run.** Every claim is a read of the tree at the two SHAs named in
  the header. No `cargo build`, no `cargo test`, no `validate.sh`, no network.
* **§5's central premise is unverified** (§6.4). The IP-keyed filter and the `rip - 2` byte test are
  argued to cover the same set; that has not been measured.
* **No cost estimate for §6.1.** How many SIGSYS traps the SaBRe plugin's own syscalls generate, and
  what that does to SaBRe's overhead, is unmeasured. It could be material and would be the honest
  reason to reject §5.
* **e9patch's branch state is not assessed here.** Reverie #377 / hermit #1638 are reported to have
  demonstrated the end-to-end zero-ptracer path; this document is bound to `main` at `025d3780`,
  where `install_hybrid_runtime` is still `Unsupported`.
* **No slot, no branch, no PR.** Implementation of any increment needs a slot and a Reverie PR.
