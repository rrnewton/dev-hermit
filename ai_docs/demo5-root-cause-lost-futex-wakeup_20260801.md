# demo5 root cause: mutex/poller livelock (a lost wakeup is NOT the terminal cause)

Date: 2026-08-01. Author: impl agent, opus-4.8 (task `demo5-fix-scheduler-fairness-impl`).
Status: **CORRECTED — see "CORRECTION" below.** The lost-futex-wakeup framing in
the body was the working hypothesis; it is now FALSIFIED as the *terminal* cause
by a direct fix experiment. The terminal cause is a userspace mutex/poller
**livelock**. The lower sections are retained as the honest investigation trail
that led here, but the corrected conclusion supersedes them.

## CORRECTION (2026-08-01, later): a lost wakeup is not the terminal cause

The "lost futex wakeup" conclusion below predicted that recording a fizzled
`FUTEX_WAKE` and replaying it as a spec-legal spurious wakeup at the next
matching `FUTEX_WAIT` would let the vCPU proceed and demo5 would boot. That fix
was implemented, unit-tested, and run — and it **does not green demo5**:

- Branch `claude/detcore-sticky-futex-wakes` @`d79fe238`, flag
  `--sched-sticky-futex-wakes` (default off), unit tests 3/3.
- Canonical rcb-armed config, out-of-container enforcer
  (`boot_sweep.py --rcb on --sticky`): **0/3 boot, byte-identical 17869-byte
  wedge** at `hpet0: 3 comparators`, guest `0.724403s` — indistinguishable from
  the OFF baseline. The overlay was demonstrably active (124 sticky records / 45
  consumes) yet produced **zero** boot progress.

If a single lost wakeup were the terminal cause, crediting and replaying it would
change the terminal state. It does not. Therefore the wedge is NOT a lost wakeup
that this futex-layer fix can cure.

**Corrected terminal cause — userspace mutex/poller livelock.** Under detlog at
the wedge, the QEMU vCPU (`dtid 7`) is blocked on a **glibc mutex**
`0x5555570c8ec0` (`FUTEX_WAIT_PRIVATE val=2`) that sees **~13.9k balanced
FUTEX_WAIT/FUTEX_WAKE — wakes are NOT lost**; meanwhile iothreads (`dtid 11/13`)
spin on `SleepUntil(LogicalTime(0))` pollers. Committed virtual time races ahead
because step2d only jumps vtime when the run queue is empty, but the `SleepUntil(0)`
pollers keep it non-empty, so vtime never advances past the unproductive pollers
and the mutex owner is never scheduled to release. This is exactly
`scheduler-vtime-jump-unproductive-pollers`, not a futex-ordering defect.

