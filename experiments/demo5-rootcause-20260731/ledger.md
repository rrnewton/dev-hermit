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

> **REVISION 3 (2026-07-31, two adversarial self-corrections, now RESOLVED by a
> controlled A/B).**
> **(a)** An earlier version claimed a *bare* QEMU boot under `--no-rcb-time`
> wedges and that H6 was KILLED. My own bare-QEMU busybox boots **REFUTED that**:
> both `-serial stdio` (`wedge-off-run1`) and `-serial file:` (`wedge-filecon-run1`)
> boot to `HERMIT-QEMU-BUSYBOX-PASS` / `reboot: Power down` (guest ts 1.903) — slow
> (crawl through `hpet0` ~75 s) but successful; `status=124` was a post-power-down
> teardown hang, not an HPET wedge.
> **(b)** I initially cited `dtid_activity.rs` STARVED-TAIL as the wedge witness.
> That flag **also fires in the SUCCESSFUL bare boot** (dtid 5 starved 76.2%, yet
> PASS) — a false positive. So neither the `SleepUntil(0)`/`step2d`=0/racing-clock
> signatures nor the starvation-tail flag are *sufficient* for the wedge; the sound
> discriminator is the OUTCOME (reaches PASS vs frozen at `hpet0` forever).

**ROOT CAUSE (CONFIRMED by single-variable A/B).** The demo5 wedge is a
deterministic guest-starvation livelock **triggered by a host-pollable listening
socket fd** (the QMP socket + serial socket the controller harness adds), on top of
the `--no-rcb-time --max-timeslice disabled` background state that removes both PMU
preemption and RCB→vtime advance. Controlled experiment (identical bare busybox,
identical `--no-rcb-time`/`-icount`/`--target-timeslice` flags; the ONLY variable
is the injected listening sockets — full table in `source-mechanism.md` §6):

| variant | console | listening sockets | outcome |
|---|---|---|---|
| `boot_qemu_off.sh` | `-serial stdio` | none | **BOOTS** — PASS at guest ts 1.903 |
| `boot_qemu_filecon.sh` | `-serial file:` | none | **BOOTS** — PASS at guest ts 1.903 |
| `boot_qemu_sock.sh` | `-serial file:` | +2 (serial unix + QMP) | **WEDGES** — frozen at `hpet0` ts 0.716, 3 M+ turns / 7+ min, never PASS |

This matches the parent demo's own inline comment (`05-qemu-boot.py:94-97`: a
socket chardev is a host-timing-driven pollable fd that starves the `-icount` vCPU
under `hermit --no-rcb-time`), 237's controller trace (dtid 9 `qemu-system-x86_64`
runnable after a completed `read()`, never rescheduled → "timed out waiting for
qmp.sock"), and memory `qemu-serial-socket-starves-vcpu-under-hermit`.

