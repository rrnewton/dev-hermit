# demo5 cause-(B) fix: empirical measurement of committed vtime suppression

- **Task:** `demo5-fix-B-vtime-jump-validate` (P1, branch-only, no land)
- **Agent:** opus-4.8 (impl/measurement), slot 235
- **Date:** 2026-07-31
- **Ground:** hermit `main` @ `0ca0dec2`; prototype branch
  `codex/demo5-vtime-jump-optB-validate` @ `9d5290b7` (base origin/main
  `0ca0dec2`). Reverie pin `adc14734`.
- **Directive:** *"measure, don't assume"* — every claim below is bound to a run
  artifact or a file:line, not analysis alone.

## Question

The vtime-jump design doc (`ai_docs/scheduler-vtime-jump-unproductive-pollers-explainer_20260730.md`
@ `4ac9ab2a`) proposes a forward-progress detector with two acts on an
"unproductive round":

- **Option A** — jump `committed_time` to the nearest future `timed_waiter`.
- **Option B** — when there is no future waiter, *suppress* the per-turn
  scheduler-time tick so committed stops racing ahead of the guest.

Both are framed as the **cause-(B)** fix: committed time decoupling from / racing
ahead of guest progress (the `extra_time` over-count, see
`ai_docs/detcore-global-vs-local-vtime-audit_20260731.md`). This task validates
whether the cause-(B) family actually un-wedges demo5.

## What was built (prototype, branch-only)

A default-off, hidden config flag `sched_no_scheduler_time`
(`detcore-model/src/config.rs`), cached into `Scheduler`
(`detcore/src/scheduler.rs`) and gating `bump_global_time`: when set, the
per-turn `add_scheduler_time()` (`NANOS_PER_SCHED = 500_000ns`) bump is skipped,
so **global committed time advances only by real per-thread work.** This is the
*maximal* form of Option B — not merely suppressing the 1.2% of `InternalIOPolling`
retries, but removing the entire scheduler-time inflation term. If even the
maximal cause-(B) suppression fails, the whole family is refuted.

Rationale for the maximal form: measurement of the retained BROKEN trace
(`ignored/h/a/r3/.work/boot-o68sg66f/hermit-info.log`, terminal 35,953-turn
steady state) shows the run_queue is **never "all-unproductive"** — genuine
`FutexWait` handshakes (10.2%) interleave with `SleepUntil(0)` yields (85.4%) and
`InternalIOPolling` (1.2%). So the design-doc detector's *trigger* is absent in
demo5; the only way to give Option B any chance was to apply it unconditionally.

## Testbed

`demos/05-qemu-busybox.sh` — QEMU busybox-initramfs boot, `hermit run --strict`,
serial to a **file** (no qmp controller, no cross-process poll). Kernel
`ignored/qemu-linux/bzImage`. `DEMO_TIMEOUT_SECONDS=300`. Option B injected via a
wrapper that inserts `--sched-no-scheduler-time` after the `run` token
(`ignored/d5-optB-235/hermit-optB-wrapper.sh`).

> **CONFIG CAVEAT (correction, 2026-07-31).** This testbed is **NOT** the
> canonical demo5 config, and it does **not** reproduce 220's cond-var wedge.
> Mine is `run --strict` (RCB-time **ON**, default `max-timeslice`) + busybox +
> no controller @ `0ca0dec2`; the canonical/authoritative demo5 wedge trace is
> 220's `--no-rcb-time --target-timeslice 100000 --max-timeslice disabled` +
> `05-qemu-boot.py` controller @ `ae2565be`. In my trace the terminal state is
> `scheduler (step2_process_blocked): zero threads left anywhere, fizzling` at
> the 300 s timeout boundary (guest processes **exited** — dtid 3 does
> stdout/stderr writes, reads `/proc/1/cmdline`, then `Exit{group:true,
> DetPid(3)}`), and dtid 7 is a **short-lived setup PROCESS** that exits at
> turn 104 (~0.09 s), **not** a parked vCPU. `--log info` records no futex
> bodies. So this run measures the RCB-ON committed-inflation lever, **not** the
> cond-var deadlock; the mechanism characterization below rests on 220's
> config-matched trace, and this run is only secondary corroboration.

## Results

| run | flags | turns | committed advance (guest clock) | ms/turn | outcome | max dtid |
|---|---|---|---|---|---|---|
| baseline | `--strict` | 37,091 | +172.48s | 4.65 | **WEDGE** (status 124) | 13 |
| optB run1 | `+ --sched-no-scheduler-time` | 38,268 | +154.21s | 4.03 | **WEDGE** (status 124) | 13 |
| optB run2 | `+ --sched-no-scheduler-time` | 37,459 | +154.19s | 4.12 | **WEDGE** (status 124) | 13 |
| optB run3 | `+ --sched-no-scheduler-time` | 36,654 | +153.79s | 4.20 | **WEDGE** (status 124) | 13 |

**Option B booted demo5 green 0/3 runs** (baseline also wedged). All four runs
wedged with max dtid 13 (guest never reached userspace). The committed advance
rate was consistently cut ~10–13% by the flag (4.03–4.20 vs 4.65 ms/turn) — the
lever fired every run — and the wedge persisted unchanged every run.
Artifacts: `ignored/d5-optB-235/{baseline,optB,optB-run2,optB-run3}/`.

### The lever provably fired — and the wedge was unchanged

- **Committed inflation was measurably cut**: guest-clock advance fell from
  4.65 ms/turn (baseline) to 4.03 ms/turn (optB) — the `add_scheduler_time`
  suppression took effect.
