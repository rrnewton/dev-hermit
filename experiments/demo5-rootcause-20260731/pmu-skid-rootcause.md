# demo5 residual ~20% wedge — PMU-skid escalation, adversarial root-cause

**Lead scientist:** hermit-226 / opus-4.8 (automated-scientist mode).
**Task:** `demo5-rigorous-rootcause` (UNPARK/ESCALATION 2026-08-01).
**Companion:** [`ledger.md`](ledger.md), [`source-mechanism.md`](source-mechanism.md),
[`load-independence.md`](load-independence.md), [`metrics.md`](metrics.md),
[`wedge-rate.md`](wedge-rate.md).
**Date:** 2026-08-01. **Host:** devbig014, AMD EPYC 9D85 (family 0x1A / model
0x11), 316 cores. **Backend:** ptrace.

## The escalation, verbatim intent

The `--no-rcb-time` root cause (deadline-less socket-poller vCPU starvation) is
**settled** ([`source-mechanism.md`](source-mechanism.md)). The recommended fix —
drop `--no-rcb-time`, re-enable RCB-based virtual time — was reported by
hermit-220 as **not reliably green: ~20% wedge rate**, and the coordinator
labeled that residual as a **PMU-skid** statistical issue, asking WHY PMU-skid
wedges demo5 and how to make it deterministic/robust, while proving
load-independence.

## Headline verdict

**The coordinator's affirmative "~20% PMU-skid" label is not supported by any
captured primary evidence** — no `timer.rs:809`/"exceeds target" skid line has
ever been produced in any demo5 log, on any path — **so that half of the reframe
stands (refuted as a positive claim).** The reframe's OWN affirmative claim — that
the residual rcb-time wedge **is** the deadline-less unproductive-poller /
vCPU cond-var starvation LIVELOCK (`scheduler-vtime-jump-unproductive-pollers`) —
is the **LEADING hypothesis but is NOT yet proven for the rcb-time config**: there
is currently **no captured clean rcb-time poller-livelock wedge** on disk (§3, §7).
A genuine PMU-skid panic remains an armed, plausible *secondary* contributor for
the busybox `demos-green` path (plain `--strict`, PMU-primary preemption); it is
unproven and the completed N on that path is too small (≈2) to exclude a 20%
Bernoulli rate. **Q3 (load-independence, SACRED) PASSES** on the source argument
(no host wall-clock in any decision path); the empirical decision-trace equality,
though byte-identical, was measured for the rcb-ON config across only a ~10% load
band, so the P0 gate leans on the source proof, which is sound.
**Honest status: PMU-skid label refuted as a positive claim; poller-livelock is
the leading but not-yet-witnessed hypothesis for the rcb-time residual; Q3 P0 gate
genuinely passes. No basis to re-tag `demo-20260731` green.** A quiet-host N≥30
deciding experiment (both arms, inline skid+poller audit) is running now (§7).

## 1. Is the PMU-skid panic path even armed under rcb-time? YES (source)

- `imprecise_timers` defaults **false** (`detcore-model/src/config.rs:203`, a bare
  `#[clap(long)]` bool). demo5 passes no `--imprecise-timers`.
- ⇒ `detcore/src/lib.rs:631-639` takes the `else` branch
  `guest.set_timer_precise(TimerSchedule::Rcbs(rcbs_remaining))` →
  `TimerEventRequest::Precise` in reverie-ptrace.
