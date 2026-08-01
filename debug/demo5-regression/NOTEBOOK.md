# Lab notebook — demo5 QEMU-boot regression

> **Curated, agent-synthesized prose.** This is not generated from the JSON; it is
> written and globally re-read for consistency on each change. The machine-readable
> state (`hypotheses/evidence/suspects.json`) is the ledger of record; this notebook
> is the *synopsis a new reader should start from*. When state changes, run
> `dbg changed demo5-regression`, fold the delta in here, then re-read the whole
> notebook end-to-end for consistency and `dbg notebook-sync`.
> Lead: hermit-226. Evidence fleet: 210 turn-order, 231 metrics, 237 log-science,
> 238 qemu-strace.

## Synopsis

`demos/05-qemu-boot.py` boots a full QEMU-Linux VM under hermit. It regressed from
a sub-minute boot (GOOD `2a7ca98`, ~75 s) to a hard wedge that freezes at guest
**HPET/clocksource calibration** (`hpet0`, guest ts ~0.72) and never completes
(BAD `ae2565be`, 600 s timeout). The investigation has converged on a **two-part
deterministic livelock** (not a race — scheduler decisions are load-independent):

1. **Substrate** — under the demo's `--no-rcb-time --max-timeslice disabled`
   flags, `use_rcb_time()==false`, so guest branch retirement adds *zero* virtual
   time **and** no PMU preemption timer is armed. The only thing that moves the
   clock is unproductive per-turn / poll-timeout creep, which races committed
   virtual time ~400× ahead of guest time while the guest makes no progress.
2. **Trigger** — a **host-pollable listening socket fd** (the controller's QMP +
   serial-unix sockets). Its poller thread monopolizes scheduler turns; with no
   PMU preemption to force the `-icount` vCPU forward, the vCPU never executes the
   instructions that finish HPET calibration. The same `--no-rcb-time` config
   **boots** on bare busybox *without* the sockets — the socket is the sufficient
   trigger, single-variable-proven.