- **Yet the busybox boot still failed to finish** in 300 s, with the identical
  gross shape (max dtid = 13; commit distribution dominated by dtid 13 = 24,761
  and dtid 3 = 11,024; dtid 7 only 33 commits). **Correction:** I originally
  called this "vCPU parked" by analogy to 220's trace. That is not verifiable in
  this run — dtid 7 here is a short-lived setup process that exits at turn 104,
  the terminal state is `zero threads left … fizzling` (all guest processes
  exited at the 300 s boundary), and `--log info` has no futex bodies to confirm
  a cond-var wait. This run does **not** independently establish the cond-var
  deadlock; it establishes only that suppressing `add_scheduler_time` (−13 %
  committed rate) does not change the busybox-boot outcome.
- **Surprise datapoint**: `add_scheduler_time` is only ~13% of committed advance
  in demo5 (4.65→4.03). The dominant driver of committed advance is the guest's
  own timed operations (kernel-boot `clock_nanosleep`/delay deadlines granted by
  the scheduler), **not** scheduler-time inflation. So cause-(B) as "the 0.5ms/turn
  tick balloons committed" is a *minor* contributor here, and removing it entirely
  changes neither the boot outcome nor the wedge structure.

## Side-by-side: the whole cause-(B) family vs the real fix

| lever | who / where | fired? | greens demo5? | why |
|---|---|---|---|---|
| **A** — jump committed to next future waiter | 220, `codex/sched-poller-forward-progress` @6c6ecbe9 | **0×** | **NO** | the two conjuncts (only-pollers ∧ a registered future waiter) never co-occur; when only-pollers, there is no future event to jump to |
| **B** — suppress scheduler-time tick (maximal) | this task, @9d5290b7 | yes (−13% committed rate) | **NO** | committed-racing is not the cause (GOOD races too); suppressing it changes nothing |
| **root-cause fix** — scheduler policy that breaks the cond-var starvation in a deadline-less steady state | 220 (`demo5-fix-detcore-deadlineless`), 226 (`demo5-fix-qemu-icount-idlewarp`) | — | (prototyping) | attacks the actual wedge |

**Root cause (from 220's config-matched r3 GOOD-vs-BROKEN trace, the
authoritative source — see reconciliation note below):** a **deterministic-
serialization-induced QEMU-internal vCPU cond-var starvation**. In 220's broken
run the vCPU (dtid 7) blocks on futex `0x5555570f6708` (`FUTEX_WAIT`, no
timeout); its 7th wait @turn 171,416 is never re-signalled; it stays **blocked**
for 83 % of the run while the BQL/iothread threads (dtid 5/13) commit a
genuinely-woken handshake `0x5555570c8ec0`. Committed racing ahead is present in
the **GOOD** run too (+5792 s) — so it is *not* the discriminator; the only
structural difference is whether the vCPU cond-var keeps getting signalled.

**Reconciliation of the three apparently-conflicting claims (2026-07-31).**
- "vCPU parked 83 % (starvation)" and 220's "fair round-robin of runnable dtids,
  no thread starved, rules out selection" are the **same run, two facets**: the
  4 **runnable** threads {3,5,11,13} are scheduled fairly (selection-fairness is
  not the bug), while the vCPU is **blocked** (not in the runnable set). "Parked"
  = deadlock-blocked, not "denied a turn by an unfair scheduler."
- "root = time-accounting / committed-races-ahead" is **REFUTED**, not held by
  220: 220's GOOD-vs-BROKEN shows GOOD races committed ahead and still boots, and
  the Option-B measurement above cuts `add_scheduler_time` by −13 % with no
  change. The time-accounting/cause-(B) framing came from an earlier audit
  (`detcore-global-vs-local-vtime-audit_20260731.md`); it is a non-causal symptom.

**A forward time-jump (A) or a tick-suppression (B) cannot fix a deadlock whose
cause is a never-re-signalled cond-var, not time-accounting.** Full evidence:
`ai_docs/demo5-green-vs-broken-vcpu-condvar-starvation_20260731.md`.

## Determinism / no-blunting note

The prototype is explicitly **not** determinism-preserving and is not proposed
for landing. It does not round, quantize, freeze, or per-exec-reset any clock —
it removes a global-only additive term — but it *does* change committed's rate,
which is why it is branch-only for measurement. The result argues *against* any
cause-(B) landing for demo5: the correct fix is a scheduler thread-selection
policy in the deadline-less steady state (220/226 forks), which leaves continuous
virtual time intact.

## Recommendation for the owner

1. **Retire the cause-(B) framing for demo5.** Options A and B of @4ac9ab2a are
   both now empirically refuted (A by 220, B here). Neither greens demo5; the
   committed-inflation term is a ~13% minor contributor, not the wedge.
2. **The cause-(B) levers are still legitimate for their original targets** —
   SIGCHLD/nanosleep-style workloads with a *real registered future deadline*
   behind an only-poller queue (Option A). Keep 220's @6c6ecbe9 commit for that
   soundness review (`discuss-vtime-advance-heuristic-soundness`); it is
   principled, just non-covering for demo5.
3. **Pick the demo5 fix from the root-cause forks**, not the vtime family:
   #226 QEMU `-icount sleep=on` idle-warp (operational) vs #220 deterministic
   scheduler policy favoring the blocked-then-woken guest-execution thread over
   the non-productive handshake (core, trigger #4). Both are branch-only
   prototypes; choose on which actually boots demo5 green multi-run.