**Real fix direction (owner/design-gated, trigger #4).** A core DetCore scheduler
change: let committed virtual time jump over provably unproductive pollers so the
mutex owner runs, i.e. livelock/progress handling — NOT a futex sticky-wake and
NOT the runnable fairness/aging overlay. Both of those levers are now falsified
with byte-identical evidence. This must be discussed with the owner before
implementation per CLAUDE.md (core scheduling change).

---

_Original working hypothesis (retained as investigation trail; superseded above):_

## Question

Why does demo5 (QEMU Linux boot, out-of-container enforcer) wedge instead of
booting to its shell, and can the bounded service-lead fairness/aging overlay
(design `ai_docs/scheduler-time-model-fairness-aging-design_20260801.md`) fix it?
This was first established under the legacy `--no-rcb-time --max-timeslice
disabled` config and then **confirmed identical under the CANONICAL rcb-armed
config** (`--strict --target-timeslice 100000 --max-timeslice 2000000000`, the
one demos/05-qemu-boot.py and the green-restore use) — see "Generalization" below.

## Answer: a lost futex wakeup blocks the vCPU outside the run queue

The wedge is a **lost futex wakeup**, definitively NOT the runnable-poller
"equal-footing-forever" starvation previously hypothesized.

Evidence (detlog, `RUST_LOG=...,detcore=info`, budget B=1000, 187,443 turns,
`ignored/fairness-val/detlog_B1000/run0/hermit-info.log`):

- The QEMU vCPU thread is `dtid 7` (its syscall mix is `madvise` / `futex` /
  `clock_nanosleep`). It goes silent at turn 162,779.
- Its **last action is an untimed blocking wait**:
  `futex(0x5555570f6708, FUTEX_WAIT, -1, NULL)`, which never returns. Over the
  run this address sees 8 inbound waits but only 7 finish.
- The **last** `FUTEX_WAKE` on `0x5555570f6708` is at log line **745,130**,
  which **precedes** the vCPU's final `FUTEX_WAIT` at line **750,692**. There is
  **no** `FUTEX_WAKE` on that address after the vCPU's wait.
- The waker (dtid 13 / 5) therefore woke an **empty** waitlist; the vCPU then
  entered `FUTEX_WAIT` and blocked forever — **outside** the run queue.
- After the vCPU disappears, the trace records **24,663** poller turns among the
  remaining runnable threads. That count is far larger than any fairness budget
  tested (B ∈ {2..10000}), which confirms the vCPU is **genuinely blocked**, not
  runnable-but-starved.

The guest boot freezes at guest time `0.724403s`, right after `hpet0: 3
comparators` in the serial transcript (17,869 bytes), and never reaches the
`2022-01-01T` RTC shell marker.

## Why runnable fairness cannot fix it

The bounded service-lead overlay selects among **runnable** threads and charges
committed turns. It has **no mechanism** to select a thread that is blocked
outside the run queue, and it explicitly **synthesizes no wakeups**. Empirically
this is confirmed: with the overlay ON at every budget B ∈ {2, 5, 20, 50, 100,
1000, 10000}, demo5 wedges **byte-identically** at the same guest time. This is
exactly the caveat the design itself flagged under "Demo5: what this can and
cannot claim." The overlay is the wrong lever for this wedge.

## Generalization: identical under the canonical rcb-armed config (2026-08-01)

The green-restore path does NOT use `--no-rcb-time`; it uses the canonical
demos/05-qemu-boot.py config `--strict --target-timeslice 100000
--max-timeslice 2000000000` (RCB/PMU preemption ARMED). To rule out the
possibility that the wedge and the overlay's inertness were artifacts of the
legacy `--no-rcb-time` config, the whole experiment was rerun at HEAD under the
canonical config (out-of-container enforcer, `boot_sweep.py --rcb on`):

- **OFF baseline: 0/3 boot.** All three runs TIMEOUT at 180s; first serial line
  ~30s, then stall. (This is why the "cheap config-revert" does not green demo5:
  the config was never the lever.)
- **Overlay ON B=5: 0/6 boot.** All six wedge identically; first serial ~38s,
  then stall. The overlay is inert here exactly as at every legacy budget.
- **detlog classification (diag5, 136 MB info log):** vCPU = `dtid 7`
  (`madvise`/`clock_nanosleep`/`futex` fingerprint) goes silent at turn 208,802
  of 236,852; its LAST action is the SAME untimed
  `futex(0x5555570f6708, FUTEX_WAIT, -1, NULL) = ?` that never returns. The last
  `FUTEX_WAKE` on `0x5555570f6708` is log line **909,377**, the vCPU's final
  `FUTEX_WAIT` is line **921,350** → wake PRECEDES wait, **zero** wakes after.
  **28,049** scheduler turns commit afterward (busy-pollers dtid 13/11/5 spin
  forever), confirming the vCPU is blocked OUTSIDE the run queue, not
  runnable-starved. Same address, same guest time (`hpet0`, 0.724403 s), same
  mechanism as the legacy config.

**Conclusion:** the lost futex wakeup is the demo5 root cause **regardless of
rcb-on/off**. The fairness overlay is definitively NOT the demo5 fix — it is
the wrong lever in both configs. (Experiments `bmdmv83d6`, `diag5`; enforcer
`ignored/fairness-val/boot_sweep.py --rcb on`.)

## The real fix direction

The defect is **futex wake/wait ORDERING under DetCore sequentialization**: the
`FUTEX_WAKE` is scheduled before the vCPU reaches `FUTEX_WAIT`, so the wake is
lost. Candidate fixes (owner/design-gated — core scheduling / futex model):

- correct wake/wait ordering so a wake cannot be committed before a wait that a
  correct interleaving would place first, or
- a sticky-wake / re-check-condition-on-wait so a `FUTEX_WAIT` whose condition
  was already satisfied does not block, or
- avoid the earlier interleaving that produces the premature wake.

This is a NEW, separate task from the fairness overlay. Related: the committed
virtual time races ahead here to ~1.77e9 s because there is no future
`timed_waiter` to bound step2d's vtime jump
(`scheduler-vtime-jump-unproductive-pollers`).

## Relationship to the delivered fairness overlay

The overlay was implemented per 241's design and shipped as **default-off,
labeled, research-only** infrastructure in Hermit PR #1386
(`--sched-fairness-budget=B`, head `4970a5de`). It is correct for the
*runnable*-poller-contention case (unit-proven burn-out mechanism, OFF
byte-identical, ON-path L2 on a closed-world multithreaded program) but has an
**unresolved ON-path determinism hole for external-actor poll-heavy workloads**
(#140: host-timing-dependent count of committed `InternalIOPolling` poll-retries
feeds selection-gating `S`), so it must not be enabled by default until the
`make -j8 --strict --verify` overlay-ON ≥5×-bitwise gate is green. See the PR
body for the enablement condition.

## Reproduction / enforcer

Valid enforcer: out-of-container
`ignored/fairness-val/boot_sweep.py` (own pgid via `start_new_session=True`,
outer wall-clock timeout, SIGKILL to the pgid on timeout — required because
PMU-skid leaves the supervisor holding the guest tree ptrace-STOPPED). Add
`--rcb on` for the canonical rcb-armed config. Do NOT use the in-container
`qemu_controller.py --timeout`; it
is virtualized and trips on vtime skew before `qmp.sock` exists, giving a false
wedge (`demo5-pmu-skid-refuted-target-timeslice-not-fix`).