**Independent convergence + refinement (231's `metrics.md`).** 231 independently
reached the same two-part conclusion from a different angle: `--no-rcb-time` alone
boots a bare busybox (racing vtime ~4.5×), and even the **full 3-knob stack** on
bare busybox **boots** (crawls: ~493 k turns, ~12× green, ~85 k timeslices) — so
"config alone" is NOT sufficient; 231 attributes the permanent wedge to the
"controller topology." **My socket A/B SHARPENS that attribution**: the sufficient
final ingredient is specifically the **host-pollable listening socket fd**, not the
full-Linux controller complexity — a bare busybox (no full Linux, no python
controller, no BQL/iothreads, no `savevm`) wedges with just +2 listening sockets
added (my `wedge-sock-run1` vs 231's booting bare-busybox 3-knob row, which used
`-serial stdio` with no sockets). 231's cleanest wedge signature — timeslice count
~1,000 (green) → ~85 k (busybox+3-knob, still boots) → 10⁵–10⁶ (wedge) — is the
same fragmentation my dtid_activity busy-pollers show.

**Q1 (mechanism) — answered.** Background (definitive in source, H1): under
`--no-rcb-time --max-timeslice disabled`, `use_rcb_time()==false` → guest branch
retirement adds ZERO vtime AND no PMU preemption timer is armed; the only
preemption is the `--target-timeslice` *logical* deadline, reachable only via an
intercepted checkin. Trigger (A/B, H6): adding a host-pollable listening socket
introduces a chardev poller whose readiness is host-timing-driven; with no PMU
preemption it monopolizes turns and the `-icount` vCPU is starved of re-selection,
frozen at `hpet0`. Without the socket the same background state still lets the vCPU
progress (slowly) to PASS. Why plain `--strict` greens regardless: PMU preemption
is armed, so the vCPU cannot be starved indefinitely (H2 CONFIRMED). The precise
turn-by-turn interleaving of the starvation is the open turn-order question (210).

- **Q2 (#151 classification):** `--no-rcb-time` **EXPOSES** the
  `scheduler-vtime-jump-unproductive-pollers` foundation bug; it does NOT mask a
  determinism bug. Re-enabling rcb-time (the crawl) is a legitimate STEP-BACK to a
  decision-deterministic (if ~5x slow) boot (H3 CONFIRMED).
- **Q3 (load-independence, SACRED P0):** **PASSED, no escalation.** No wall-clock
  read in any decision path; contrasting-load decision traces byte-identical over
  41043 turns; only the verify-excluded committed_time value drifts ≤238ns
  (H4 CONFIRMED). The prior "load-sensitive" verdict is confirmed-harmless
  (wall-clock + verify-excluded-value only). Unaffected by the corrections above:
  WHICH thread starves is a deterministic function of the flag/fd set.
- **Q4 (perf parity):** QUANTIFIED, OPEN on parity. 231's `metrics.md` measures the
  regression (green rcb-ON ~1,000 timeslices/~200s vtime vs 3-knob busybox ~85k
  timeslices/~1,450s vtime vs wedge 10⁵–10⁶ timeslices). rcb-crawl is a WORKAROUND,
  not the fix; true sub-minute parity under `--no-rcb-time` is untestable until a
  scheduler-side fix exists (out of scope here) (H5).

Remaining corroboration (not load-bearing on the root cause): 237 clock-lag
inspection (H7, expected KILL of #1095-lag).

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
  4. `step2d_handle_empty_queue` (`scheduler.rs:1989-2051`) is guarded by
     `if self.run_queue.is_empty()` (`scheduler.rs:1997`). While `SleepUntil(0)`
     pollers stay runnable it does not fire; and any `target <= committed_time`
     (incl. `LogicalTime(0)`) returns `Ok(())` immediately and is NEVER inserted as
     a `timed_waiter` (`scheduler.rs:2205-2225`) → no future event to jump to.
  5. Per-turn `add_scheduler_time` creep (`NANOS_PER_SCHED=500_000`, `time.rs:98`,
     `scheduler.rs:2524`) advances committed_time on productive turns; combined with
     the 500× `--strict` syscall multiplier (`time.rs:508-524`) the clock RACES far
     ahead of any guest deadline while providing no bounded preemption.
- **SCOPE (corrected).** This chain is the *background state* under
  `--no-rcb-time --max-timeslice disabled`: no RCB→vtime advance, no PMU
  preemption, step2d gated, clock racing. It is NECESSARY but by itself
  **NOT SUFFICIENT** for the wedge — see the A/B in the ROOT CAUSE section: a bare
  boot with all of it still reaches PASS. The wedge additionally requires the
  host-pollable listening socket trigger (H6). An earlier revision wrongly asserted
  "in-guest spin → run_queue never empties → hard livelock" as if the bare boot
  wedged; the bare boot in fact progresses through `hpet0` and boots.
- **Evidence (EMPIRICAL):** in a bare `--no-rcb-time` boot the background
  signatures are all present — step2d `Skipping global time ahead` = 0,
  `SleepUntil(LogicalTime(0))` commits dominate (384704 vs 115 future), committed_time
  races +1425s, `rcbs: 0` (237 log_timeslice) — **yet the boot SUCCEEDS**
  (`wedge-off-run1`, `wedge-filecon-run1`: PASS at guest ts 1.903). The
  `dtid_activity.rs` STARVED-TAIL flag fires in that successful boot too (dtid 5,
  76.2%), so it is not a wedge discriminator. Only when the listening sockets are
  present does the guest freeze at `hpet0` indefinitely.
- **Verdict:** **CONFIRMED (source) as the background mechanism**, with SCOPE
  corrected: it is the substrate, not the trigger. The trigger is H6 (socket fd),
  confirmed by single-variable A/B. The wedge is a deterministic guest-starvation
  livelock (not a race — consistent with H4 load-independence), but requires
  background + socket, not background alone.

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
- **Evidence (231 `metrics.md`, controlled single-variable busybox, binary
  `670209ba`):** green `--strict` (rcb ON) = ~345 s wall / 39–41 k turns / ~195–221 s
  vtime / **~1,000 timeslices** / ~252–257 k syscalls → BOOT_OK. Full 3-knob stack
  on the SAME bare busybox = ~382 s wall / **493 k turns** / 1,450 s vtime /
  **~85 k timeslices** / 387 k syscalls → still BOOT_OK but ~12× slower. Wedge
  harness (Table A) = millions of turns, guest frozen <1 s. Timeslice count is the
  cleanest axis: ~1,000 (green) → ~85 k (busybox+3-knob) → 10⁵–10⁶ (wedge).
- **Verdict:** **QUANTIFIED, still OPEN on parity.** The regression magnitude is
  measured, and rcb-time green is itself already a ~5× "crawl" (not a sub-minute
  restore) — so re-enabling rcb-time is a WORKAROUND, not a perf fix. True perf
  parity (sub-minute under `--no-rcb-time` with no wedge) cannot be measured until a
  scheduler-side fix exists; no such fix is in scope for this task. Remains OPEN
  pending a candidate fix to test against 231's baseline table.

### H6 — Wedge trigger is a host-pollable listening socket fd (NOT a general gap, NOT the python controller code per se)
The wedge is triggered specifically by the presence of a **host-pollable listening
socket fd** (QEMU chardev backend for the QMP/serial sockets), not by a general
deadline-less scheduler gap and not by the python controller *code* as such — any
harness that hands QEMU a listening socket reproduces it.
- **Kill test (refined after the bare-boot refutation):** (a) bare QEMU boot under
  `--no-rcb-time` with NO listening sockets — if it wedges, the trigger is general
  (H1-alone); if it boots, H1-alone is insufficient. (b) Same bare boot + injected
  listening sockets as the ONLY change — if it wedges, the socket fd is the
  confirmed single-variable trigger.
- **Evidence (EMPIRICAL — own, single-variable A/B):**
  - `boot_qemu_off.sh` (`-serial stdio`, no sockets): **BOOTS** to PASS (guest ts
    1.903), `wedge-off-run1`.
  - `boot_qemu_filecon.sh` (`-serial file:` console, no sockets): **BOOTS** to PASS
    (guest ts 1.903), `wedge-filecon-run1` — rules out the file-console confound.
  - `boot_qemu_sock.sh` (`-serial file:` console **+2 listening sockets**: serial
    unix + QMP, otherwise byte-identical invocation): **WEDGES** — frozen at
    `hpet0` (guest ts 0.716) for 7+ min / 3 M+ turns, never PASS, `wedge-sock-run1`.
  The ONLY variable across the three is the injected listening sockets.
- **Evidence (corroboration):** 237's controller trace (aa5258b) — dtid 9
  `qemu-system-x86_64` completes a `read()` (runnable) then is never rescheduled →
  "timed out waiting for qmp.sock"; parent `05-qemu-boot.py:94-97` comment; memory
  `qemu-serial-socket-starves-vcpu-under-hermit`.
- **Verdict:** **CONFIRMED (single-variable A/B).** The wedge trigger is a
  host-pollable listening socket fd, not a general in-guest-spin gap and not the
  python controller code specifically. The original "controller-specific" framing
  was too narrow (it's the socket the controller adds, reproducible without the
  controller) and the earlier "KILLED / general gap" verdict was WRONG (it rested
  on a bare-wedge that never actually wedged). This NARROWS H1: H1 is the necessary
  background substrate; H6 is the trigger.

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
- **Evidence (EMPIRICAL — own, minimal socket wedge `wedge-sock-run1`):** the
  committed-resource distribution is dominated by DEADLINE-LESS pollers, not
  past-deadline ones: **558,354 `SleepUntil(LogicalTime(0))`** (immediate) vs only
  **104** nonzero-target `SleepUntil`, and those 104 are legitimate FUTURE guest
  timers (absolute targets ≈ epoch+26 s / +38 s / +49 s), not a past deadline being
  re-committed. FutexWait 71,740, InternalIOPolling 14,770. The #1095 signature —
  a `SleepUntil(LogicalTime(T))` with `T` a PAST nonzero value repeatedly committed
  because guest CLOCK_MONOTONIC lags committed vtime — is ABSENT. This minimal repro
  also has NO python controller / fork-exec-split-clock-domain (bare busybox exec'd
  directly), yet wedges — so the #1095 fork/exec clock-domain split is not required.
- **Verdict:** **KILLED for the socket-triggered wedge.** The mechanism is the
  deadline-less (`SleepUntil(0)`) unproductive-poller monopoly
  (`scheduler-vtime-jump-unproductive-pollers`), categorically distinct from the
  #1095 clock-domain-lag / expired-past-deadline poll. (Caveat: the full-Linux
  controller harness may layer additional guest-clock-lag effects on top; but the
  socket-triggered wedge mechanism itself is deadline-less-poller starvation, not
  clock-domain skew. This updates memory `demo5-wedge-clock-skew-past-deadline-poller`,
  whose "past-deadline" framing does not hold for the isolated socket wedge.)

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
- **H6 single-variable A/B (own, `scratch/demo5-icount-sleep/`):**
  - `boot_qemu_off.sh` + `out/wedge-off-run1/` (`-serial stdio`, no sockets): BOOTS
    to PASS (`console.log` has `HERMIT-QEMU-BUSYBOX-PASS` + `reboot: Power down`,
    guest ts 1.903).
  - `boot_qemu_filecon.sh` + `out/wedge-filecon-run1/` (`-serial file:`, no
    sockets): BOOTS to PASS — closes the console confound.
  - `boot_qemu_sock.sh` + `out/wedge-sock-run1/` (`-serial file:` + serial-unix +
    QMP listening sockets): WEDGES at `hpet0` (guest ts 0.716), 3 M+ turns, never
    PASS. `out/wedge-sock-run1/dtid_activity.txt` = starvation witness (note:
    STARVED-TAIL flag also fires in the passing bare boots — outcome is the sound
    discriminator, not the flag).
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
- **2026-07-31 — H1 (background mechanism): CONFIRMED (source), SCOPE CORRECTED.**
  File:line chain proven (config gate → RCB-conversion double-skip → PMU-timer skip
  → target-timeslice-only preemption → step2d guard → no timed_waiter → clock
  races). **Adversarial self-refutation:** the original verdict claimed this alone
  produces a "hard livelock" and cited a bare-QEMU wedge. My own bare boots
  (`wedge-off-run1`, `wedge-filecon-run1`) BOOT to PASS with all these signatures
  present, so H1 is the necessary SUBSTRATE, not sufficient. Re-scoped: H1 =
  background; trigger = H6. Race angle still killed (deterministic, per H4).
- **2026-07-31 — H6 (wedge trigger = host-pollable listening socket fd):
  CONFIRMED (single-variable A/B), REVERSING an earlier wrong "KILLED".** The
  earlier KILL rested on a bare-wedge that never wedged. Controlled A/B: identical
  bare busybox + `--no-rcb-time`/`-icount` flags; no sockets (stdio OR file
  console) → BOOTS; +2 listening sockets → WEDGES at `hpet0` forever. The socket fd
  is the sole variable. Corroborated by 237's controller trace, the demo's own
  `05-qemu-boot.py:94-97` comment, and memory
  `qemu-serial-socket-starves-vcpu-under-hermit`.
- **2026-07-31 — tooling caveat logged: `dtid_activity.rs` STARVED-TAIL is a
  false-positive discriminator** — it fires in the SUCCESSFUL bare boot (dtid 5,
  76.2%) as well as the wedge. Needs a terminal-marker / parked-vs-exited
  refinement before its flag can be trusted as a wedge witness. Recorded for the
  impl agent who lands `dtid_activity.rs` (237's gap list).
- **2026-07-31 — H2 (greening mechanism): CONFIRMED (source).** Crawl differs from
  wedge only in CONFIG: plain `--strict` keeps `use_rcb_time()==true` (default
  max_timeslice + no `--no-rcb-time`), re-enabling BOTH RCB→vtime creep and PMU
  preemption — the two mechanisms the wedge removes.
- **2026-07-31 — H3 (classification #151): CONFIRMED.** Crawl boot is
  decision-deterministic across runs (0 ordering diffs; only verify-excluded
  numeric drift). rcb-time masks the LIVELOCK, not a determinism bug;
  `--no-rcb-time` EXPOSES the `scheduler-vtime-jump-unproductive-pollers`
  foundation bug. Latent-bug-EXPOSED, not masked.
- **2026-07-31 — H7 (#1095 clock-lag): KILLED for the socket-triggered wedge.**
  Empirical: minimal socket wedge is 558,354 `SleepUntil(LogicalTime(0))` vs 104
  nonzero (all FUTURE guest timers) — the #1095 past-deadline-repeatedly-committed
  signature is absent, and the repro has no python-controller fork/exec clock split
  yet wedges. Mechanism = deadline-less unproductive-poller monopoly, distinct from
  clock-domain skew. Updates memory `demo5-wedge-clock-skew-past-deadline-poller`.
- **2026-07-31 — H5 (perf parity): QUANTIFIED, OPEN on parity.** 231's `metrics.md`
  measures the regression (timeslice count ~1,000 green → ~85k busybox+3-knob →
  10⁵–10⁶ wedge; green rcb-ON is itself a ~5× crawl). rcb-time is a WORKAROUND, not
  a perf restore; sub-minute parity untestable until a scheduler-side fix exists
  (out of scope). Independent convergence with H1/H6: 231 also finds config-alone
  insufficient; my socket A/B narrows 231's "controller topology" to the socket fd.
- **2026-07-31 — H5 (perf parity): OPEN.** Awaiting 231 pre-regression sub-minute
  baseline + turns/vtime/syscall table.
