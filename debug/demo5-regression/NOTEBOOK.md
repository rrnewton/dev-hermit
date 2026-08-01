# Lab notebook — demo5 QEMU-boot regression

> **Curated, agent-synthesized prose.** This is not generated from the JSON; it is
> written and globally re-read for consistency on each change. The machine-readable
> state (`hypotheses/evidence/suspects.json`) is the ledger of record; this notebook
> is the *synopsis a new reader should start from*. When state changes, run
> `dbg changed demo5-regression`, fold the delta in here, then re-read the whole
> notebook end-to-end for consistency and `dbg notebook-sync`.
> Lead: hermit-226. Evidence fleet: 210 turn-order, 231 metrics, 237 log-science,
> 238 qemu-strace.

## CURRENT STATUS (frontier-first — driven, not buried)

Two tracks are being driven in parallel toward closing this episode:

- **Candidate SOLUTION (forward fix) — in implementation by hermit-226:** a
  **scheduler fairness / priority-aging fix** to the deadline-less
  unproductive-poller steady state (root cause H1/H8 +
  `scheduler-vtime-jump-unproductive-pollers`). The fix must make the starved vCPU
  make forward progress — advance/age it instead of letting the socket poller
  monopolize turns — rather than reverting to the rcb-time workaround.
  **OWNER-GATED on a fairness review** (core DetCore scheduling = post-facto
  trigger #4); not yet landed. *RESOLVED (2026-08-01, hermit-231 E22; owner was
  right):* the green path did **NOT** use `--no-rcb-time`. **H10 is KILLED**, its
  successor **H11 CONFIRMED**: the last-good demo config kept **RCBs ON** with a
  large-but-finite `--max-timeslice 2000000000` (2 s) safety that fired **rarely
  but non-zero** (41 RCB-boundary preemptions / ~100k timeslices on hermit
  `2a7ca98`, boots+exits rc=0). Parent commit `0591104` flipped it to
  `--no-rcb-time --max-timeslice disabled` (0 timer preemptions → wedge). So there
  are now **two** fixes: (a) **cheap, un-gated** — re-arm `--max-timeslice` in the
  demo config (restores green immediately); (b) the deep owner-gated scheduler
  fairness fix so even the 0-PMU config terminates.
- **Immediate-CAUSE commit (what regressed) — RESOLVED by hermit-231 (2026-08-01):**
  **BLAMED COMMIT = PARENT-repo `0591104` "sync 1100"** (2026-07-28 15:02 UTC), a
  demo-*config* commit (NOT a hermit code commit). It flipped
  `demos/05-qemu-boot.py` from `--max-timeslice 2000000000` to
  `--no-rcb-time --max-timeslice disabled`. **All 20 hermit suspects (S01–S20) are
  CLEARED**, confirmed suspect **S21 = `0591104`**. The bisect COLLAPSED: building
  hermit @`2a7ca98` (GOOD anchor, predates every suspect) and running the busybox
  demo shows the flipped config wedges (0 RCB preemptions, `SleepUntil0`=385934,
  rc=124) while the last-good RCB-ON config boots+exits (41 RCB preemptions, rc=0)
  on the *same* binary — so no hermit code commit in `2a7ca98..ae2565be` is the
  regressor; the config flip is. This also explains why the earlier hermit-code
  bisect was non-monotone (the demo config held `--no-rcb-time` throughout, so
  every hermit commit wedged). The last-good pre-flip commit is tagged
  **`demo5-lastgood` → `9371e5b`** (pushed to rrnewton/dev-hermit). Evidence
  E21 + E22; artifacts under
  `experiments/demo5_bisect_20260731/ignored/anchor-2a7ca98/`.

