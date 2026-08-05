# In-guest RCB accounting + zero-ptracer acceptance gate (design, post-#373)

Author: impl agent, opus-4.8, 2026-08-04 ~23:45Z. Task: `unified-in-guest-patching-backend`.
Supersedes the pre-#373 draft `in-guest-rcb-accounting-spec_20260804.md` by re-anchoring the
bracketing dance to the **shared** in-guest host that reverie PR #373 landed, and folding in the
owner's zero-ptracer acceptance gate. This is DESIGN ONLY — no code inventory required.

**Ledger re-verified live @ reverie main `8688189a`, 2026-08-05 00:06Z** (impl agent, opus-4.8) —
line anchors below that were `[confirm vs map]` are now checked against landed code and marked
VERIFIED. Net change since 23:45Z: **rdpmc PR #363 is now LANDED** (not "open"), so the *only*
remaining read-side blocker is the visibility wall in §5 (`mod perf` private; `reverie-preload`
has zero `reverie_ptrace`/`PerfCounter`/`rdpmc` refs). C2 is now a small, precisely-scoped
additive change, not a pending PR.

Prior art (same workstream): `patching-backends-ptrace-on-syscall-path-audit-20260804.md` (B-class
audit + A/B discriminator), `patching-backends-zero-ptracer-three-bucket-classification-20260804.md`
(REMOVABLE-NOW / NEEDS-SHARED-HOST-FIRST / RARE-FALLBACK buckets).

---

## 1. Owner architecture gate — the acceptance condition (not a code inventory)

A patching backend whose **syscall path needs a ptrace round trip is NOT architecturally correct
and NOT ready for perf work.** The ~67 µs det-mode hop (Detcore sched + PTRACE host + tokio reactor)
is **evidence of the defect, not a budget to decompose.** Ptracer OUT FIRST, measure AFTER.

FINAL FORM across sabre / e9patch / liteinst: **SHARED CODE, ZERO PTRACER** (a rare fallback at most).

- e9patch routing DETLOG via the ptrace host **is exactly this defect** (it is the shipped
  `runtime_backend()` E9patch→Ptrace downgrade running `TracerBuilder::<Detcore>`).
- The fail-closed path for an **un-instrumented** syscall = a **SYSTRAP-STYLE IN-GUEST SIGNAL
  HANDLER (SIGSYS)**, NEVER a ptrace trap.
- A/B discriminator (in-tree, `reverie-liteinst/CLAUDE.md` Supported Boundary): A-class ALLOWED =
  `reverie_ptrace::TracerBuilder<()>` lifecycle-only reaper (follow+reap, one-time trap install +
  RIP redirect, NO syscall subscription, NO Tool in host). B-class VIOLATION = `TracerBuilder<Detcore>`
  (Tool in host) OR any per-syscall trap-to-host fallback.

## 2. Dependency ordering (checkable)

