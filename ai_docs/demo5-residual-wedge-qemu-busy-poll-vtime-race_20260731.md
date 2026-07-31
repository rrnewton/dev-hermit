# demo5 residual wedge (post-#1190) — QEMU busy-poll vtime race

Task: `verify-demo5-green-reliability-post1190` (hermit-220). Date: 2026-07-31.
P0 differential trace-debug, demos-green critical path. This SUPERSEDES the
pre-#1190 analysis in `demo5-good-vs-broken-trace-diff-divergence_20260731.md`
for the *current-main* wedge.

## TL;DR — the residual wedge is NEW, not the 8.53 s lag

demo5 is **0/3 green on recent-main `ae2565be`** (all three boots HANG to the
600 s wall timeout). **#1190's clock-domain fix IS working** — the guest
`CLOCK_MONOTONIC` now tracks committed virtual time (~33 ms skew, not the prior
8.53 s lag), so the python controller's abs-deadline polls block correctly.

The residual wedge is a **different, pre-existing scheduler foundation bug**:
QEMU's *own* internal threads busy-handshake with non-blocking, immediately
satisfied futexes (`SleepUntil(LogicalTime(0))` every turn). Those pollers keep
the Detcore run queue non-empty, so scheduler **step2d never fires its
`committed_time` time-jump**. Committed virtual time races ahead (+2,251 s of
vtime in r1) while the **nested guest `-icount` clock barely advances (~1.96 s)**.
Hermit never terminates because the QEMU child busy-loops forever and is never
reaped → 600 s timeout → SIGKILL.

This is [[scheduler-vtime-jump-unproductive-pollers]], made fatal now that
[[pr1095-fake-determinism-clock-review-lesson]]'s clock lag (which #1190 fixed)
no longer masks it.

## The mirror image of the pre-#1190 wedge

| | pre-#1190 (`aa5258b6`) | post-#1190 (`ae2565be`, this run) |
|---|---|---|
| Clock skew | guest `CLOCK_MONOTONIC` **8.53 s BEHIND** committed | ~**33 ms** — tracks committed (fixed) |
| Who spins | **controller** (dtid 7): expired abs-deadline `clock_nanosleep` COMMITs | **QEMU threads** (dtid 5/13): non-blocking futex handshake COMMITs |
| Who starves | **QEMU** (dtid 9) frozen at 34 syscalls, qmp.sock never created | (no single starved victim; the whole VM stalls) |
| Symptom | "timed out waiting for socket qmp.sock" ~35 s | 600 s wall timeout; committed races to +2,251 s, guest icount ~1.96 s |
| Class | unproductive poller keeps run queue non-empty → no vtime-jump | **same class** — unproductive poller keeps run queue non-empty → no vtime-jump |

Same foundation bug (step2d only time-jumps when the run queue is strictly
empty; immediate/expired pollers keep it non-empty). #1190 removed the
controller-side instance of it; the QEMU-internal instance remains.

## Evidence (recent-main `ae2565be`)

Traces: `ignored/h/a/r{1,2,3}/.work/boot-*/hermit-info.log` (39 M / 266 M / 890 M),
each `hermit --log=info run --strict --no-rcb-time --target-timeslice 100000
--max-timeslice disabled -- python3 qemu_controller.py boot ...`; outer
`timeout 660s`, `QEMU_TIMEOUT=600`.

Reliability: 3/3 HUNG (651 s / 647 s / 647 s wall), first serial 45–48 s.

### 1. No clock lag — #1190 confirmed working