> **Honest caveat — infra not yet adversarially reviewed.** This debug/ episode
> infrastructure itself (the `dbg` CLI, the JSON schema, the size hook, this
> notebook workflow) has **not** yet had a dedicated adversarial review. Treat it
> as working-but-unreviewed scaffolding (PR rrnewton/dev-hermit#25, still draft);
> the *investigation findings* below carry their own per-hypothesis verdicts and
> evidence, which are independent of the tooling's review status.

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

Anchors + WHEN: GOOD `2a7ca98` (#1077) booted in ~75 s on **2026-07-28** (commit
10:31 EDT); by `f6c836b1` (**2026-07-29** 22:52) demo5 already hangs at
qemu-startup, and `ae2565be` (**2026-07-31** 10:18) hangs at hpet. So demo5 broke
in the **2026-07-28 → 07-29** window and has been broken for **~3–4 days** (through
**2026-08-01**, today). Any *code* regressor predates `f6c836b1`; `2a7ca98` is the
true ~1-min-good hermit anchor — **but see "What the demo actually tests" below:
the demo's own boot config also changed inside this window** (`--no-rcb-time` was
added to the demo on 2026-07-28 ~11:00, *after* the `2a7ca98` commit), so "what is
tested" is a confound the bisection must control for.

**P0 — RESOLVED (231, 2026-08-01); the owner was right, H10 is KILLED.** An
earlier revision claimed (H10/E17) that the green path never used PMU because the
demo config's `--max-timeslice disabled` forces 0 PMU / 100 % syscall-boundary
preemption. **That was FALSE — it read the CURRENT (post-flip) config, not the
historical green one.** Config archaeology + a direct build/run at `2a7ca98`
(E21/E22) settle it: the last-good demo config (`9371e5b`, tag `demo5-lastgood`)
was `--strict --target-timeslice 100000 --max-timeslice 2000000000` — **RCBs ON,
2 s finite safety, NO `--no-rcb-time`** — and `2a7ca98` (14:31 UTC) sits *inside*
that RCB-ON window, 31 min before parent `0591104` (15:02 UTC) added
`--no-rcb-time` **and** flipped `--max-timeslice 2000000000 → disabled`. Measured
on hermit `2a7ca98`: the RCB-ON config booted+exited with **41** RCB-boundary
preemptions (`inbound timer preemption event`) — **non-zero, rarely hit** — while
the flipped config had **0** and wedged. So green **did** use PMU/RCB preemption;
the fix is **not** "restore the syscall-boundary regime" but either (a) re-arm
`--max-timeslice` in the demo (cheap, un-gated) or (b) fix
`scheduler-vtime-jump-unproductive-pollers` (deep, owner-gated). See H11.

---

## What the demo actually tests / boot-flag evolution

*Changes in **what** we boot matter as much as which hermit SHA — the demo's own
boot config changed inside the regression window.* Timeline (parent
`demos/05-qemu-boot.py` + `demos/lib/qemu_controller.py`):

| date | commit | change to how Linux is booted |
|---|---|---|
| 2026-07-27 | `96a1874` | Python-controller rewrite of the QEMU demos; introduced `-icount shift=0,sleep=off`, `--target-timeslice 100000`, and **`--max-timeslice 2000000000` (RCBs ON, 2 s finite safety, NO `--no-rcb-time`)**. |
| 2026-07-28 13:02 UTC | `9371e5b` | "sync 0900" — demo config still RCB-ON (unchanged from `96a1874`). **Last-good pre-flip commit; tagged `demo5-lastgood`.** |
| 2026-07-28 15:02 UTC | `0591104` | **THE FLIP: `+--no-rcb-time`, `--max-timeslice 2000000000 → disabled`** ("sync 1100"). Disables *all* timer preemption → exposes the foundation bug. Lands *after* the `2a7ca98` (14:31 UTC) green anchor. **This is the blamed immediate-cause commit (S21).** |
| 2026-07-28 | `2cf85d3` | Made demo 05 safe to run concurrently (per-run dirs/sockets). |
| 2026-07-29 | `8a26a45` | Portable QEMU + deterministic drgn demos. |
| 2026-07-29 | `9e077f4` | **Boot serial: unix socket → `-serial file:`** — fixed a boot timeout, because a pollable serial *socket* starves the `-icount` vCPU under `--no-rcb-time`. (So the boot path deliberately avoids the H6 socket trigger for the serial console; the QMP socket remains.) |
| 2026-07-29 | `38acea8` | Resume demos (06/07): `-serial pipe:` FIFO (non-blocking) to avoid the same starvation on the resume path. |

