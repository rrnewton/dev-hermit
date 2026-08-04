# Load-dependent scheduling / vtime divergence — root cause + fix findings

- Task: `fix-load-dependent-scheduling-vtime` (P1, slot 231, owner=impl agent opus-4.8)
- Base: `origin/main` @ `e8ddd925` (unmodified for all repros)
- Family: demo5 clock-domain / GuestClock / scheduler-vtime-jump (sacred continuous-vtime #140)
- Landing gate: CORE DetCore scheduling change → post-facto trigger 4; **dual adversarial
  review (#141) + owner discussion on the vtime approach (#159) REQUIRED before land**.
- Status: **root cause confirmed; two accounting-level fixes implemented and EMPIRICALLY REFUTED.**
  The residual (dominant) coupling is *physical* preemption-position skid, which accounting
  cannot repair. The real fix is precise physical delivery or a skid-free backend (owner-gated).

## Symptom

Under heavy host load, `hermit run --strict --verify -- python3 examples/timed-progress-bar.py`
fails verification: the two runs produce **byte-identical guest output** (exactly 50 dots) but
a **load-dependent number of scheduler decisions**, so their DETLOG/COMMIT fingerprints diverge
and `--verify` reports a mismatch. On a quiet host it is 3/3 GREEN.

`timed-progress-bar.py` busy-spins `while millis() - prev < 20ms: current = millis()`, printing
one dot per 20ms of **guest** virtual time, 50 dots total. Because the vDSO clock symbols are
neutralized (reverie `vdso_patch`), every `millis()` is a real `clock_gettime` trap into Detcore.
The dominant busy-spinner is dtid 7; ~22 guest runtime threads exist and are sequentialized.

## Reproduction (confirmed)

The load is concurrent hermit churn, not inert CPU burn: a pool of concurrent `--strict --verify`
reps (or a single simultaneous wave) reliably reproduces; CPU burners (awk spin) do NOT — the
skid needs ptrace-stop/interrupt-delivery churn on the guest's core. Scripts in
`ignored/load-vtime-repro/` (`wave_ab.sh` is the cleanest discriminator).

| condition | result |
| --- | --- |
| quiet host, single rep, flag on or off | GREEN |
| wave A/B, N=40/arm concurrent, load ~280 | baseline **40/40 FAIL** |
| load ~200, POOL=220 (earlier) | 83/220 verify-FAIL, output hash identical |

Signal is load-monotone: more concurrent hermit churn → more verify failures.

## Root cause (decisive, corrected)

The first DETLOG divergence in a failing pair (`wave_ab_out/A_1.err`) is exact and diagnostic:

```
run 1, msg 166379: [dtid 7] finish  syscall #40424: clock_gettime(CLOCK_REALTIME) = { tv_nsec: 7555050 }
run 2, msg 166378: [dtid 7] inbound syscall     clock_gettime(CLOCK_REALTIME) = ?      (not yet finished)
   run1 (post) registers [dtid 7][rcbs 46576932]
   run2 (pre)  registers [dtid 7][rcbs 46585230]     <- ~8298 more retired branches
```

Key facts from this pair:

- The **virtual clock values are identical and deterministic** across runs: the busy-spin's
  `clock_gettime` results advance by a fixed ~19220 ns per read (…7516610, 7535830, 7555050…).
  The bug is NOT in time virtualization.
- The **raw PMU RCB counters differ** at the divergence: run 2 has retired ~8298 more conditional
  branches than run 1 by the "same" logical point. This is delivery skid.
- The observable divergence is that **run 1's timeslice admitted one more busy-spin iteration
  (clock_gettime #40424 completed) than run 2's before the max-timeslice preemption ended the
  slice.** Guest output is still byte-identical (50 dots); only the per-slice iteration count and
  thus the interleaving/fingerprint differ.

Mechanism: the busy-spin thread has no long voluntary blocking syscall; its timeslice is ended by
the **asynchronous PMU max-timeslice preemption**. A retired-branch counter overflow is *delivered*
some branches after it fires (delivery skid), and the skid magnitude is host-load-dependent
(it requires the guest thread being descheduled by the host OS between overflow and delivery).
So the preemption lands at a **load-dependent physical RCB position**, i.e. after a load-dependent
number of completed busy-spin iterations. Each completed iteration emits a `clock_gettime` DETLOG
entry, so the count of DETLOG entries in the slice — and every scheduling decision downstream —
inherits the host-load dependence. This violates continuous-vtime #140.

The accounting coupling point is `detcore/src/lib.rs::update_logical_time_rcbs` (359–475), called
at the top of every handler: it reads the raw counter (`clock_value`), charges the raw
`delta_rcbs` into `thread_logical_time`, and records `SchedEvent::branches(delta_rcbs)`. That is a
*second* skid channel (skid enters vtime and the fingerprint directly). But it is NOT the dominant
one — see the refutations below.

## Refuted fix 1: `--target-timeslice` (soft syscall-boundary timeslice)

A/B under identical load (N=80/arm, base e8ddd925): baseline 6/80 fail vs `--target-timeslice
5000000` **27/80 fail (4.5× WORSE)**. `use_rcb_time()` keeps the PMU `max_timeslice` armed;
`target_timeslice` *adds* soft preemptions on top, each an extra skid-charging
`update_logical_time_rcbs` call. Attacks the wrong axis.

## Refuted fix 2: `--defer-early-max-preempt` (defer *early*-delivered preemptions)

Implemented then refuted by construction. It deferred a max-timeslice preemption whose measured
`delta_rcbs < armed` (delivered *before* the armed deadline). Instrumented runs under load
128–222 show every max-timeslice event has `is_max=true` and, when observable, `delta == armed`
**exactly** (534/534, 501/501, 236/236, 732/732); **`defers-fired = 0`.** A PMU counter overflow
is never delivered *early* — skid is a *late/overshoot* phenomenon — so the guard is structurally
unreachable. A/B showed B ≈ A. The direction was wrong.

## Refuted fix 3: `--deterministic-timeslice` (clamp *late* overshoot to armed) — implemented

This is the correct-direction version of the doc's original recommendation
(`clamp-preempt-delta-to-armed`). At `update_logical_time_rcbs`, when a max-timeslice overshoot
is detected (`delta_rcbs > armed && last_rcb_timer_is_max` under precise timers), charge `armed`
(not the raw skidded delta) into `thread_logical_time` and record `SchedEvent::branches(armed)`,
while `committed_clock_value` still advances to the real `clock_value` (skid absorbed, not
deferred). Default off. Quiescent `--verify` GREEN with and without the flag.

**Result: REFUTED as a fix for the symptom.** Wave A/B, N=40/arm, load ~280:

| arm | outcome |
| --- | --- |
| A baseline `--strict --verify` | **40/40 FAIL** |
| B `--strict --deterministic-timeslice --verify` | **38 FAIL + 2 TIMEOUT / 40** |

Why it cannot work: the clamp makes the *accounting* (vtime + SchedEvent) for a max-timeslice
preemption load-independent, but it cannot undo the **physical** extra busy-spin iterations the
guest already executed before the skidded interrupt landed. Those iterations emit real
`clock_gettime` DETLOG entries (run 1 completed #40424, run 2 did not) — the fingerprint diverges
regardless of how the branch delta is booked. The problem is *physical preemption position*, not
bookkeeping. (There are also ~4 max-timeslice preemptions per run but ~136k COMMIT messages, so
even a perfect timer-accounting fix touches a tiny fraction of the schedule.)

## What the fix actually requires (owner-gated #159)

The invariant to restore is stronger than accounting: **a thread's timeslice must end at a
deterministic *physical* RCB position, independent of host load.** Candidate approaches, all core
and owner-gated:

1. **Precise physical delivery (`reverie-precise-delivery`).** Arm the PMU to fire slightly early,
   then single-step the guest to the exact armed RCB before ending the slice, so the preemption
   lands at exactly `armed` retired branches every run. Faithful and backend-general, but costly
   on the hot path (single-stepping the tail of every slice). This is the only approach that
   removes the physical skid channel; the `--deterministic-timeslice` clamp is its cheap-but-
   insufficient accounting-only shadow.
2. **Skid-free backend.** Count branches inline in the backend (DBI/DynamoRIO) so preemption is
   deterministic by construction, no PMU skid. See `gvisor-no-interpreter-dbi-is-skidfree-path`
   and `dbi-l2-corpus-baseline`. Long-term backend-maturity path, not a ptrace fix.
3. **Deterministic soft preemption that *replaces* (not augments) the PMU timeslice.** Preempt
   busy-spin threads only at voluntary syscall boundaries and disarm the async PMU max-timeslice
   entirely (`no_rcb_time`-style), so slice boundaries are deterministic syscall counts. Risk:
   a genuinely CPU-bound (syscall-free) thread never yields → livelock (see
   `jvm-maxtimeslice-disabled-livelock`, `min-vtime-scheduler-study`). Needs a livelock guard.

## Deliverable disposition

- The `--deterministic-timeslice` clamp is shipped **default-off** in the draft PR as a
  tested-but-insufficient artifact: it is correct, safe, and makes max-timeslice vtime accounting
  genuinely load-independent (a real but partial property), and it gives the owner a concrete
  probe for the #159 vtime-model decision. It is **explicitly NOT a fix** for the reported
  `--verify` divergence and must not be presented as one.
- Recommendation to owner (#159): pursue precise physical delivery (approach 1) or the skid-free
  backend (approach 2). Accounting clamps alone cannot make `--strict --verify` load-independent
  for busy-spin/timer-driven guests on the ptrace backend.

## Open question for the owner (#159)

Is guest virtual time *defined by the deterministic schedule* (so charging the armed target and
dropping skid is acceptable, and the remaining task is only to make physical delivery precise), or
must vtime stay equal to true retired branches (forbidding the clamp and requiring precise
delivery to also fix accounting)? This choice determines whether approach 1 alone suffices or must
be paired with the clamp.