r3 controller (dtid 3):
```
finish syscall #14184: clock_gettime(CLOCK_MONOTONIC, ... -> { tv_sec: 1767225849, tv_nsec: 778175000 }) = Ok(0)   # 1767225849.778 s
COMMIT turn 65231, dettid 3 ... on previously committed 1_767_225_849.811_550_000s                                  # 1767225849.811 s
```
Guest CLOCK_MONOTONIC vs committed = **~33 ms** skew (was 8.53 s pre-#1190).

### 2. QEMU busy-handshake is the spinner

Dominant actors in the final 2000 log lines are the QEMU threads, not the
controller:
```
r1 tail dtids:  498 dtid 5,  236 dtid 13,  168 dtid 11   (controller dtid 3 already exited)
r2 tail dtids:  406 dtid 13, 378 dtid 11,  135 dtid 5
r3 tail dtids:  399 dtid 13, 397 dtid 3,   132 dtid 5
```
r3 per-turn pattern (same futex word `0x5555570c8ec0` = QEMU BQL / iothread↔vCPU):
```
[dtid 5]  futex(0x5555570c8ec0, 128 FUTEX_WAIT_PRIVATE, val=2) = Ok(0)   # returns IMMEDIATELY, never blocks
[dtid 13] futex(0x5555570c8ec0, 129 FUTEX_WAKE_PRIVATE)        = Ok(1)
[dtid 13] writev(14, ..., 1)                                   = Ok(1)
```
Every such turn: `COMMIT ... SleepUntil(LogicalTime(0)): W` (expired → run
immediately). Run queue never empties → step2d never time-jumps.

### 3. Committed races ahead of guest icount

r1 end: `COMMIT turn 1623871 ... committed 1_767_231_888.43s`. Boot start was
committed ~1_767_229_637s → **+2,251 s** of committed vtime burned, while the
nested guest `-icount` TSC reached only **~1.96 s**. Committed advanced ~6× wall
(3,943 s over 651 s) purely on the busy handshake.

### 4. Load-sensitive phase; same wedge, all die at 600 s

- **r1**: booted FULLY — serial has `HERMIT-QEMU-BASELINE-BOOT-OK` + `~ #`
  prompt. Controller reached the QMP savevm phase, then `exit_group(1)` at
  15:37:31 (connect(4) to QMP → close serial fd 3 → wait4/kill(5,SIGTERM) via
  `finally: stop_process` → 59-byte stderr → exit 1). QEMU was **not** reaped —
  orphaned, busy-polls on to committed 231,888 s.
- **r2**: wedged DURING boot (no marker, 17.8 KB serial); controller still
  alive; QEMU dtid 13/11/5 dominate; committed 227,340 s.
- **r3**: wedged EARLY (2.7 KB serial); controller dtid 3 + QEMU dtid 13/5 all
  busy-polling; committed 225,849 s.

In every case hermit cannot terminate: the QEMU child busy-loops and is never
reaped, so the outer 600 s timeout SIGKILLs the run.

## Routing / fix

- **Core DetCore scheduling (post-facto trigger #4, determinism-critical) →
  OWNER design.** Do not freelance a scheduler change. The fix is the
  step2d/`committed_time` time-jump generalization: jump when the only runnable
  threads are immediate/expired pollers (`SleepUntil(LogicalTime(0))`), not only
  when the run queue is strictly empty. A naive defer risks nondeterministic
  queue order — needs a deterministic admission/jump point (cf. #1162/#1152).
  Design explainer already on parent `main` @`4ac9ab2a`
  ([[scheduler-vtime-jump-unproductive-pollers]]).
- **#1190 is correct and necessary** but partial: it removed the controller-side
  clock lag; it does not make demo5 green. **demos-green stays GATED** on the
  vtime-jump fix.
- The a8195cfc ptrace perf regression (reverie #305) is NOT this wedge (see the
  task note from hermit-238); demo5 first-serial 46 s < 74 s green baseline.

## UPDATE — vtime-jump fix IMPLEMENTED and empirically REFUTED for demo5

The owner authorized and I implemented the exact directed fix: generalize
scheduler `step2d` so the `committed_time` forward-jump also fires when the run
queue is non-empty but holds *only* unproductive immediate pollers (all at
`LAST_PRIORITY`), not only when the run queue is strictly empty. Committed on
branch `codex/sched-poller-forward-progress` @ `6c6ecbe9` (base origin/main
`0ca0dec2`): shared helper `fast_forward_to_next_timed_event`, predicate
`run_queue_only_immediate_pollers`, and two unit regressions (positive
only-pollers jump; negative productive-thread no-jump). All 37 detcore scheduler
unit tests pass. The jump only ever advances to an *already-registered*
`timed_waiter` deadline — no freeze/round/synthesize — so it is
continuous-virtual-time-legitimate and deterministic (pure functions of state).

**It does NOT green demo5.** Rebuilt release hermit with the fix and re-ran the
boot: guest wedged at hpet0 (~0.724 s guest time) for 3+ minutes, hermit at 89 %
CPU (spinning), qemu at 8.6 % (vCPU starved), host load 79/316 cores (NOT
overloaded). Diagnostic boot with `detcore=info`
(`ignored/h/a/r4/.work/boot-_tdp7k0m/hermit-info.log`, 356 M):

- **`Skipping global time ahead` = 0** across the entire run — neither the
  pre-existing empty-queue jump nor the new poller-only jump ever fires.
- **192,344** `SleepUntil(LogicalTime(0))` poller commits.
- Future `timed_waiter` registrations (`NONCOMMIT … blocking`) = **3,395, all in
  deciles 0–7, ZERO in deciles 8–9** — the terminal spin registers no future
  deadline (`timed_empty` = TRUE during the wedge).
- external-IO reschedules = 0.
- Tail spinners dtid 5/11/13 handshake on QEMU BQL/iothread futex word
  `0x5555570c8ec0`: `futex(…128 FUTEX_WAIT, val=2)=Ok(0)` returns immediately;
  `futex(…129 FUTEX_WAKE)=Ok(1)`. Committed crawls +5.5 ms/turn via the per-turn
  tick.

**Why it is inert (this is the key finding).** The jump's precondition is
`!timed_empty && run_queue_only_immediate_pollers()`. In demo5 the two conjuncts
never co-occur: while future deadlines exist (deciles 0–7) there is productive
boot work so the queue is not only-pollers; once the queue is only-pollers (the
terminal wedge) `timed_empty` is TRUE — there is **no registered future event to
jump to**. The wedge is not "committed crawling toward a known future deadline";
it is a **deadline-less QEMU-internal BQL/iothread futex livelock**. A forward
time-jump is architecturally incapable of resolving a spin that has no forward
event. This confirms the pre-implementation concern verbatim.

**Heuristic-soundness verdict (owner asked).** The poller-only jump is a
*principled* generalization of the existing empty-queue deadlock-avoidance jump,
not a fragile heuristic:
- *Misclassification risk is low and fail-safe.* "Only pollers" = every runnable
  thread sits at `first_priority() >= LAST_PRIORITY`. If a genuinely productive
  thread is misclassified into `LAST_PRIORITY`, the jump would advance past it —
  but that is exactly the pre-existing `LAST_PRIORITY` contract (pollers are
  defined by that band); the fix inherits, not widens, that classification. The
  negative unit test pins that a `DEFAULT_PRIORITY` worker blocks the jump.
- *Wrong-target risk is nil.* The jump target is `timed_waiters.pop()` — the
  earliest already-registered deadline, the same source the empty-queue jump
  uses. It cannot synthesize or overshoot a deadline; it only advances to a real
  one and wakes exactly that event.
- *Starvation risk is nil.* Pollers keep their run-queue slots and run again
  after the woken event.
- *The real limitation is coverage, not soundness:* the jump is a no-op whenever
  `timed_empty` (no event to jump to), which is precisely demo5's terminal spin.

**Disposition.** Per owner guidance ("if it WORKS … land it"), the fix is **NOT
landed** and the task is **NOT** tagged `implemented` — it does not resolve the
wedge. The commit is retained on `codex/sched-poller-forward-progress` as a
sound, tested increment for owner review (it does help any workload where a real
deadline sits behind only-poller queues), but no PR is opened claiming a demo5
fix. The real demo5 blocker is the deadline-less QEMU BQL/iothread futex
livelock under sequentialized deterministic scheduling — a distinct bug needing
owner direction. Candidate non-blunting directions to evaluate next:
(1) QEMU-side `-icount sleep=on` idle-warp (memory
`qemu-demos-host-provisioning-devbig014` notes bare qemu + `sleep=on` boots;
must be tested under Hermit and against the controller's always-runnable-vCPU
assumption); (2) a deterministic scheduler response to a *deadline-less*
only-pollers steady state (harder; genuine trigger #4 design).

## Artifacts

- Traces: `ignored/h/a/r{1,2,3}/.work/boot-*/{hermit-info.log,serial.log}`.
- Reliability harness + summary: `ignored/demo5-reliability-post1190-ae2565be/`.
- Divergence anchors: r3 dtid-3 CLOCK_MONOTONIC 1767225849.778 s vs committed
  .811 s (no lag); r3 futex handshake on `0x5555570c8ec0`; r1 controller
  `exit_group(1)` @15:37:31 vs log end @committed 231,888 s (QEMU busy-poll).
