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
- **Evidence:** _pending 210 (turn-order) + 237 (log-science) + own wedge run._
- **Verdict:** OPEN.

### H2 — Greening mechanism: rcb-time brute-forces vtime forward
With rcb-time ON, guest branch retirement advances committed_time continuously
(RCB→ns). The busy-poll loops DO retire branches, so committed_time creeps until
it crosses the HPET/timer deadline in guest time; the vCPU timer fires; boot
proceeds — slowly (fine-grained creep), hence the ~325s crawl.
- **Predicted evidence:** booting run shows committed_time advancing with nonzero
  RCB contribution and eventually crossing the deadline; boot completes but ~5x
  slower than pre-regression sub-minute.
- **Evidence:** prior (last turn) crawl-to-boot ~323–328s under `--strict`; need
  RCB-contribution confirmation from 231/210.
- **Verdict:** OPEN (mechanism plausible, needs vtime-source breakdown).

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
- **Evidence:** _pending own DETLOG cross-run diff (crawl harness)._
- **Verdict:** OPEN.

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
- **Evidence:** _pending code-search source audit + own contrasting-load runs._
- **Verdict:** OPEN.

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
- **Evidence:** _pending own bare `--no-rcb-time` run._
- **Verdict:** OPEN.

### H7 — (competing) Wedge is the #1095 guest-clock-lag past-deadline poller
The wedge is the clock-domain lag (guest CLOCK_MONOTONIC lagging committed vtime
~8.5s ⇒ expired abs-deadline poll). #1190 landed to fix that; if the residual
wedge still shows the lag, #1190 is incomplete.
- **Kill test:** post-#1190 wedge logs — is the ~8.5s guest-vs-committed lag
  present? Absent ⇒ H7 KILLED for the current wedge (it's H1, not clock-domain).
- **Evidence:** _pending log inspection (237)._
- **Verdict:** OPEN.

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
- _(to be added: metrics.md (231), turnorder.md (210), qemu-strace profile (238),
  own load-independence + bare-wedge runs.)_

## Load-Independence Protocol (Q3 — the P0 gate)
1. SOURCE proof: audit scheduler/detcore decision path for any host wall-clock
   read. Pure-function-of-guest-events ⇒ load-independent by construction.
2. EMPIRICAL proof: same config × 2 runs at contrasting induced host load;
   canonicalize DETLOG (strip wall timestamps, ASLR addrs, run-dir paths);
   require byte-identical decision sequence over a fixed turn window.
3. Any load-dependent decision ⇒ STOP, escalate P0.

## Judge Log
- _(chronological confirm/kill decisions appended here.)_
