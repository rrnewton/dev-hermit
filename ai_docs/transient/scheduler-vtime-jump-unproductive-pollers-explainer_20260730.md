# Scheduler forward-progress: virtual time cannot advance past unproductive pollers

**Task:** `scheduler-vtime-jump-unproductive-pollers` (P1 FOUNDATION) · **Author:** impl agent, opus-4.8 · **Date:** 2026-07-30
**Status:** EXPLAINER + DESIGN OPTIONS for owner review. **No code.** This is a core DetCore
scheduling change (`post-facto-human-review` trigger #4) — owner picks a direction before any impl.
**Related:** [[min-vtime-scheduler-study]], [[sigchld-timed-waiters-determinization-design]],
[[make-jn-sigchld-nondeterminism-root-cause]], and memory `qemu-demos-host-provisioning-devbig014`.

All source citations are against `hermit/` at the tree examined on 2026-07-30 (parent primary on
`main`, HEAD `0321a015`). Log evidence is the retained wedged demo5 boot at
`ignored/qemu-linux/.work/boot-baaiha3k/{serial.log,hermit-info.log}` (serial 3.15 MB info log,
590k+ scheduler turns).

---

## 0. Executive summary (for the owner)

- **The bug.** Detcore advances one global logical clock (`committed_time`). It only *jumps* that
  clock forward to a pending deadline **when the run queue is empty**
  (`scheduler.rs:1996`). When every runnable thread is *unproductively yielding* — each commits
  `SleepUntil(LogicalTime(0))` (a yield) or an already-elapsed absolute sleep, then re-queues — the
  run queue is **never empty**, so the jump **never happens**. `committed_time` then only creeps
  forward by a fixed per-turn tick (`NANOS_PER_SCHED = 500 µs`, `time.rs:98` / `add_scheduler_time`,
  `time.rs:793`). Real demo5 evidence: **0 time-jumps across 590k+ turns**, dtids 3/21/19/5 cycling
  yields forever, guest wedged at `hpet0: 3 comparators` (guest uptime frozen at **0.724403 s**).

- **The subtlety the task's one-line hypothesis missed (important).** The task framed the fix as
  "jump vtime to the *pending timer deadline*." **In the demo5 trace there is no pending future
  deadline to jump to.** `timed_waiters` is empty of future events: `registering waiter at future
  time` occurs **0 times**, `Skipping global time ahead` **0 times**. The QEMU vCPU expresses its
  wait as a **busy-poll** — repeated absolute `clock_nanosleep` targets that trail `committed_time`
  (targets …896.25 s → …897.69 s in ~100 ms steps, while `committed_time` is already ~898.1 s), so
  each sleep is "in the past" and returns immediately. The guest's own clock (QEMU `-icount`) is
  *behind* `committed_time` and the gap **grows**. So this is not "committed time is stuck below a
  deadline"; it is "**committed time races ahead of the guest while no thread makes real progress.**"
  Any fix that only fires when `timed_waiters` is non-empty (Option A) will **not** cover demo5.

- **Why it is FOUNDATIONAL.** The same shape — *the scheduler cannot get past work that is present
  in the queue but not making progress* — underlies three other open problems, so one correct
  forward-progress mechanism unifies them (§5): demo5 HPET wedge, `make -jN` SIGCHLD starvation
  (#1157 residual), nix `--strict` build sequentialization slowness, and the priority-aging idea.

- **Determinism is the whole game.** Whatever we do must be a function of *committed scheduler
  state only* (pending `SchedRequest`s, `timed_waiters`, `committed_time`) and must fire after a
  **deterministically bounded** number of poll rounds — never after "N host-timed polls." §6 scores
  four options against this bar. My recommendation is a combined **forward-progress detector** (§7).

---

## 1. Symptom

`demos/05-qemu-boot.py` runs, under heavy host load:

```
hermit run --strict --no-rcb-time --target-timeslice 100000 --max-timeslice disabled -- \
    python3 qemu_controller.py boot -- qemu-system-x86_64 -icount shift=0,sleep=off ...
```

The guest kernel boots to:

```
[    0.724380] hpet0: at MMIO 0xfed00000, IRQs 2, 8, 0
[    0.724403] hpet0: 3 comparators, 64-bit 100.000000 MHz counter
```

…and then **stops** — guest uptime frozen at 0.724403 s while the harness burns its full timeout.
The vCPU is not spinning hot (host CPU ~5%); it is *blocked waiting for a virtual-time event that
never arrives*. (This is load-sensitive, not a code regression: see memory
`qemu-demos-host-provisioning-devbig014` — the good-era binary `9c67fd34` wedges identically now.)

---

## 2. The three scheduler primitives involved

### 2.1 A yield is `SleepUntil(LogicalTime(0))`
`Detcore::yield_request` (`detcore/src/syscalls/time.rs:137`):

```rust
pub fn yield_request<G: Guest<Self>>(guest: &mut G) -> Resources {
    let resource = ResourceID::SleepUntil(LogicalTime::from_nanos(0));   // ends at the epoch (the past)
    guest.thread_state().mk_request(resource, Permission::W)
}
```

Timeslice preemption issues exactly this in the common (non-chaos, non-`sched_yield`) path
(`detcore/src/lib.rs:694`, inside the timer-event handler):

```rust
} else {
    Self::yield_request(guest)          // <-- every ordinary preemption yields
};
resource_request(guest, req).await;
```

### 2.2 How the scheduler consumes `SleepUntil`
`scheduler.rs:2205` decides *ready now* vs *future waiter* purely by comparing the target to
`committed_time`:

```rust
ResourceID::SleepUntil(target_ns) => {
    if *target_ns <= self.committed_time {
        // time-based action ready to execute -> thread STAYS RUNNABLE (Ok, remains in run_queue)
        Ok(())
    } else {
        // future -> register a timed_waiter and DESCHEDULE
        self.blocked.timed_waiters.insert(*target_ns, dettid);   // scheduler.rs:2221
        self.skip_turn_blocked(dettid)
    }
}
```

**Consequence:** a yield (`target = 0`) is *always* `≤ committed_time`, so it never blocks — the
thread is immediately re-runnable. An absolute sleep whose target `committed_time` has already
passed behaves identically. Either keeps the thread **in the run queue**.

### 2.3 The only place virtual time *jumps*: `step2d_handle_empty_queue`
`scheduler.rs:1988`. The jump is gated on an **empty run queue** (`scheduler.rs:1996`):

```rust
fn step2d_handle_empty_queue(&mut self, global_time: &Arc<Mutex<GlobalTime>>) -> Result<(), SkipTurn> {
    let timed_empty = self.blocked.timed_waiters.is_empty();
    ...
    if self.run_queue.is_empty() {                       // <-- GATE
        ...
        } else if !timed_empty {
            // Deadlock avoidance: pop nearest timed_waiter and JUMP committed_time to it
            let (event_ns, evt) = self.blocked.timed_waiters.pop().expect(...);
            let delta = event_ns.duration_since(gt_now_ns);
            gt.add_extra_time(delta);                    // scheduler.rs:2037  <-- the vtime jump
            ...
        }
    }
    Ok(())
}
```

Everywhere else, committed time only advances by the fixed per-turn tick
`add_scheduler_time()` → `NANOS_PER_SCHED * multiplier` = **500 µs × multiplier**
(`detcore-model/src/time.rs:793` and `:98`), invoked from `scheduler.rs:2515`.

---

## 3. The mechanism, grounded in the demo5 log

From `boot-baaiha3k/hermit-info.log`, the tail is a pure yield/poll cycle among four guest threads
(dtids 3 = vCPU, 5/19/21 = QEMU helper threads):

```
COMMIT turn 590339, dettid  3 using resources {SleepUntil(LogicalTime(0)): W}, committed 1_767_227_898.095_925_000s
COMMIT turn 590340, dettid 21 using resources {SleepUntil(LogicalTime(0)): W}, committed 1_767_227_898.098_925_000s
COMMIT turn 590341, dettid  5 using resources {SleepUntil(LogicalTime(0)): W}, committed 1_767_227_898.104_425_000s
COMMIT turn 590342, dettid 19 using resources {SleepUntil(LogicalTime(0)): W}, committed 1_767_227_898.107_425_000s
COMMIT turn 590343, dettid  3 using resources {SleepUntil(LogicalTime(1767227897694562500)): W}, committed 1_767_227_898.112_925_000s
COMMIT turn 590344, dettid 21 using resources {SleepUntil(LogicalTime(0)): W}, committed 1_767_227_898.113_425_000s
...
```

Four measured facts (all from this one log):

1. **Run queue is never empty.** Last 2000 turns: dtid 3 ×546, dtid 21 ×546, dtid 19 ×545,
   dtid 5 ×363 — a perpetual round-robin. Resource mix over the last ~20k turns:
   `SleepUntil` ×3208, `FutexWait` ×327, `InternalIOPolling` ×65. There is always something to run.

2. **The jump never fires.** `Skipping global time ahead` = **0**, `deadlock avoidance` = **0** in
   590k+ turns. `step2d`'s `run_queue.is_empty()` gate (§2.3) is never satisfied.

3. **No future deadline is ever registered.** `registering waiter at future time` = **0**. Every
   non-zero `SleepUntil` target the guest requests (…896.25 s … …897.69 s, ~100 ms apart) is *less
   than* `committed_time` (~898.1 s), so it takes the *ready-now* branch (§2.2) and never enters
   `timed_waiters`. **There is nothing for a jump to jump to.**

4. **committed_time creeps and overtakes the guest.** Between turns 590339 and 590351,
   `committed_time` advances 898.095925 → 898.144425 s ≈ **48.5 ms over 12 turns ≈ 3–5 ms/turn**
   (the `NANOS_PER_SCHED` tick, magnified by sub-steps). Meanwhile the guest's own `-icount` clock
   (reflected in the sleep targets it computes) trails at ~897 s and falls **further** behind each
   round. The guest is trying to reach an HPET calibration deadline in *its* timeline; but its
   timeline barely moves (its instructions are throttled by single-CPU serialization + poll
   overhead) while `committed_time` sprints ahead uselessly. The deadline in guest-time is never
   reached → the vCPU's timer never fires → boot wedges.

**Restated:** the classic "poll keeps the queue non-empty so time can't jump" defeat of `step2d`
is real (facts 1–2), *and* demo5 adds a twist (facts 3–4): the thing the guest is waiting on is not
a registered `timed_waiter` at all — it is internal to QEMU's icount clock, which lags
`committed_time`. So demo5 needs **both** "recognise an all-unproductive round" **and** a notion of
progress that does not depend on a registered deadline.

---

## 4. Minimal diagram

```
                 committed_time  (Detcore global logical clock)
   897.6s   897.8s   898.0s   898.1s ───────────────►  creeps +3–5 ms every turn (NANOS_PER_SCHED)
     │        │        │        ▲
     │        │        │        └─ scheduler is HERE
     │        │        │
 guest -icount clock (QEMU) ~897.0s ──►  barely advances; LAGS committed and gap GROWS
     ▲
     └─ HPET calibration deadline lives in GUEST time, further ahead of the guest clock

   run_queue (never empty):  [ vCPU-3 , helper-21 , helper-19 , helper-5 ] ⟳
        each turn: pop → guest runs a hair → SleepUntil(0)/elapsed-sleep → RE-QUEUE
                                   │
                                   ▼
        step2d_handle_empty_queue:  if run_queue.is_empty() { jump to min(timed_waiters) }
                                    └──► GATE never true  ─► JUMP NEVER FIRES (0/590k turns)
        timed_waiters:  ∅  (no future deadline registered anyway)

   RESULT: committed_time sprints; guest clock stalls; deadline unreachable; boot wedged @0.724403s
```

Contrast with the *healthy* deadlock-avoidance case (a real `nanosleep`/timeout): the sleeper
descheduls into `timed_waiters`, the queue drains to empty, `step2d` pops the nearest deadline and
jumps `committed_time` to it. demo5 defeats this because the pollers keep the queue full **and** no
deadline is registered.

---

## 5. Why this is the FOUNDATION bug (one shape, four symptoms)

The invariant Detcore is missing is: *"if a full scheduling round produces no real progress,
deterministically advance to the next thing that can make progress."* Today that only exists for the
narrow `run_queue.is_empty()` + `timed_waiters` case. Four open problems are the same missing
invariant:

| Symptom | How the "can't get past unproductive work" shape appears | Today's partial mechanism |
|---|---|---|
| **demo5 HPET wedge** (this doc) | pollers keep queue full; committed overtakes guest icount; no deadline registered | none — wedges |
| **`make -jN` SIGCHLD starvation** ([[make-jn-sigchld-nondeterminism-root-cause]], #1157) | a child-reaping parent is *absent from the run queue* until a host-async SIGCHLD admits it; siblings busy-continue meanwhile | `step2e` deferred re-admission at quiescence (partial; residual host-timed entry) |
| **nix `--strict` sequentialization slowness** | independent build steps serialized on one logical CPU spend turns polling instead of advancing | none targeted |
| **priority-aging idea** ([[min-vtime-scheduler-study]] §0) | starvation-freedom for the `sched_yield` class (#81): a ready-but-never-selected thread should age up | prototype only, opt-in |

The [[min-vtime-scheduler-study]] already established the two load-bearing facts this design must
respect: (a) `timed_waiters` is Detcore's **only** legitimate min-vtime structure — time is a
scheduling *output*, so it must not become the general selection *input*; and (b) a wholesale switch
to min-vtime selection **livelocks on `make -jN`** precisely because of Detcore's inherited
*blocking-via-polling* model. So the fix must be a **scoped forward-progress detector layered on the
existing priority-turn-FIFO queue**, not a new selection algorithm.

---

## 6. Fix options (owner decides — determinism scored for each)

Determinism bar for all: the *trigger* and the *action* must be pure functions of committed
scheduler state (`run_queue` contents + each thread's pending `SchedRequest`, `timed_waiters`,
`committed_time`), and the trigger must fire after a **deterministically bounded** number of rounds
— never "after the host let N polls happen."

### Option A — Broaden the existing jump: fire `step2d` when all runnable threads are unproductive yielders
Detect "every thread in `run_queue` has a pending request that is a yield or already-elapsed
`SleepUntil` (i.e. no thread requested CPU/IO progress)"; if so, treat the queue as *effectively
empty* and run the existing `timed_waiters` jump.
- **Determinism:** GOOD. Trigger is a predicate over pending requests; jump target is
  `min(timed_waiters)`, already deterministic.
- **Covers:** SIGCHLD/nanosleep/timeout cases where a real future deadline exists.
- **Does NOT cover demo5:** `timed_waiters` is empty (§3 fact 3) — nothing to jump to. Necessary but
  not sufficient.

### Option B — Stop `committed_time` from overtaking the guest: suppress the per-turn tick on unproductive rounds
On a detected all-unproductive round, **do not** call `add_scheduler_time()` (§2.3). This halts the
runaway lead so the guest's icount clock can catch up and its short absolute sleeps stop landing in
the past. Mirrors the existing `last_turn_was_polling` suppression already at `scheduler.rs:2516`
and the `only_external_blocked` skip at `scheduler.rs:2492`.
- **Determinism:** GOOD (deterministic predicate → deterministic "don't advance"). Advancing *less*
  is as deterministic as advancing.
- **Covers demo5's root cause** (committed racing ahead) without needing a registered deadline.
- **Risk:** if *nothing* external ever advances the guest either, we trade a wedge for a different
  wedge; must be paired with a mechanism that lets the guest actually progress (Option D) or a
  bounded fizzle.

### Option C — Charge yields nothing (don't advance on pure-yield turns at all, always)
Narrower than B: make `add_scheduler_time` a no-op specifically for turns whose committed request
was a yield / elapsed-sleep, unconditionally. This is close to what the min-vtime prototype's
"non-polling turns only" charging did.
- **Determinism:** GOOD.
- **Risk:** removes a source of monotonic progress that timeout enforcement relies on; could stall
  legitimate timeouts. Needs care around `timed_waiters` deadline accounting.

### Option D — Real-time / icount pacing (let the vCPU actually wait)
The empirical escape hatch already known (memory `qemu-serial-socket-starves-vcpu…`,
`qemu-demos-host-provisioning-devbig014`): bare `qemu -icount sleep=on` boots reliably because QEMU
*sleeps* for idle virtual time instead of busy-warping, which lets the deadline arrive. A
Detcore-side analogue would pace committed time to a real or icount-derived rate when idle.
- **Determinism:** BAD if it pegs to wall-clock. Only viable if the pacing source is the guest's own
  deterministic icount, not host time. Likely a QEMU-config workaround (`sleep=on`) rather than a
  Detcore change — keep as the operational fallback, not the determinism fix.

### Not-an-option — switch core selection to min-vtime
Ruled out by [[min-vtime-scheduler-study]]: livelocks on `make -jN`, and turns time into the
selection input (circular). Mentioned only to close it off.

---

## 7. Recommendation (for owner review — no code until you pick)

A single **forward-progress detector** that combines A + B, checked once per full scheduling round:

1. **Detect** an *unproductive round*: one complete pass over `run_queue` in which **every**
   committed request was a yield or an already-elapsed `SleepUntil`, with **no** `BlockingExternalIO`
   completion, futex wake, or non-zero committed work. This is a deterministic predicate; counting
   "one full round" (each runnable dtid seen once) makes the trigger host-timing-independent (fires
   after a bounded, deterministic number of turns rather than after however many polls the host
   allowed).
2. **Act**, deterministically, in priority order:
   - if `timed_waiters` is non-empty → **jump** `committed_time` to `min(timed_waiters)` (Option A —
     serves SIGCHLD/nanosleep/timeout);
   - else → **stop advancing** `committed_time` for unproductive rounds (Option B/C — serves demo5,
     lets the guest icount catch up), with a deterministic bounded fizzle if truly nothing can
     progress (reuse the `zero threads left … fizzling` path at `scheduler.rs:2010`).
3. **Unify** with §5: the same detector's "one thread is ready but never selected across K rounds"
   counter is the hook for priority-aging (#81), and its "quiescence = no real progress this round"
   definition is the same quiescence `step2e` wants for SIGCHLD re-admission — so the SIGCHLD work
   ([[sigchld-timed-waiters-determinization-design]]) and this become one mechanism instead of two.

**Open questions for the owner before impl:**
- Is "stop advancing committed_time on unproductive rounds" (B/C) acceptable given timeout
  semantics, or do we require a registered deadline always (accept demo5 stays a `sleep=on` /
  quiet-host operational fix and scope this task to the SIGCHLD/nanosleep A-case)?
- Round granularity: strictly "each runnable dtid once," or a small deterministic K-round hysteresis
  to avoid over-eager jumps mid-legitimate-spin?
- Because this is trigger #4, it lands behind an opt-in flag first (as the min-vtime prototype did),
  validated on demo5-under-load + `make -jN --strict --verify` + the SIGCHLD suite, before becoming
  default.

---

## 8. Reproduction / evidence pointers

- Wedged trace: `ignored/qemu-linux/.work/boot-baaiha3k/{serial.log,hermit-info.log}` (frozen at
  `hpet0: 3 comparators`, 0 time-jumps, 0 future waiters, creep ~3–5 ms/turn).
- Healthy comparison: `ignored/qemu-linux/boot-anchor.socket-stale.1785356420/serial.log` reaches
  the busybox shell (`Interactive busybox shell`), same binary, lighter load.
- Source: `detcore/src/syscalls/time.rs:137` (yield), `detcore/src/scheduler.rs:2205` (SleepUntil
  consume), `:1988`/`:1996`/`:2037` (step2d jump + gate), `detcore-model/src/time.rs:98`/`:793`
  (`NANOS_PER_SCHED`, `add_scheduler_time`), `detcore/src/lib.rs:694` (preemption → yield).