1. **In-guest RCB/timer MUST NOT start before the shared host exists.**
   Shared host = `reverie-preload::tool_host` (PR #373). **SATISFIED**:
   `git -C reverie merge-base --is-ancestor 9a7c0aa701d0d53413aaeb9c351377b0bc481918 origin/main`
   → rc 0 (merge commit ancestor of `origin/main` @ `8688189a`).
2. **rdpmc read primitive** = reverie PR #363 `PerfCounter::ctr_value_rdpmc`. **LANDED** — VERIFIED
   at `reverie-ptrace/src/perf.rs:420` on main `8688189a`. The residual read-side dependency for §3
   is now ONLY *reachability* from `reverie-preload` (the §5 visibility wall), not landing #363.

## 3. In-guest RCB accounting — the bracketing dance (pure design)

Counter: retired conditional branches, event `0x5100d1`, `pinned=1`, release build only (a debug
build runs an extra syscall read for a `debug_assert_eq!`).

**Clean-baseline property (why the dance is correct):** the installed patch is an **unconditional
jmp/call** into the trampoline. An unconditional branch does **not** increment the RCB counter.
Therefore the counter value read as the *first* act inside the trampoline is a clean baseline `B`
of guest-attributable branches — the trampoline entry itself contributed zero.

- **Read #1 = baseline `B`:** first act on entering the shared trampoline dispatch, BEFORE any
  handler branch executes. `[confirm vs current-main map: reverie-preload::tool_host entry, the
  point that drive_tool_syscall is called]`
- **Read #2 = end `E`:** last act before returning control to the guest, AFTER
  `handle_syscall_event` and any injected syscalls have run. `[confirm vs map]`
- **Deduct:** `tool_debt += (E - B) - C_read`, where `C_read` is the counter cost of the two reads
  themselves, so the dance is self-neutral (it does not charge the guest for its own accounting).
- **Guest clock:** `guest_clock() = raw_rcb_now() - tool_debt`. **Monotone, never rebased.**

**Placement is the whole point:** the two reads + deduction live in the **SHARED driver**
(`reverie-preload::tool_host::drive_tool_syscall`), exactly where PR #373 hoisted the ERESTARTSYS
loop — so **every** Family-A backend (liteinst, e9patch, and any future in-guest backend) inherits
one reviewed implementation and none can silently diverge. It is NOT re-implemented in liteinst's
former private host.

## 4. The PMU alarm's ONLY role

The alarm exists **solely to break unbroken runs of guest compute that contain no syscalls** —
stretches where no handler ever fires, so there is no natural bracketing point and the scheduler
would otherwise never preempt. On a syscall-dense guest the alarm is largely idle: the syscall
handler IS the preemption point.

- **Every handler entry repeats the bracketing dance AND resets the timer** — the just-serviced
  syscall already supplied a clean preemption/accounting boundary, so the alarm's countdown to the
  next *compute-only* preemption starts fresh.
- It is **not** a per-syscall mechanism and **not** a ptrace trap.

## 5. Read primitive: `rdpmc`, not `read()` / ptrace-mmap seqlock

The ptrace fast-read (`ctr_value_fast`, seqlock mmap load) is cheap ONLY because the tracee is
STOPPED (`page.index == 0`). An in-guest tool bracketing its own handler reads a **LIVE counter on
its own core** (`index != 0`); reverie then bails to the `read()` syscall (~264 ns). The in-guest
native primitive is **`rdpmc`** (~10 ns), valid precisely in the `index != 0` own-core case.

- Use `PerfCounter::ctr_value_rdpmc` (PR #363). Measured ~26.8× cheaper than `read()`
  (9.8 ns vs 264.2 ns median, devbig014 EPYC, release, taskset -c 3).
- Effect on the trap win: 2 reads/handler at rdpmc keeps the ~31× in-guest trap win at ~30×
  (vs ~19–20× if it fell to `read()`). This primitive choice is what makes the design viable.
- **Visibility gap `[confirm vs map]`:** `mod perf` is private (`reverie-ptrace/src/lib.rs:45`), so
  `PerfCounter`/`ctr_value_rdpmc` are unreachable from `reverie-preload` today. An **additive**
  visibility change (pub(crate) hoist or re-export) is needed — NOT a Tool/Guest/Backend contract
  change (changes HOW a counter is read, not how time/ordering is observed).

## 6. Nesting / re-entrancy

The trusted-syscall gate (`raw_syscall6` + `injected_syscall_guard` in the shared host) precludes
nesting today ⇒ handler depth ≡ 1. A depth-gate keeps the accounting re-entrancy-safe if that
constraint is ever relaxed.

## 7. Composition with the single in-guest Detcore subscriber (#1095 tripwire)

The one in-guest Detcore subscriber reads `guest_clock()` (monotone, never rebased). This changes
**WHERE DETLOG is emitted**, not the time model. Honor the PR #1095 fake-determinism tripwire:
validate clock parity on the **EVOLUTION** of the clock across the run, never on a single
post-exec sample (a single sample can look identical while the trajectory diverges).

## 8. Determinism achievability — SETTLED, do not re-litigate

RCB preemption + skid-aware single-stepping CAN reach a deterministic branch count: both
hermit/ptrace (`reverie-ptrace/src/timer.rs`) and mozilla rr already do it. This is a solved
problem being ported to a new read site, not an open research question.

## 9. Third-party-checkable acceptance conditions (explicit paths)

| id | condition | how a third party checks it |
| --- | --- | --- |
| C1 | shared host exists | `git -C reverie merge-base --is-ancestor 9a7c0aa701d0d53413aaeb9c351377b0bc481918 origin/main` → rc 0. **PASSES now.** |
| C2 | rdpmc primitive landed + reachable | PR #363 merged AND `ctr_value_rdpmc` visible from `reverie-preload` (no `mod perf` privacy wall). |
| C3 | RCB read+deduct is SHARED, not per-backend | the two reads + `tool_debt` deduction appear in `reverie-preload/src/tool_host.rs` only; grep both `reverie-liteinst` and `reverie-e9patch` hosts → zero private copies. |
| C4 | no B-class ptrace-on-syscall-path row for the flipped backend | audit predicate: no `TracerBuilder<Detcore>` on its run path; no per-subscribed-syscall trap-to-host fallback. (`hermit-cli/.../run.rs` runtime selection.) |
| C5 | fail-closed = in-guest SIGSYS, not ptrace | the un-instrumented-syscall fallback in the shared host is a SIGSYS handler; no ptrace trap on the syscall path. |
| C6 | `guest_clock()` monotone | parity validated on clock EVOLUTION across the run, not a single sample (#1095). |

## 10. Sequencing (what is unblocked now vs. still gated)

- **Unblocked now (C1 satisfied):** wire the §3 bracketing dance into `reverie-preload::tool_host`.
- **Gated on C2:** the read must be `ctr_value_rdpmc` (PR #363) and reachable — do the additive
  visibility change with it, not a `read()`-based placeholder that would bury a ~20× regression.
- **Independent of the shared host:** SaBRe sb-3 (persistent `PTRACE_SYSCALL` net) — do NOT fold it
  into this work (per three-bucket classification).