Re-enabling rcb-time **greens** the boot (slowly, ~5×): PMU preemption forces vCPU
RCB/icount progress *between* polls, so calibration eventually completes. That is a
legitimate **step-back**, not a masking of nondeterminism (#151). The once-competing
"#1095 guest-clock-lag" explanation is **dead** post-#1190. The open questions are
now about the *foundation*: is the spin fundamentally unbounded because the burn-out
mechanism is missing (H8), are the ~20% PMU-skid-panic and the poller-livelock the
*same* bug via PMU-rearm failure (H9), and what restores sub-minute parity (H5)?

Anchors: GOOD `2a7ca98` (#1077, ~75 s boot) → BAD `ae2565be` (hpet wedge). The
fleet's earlier "window-start" `f6c836b1` already hangs, so any code regressor
predates it; `2a7ca98` is the true ~1-min-good anchor.

---

## EXPLORED — established, with evidence

- **The wedge substrate is deterministic vtime starvation under `--no-rcb-time`
  (H1, confirmed in source).** `use_rcb_time() = max_timeslice.is_some() &&
  !no_rcb_time`; the demo sets both off, so RCB→vtime is doubly gated off, no PMU
  timer is armed, `step2d`'s vtime-jump is gated on an empty run-queue with no
  future `timed_waiter` to jump to, and committed time only creeps per-turn.
  *Evidence:* E01 (source chain), E02 (a bare `--no-rcb-time` boot shows **all**
  these signatures — `Skipping global time ahead`=0, `SleepUntil(0)` dominant, 0
  future waiters, committed races +1425 s, rcbs:0 — **yet still boots**, so the
  substrate is *necessary but not sufficient*), E07.
- **The sufficient trigger is a host-pollable listening socket fd (H6, confirmed
  by single-variable A/B).** Identical bare-busybox `--no-rcb-time` boot: no
  sockets → **boots** (`-serial stdio` and `-serial file:` both PASS); + two
  listening sockets → **wedges** at `hpet0`. *Evidence:* E03 (the A/B), E04 (237
  same-config per-dtid diff: the WEDGE has an extra socket-poller thread —
  `clock_gettime`/`poll`/`read`, 32 % of turns — absent in green; the vCPU worker
  gets ~equal *absolute* turns in both, so it is poller **interleaving**, not
  aggregate turn-starvation), E14 (smoking-gun: the guest sits in a ~74 ms
  relative-timeout poll loop; Detcore commits each unproductive epsilon-poll,
  inflating committed ~74 ms/poll → ~393 s vtime, while the vCPU never runs the
  calibration-completing code).
- **rcb-time greens by supplying the missing burn-out (H2, confirmed).** With PMU
  preemption armed, the vCPU is forced forward between polls until committed time
  crosses the guest timer deadline. *Evidence:* E08, E13 (healthy rcbtime runs:
  preemption begins ~turn 39 k after a long deadline-less warm-up — which is why
  the green boot *crawls* — and continues to the tail; 0 skids).
- **This is an EXPOSED foundation bug, not a masked determinism bug (H3,
  confirmed).** The wedge is a deterministic livelock; rcb-time restores a
  decision-deterministic (if slow) boot. `--no-rcb-time` exposed
  `scheduler-vtime-jump-unproductive-pollers`; reverting to rcb-time is a
  principled step-back. *Evidence:* E09.
- **Scheduler DECISIONS are host-load independent — SACRED P0 gate passes (H4,
  confirmed).** Contrasting-load decision traces are byte-identical over 41 043
  turns; only the verify-excluded committed_time value drifts ≤238 ns. *Which*
  thread starves is a deterministic function of the flag/fd set, not of load.
  *Evidence:* E10. (The historically "load-sensitive" reputation is confirmed
  wall-clock-only and harmless to determinism.)

## INVALIDATED — killed, with the evidence and the claim that would resurrect it

- **H7 — "the wedge is the #1095 guest-clock-lag past-deadline poller." KILLED.**
  Adversarially-evaluable claim: *post-#1190, the guest clock no longer lags
  committed time by seconds.* E05 measures guest `CLOCK_MONOTONIC` tracking
  committed to ~ms in a post-#1190 wedge (`1767228032.485…0.553` vs committed
  `.553`), versus the pre-#1190 ~8.53 s lag in `aa5258b`. **Resurrect only if**
  someone exhibits a post-#1190 wedge with a >1 s guest-vs-committed lag; none
  exists. The wedge is H1/H6, not clock-domain.
- **Dead end: "the bare `--no-rcb-time` boot itself livelocks."** An early H1
  revision asserted the substrate alone wedges. E02 refutes it — the bare boot
  reaches PASS. The substrate needs the H6 socket trigger.
- **Dead end: "H6 is a general gap / the python controller code is the cause"
  (earlier wrongly marked KILLED).** Reversed by the single-variable A/B (E03):
  the *fd*, not controller complexity, is the trigger — a bare busybox with no
  controller wedges once the listening sockets are added.

## FRONTIER — open hypotheses & suspects, with supporting evidence

### Open hypotheses
- **H8 — the spin is UNBOUNDED because the burn-out mechanism is missing, not
  mistuned.** `--target-timeslice` is *inert* under `--no-rcb-time` (it changes
  no per-turn quantum and produces no escape); the only burn-out is rcb-time PMU
  preemption. *Supporting:* E12 (`preempt_events=0`, vtime_burned 393 s vs guest
  0.08 s = ~400×, 88.8 % zero-duration polls), E14 (the 74 ms unproductive-poll
  inflation is the concrete form of the decoupling). *Adversarially-evaluable:* if
  `--target-timeslice` produced **any** escape under `--no-rcb-time`, H8 falls —
  E12's `preempt_events=0` says it does not. *To close:* confirm in source that no
  non-PMU preemption path can fire when `use_rcb_time()==false`.
- **H9 — UNIFICATION: the ~20 % PMU-skid-panic and the poller-livelock are ONE
  bug.** Hypothesis: after an over-skid the PMU fails to rearm → `inbound timer
  preemption event` stops → the rcbtime run degrades into the same deadline-less
  `SleepUntil(0)` spin as `--no-rcb-time`. *Supporting:* E13 shows **healthy**
  rcbtime runs have **no** rearm-failure (preemption continues to the tail) — so
  the test is not yet run against a failing case. *To close (decisive next step):*
  capture a **wedged/skidding rcbtime** run and check whether preemption stops
  firing after the skid. Until then H9 is plausible-unconfirmed.
- **H5 — perf parity: the rcb-time crawl is a workaround, not the fix.** *Supporting:*
  E11 (green rcb ~1 k timeslices vs 3-knob busybox ~85 k vs wedge 10⁵–10⁶). *To
  close:* a pre-regression sub-minute baseline (231) plus a scheduler-side
  burn-out fix; sub-minute parity under `--no-rcb-time` is untestable until such a
  fix exists.

### Open suspects (regressor hunt)
20 scheduler/time commits in `2a7ca98..ae2565be`, all **open** (8 high-priority) —
`dbg suspects demo5-regression --open`. Reading, cross-referenced to the above:
- **Weakened by H7's kill:** the guest-clock commits `cc3730fd`/`3ac51e11`/`c4a4bba2`
  (S01/S02/S20) are the #1190 clock **fix**, not the regressor — demote unless a
  pre-hpet timing change is shown.
- **Strongest structural candidates** for the foundation bug H1/H8 rest on:
  chaos-slowdown-epoch `61078e29`/`0c096177`/`d00d3a73` (S13/S11/S15, PR#1151/#1149),
  SIGCHLD admission `1663138d` (S07), shared stdio scheduler resources `9482e344`
  (S18 — directly the poller-resource path H6 implicates).
- **Caveat that reframes the whole hunt:** the H6 A/B reproduces the wedge on a
  bare busybox with the demo *config* alone (no controller, no fleet code) — so the
  "regressor" may be the **`--no-rcb-time` demo config exposing a latent foundation
  bug**, not any single commit. The sweep exists to *confirm-or-deny* a code
  regressor; a null result (no single commit flips GOOD↔BAD) is itself a finding
  that points at the config/foundation, consistent with H3/H8.

### Decisive open experiment
Capture a **skidding rcbtime** boot log and test H9 (does preemption stop after a
skid?). That single artifact either unifies the skid-panic with the poller-livelock
(H9 confirmed → one fix) or splits them (H9 killed → two problems).