Current boot config (`demos/05-qemu-boot.py:131-146`): `hermit run --strict
--no-rcb-time --target-timeslice 100000 --max-timeslice disabled -- qemu-system-x86_64
… -icount shift=0,sleep=off -serial file:<log> --qmp-socket <sock>`. The QMP unix
socket is the remaining host-pollable listening fd (H6 trigger).

**Why this section exists:** the "GOOD `2a7ca98` boots in ~75 s" datum was produced
by running *some* demo config against that hermit SHA — and `--no-rcb-time`/
`--max-timeslice disabled` were NOT in the demo until 31 min after that commit.
**RESOLVED (231):** the green anchor ran the **RCB-ON** config (`--max-timeslice
2000000000`, tag `demo5-lastgood` = `9371e5b`); `--no-rcb-time --max-timeslice
disabled` entered at `0591104` and is what wedges. This settled the H10 dispute
above (H10 killed, H11 confirmed): green was PMU-armed, not syscall-boundary.

---

## EXPLORED — established, with evidence

- **The wedge substrate is deterministic vtime starvation under `--no-rcb-time`
  (H1, confirmed in source).** `use_rcb_time() = max_timeslice.is_some() &&
  !no_rcb_time`; the demo sets `--no-rcb-time` **and** `--max-timeslice disabled`,
  so RCB→vtime is gated off, **no PMU timer is armed** (the timer is armed only
  when `max_timeslice.is_some()`, `post_handler_hook` lib.rs:640-644),
  `step2d`'s vtime-jump is gated on an empty run-queue with no future
  `timed_waiter` to jump to, and committed time only creeps per-turn.
  *Evidence:* E01 (source chain), E02 (a bare `--no-rcb-time` boot shows **all**
  these signatures — `Skipping global time ahead`=0, `SleepUntil(0)` dominant, 0
  future waiters, committed races +1425 s, rcbs:0 — **yet still boots**, so the
  substrate is *necessary but not sufficient*), E07.
  *Precision fix (E17):* it is **`--max-timeslice disabled`**, not `--no-rcb-time`
  alone, that zeroes the PMU. Bare `--no-rcb-time` with the *default* max
  (200 ms) **still arms** the PMU timer (`run-bbx-wedge`: 519 `inbound timer
  preemption event`s); the demo config disables the PMU only because it *also*
  passes `--max-timeslice disabled`.
- **P0 (H10 KILLED → H11 CONFIRMED) — RESOLVED by 231 (2026-08-01); the owner was
  right.** *Killed claim:* "the GREEN regime uses ZERO PMU by construction." That
  read the **current post-flip** demo config, not the historical green one. The
  historical green (`2a7ca98`, inside the RCB-ON window) ran the last-good config
  `--strict --target-timeslice 100000 --max-timeslice 2000000000` (**RCBs ON**),
  which arms the RCB/max-timeslice timer, so `inbound timer preemption event` (the
  RCB path, lib.rs:1264 — the sole `handle_timer_event` preemption site, so its
  `grep -c` is an exact RCB-boundary preemption count) **does** fire. *Evidence
  E21/E22 — measured on hermit `2a7ca98`, busybox demo:*
  - last-good RCB-ON config (`--max-timeslice 2000000000`): **41** RCB-boundary
    preemptions, boots+exits **rc=0** (~101 s). **Non-zero — RCBs rarely hit.**
  - plain `--strict` (default `--max-timeslice` 200 ms): **902** RCB preemptions,
    boots+exits rc=0.
  - flipped `0591104` config (`--no-rcb-time --max-timeslice disabled`): **0** RCB
    preemptions (no timer armed), **WEDGE** (`SleepUntil0`=385934, rc=124).

  So green **depended on** an armed `--max-timeslice`; the flip removed it. No
  `#1341` back-port was needed — the native RCB marker gives the exact count. (The
  older E17 "0 PMU / 100 % syscall-boundary" counts were all from `--no-rcb-time`
  runs the *fleet* configured, i.e. the post-flip config — never the historical
  green.) **Consequence:** the fix is (a) re-arm `--max-timeslice` in the demo
  (cheap, un-gated) or (b) `scheduler-vtime-jump-unproductive-pollers` (deep,
  owner-gated) — **not** "restore the syscall-boundary regime."
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
