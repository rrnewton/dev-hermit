# Source-level root-cause of the demo5 `--no-rcb-time` wedge

Authoritative file:line proof (RCB-time/timeslice/step2d source audit). All paths
under `/home/newton/work/dev-hermit/hermit/`. Verbatim short excerpts.

Wedge config (parent `demos/05-qemu-boot.py`, also reproduced bare in
`scratch/demo5-icount-sleep/run_wedge.sh`):
`hermit run --strict --no-rcb-time --target-timeslice 100000 --max-timeslice disabled`.

## The causal chain

1. **`use_rcb_time()` is false.**
   `detcore-model/src/config.rs:635-637`:
   ```rust
   pub fn use_rcb_time(&self) -> bool { self.max_timeslice.is_some() && !self.no_rcb_time }
   ```
   `--max-timeslice disabled` → `max_timeslice == None` (`config.rs:984-1007`:
   `"disabled" => Ok(None)`), and `--no-rcb-time` sets `no_rcb_time == true`
   (`config.rs:475`). Either alone makes `use_rcb_time()` false; demo5 sets both.

2. **Guest branch retirement advances virtual time by ZERO.**
   `Detcore::update_logical_time_rcbs` (`detcore/src/lib.rs:359-475`) is doubly
   gated:
   - outer `if self.cfg.max_timeslice.is_some() {` (`lib.rs:364`) is FALSE → entire
     body skipped, `guest.read_clock()` never called, no RCB bookkeeping;
   - inner `if self.cfg.use_rcb_time() {` (`lib.rs:376-387`) also FALSE →
     `thread_logical_time.add_rcbs(delta_rcbs)` never runs.
   (Per-RCB weight would be `NANOS_PER_RCB = 10.0`, `detcore-model/src/time.rs:39`.)

3. **No PMU preemption timer is armed.**
   `ThreadState::next_timeslice` (`detcore/src/tool_local.rs:1857-2097`):
   `logical_timeslice = cfg.target_timeslice.or(cfg.max_timeslice)` = `Some(100000)`
   (from `--target-timeslice`), so `end_of_timeslice = current + 100000ns`
   (`tool_local.rs:1994-1999`); but `max_timeslice_end` resolves to `None`
   (`tool_local.rs:2052-2076`; unit test
   `target_only_mode_does_not_create_a_pmu_deadline` `tool_local.rs:2634-2650`
   asserts `max_timeslice_end == None`). Then `post_handler_hook`
   (`detcore/src/lib.rs:529-644`) takes the SKIP path `lib.rs:640-644`:
   ```rust
   assert!(guest.config().max_timeslice.is_none());
   guest.thread_state_mut().last_rcb_timer = None;   // no RCB/PMU timer armed
   ```
   The only preemption left is the `--target-timeslice` *logical* deadline,
   reachable ONLY via an intercepted syscall/rdtsc/cpuid checkin at a handler
   boundary (`end_timeslice_if_needed` `lib.rs:506-522`, `timeslice_expired`
   `tool_local.rs:1804-1811`, `current_time >= end_of_timeslice`).

4. **QEMU's HPET-init spin never checks in → run_queue never empties.**
   The vCPU/TCG thread spins in-guest with no intercepted syscall, so
   `current_time` (= `thread_logical_time`, which by step 2 does not advance)
   never reaches `end_of_timeslice`, and with no PMU timer nothing forces a
   checkin. The thread stays runnable indefinitely.

5. **The virtual-time forward-jump (`step2d`) never fires.**
   `Scheduler::step2d_handle_empty_queue` (`detcore/src/scheduler.rs:1989-2051`)
   is guarded by `if self.run_queue.is_empty() {` (`scheduler.rs:1997`) — false
   here. Even if the queue emptied, the jump target is the earliest
   `timed_waiter` (a `BTreeMap<LogicalTime,…>` pop, `scheduler.rs:2019-2027`,
   log `"Skipping global time ahead to {}."` `:2028`), and there is none:
   `ResourceID::SleepUntil` with `target <= committed_time` (incl.
   `SleepUntil(LogicalTime(0))`) returns `Ok(())` immediately and is NEVER
   inserted as a `timed_waiter` (`scheduler.rs:2205-2225`). So even an empty queue
   would fall through without advancing time.

6. **Per-turn creep can't rescue it.**
   `add_scheduler_time` adds `NANOS_PER_SCHED = 500_000` ns/turn
   (`detcore-model/src/time.rs:98`, applied `scheduler.rs:2524`) but only on a
   *productive* scheduler turn; an in-guest spin yields no turns.

⇒ committed_time cannot reach the guest HPET deadline ⇒ HPET calibration never
completes ⇒ **hard, deterministic livelock.**

## Why plain `--strict` (the crawl) boots

No `--no-rcb-time`, no `--max-timeslice disabled` → `max_timeslice` keeps default
`200000000` (`config.rs:396-403`) and `no_rcb_time == false` ⇒
**`use_rcb_time() == true`**. Both rescue channels the wedge removes are active:
RCB retirement folds into vtime (step 2 gates now pass), and the PMU preemption
timer IS armed (`lib.rs:529-639` install path, `guest.set_timer_precise(...)`),
forcing periodic checkins. Together they creep committed_time across the HPET
deadline — slowly (~325s), hence the crawl.

## Load-independence corollary (Q3)

The decision path reads no host wall-clock (§Axis-1 of `load-independence.md`).
The wedge is therefore a *deterministic* property of the flag set, not a
load-dependent race — matching the empirical byte-identical cross-load decision
traces.

## The fix direction (not this task's deliverable)

The genuine fix is scheduler-side: give the deadline-less unproductive-poller /
in-guest-spin steady state a deterministic forward-progress path (e.g. a
vtime-jump that can fire without an empty run_queue when the only runnable work is
unproductive polling, or a synthetic wake target for the vCPU). Tracked as
`scheduler-vtime-jump-unproductive-pollers`. Re-enabling rcb-time (the crawl) is a
legitimate but ~5x-slow step-back, not the fix.
