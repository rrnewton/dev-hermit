# `clone_with_stack` SIGSEGV is an infallible-`Timer::new` panic converted by an `extern "C"` frame — NOT the `detcore_misc` / reverie#355 livelock

**Task:** `reverie_clone_with_stack` ("reverie-clone-with-stack-sigsegv-under-concurrency")
**Agent:** hermit-clone (opus-5), 2026-08-05. **Constraint:** box-wide egress 403 → LOCAL ONLY
(no fetch/push/PR). Research-only; no product file was modified.
**Prior work incorporated:** kvm-lane's root-cause notes on this task (2026-08-04) and its
standalone forcing harness `scratch/curve3-concurrent-hermit/force/`.

---

## Headline

The CURVE-3 crash is now **deterministically reproducible SOLO at N=1 with zero concurrency**,
and its full backtrace — never captured in CURVE-3 — is recovered. It is **not** the reverie#355
chain, on both mechanism and pin evidence.

| | `clone_with_stack` crash (this task) | `detcore_misc` livelock (reverie#355) |
|---|---|---|
| Code site | `reverie-ptrace/src/timer.rs:401` panic, converted at `reverie-process/src/clone.rs:27` | `safeptrace/src/notifier.rs` `decode_status_return` |
| Layer | **host-side sandbox-container creation**, before the guest runs one instruction | **guest tracee ptrace-status decoding**, at guest teardown |
| Failure class | **fault conversion** — panic → `panic_cannot_unwind` → died-by-signal | **liveness** — `PTRACE_GETEVENTMSG` ESRCH hot spin |
| Signature | instant death, negligible CPU, `Signaled(SIGSEGV, true)` | wall ≈ CPU at budget, one core burned, never completes |
| Concurrency | **NOT required** — forced at N=1 (this work) | load-dependent race window |
| In the CURVE-3 build? | yes, crashed | **no — #355 was already fixed in that binary** |

**Verdict: different bug, different layer, different failure class. Not the #355 chain and not
amplified by it.** They share only the coarse property "load-correlated reverie child-lifecycle
failure that silently loses cells from a wide validate fan-out".

---

## 1. Pin evidence that rules out the #355 chain

CURVE-3 was measured on hermit `a6201fc6`. Its **`Cargo.lock` pins reverie
`79517704b0d19eeb3c4c234d0bfbe9f0a17c1199`** — which *is* the #355 landing commit
(`7951770 safeptrace: consume dead ptrace status on decode error to end ESRCH hot spin (#355)`,
authored 2026-08-04 03:06, i.e. hours before the CURVE-3 sweep).

```
git -C hermit show a6201fc6:Cargo.lock | grep 'rrnewton/reverie'
#  -> git+https://github.com/rrnewton/reverie.git?rev=79517704b0d19eeb3c4c234d0bfbe9f0a17c1199
git -C reverie merge-base --is-ancestor 7951770 59115421   # rc=0
```

So the ESRCH hot spin was **already fixed** in the binary that produced the SIGSEGVs. #355 cannot
be the mechanism, and cannot be the resource-pressure amplifier for it either.

*Trap avoided:* the pre-merge branch SHA `820b2b64` (quoted in the #355 memory) is **not** an
ancestor of anything on reverie main — the fix landed squashed as `7951770`. Testing ancestry with
`820b2b64` gives a false "not fixed" answer. Also, the task header's "Reverie 59115421" is the
*submodule/primary* state, not the *cargo* pin the binary was actually built from (cf.
`reverie-submodule-vs-cargo-pin-resolve-independently`); both contain #355, but only the cargo pin
is load-bearing.

---

## 2. The mechanism, end to end (forced solo, N=1)

Two independent defects compose. Neither alone produces the observed crash.

### (A) Producer — `Timer::new` is a deliberately infallible API over a resource acquisition

`reverie/reverie-ptrace/src/timer.rs:393-405`:

```rust
impl Timer {
    pub fn new(guest_pid: Pid, guest_tid: Tid) -> Self {
        // No errors are exposed here, as the construction should be
        // bullet-proof, and if it wasn't, consumers wouldn't be able to
        // meaningfully handle the error anyway.
        Self {
            inner: if is_perf_supported() {
                Some(TimerImpl::new(guest_pid, guest_tid).unwrap_or_else(|err| {
                    panic!("failed to initialize perf timer for tracee {guest_tid} ...")
```

`TimerImpl::new` calls `perf_event_open`, which **acquires a per-tracee kernel resource and can
fail for many reasons**. The API models it as infallible and panics on *any* errno. One `Timer` is
created per tracee thread (`TracedTask::new`), so the exposure scales with tracee count × instance
count.

### (B) Converter — the `extern "C"` clone callback turns that panic into death-by-signal

`reverie/reverie-process/src/clone.rs:27-30` (unchanged since the initial commit):

```rust
extern "C" fn callback(data: *mut CloneCb) -> libc::c_int {
    let cb: &mut CloneCb = unsafe { &mut *data };
    (*cb)() as libc::c_int          // any panic in here cannot unwind
}
```

The **only** real caller is `Container::run` (`reverie-process/src/container.rs:784`), whose child
closure runs an arbitrary Rust `f()` — in hermit, the entire supervisor
(`RunOpts::run_in_container` → `run_with_backend` → tokio → `TracerBuilder::spawn` →
`TracedTask::new` → `Timer::new`). A panic anywhere in that stack reaches the `extern "C"` frame →
`core::panicking::panic_cannot_unwind` → `"thread caused non-unwinding panic. aborting."` → the
child dies by signal → the parent surfaces only
`Error: Sandbox container exited unexpectedly (Signaled(SIGSEGV, true))`
(`hermit-cli/src/bin/hermit/container.rs:222`).

`grep` confirms **no `catch_unwind` anywhere in `reverie-process/src`.**

---

## 3. Forcing experiment — SOLO, N=1, deterministic (no sweep, no concurrency)

The CURVE-3 crash was believed to need N≥32 concurrency. It does not: the concurrency was only a
*way to exhaust a resource*. Exhaust the resource directly and the crash is deterministic at N=1.

```bash
LD=.../hermit-cli/__hermit__/__hermit__shared_libs_symlink_tree      # supplies libunwind-x86_64.so.8
H=hermit/target/release/hermit                                       # built 2026-08-03, reverie d973a85
( ulimit -n 16; LD_LIBRARY_PATH=$LD RUST_BACKTRACE=1 $H run --strict /bin/true )
```

Box was **quiet** (316 cores, load ~5.6). Threshold bisect, one sequential run per row:

| `RLIMIT_NOFILE` | result |
|---|---|
| 256 / 64 / 32 / 28 / 24 / 20 | rc=0, clean |
| **17** | **crash, exact CURVE-3 signature** |
| **16** | **crash, exact CURVE-3 signature** |

Recovered stderr (the part CURVE-3 never captured is the **first** stanza):

```
ERROR reverie_ptrace::perf: PMU validation failed; RCB timers may be unreliable
      error=CouldNotCreateTimer { errno: EMFILE, ... }

thread 'main' panicked at reverie-ptrace/src/timer.rs:279:21:
failed to initialize perf timer for tracee 3 in process 3: -24 EMFILE (Too many open files)
   2: <reverie_ptrace::timer::Timer>::new::{closure#0}
   4: <reverie_ptrace::task::TracedTask<detcore::tool_local::Detcore>>::new
   6: <reverie_ptrace::tracer::TracerBuilder<..>>::spawn::{closure#0}
   7: hermit::run_with_backend_inner::{closure#0}
  12: <hermit::run::RunOpts>::run_in_container
  13: <reverie_process::container::Container>::run::<...>
  14: reverie_process::clone::clone_with_stack::callback
  15: __clone

thread 'main' panicked at core/src/panicking.rs:225:5:
panic in a function that cannot unwind
  11: core::panicking::panic_cannot_unwind
  12: reverie_process::clone::clone_with_stack::callback
  13: __clone
thread caused non-unwinding panic. aborting.
Error: Sandbox container exited unexpectedly
     > Process exited with code: Signaled(SIGSEGV, true)
```

The last four lines are **character-identical** to CURVE-3's Finding 3 quote
(`experiments/concurrent-hermit-scaling_20260804/README.md:99-116`), **including `SIGSEGV`**.

**This also resolves the SIGABRT-vs-SIGSEGV discrepancy** flagged as an honesty caveat on
2026-08-04: kvm-lane's standalone harness produced `SIGABRT(6)` while CURVE-3 recorded `SIGSEGV`.
Running the *real* hermit binary produces `SIGSEGV` from the identical `panic_cannot_unwind` path.
The signal number is a property of the binary's abort lowering, not of a different bug — the
harness and CURVE-3 were the same mechanism after all.

*Caveat on the binary used:* the prebuilt `hermit/target/release/hermit` was compiled against
reverie `d973a85` (pre-#355), not CURVE-3's `7951770`. Irrelevant to this result — `clone.rs` and
`timer.rs` are byte-identical across that range and #355 touches only `safeptrace/notifier.rs` —
but stated for exactness.

---

## 4. What is NOT established: which resource ran out at N≈96

**Confirmed:** the fault-*conversion* mechanism, and that EMFILE at `Timer::new` is a *sufficient*
cause of the exact observed signature.

**Not confirmed:** that EMFILE was the *actual* errno in CURVE-3. The CURVE-3 log quote is only the
tail; the panic's first line — the sole place the errno is printed — was not captured. `Timer::new`
panics on **any** `perf_event_open` failure, so ENOSPC / EACCES / EBUSY / ENFILE all yield a
byte-identical tail.

Plain per-process fd exhaustion is **implausible** as the CURVE-3 trigger on this box:
`ulimit -n` = 524288 soft *and* hard, `fs.file-max` effectively unlimited, and hermit/reverie set no
`RLIMIT_NOFILE` of their own (`grep -rn 'RLIMIT_NOFILE|setrlimit' hermit/hermit-cli/src
reverie/reverie-process/src` → only `RLIMIT_CORE` in `exit_status.rs`). A trivial
`hermit run --strict /bin/true` needs only ~18-20 fds. So under free (non-sandboxed) N=96 the
leading candidate is **perf-subsystem contention** — many instances × many tracees each calling
`perf_event_open` for a hardware counter — not fd count. Inside the 3pai sandbox a BpfJailer
FILE_OPEN cap would make EMFILE reachable directly.

**Cheapest way to close this gap — no wide sweep needed:** re-run at the *onset* (N=32, where
CURVE-3 saw 1/32) capturing **full per-instance stderr**, and read the errno off the first panic
line. One crash is sufficient.

---

## 5. Recommendations (both additive, `rrnewton/reverie` only; neither is a Tool/Guest/Backend change)

1. **(B) Guard the FFI boundary — `reverie-process/src/clone.rs`, ~6 lines.** Wrap the callback
   body in `catch_unwind(AssertUnwindSafe(...))`, `Err(_) => 101`. `clone(2)` uses the return value
   as the child exit code, so the child exits cleanly at 101; the parent's existing short-read path
   (`container.rs:820-828`) reports a retryable failure instead of a SIGSEGV. The default panic hook
   still runs first, so the stderr diagnostic is preserved. Zero-cost happy path, no new post-clone
   allocation or lock. Both repos build `panic=unwind`, so `catch_unwind` actually catches.
   Forcing-verified by kvm-lane: buggy 50/50 abort → fixed 0/50 clean exit 101.
   **Regression test:** `clone_with_stack` a panicking closure; assert child `WaitStatus` is
   `Exited(101)`, not a signal death.
2. **(A) Make the producer diagnosable.** `Timer::new`'s "consumers wouldn't be able to meaningfully
   handle the error anyway" is false at the fan-out layer: a caller *can* meaningfully back off or
   retry an `EMFILE`/`ENOSPC` perf-event failure. Note reverie already treats these errnos as
   recoverable elsewhere (`reverie-ptrace/src/tracer.rs` `skippable_tracee_open_error` handles
   `EMFILE`/`ENFILE`/`EIO`). Minimum viable: keep the panic but ensure its message reaches the
   parent — with (B) in place, serialize the panic payload into the `Result` already bincoded
   through the pipe, so `with_container` reports *"failed to initialize perf timer … EMFILE"*
   instead of *"Sandbox container exited unexpectedly"*.

Fix (B) converts a hard crash into a clean, classifiable, retryable failure. It does **not** remove
(A)'s exhaustion; post-fix, a wide fan-out past onset yields diagnosable failed cells rather than
silent SIGSEGV loss.

---

## 6. Answer to "could root-causing this unblock the validate producer path?"

**Partly, and not via the livelock.** The two are decoupled:

- The **validate producer path** (`ci-hub validate-run` → one boxed `systemd-run --user` transient
  unit) is single-instance. This crash needs a *resource-exhausted* supervisor startup; it is not
  what wedges a normal validate.
- What this *does* unblock is the **wide-fan-out corpus strategy**: CURVE-3's recommendation of
  ~128-way with crash-retry exists only because cells are lost to SIGSEGV. With (B), a lost cell
  becomes a clean exit code the harness can classify and retry, and with (A)'s diagnostic it becomes
  attributable to a named resource.
- The **still-open liveness residue** is the separate detcore_misc "Face B" (~1/2760 guest `wait4`
  poll spin after a SIGKILLed child), backlogged as
  `detcore-misc-residual-passive-block-1-in-2760`. That, not this crash, is the surviving
  livelock-class item. Its wall≈CPU burned-core signature is the discriminator; this crash has
  negligible CPU and dies instantly.

---

## Reproduction

```bash
cd ~/work/dev-hermit
LD=/data/users/newton/dotsync-home/work/orc-dev/fbsource-hermit-import-20260731/buck-out/v2/art/\
fbcode/ba600ed59cf27948/hermetic_infra/hermit/hermit-cli/__hermit__/__hermit__shared_libs_symlink_tree
( ulimit -n 16; LD_LIBRARY_PATH=$LD RUST_BACKTRACE=1 \
  hermit/target/release/hermit run --strict /bin/true )
# expect: Signaled(SIGSEGV, true), preceded by timer.rs "failed to initialize perf timer ... EMFILE"
# control: ulimit -n 20 -> rc=0
```

Run **SOLO**. Scratch outputs: `scratch/clone-with-stack-probe/` (ignored). kvm-lane's independent
`extern "C"`-only harness: `scratch/curve3-concurrent-hermit/force/`.

## Evidence index

- Crash source: `experiments/concurrent-hermit-scaling_20260804/README.md:99-116` (Finding 3)
- Producer: `reverie/reverie-ptrace/src/timer.rs:393-405`
- Converter: `reverie/reverie-process/src/clone.rs:27-30`; sole caller
  `reverie/reverie-process/src/container.rs:784`
- Parent report site: `hermit/hermit-cli/src/bin/hermit/container.rs:215-223`
- #355 as landed: reverie `7951770`; refined shape at
  `reverie/safeptrace/src/notifier.rs` (async consume-on-`Died`, sync roll-back-and-wake-cleanup)
- Reverie primary at time of writing: `025d3780`; hermit primary `b64d893a`
