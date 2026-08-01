# demo5 root cause: lost futex wakeup (not poller starvation)

Date: 2026-08-01. Author: impl agent, opus-4.8 (task `demo5-fix-scheduler-fairness-impl`).
Status: DURABLE finding. Supersedes the "unbounded poller spin / burn-out
missing" framing (`demo5-spin-unbounded-burnout-missing`) as the *terminal*
cause of the wedge.

## Question

Why does demo5 (QEMU Linux boot under `hermit run --strict --no-rcb-time
--target-timeslice 100000 --max-timeslice disabled`, out-of-container enforcer)
wedge instead of booting to its shell, and can the bounded service-lead
fairness/aging overlay (design
`ai_docs/scheduler-time-model-fairness-aging-design_20260801.md`) fix it?

## Answer: a lost futex wakeup blocks the vCPU outside the run queue

The wedge is a **lost futex wakeup**, definitively NOT the runnable-poller
"equal-footing-forever" starvation previously hypothesized.

Evidence (detlog, `RUST_LOG=...,detcore=info`, budget B=1000, 187,443 turns,
`worktrees/226/hermit/ignored/fairness-val/detlog_B1000/run0/hermit-info.log`):

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
`worktrees/226/hermit/ignored/fairness-val/boot_sweep.py` (own pgid via
`start_new_session=True`, outer wall-clock timeout, SIGKILL to the pgid on
timeout — required because PMU-skid leaves the supervisor holding the guest tree
ptrace-STOPPED). Do NOT use the in-container `qemu_controller.py --timeout`; it
is virtualized and trips on vtime skew before `qmp.sock` exists, giving a false
wedge (`demo5-pmu-skid-refuted-target-timeslice-not-fix`).
