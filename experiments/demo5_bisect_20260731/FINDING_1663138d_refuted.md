# demo5 N+K second-regression: 1663138d REFUTED; boundary is (1663138d, 2f3689bd]

Task: `demo5-post-breakage-bisect-second-regression` (owner, P0).
Date: 2026-08-01. Host: 316-core devbig. All boots: config-reverted
`demos/05-qemu-boot.py` = `hermit run --strict --target-timeslice 100000
--max-timeslice 2000000000` (RCB armed) driving the full-linux QMP demo.
Classifier: `boot_classify_iso.sh` (serial markers → GREEN / SLOW_BOOT / HARD_WEDGE).

## Method

Load is the confound: the wedge is load-sensitive and the host floor swings
55→800+ from other agents' orphan spinners. So the causal signal is the
**within-round** contrast: run 4 binaries CONCURRENTLY on disjoint core bands
(each mem-capped in its own systemd scope), so all four see the identical
instantaneous load. Bands are **rotated each round** so every binary visits
every band — this removes any per-band load asymmetry (a second confound found
in the earlier fixed-band run).

Binaries (all distinct sha256):
- A = 2f3689bd  = HEAD (task's K endpoint)
- B = 2f3689bd-rev1663 = HEAD with commit 1663138d cleanly reverted
- C = 1663138d  = the *hypothesized* N+K regressor
- D = 0df976bb  = older green anchor

## Result (rounds 1-3, load 55-57, band-rotated)

    r1: A(HEAD)=EMPTY*     B(rev1663)=WEDGE  C(1663)=GREEN  D(par)=GREEN
    r2: A(HEAD)=WEDGE      B(rev1663)=WEDGE  C(1663)=GREEN  D(par)=GREEN
    r3: A(HEAD)=WEDGE      B(rev1663)=WEDGE  C(1663)=GREEN  D(par)=GREEN
    (* r1 A scope-killed before writing a verdict; WEDGE in the fixed-band run.)

Per-binary: A 0/2 green, B 0/3 green, C 3/3 GREEN, D 3/3 GREEN.
The earlier fixed-band round reproduced this exactly (A=WEDGE, B=WEDGE,
C=GREEN, D=GREEN).

## Conclusion

**1663138d is NOT the N+K regressor**, on two independent grounds:
1. **C = 1663138d itself boots GREEN** every round.
2. **B = reverting 1663138d from HEAD still WEDGES**, byte-identical last_ts
   (0.724403) to A = HEAD.

Band rotation confirms the split tracks the *binary*, not the core band:
B wedges on band 300-307 in r2 where C booted green in r1; D greens on band
284-291 in r2 where A wedged in r1.

**The GREEN→WEDGE boundary therefore lies in `(1663138d, 2f3689bd]`** — C at
+57 from HEAD is GREEN, HEAD is WEDGE. That range is inside the task's
`adbfaca3..HEAD` search window (adbfaca3 is +148, an ancestor of 1663138d).

This CORRECTS the earlier interim note that reported a boxed-serial green-rate
STEP at 1663138d; that step was a serial-execution/load artifact, refuted by
the load-controlled parallel test.

## Next

Bisect `(1663138d, 2f3689bd]`. Prime suspect = the guest-clock cluster
**3ac51e11 "Share guest clock across process trees"** (+23) and
**cc3730fd "Track committed logical time in the guest clock"** (+22), which
rewrite `detcore/src/tool_local.rs` + `lib.rs` (+ `syscalls/time.rs`) — the
committed-logical-time plumbing that memory ties to the demo5 clock-skew
past-deadline-poller wedge (#1095 clock-domain). Anchors built:
`hermit-25d908c6` (parent of the cluster, expect GREEN) and `hermit-cc3730fd`
(after the cluster, expect WEDGE). The confirming run is HELD for host load to
fall below ~90 (was 800+ from a foreign build/spinner storm).

Data: `ignored/demo5-multisect/interleave/{rotated-result.csv,fixedband-r1.csv}`.
Binaries: `ignored/demo5-multisect/bin/hermit-*`.

## Owner hypothesis: does 1663138d change child-vs-parent post-clone priority?

Checked against `git show 1663138d` (detcore/src/scheduler.rs +81,
scheduler/runqueue.rs +11, syscalls/io.rs +51). **No.** The diff does not
touch clone/fork priority at all:

- **runqueue.rs**: purely additive — one read-only query `has_runnable_besides`
  (`k.priority < LAST_PRIORITY`). No change to `push`, priority assignment, or
  ordering. New children still enter at the unchanged `FIRST_PRIORITY` path
  (`runqueue.rs:661 push_back(higher_priority, FIRST_PRIORITY)`); no reparent,
  no child/parent priority swap. `push_eager_io_repoll` is **pre-existing**
  (reused, not introduced here).
- **scheduler.rs**: adds two blocked-sets `sigchld_deferred` / `sigchld_ready`
  and `step2e_process_signal_deferred`. The only behavioral change is *when a
  parent's already-delivered host-async SIGCHLD `InboundSignal` turn is
  committed*: it is parked out of the run queue and re-admitted only once no
  ordinary (non-poller) work remains (`first_priority() >= LAST_PRIORITY`),
  mirroring the existing `external_io_blockers` deterministic-work-first gate.

So the mechanism the owner suspected — "wrong thread prioritized post-clone →
the thread that must deliver the wakeup / make progress gets starved" — is
**not** what 1663138d implements. It changes SIGCHLD *delivery ordering to the
parent*, not child-vs-parent run priority at clone. This is mechanistically
consistent with the empirical refutation above: 1663138d boots demo5 GREEN, and
demo5's boot-phase wedge (QEMU vCPU starved by `SleepUntil(0)` pollers at hpet0)
does not go through the make-jobserver SIGCHLD/pselect6 path this commit
retimed. The poller-starvation mechanism lives elsewhere in
`(1663138d, 2f3689bd]` — most plausibly the guest-clock committed-time cluster
(3ac51e11 / cc3730fd), which is the next test.