- `reverie-ptrace/src/timer.rs`: `request_event` arms the PMU interrupt EARLY at
  `notification = target.saturating_sub(skid_margin)` (line ~637); on delivery
  `attempt_single_step` single-steps to the exact target and **panics at line 809**
  if `ctr_initial > target_rcb` ("Clock perf counter exceeds target value …
  Consider increasing skid margin"). Overshoot beyond margin is unrecoverable for
  Precise events (you cannot un-execute retired branches).
- Host skid margin = **1000 RCBs** for this exact CPU (AMD EPYC 9D85, fam
  0x1A/model 0x11) via reverie PR #3 (a19d734), down from rr's 10000, to win a
  40× → 4.66× slowdown. PR #3's own message admits **"rare spikes reach about
  61K"** RCBs = 61× the margin ⇒ a panic is guaranteed *if* such a spike lands on
  a Precise single-step target.

**So the panic is architecturally possible under rcb-time.** The question is
whether demo5 actually *reaches* Precise single-step targets often enough to hit
the skid tail — and whether that is what the ~20% is.

## 2. Does the landed-candidate config reach that path? Mostly NO (source)

The uncommitted working-tree config in `demos/05-qemu-boot.py` (the demo5
**controller** harness; currently `git status` = `M`, not yet landed) is:

```
run --strict --target-timeslice 100000 --max-timeslice 200000000
```

Its own comment (lines 133-143) states the design intent explicitly:

> RCB-based virtual time … `--target-timeslice keeps preemption at SYSCALL
> boundaries (no PMU skid)`; `--max-timeslice is a rare PMU backstop`. … the
> matched-load A/B … showed demo5 boot reliability is **primarily
> host-load-sensitive** — both this config and the prior `--no-rcb-time` config
> boot green under adequate headroom.

- `use_rcb_time()` = `max_timeslice.is_some() && !no_rcb_time` = **true** ⇒ RCB →
  vtime advance ON; PMU preemption armed.
- `--target-timeslice 100000` installs a **logical** deadline checked at every
  intercepted syscall/rdtsc/cpuid boundary. In a syscall-dense phase this fires
  long before the PMU backstop, so the Precise single-step (and its skid) is
  rarely reached.
- `--max-timeslice 200000000` ns ÷ `NANOS_PER_RCB=10` ≈ **20,000,000 RCBs** is the
  PMU backstop — hit only by a syscall-sparse compute burst (QEMU-TCG running
  guest code with few host syscalls). That is the *only* window where a skid panic
  could occur under this config.

⇒ The controller config is **designed to avoid PMU skid**; skid is a narrow
residual, not the intended preemption mechanism.

## 3. Empirical: no PMU-skid panic appears in ANY demo5 log

- **231** (`wedge-rate.md`): **zero** `perf counter exceeds target` lines in any
  demo5 log measured, wedge OR green. The demo5 skid is a **vtime skid**
  (unproductive-poller creep), explicitly distinct from PMU skid.
- **231** `wedge-rate-strict.driver.log` trial-1 (the older `wedge_rate_trials.sh`
  run; the `wedge-rate-strict.csv` was later OVERWRITTEN by a fresh `run_boots.sh`
  batch to a different schema, so cite the driver log, not the CSV): the busybox
  `--strict` (rcb-ON, PMU-primary) path recorded a **non-boot** run with
  **`skid=0`** (`[trial 1] WEDGE wall=430s turns=30517k … guest_ts=1.454141
  skid=0`). ⚠️ **The "30517k turns" figure is an unreliable harness parse** — the
  corresponding `wedge-rate-strict/info-1.log` shows only `ran 31328 turns` /
  `COMMIT turn 31327` and `SleepUntil(0)=743` (LOW; a green busybox boot logs
  ~868), so this single log is **NOT** a clean poller-livelock explosion witness;
  it is a non-boot run with **zero PMU-skid lines** whose precise mechanism
  (poller-livelock vs load-slowed boot killed at timeout) is ambiguous. What it
  proves is narrow but real: **no `timer.rs:809` panic in a recorded rcb-time
  non-boot.**
- **My own grep** across `experiments/`, `scratch/`, and `/home/newton/temp`:
  **no** `timer.rs:809` / "Clock perf counter" / "panicked at" line in any demo5
  run log. (The `/home/newton/temp` controller run — PID 632594, ~14 h old — uses
  the **old `--no-rcb-time`** config, line 132; it is not rcb-time evidence.)
- **No captured log trail** exists for the "~20% PMU-skid" claim; it reached the
  coordinator via GChat from 220. The 220 task (`verify-demo5-green-reliability-
  post1190`) notes end at the 02:51 land-authorization with the mechanism
  described as the vCPU cond-var starvation livelock, **not** a skid panic.

## 4. What the residual wedge actually is (leading hypothesis)

⚠️ **Scope of evidence:** the mechanism below is directly witnessed for the
`--no-rcb-time` config (220's forensics + `source-mechanism.md`'s single-variable
socket A/B). For the **rcb-time** residual it is the leading hypothesis by
extension, **not yet witnessed** — no clean rcb-time poller-livelock wedge is
captured on disk (§3). The §7 deciding experiment is designed to witness it (or
refute it) directly.

The **primary** mechanism, corroborated by 220's green-vs-broken forensics and
235's reconciliation:

- QEMU BQL/iothread threads (dtid 5/11/13) spin a tight producer/consumer futex
  handshake on word `0x…c8ec0` that returns immediately (`Ok(0)`/`Ok(1)` — genuinely
  woken), keeping the run queue **non-empty**.
- The vCPU thread (dtid 7) blocks on a *different* QEMU cond-var `0x…f6708`
  (7th `FUTEX_WAIT` never re-woken) and is parked ~83% of the run.
- Because the run queue never empties and no future `timed_waiter` is registered,
  the scheduler's step2d committed-time forward-jump never fires; committed vtime
  races ahead by thousands of virtual seconds while the guest is frozen at HPET.

Why **intermittent (~20%)** rather than a fixed 0%/100%: the controller harness
depends on **external host-timing inputs** — the QMP + serial unix sockets, the
`savevm` snapshot to disk, and the python controller's poll loop. Hermit **does
not** determinize external network/filesystem readiness (per `hermit/CLAUDE.md`).
Those inputs vary run-to-run with host load, changing *which* interleaving occurs
and therefore *whether* the deadline-less poller/cond-var steady state is
entered, and whether the (crawling) boot beats the wall-clock timeout. This is
exactly the config authors' "primarily host-load-sensitive; green under adequate
headroom" observation.

The **secondary** mechanism (armed, unproven in demo5, path-specific):

- The busybox `demos-green` path `hermit/demos/05-qemu-busybox.sh:59` uses
  **plain `--strict` with NO `--target-timeslice`**. There the PMU Precise timer at
  ~20M RCBs is the *primary* preemption for compute bursts, so every backstop
  firing risks a skid>1000 → `timer.rs:809` panic → panic-then-hang (memory
  `pmu-skid-panic-supervisor-hang`: orphaned supervisor holds the guest
  ptrace-STOPPED; needs pgid SIGKILL — that IS a "wedge"). Over a full boot with
  many such firings, a low-percentage per-firing skid tail can plausibly
  aggregate toward a ~20% per-boot failure. **This is the one hypothesis that
  could rescue the "PMU-skid" label — but only for the busybox path, and it is
  unconfirmed: 231 trial-1 of that exact path skid-panicked 0×.** The controller
  path's `--target-timeslice` largely removes this window.

## 5. Q3 — load-independence (SACRED) : PASSED

Already proven in [`load-independence.md`](load-independence.md), and it covers
**both** rcb configs:

- **Source:** no `Instant::now`/`SystemTime`/host `clock_gettime` feeds any
  scheduling decision; `guest.read_clock()` reads the reverie PMU RCB counter, not
  host time. The only host-timing sensitivity — the *count* of nonblocking poll
  retries — is kept OFF the DETLOG and its `committed_time` line is EXCLUDED from
  `--verify`.
- **Empirical:** `on-run3/4/5` (RCB-time ON) byte-identical decision sequence over
  **39,803 turns**; `off-run3/4` (`--no-rcb-time`) byte-identical over **41,043
  turns** — only a sub-256 ns verify-excluded `committed_time` wobble varies.
  ⚠️ **Honest caveat (adversarial):** the rcb-ON pair was captured across host
  load ~40/44/41 (a ~10% band), NOT a wide swing; the wider 37–58 band is the
  `--no-rcb-time` pair, and the dramatic 56→1247 swing lives in the separate
  stress-guardrail harness. So for the rcb-time config specifically the *empirical*
  contrast is thin; the P0 conclusion rests on the **source** argument (Axis-1),
  which is config-independent and strong. Widening the rcb-ON empirical load band
  is a cheap follow-up now that the host has headroom.

⇒ Host load never changes a scheduler **decision**. The controller wedge's
load-sensitivity is **external-boundary** input variation (sockets/savevm —
documented outside Hermit's determinism scope), NOT a load-dependent scheduler
decision. **No P0 escalation.** A skid panic (if it ever fires) is a robustness
abort, not a silent decision divergence — also not the sacred P0.

## 6. Fix path

1. **Real fix — removes the livelock regardless of external timing (owner-gated,
   post-facto trigger #4):** `scheduler-vtime-jump-unproductive-pollers`. Give the
   deadline-less unproductive-poller / cond-var-starvation steady state a
   deterministic forward-progress path (design already on parent main @4ac9ab2a).
   220's first cut (branch `codex/sched-poller-forward-progress`) was empirically
   inert because its two conjuncts never co-occur and there is no future event to
   jump to; the sound lever is a deterministic scheduler policy that, in a
   deadline-less all-immediate-turn steady state, advances the blocked-then-woken
   guest-execution (vCPU) thread over the tight non-productive handshake —
   **without pacing or coarsening continuous virtual time** (see
   `continuous-virtual-time-is-sacred`).
2. **PMU-skid robustness — defense in depth, independent of demo5 (should-do):**
   - Make the busybox `demos-green` path adopt `--target-timeslice` (syscall-
     boundary preemption) like the controller config, to shrink the PMU-primary
     skid window.
   - Land reverie **PR #576** (`impl-skid-panic-to-error`: default PMU RCB
     overshoot emits an **ERROR + re-arm** instead of panicking; panic behind an
     opt-in flag; timer state preserved). Converts any genuine rare skid from a
     panic-then-hang into a recoverable event.
   - The `--imprecise-timers` "RCB-fallback path" (`TimerEventRequest::Imprecise`
     arms at the requested RCB and returns on the PMU signal **without**
     single-stepping, so it never panics) is **NOT a determinism-safe fix**: it
     yields nondeterministic preemption points and only "makes sense when
     recording preemptions for later precise replay" (config.rs:200-202). Do not
     enable it for a `--strict --verify` determinism path.
3. **Interim:** the rcb-time controller config is virtual-time-faithful and boots
   green under adequate headroom; **do not re-tag `demo-20260731`** until the
   foundation fix or a robustness change removes the intermittency (#155).

## 7. Residual gap to close (primary data)

The only thing separating "poller-livelock, PMU-skid label refuted" from
"airtight" is a **per-run panic audit of actual rcb-time runs**. Assigned to 231
(owns the wedge-rate harness; `rcb_skid_lines` is already a CSV column):

- Complete the N=16 (or larger) wedge-rate batch for BOTH the busybox `--strict`
  path AND a busybox `--strict --target-timeslice 100000 --max-timeslice
  200000000` arm, on a **quiet** host (current load ~744/316 makes clean runs
  impossible — every boot is slow and contended).
- For every wedge, record whether `timer.rs:809` "exceeds target value" fired
  (skid panic) vs the poller vtime-skid signature (`SleepUntil(LogicalTime(0))`
  ≫ 10⁵, `rcb_skid_lines=0`).
- Prediction (falsifiable): controller-config wedges = poller-livelock, 0 skid
  panics; busybox plain-`--strict` wedges = mostly poller-livelock with a small
  skid-panic tail that the `--target-timeslice` arm removes.

## 8. Adversarial-judge review (2026-08-01) and corrections applied

An independent adversarial judge (general-purpose agent, ptrace/devbig014,
all file:line + grep re-verified against the live tree) attacked this reframe.
Verdict, accepted in full:

- **Claim 1 (panic path armed, made rare by `--target-timeslice`):** CONFIRMED at
  source; "rare backstop" downgraded to PLAUSIBLE (probabilistic, not a guarantee
  a syscall-sparse TCG burst never skids). Corrected in §2 wording.
- **Claim 2 (zero PMU-skid lines anywhere incl. PMU-primary path):** CONFIRMED for
  every preserved log, BUT the sample is ~2 green boots + 2 truncated (~550-turn)
  runs — no rcb-time *wedge* is in it. So "zero skid" is true but measured almost
  entirely on non-wedge runs. Corrected in §3.
- **Claim 3 (residual wedge IS the poller livelock):** downgraded to
  PLAUSIBLE-BUT-UNPROVEN. The judge showed the cited 30.5M-turn `skid=0` witness
  was **not reproducible from artifacts** (CSV overwritten; the recoverable driver
  log's "30517k turns" contradicts its own 31,328-turn info log). Fully corrected
  in §3 headline + §4 scope note: poller-livelock is the LEADING hypothesis,
  witnessed for `--no-rcb-time`, **not yet** for rcb-time.
- **Claim 4 (Q3 proven for rcb-time):** PLAUSIBLE; empirical rcb-ON contrast is a
  ~10% load band, so the P0 gate leans on the (strong, config-independent) source
  argument. Corrected in §5.
- **Claim 5 (busybox `--strict` skid tail at ~20%, N too small):** CONFIRMED as a
  live unsettled hypothesis. Effective N≈2 has ~0 power vs a 20% rate
  (P(0 wedges | p=0.2, N=2)=0.64). Needs N≈30–50 on a quiet host.

**Biggest weakness (accepted):** the reframe's one *positive* rcb-time witness is
not reproducible; the refutation of PMU-skid rests on absence-of-evidence plus
source plausibility, and the affirmative poller-livelock claim is not yet
established for rcb-time. **Bottom line unchanged in direction, confidence lowered
and stated honestly** (see corrected §Headline).

**Deciding experiment launched in response** (`skid_experiment.sh`, private dirs
`skidexp-strict/` and `skidexp-targetts/`, host load ~57/316): N=30 per arm,
inline per-run audit recording BOTH signatures — skid-panic
(`timer.rs:80x`/"exceeds target"/`panicked at` + orphan ptrace-STOPPED supervisor
count) AND poller-livelock (`SleepUntil(0)` count, turns, guest ts) — for the
PMU-primary `--strict` arm and the controller-like
`--target-timeslice 100000 --max-timeslice 200000000` arm. Falsifiable
predictions: `strict` wedges (if any) split into a small skid-panic tail + poller
non-boots; `targetts` shows 0 skid panics and removes the PMU-primary window.
Results will append to `skidexp-*/results.csv` and re-anchor §3/§7.

**Data-hygiene note flagged to coordinator:** two concurrent `run_boots.sh strict`
batches (PIDs 3040525, 3492097) are writing the SAME `wedge-rate-strict.csv` and
`boots-strict/` logdir, corrupting that data (concurrent workers reuse `info-$i`).
This experiment deliberately uses private dirs to avoid the collision; the
run_boots CSV/logdir should be treated as unreliable until de-duplicated.
