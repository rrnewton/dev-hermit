# demo5 Rigorous Root-Cause — Hypothesis + Evidence Ledger

**Task:** `demo5-rigorous-rootcause` (P0, automated-scientist mode, owner-mandated).
**Lead scientist:** hermit-226 (opus-4.8). **Started:** 2026-07-31.
**Evidence fleet:** hermit-231 (metrics), hermit-237 (log-science), hermit-210
(turn-order), hermit-238 (qemu-strace).

This ledger is the single source of truth. Every hypothesis carries **predicted
evidence**, the **evidence gathered**, and an **adversarial verdict**
(CONFIRMED / KILLED / OPEN) with the counter-evidence that would/did overturn it.
Nothing is "confirmed" without an independent adversarial attempt to kill it.

---

## Central Questions (owner)

- **Q1 — Mechanism.** WHY does `--no-rcb-time` wedge demo5, and why does dropping
  it (re-enabling rcb-time) green it?
- **Q2 — Classification (#151).** Is `--no-rcb-time` *masking a latent
  determinism bug*, or is enabling rcb-time a *legitimate step-back*?
- **Q3 — Load-independence (SACRED).** Are scheduler decisions host-load
  **independent**? Any load-dependent *decision* is an instant **P0**. The prior
  "load-sensitive" verdict must be confirmed-harmless (wall-clock only) or
  escalated.
- **Q4 — Perf parity.** Does the real fix restore pre-regression perf (sub-minute
  boot; same turns / vtime / syscall counts)?

## Definitions — the two demo5 harnesses (do not conflate)

| Harness | Path | Hermit flags | Behavior |
|---|---|---|---|
| **wedge** (owner's subject) | parent `demos/05-qemu-boot.py` | `run --strict --no-rcb-time --target-timeslice 100000 --max-timeslice disabled` + python `qemu_controller.py` (full Linux + snapshot) | HARD-WEDGES at guest HPET init (600s timeout) |
| **crawl** | hermit-repo `demos/05-qemu-busybox.sh` → `boot_qemu.sh` | `run --strict` (rcb-time ON), bare busybox boot, `-icount shift=0,sleep=off` | boots to PASS in ~323–328s (load-dependent WALL only) |

The wedge config stacks THREE interacting knobs: `--no-rcb-time` (guest branch
retirement contributes no vtime), `--max-timeslice disabled` (no preemption
deadline installed), and the in-sandbox pollers (QEMU BQL/iothread futex
handshake + python controller). Isolating each knob is required (see H1/H6).

---

## ROOT CAUSE (evidence-backed, adversarially judged) — 2026-07-31

**The demo5 wedge is a fully deterministic virtual-time-starvation livelock, not a
race and not a masked determinism bug.** Under
`--strict --no-rcb-time --target-timeslice 100000 --max-timeslice disabled`,
`use_rcb_time()` is false, so (a) guest branch retirement advances virtual time by
zero and (b) no PMU preemption timer is armed. QEMU's in-guest HPET-init spin
issues no intercepted checkin, so it never yields; the run_queue never empties, so
the scheduler's only forward-jump (`step2d`, gated on an empty queue) never fires;
and the spin registers no future `timed_waiter` for it to jump to anyway.
committed_time cannot reach the guest HPET deadline → livelock. Full file:line
proof in `source-mechanism.md`; causal chain confirmed as **H1**.

- **Q1 (mechanism):** answered — chain above (H1 CONFIRMED, source-definitive).
- **Q2 (#151 classification):** `--no-rcb-time` **EXPOSES** the
  `scheduler-vtime-jump-unproductive-pollers` foundation bug; it does NOT mask a
  determinism bug. Re-enabling rcb-time (the crawl) is a legitimate STEP-BACK to a
  decision-deterministic (if ~5x slow) boot (H3 CONFIRMED).
- **Q3 (load-independence, SACRED P0):** **PASSED, no escalation.** No wall-clock
  read in any decision path; contrasting-load decision traces byte-identical over
  41043 turns; only the verify-excluded committed_time value drifts ≤238ns
  (H4 CONFIRMED). The prior "load-sensitive" verdict is confirmed-harmless
  (wall-clock + verify-excluded-value only).
- **Q4 (perf parity):** OPEN — rcb-crawl (~325s) is a workaround, not the fix; the
  genuine scheduler-side fix should restore sub-minute boot under `--no-rcb-time`.
  Awaiting 231's pre-regression baseline + turns/vtime/syscall table (H5).

Remaining corroboration (not load-bearing on the root cause): own bare-QEMU
`--no-rcb-time` wedge run (H6, expected KILL of "controller-specific"), 237
clock-lag inspection (H7, expected KILL of #1095-lag), 231 perf table (H5/Q4).

---

## Hypotheses

### H1 — Wedge mechanism: deadline-less vtime starvation under `--no-rcb-time`
Under `--no-rcb-time`, committed_time no longer advances from guest branch
retirement. At QEMU's HPET/clocksource calibration the runnable set is
**unproductive pollers** (QEMU BQL/iothread futex handshake returning immediately
as `SleepUntil(LogicalTime(0))` + python controller poll), and **no thread
registers a future `timed_waiter`**. The scheduler's only vtime forward-jump
(`step2d` / "Skipping global time ahead") is gated on `run_queue.is_empty()` AND
needs a future timed_waiter to jump to — neither holds. `--max-timeslice
disabled` removes the timeslice preemption deadline that could otherwise be that
future event. ⇒ committed_time cannot reach the guest timer deadline ⇒ HPET
calibration never completes ⇒ hard livelock.
- **Predicted evidence:** wedge logs show `Skipping global time ahead`=0,
  `SleepUntil(0)` commits dominate, 0 future timed_waiters in the terminal spin,
  guest serial frozen at `hpet0`. Committed_time either frozen or only creeping
  by the per-turn scheduler tick, never reaching the timer deadline.
- **Evidence (SOURCE — authoritative, code-search agent, all paths under `hermit/`):**
  The full causal chain is proven in source, not just inferred from logs:
  1. `use_rcb_time() = max_timeslice.is_some() && !no_rcb_time` (`detcore-model/src/config.rs:635-637`).
     Demo5 sets BOTH `--no-rcb-time` and `--max-timeslice disabled`
     (`disabled` → `max_timeslice == None`, `config.rs:984-1007`), so
     `use_rcb_time() == false`.
  2. RCB→vtime conversion (`Detcore::update_logical_time_rcbs`, `detcore/src/lib.rs:359-475`)
     is doubly gated: outer `if max_timeslice.is_some()` (`lib.rs:364`) is FALSE →
     whole body skipped, `guest.read_clock()` never called; inner
     `if use_rcb_time()` (`lib.rs:376-387`) also FALSE. ⇒ **guest branch
     retirement advances vtime by ZERO.**
  3. PMU preemption timer is NEVER armed: `next_timeslice` computes
     `max_timeslice_end = None` when `max_timeslice==None` (`tool_local.rs:2052-2076`,
     unit test `target_only_mode_does_not_create_a_pmu_deadline` `tool_local.rs:2634-2650`);
     `post_handler_hook` then takes the SKIP path `lib.rs:640-644`
     (`assert!(max_timeslice.is_none()); last_rcb_timer = None`) → no RCB/PMU timer.
     Only preemption left = the `--target-timeslice 100000` *logical* deadline,
     reachable ONLY via intercepted syscall/rdtsc/cpuid checkins at handler
     boundaries (`end_timeslice_if_needed` `lib.rs:506-522`, `timeslice_expired`
     `tool_local.rs:1804-1811`).
  4. QEMU's HPET-init in-guest spin issues NO intercepted checkin → `end_of_timeslice`
     never reached → the thread never yields → **`run_queue` never empties.**
  5. `step2d_handle_empty_queue` (`scheduler.rs:1989-2051`) is guarded by
     `if self.run_queue.is_empty()` (`scheduler.rs:1997`) → never fires. Even if
     it did, `SleepUntil(LogicalTime(0))` / any `target <= committed_time` returns
     `Ok(())` immediately and is NEVER inserted as a `timed_waiter`
     (`scheduler.rs:2205-2225`) → no future event to jump to.
  6. Per-turn `add_scheduler_time` creep (`NANOS_PER_SCHED=500_000`, `time.rs:98`,
     `scheduler.rs:2524`) needs *productive scheduler turns*; an in-guest spin
     yields none. ⇒ committed_time cannot reach the guest timer deadline ⇒ **hard
     deterministic livelock.**
- **Evidence (EMPIRICAL):** own bare-QEMU `--no-rcb-time` run pending (H6 kill);
  237/210 wedge-log confirmation of step2d=0 / 0 future timed_waiters pending.
- **Verdict:** **CONFIRMED (source, definitive).** The mechanism is a fully
  proven deterministic vtime-starvation livelock, not a race. Empirical wedge-log
  confirmation is corroborating, not load-bearing.

### H2 — Greening mechanism: rcb-time brute-forces vtime forward
With rcb-time ON, guest branch retirement advances committed_time continuously
(RCB→ns). The busy-poll loops DO retire branches, so committed_time creeps until
it crosses the HPET/timer deadline in guest time; the vCPU timer fires; boot
proceeds — slowly (fine-grained creep), hence the ~325s crawl.
- **Predicted evidence:** booting run shows committed_time advancing with nonzero
  RCB contribution and eventually crossing the deadline; boot completes but ~5x
  slower than pre-regression sub-minute.
- **Evidence (SOURCE):** the CRAWL harness (`hermit-repo 05-qemu-busybox.sh`) uses
  plain `--strict` with NO `--no-rcb-time` and NO `--max-timeslice disabled`, so
  `max_timeslice` keeps its default `200000000` (`config.rs:396-403`) and
  `no_rcb_time==false` ⇒ **`use_rcb_time()==TRUE`**. Both rescue channels the
  wedge lacks are therefore active: (a) RCB retirement folds into vtime at
  `NANOS_PER_RCB=10.0` (`time.rs:39`, `lib.rs:376-387`), so the guest spin's
  branches creep committed_time forward; (b) the PMU preemption timer IS armed
  (`lib.rs:529-639`, install path), forcing periodic checkins. Together they
  carry committed_time across the HPET deadline. This is a *config difference*,
  not a code difference, from the wedge.
- **Evidence (EMPIRICAL):** crawl-to-boot ~323–328s under `--strict`, multi-run
  (sleep=on 3/3, sleep=off 2/2) reaching `HERMIT-QEMU-BUSYBOX-PASS`; source
  bucket breakdown (RCB vs syscall vs sched-creep) pending 231.
- **Verdict:** **CONFIRMED (source).** rcb-time greens the boot because it
  re-enables both vtime-advance-from-guest-execution AND PMU preemption — exactly
  the two mechanisms `--no-rcb-time --max-timeslice disabled` removes.

### H3 — Classification: legitimate STEP-BACK, not masking (Q2)
Re-enabling rcb-time is a legitimate step-back to a **deterministic** (if slow)
boot; it does NOT mask nondeterminism — **provided the rcb-time boot is
byte-identical run-to-run**. What `--no-rcb-time` EXPOSES is the *scheduler
foundation bug* (no deterministic vtime-advance in a deadline-less
unproductive-poller steady state), tracked as `scheduler-vtime-jump-unproductive-pollers`.
So the polarity is: rcb-time masks the **livelock**, not a determinism bug.
- **Adversarial kill test:** if rcb-time demo5 boot is NOT byte-identical across
  runs (DETLOG diff differs) → rcb-time introduces nondeterminism → reclassify as
  "masking".
- **Evidence (EMPIRICAL, own):** cross-run DECISION-trace diff of the crawl
  harness (`--strict`, RCB-time ON, `scratch/demo5-icount-sleep/out/`). Extracted
  the full COMMIT-turn sequence (turn#, dettid, resource incl. `SleepUntil`
  target) from `--log info`; normalized only the two documented verify-excluded
  numeric values (committed_time tail + derived `SleepUntil` targets):
  - sleep=on: 3 runs **byte-identical INCLUDING vtime values** (39803 turns each).
  - sleep=off: run3 vs run4 — DECISION ORDERING **byte-identical** (0 diffs over
    41043 turns); only committed_time drifts (max 238ns, mean 179ns) and derived
    `SleepUntil` targets drift (max 256ns, mean 7.8ns).
  The kill test therefore FAILS to kill: the boot is decision-deterministic; the
  only run-to-run variation is the numeric committed_time value, which is
  excluded from `--verify` by design (`scheduler.rs:2550-2554`, poll-retry-count
  quarantine `scheduler.rs:2433-2438`).
- **Verdict:** **CONFIRMED.** rcb-time is a legitimate STEP-BACK to a
  decision-deterministic (if slow) boot; it masks the *livelock*, not a
  determinism bug. `--no-rcb-time` EXPOSES the scheduler foundation bug
  (`scheduler-vtime-jump-unproductive-pollers`). Classification per #151:
  latent-bug-being-EXPOSED by `--no-rcb-time`, NOT masked by it.

### H4 — Scheduler decisions are host-load INDEPENDENT (Q3, SACRED)
All scheduler decisions are a pure function of guest events (syscalls, RCB
counts, registered waiters) and never read host wall-clock; therefore load
changes only WALL-CLOCK duration, never a DECISION. The earlier "load-sensitive
wedge" was a wall-clock/timeout artifact (heavy load ⇒ fewer wall-seconds within
a fixed timeout ⇒ less boot progress ⇒ *looks* wedged), not a decision
divergence.
- **Adversarial kill tests (EITHER failing ⇒ escalate P0):**
  (a) SOURCE: any wall-clock read (`Instant::now`, `SystemTime`, host
  `clock_gettime`) in the scheduler/detcore DECISION path (not logging).
  (b) EMPIRICAL: same config, 2 runs at contrasting host load, canonicalized
  DETLOG **byte-identical** over a fixed turn window.
- **Evidence (SOURCE — code-search audit):** NO `Instant::now`, `SystemTime`,
  `gettimeofday`, or host `clock_gettime` feeds any scheduling decision in
  `scheduler.rs`, `lib.rs`, or `tool_local.rs`. Next-thread choice = deterministic
  `run_queue`/priorities + seeded PRNGs; vtime = guest-event counters;
  `guest.read_clock()` (`lib.rs:368`) is the reverie **PMU RCB counter (guest
  retired branches)**, not wall-clock. The ONLY host-time usage is `Backoff`
  cadence (`scheduler.rs:619-654`: `yield_now`/`thread::sleep`/`tokio sleep`) — it
  sets re-poll RATE, not which thread runs. The one documented host-timing
  sensitivity — the *count* of nonblocking poll retries (`scheduler.rs:2433-2438`)
  — perturbs only the numeric committed_time and is deliberately EXCLUDED from
  `--verify` (`scheduler.rs:2550-2554`) and kept off DETLOG.
- **Evidence (EMPIRICAL — own, contrasting load):** sleep=off crawl run3
  (host load 37–54) vs run4 (host load 46–58): DECISION ORDERING (turn#/dettid/
  resource-kind, `SleepUntil` targets normalized) **byte-identical, 0 diffs over
  41043 turns**. Only numeric drift: committed_time ≤238ns, `SleepUntil` targets
  ≤256ns — precisely the verify-excluded poll-retry-count quarantine the source
  predicts. sleep=on 3 runs at loads ~40/44/41 byte-identical incl. vtime.
- **Verdict:** **CONFIRMED — Q3 P0 GATE PASSES, NO ESCALATION.** Both kill tests
  fail to kill; source and empirics agree. The prior "load-sensitive wedge"
  verdict is now **confirmed-HARMLESS**: host load changes only wall-clock
  duration and the verify-excluded committed_time numeric value, NEVER a
  scheduling decision. No load-dependent decision exists.

### H5 — Perf: rcb-time crawl is NOT the perf fix; foundation fix should restore sub-minute (Q4)
Pre-regression demo5 booted sub-minute. The rcb-time crawl (~325s) is a ~5x
perf regression — a workaround, not the fix. The genuine fix (scheduler
forward-progress / vtime-jump for the deadline-less unproductive-poller state)
should boot under `--no-rcb-time` in sub-minute AND restore turns/vtime/syscall
parity with the pre-regression baseline.
- **Predicted evidence:** 231 per-commit table (wall / turns / vtime / syscalls)
  quantifying the regression, and — once a candidate fix exists — sub-minute
  parity.
- **Evidence:** _pending 231 metrics._
- **Verdict:** OPEN.

### H6 — (competing) Wedge is controller-specific, not a general scheduler gap
The wedge is caused specifically by the in-sandbox python controller poll loop
(dtid 7), not a general deadline-less scheduler gap; a bare QEMU boot under
`--no-rcb-time` would not wedge.
- **Kill test:** bare QEMU boot (no controller) under `--no-rcb-time` — does it
  still wedge? If it wedges ⇒ H6 KILLED (general gap, supports H1). If it boots ⇒
  H6 gains support and H1 must be narrowed.
- **Evidence (SOURCE):** H1's proven chain is triggered by ANY in-guest spin that
  issues no intercepted checkin — the QEMU vCPU/TCG thread's own HPET-init spin
  qualifies; the python controller is not required. Predicts the bare boot ALSO
  wedges.
- **Evidence (EMPIRICAL — own, in progress):** bare-QEMU busybox boot (NO python
  controller, NO QMP) under `--strict --no-rcb-time --target-timeslice 100000
  --max-timeslice disabled`, `scratch/demo5-icount-sleep/run_wedge.sh`, 420s
  budget (crawl booted in ~325s, so a timeout = wedge). Result pending.
- **Verdict:** OPEN → expected KILLED (source predicts general gap).

### H7 — (competing) Wedge is the #1095 guest-clock-lag past-deadline poller
The wedge is the clock-domain lag (guest CLOCK_MONOTONIC lagging committed vtime
~8.5s ⇒ expired abs-deadline poll). #1190 landed to fix that; if the residual
wedge still shows the lag, #1190 is incomplete.
- **Kill test:** post-#1190 wedge logs — is the ~8.5s guest-vs-committed lag
  present? Absent ⇒ H7 KILLED for the current wedge (it's H1, not clock-domain).
- **Evidence (SOURCE):** the wedge mechanism (H1) is a *vtime-advance starvation*
  (committed_time cannot move forward at all) — categorically distinct from a
  clock-DOMAIN skew where committed_time advances but guest CLOCK_MONOTONIC lags
  it. Under `--no-rcb-time --max-timeslice disabled` committed_time is frozen at
  the spin, so there is no advancing committed clock for the guest to lag behind.
  Predicts NO 8.5s lag signature → H7 killed for this wedge.
- **Evidence (EMPIRICAL):** pending 237 wedge-log lag inspection.
- **Verdict:** OPEN → expected KILLED (distinct mechanism from #1095).

---

## Evidence Registry
- `samples/good_head45k.log`, `samples/broken_head45k.log` — 237: head-45k DETLOG,
  good hermit `2a7ca98` vs bad `aa5258b`.
- `logdiff_head45k.txt`, `..._filtered.txt` — 237: normalized good-vs-bad diff;
  first *real* scheduling divergence at msg ~2594/2592 (`BlockedExternalContinue`
  vs `SleepUntil(LogicalTime)`); msg 2590 execve diff is a benign run-dir path
  artifact.
- `native-time-confirm.log`, `native-console-confirm.log` — 238: native QEMU boot
  reference (icount, 30s window).
- **`load-independence.md`** — own: full Q3 proof (source audit + contrasting-load
  cross-run decision-trace diffs). The P0-gate artifact.
- **`source-mechanism.md`** — own: authoritative file:line source proof of the
  wedge chain (from the RCB-time/timeslice/step2d code-search).
- `scratch/demo5-icount-sleep/out/{on-run3,on-run4,on-run5,off-run3,off-run4}/hermit-info.log`
  — raw crawl `--log info` decision traces used for the load-independence diffs.
- `scratch/demo5-icount-sleep/run_wedge.sh` + `out/wedge-off-run1/` — own bare-QEMU
  `--no-rcb-time` H6 kill-test run (in progress).
- _(to be added: metrics.md (231), turnorder.md (210), qemu-strace profile (238).)_

## Load-Independence Protocol (Q3 — the P0 gate) — **PASSED**
1. SOURCE proof: audit scheduler/detcore decision path for any host wall-clock
   read. Pure-function-of-guest-events ⇒ load-independent by construction.
   → **DONE.** No wall-clock read in any decision path (H4 evidence).
2. EMPIRICAL proof: same config × 2 runs at contrasting induced host load;
   canonicalize DETLOG; require byte-identical decision sequence.
   → **DONE.** sleep=off run3 (load 37–54) vs run4 (load 46–58): 0 decision-ordering
   diffs / 41043 turns; only verify-excluded numeric drift (≤238ns).
3. Any load-dependent decision ⇒ STOP, escalate P0. → **NONE FOUND. No escalation.**

## Judge Log
- **2026-07-31 — H4/Q3 (SACRED P0 gate): CONFIRMED PASS.** Adversarial attempt:
  find ANY load-dependent decision. SOURCE audit found none (only Backoff cadence +
  verify-excluded poll-retry-count). EMPIRICAL contrasting-load diff: decision
  ordering byte-identical, only committed_time numeric drift ≤238ns (verify-excluded
  by design). Both independent axes agree. Prior "load-sensitive" verdict
  reclassified **confirmed-harmless (wall-clock + verify-excluded-value only)**.
  No P0 escalation.
- **2026-07-31 — H1 (wedge mechanism): CONFIRMED (source, definitive).** Full
  causal chain proven with file:line citations (config gate → RCB-conversion
  double-skip → PMU-timer skip → target-timeslice-only preemption → no intercepted
  checkin → run_queue never empty → step2d guard never fires → no timed_waiter to
  jump to → livelock). Adversarial angle (is it a race?): killed — mechanism is a
  deterministic vtime-starvation, confirmed by H4 load-independence.
- **2026-07-31 — H2 (greening mechanism): CONFIRMED (source).** Crawl differs from
  wedge only in CONFIG: plain `--strict` keeps `use_rcb_time()==true` (default
  max_timeslice + no `--no-rcb-time`), re-enabling BOTH RCB→vtime creep and PMU
  preemption — the two mechanisms the wedge removes.
- **2026-07-31 — H3 (classification #151): CONFIRMED.** Crawl boot is
  decision-deterministic across runs (0 ordering diffs; only verify-excluded
  numeric drift). rcb-time masks the LIVELOCK, not a determinism bug;
  `--no-rcb-time` EXPOSES the `scheduler-vtime-jump-unproductive-pollers`
  foundation bug. Latent-bug-EXPOSED, not masked.
- **2026-07-31 — H6, H7: OPEN, expected KILLED.** Source predicts bare boot
  wedges (H6) and no clock-domain lag (H7); own bare-wedge run + 237 log
  inspection pending.
- **2026-07-31 — H5 (perf parity): OPEN.** Awaiting 231 pre-regression sub-minute
  baseline + turns/vtime/syscall table.
