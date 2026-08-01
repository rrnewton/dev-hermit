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

4. **Unproductive pollers keep run_queue non-empty and register NO future
   timed_waiter.** The runnable set at HPET init is dominated by pollers yielding
   `ResourceID::SleepUntil(LogicalTime(0))` (immediate). With `target <=
   committed_time` (incl. `LogicalTime(0)`), `block_for_one_resource` returns
   `Ok(())` immediately and NEVER inserts a `timed_waiter` (`scheduler.rs:2205-2225`).
   This is present in BOTH a healthy bare-stdio boot and the wedge, so it is
   necessary background, not the trigger (see step 6 correction).

5. **The virtual-time forward-jump (`step2d`) never fires.**
   `Scheduler::step2d_handle_empty_queue` (`detcore/src/scheduler.rs:1989-2051`)
   is guarded by `if self.run_queue.is_empty() {` (`scheduler.rs:1997`). Because the
   `SleepUntil(0)` pollers stay runnable, the queue is never empty → the guard
   fails → **step2d never fires.** Even if the queue emptied, there is no future
   `timed_waiter` (step 4) to jump to. Again present in healthy boots too.

6. **CORRECTION (adversarial self-refutation) — the background signatures above
   are NOT sufficient for the wedge; the trigger is a host-pollable listening
   socket fd.**
   An earlier version of this file claimed a bare-QEMU boot wedges and cited a
   `wedge-off-run1` "dtid 5 starved" witness. **That was wrong.** The bare
   `-serial stdio` busybox boot (`scratch/demo5-icount-sleep/boot_qemu_off.sh`,
   `out/wedge-off-run1/console.log`) **BOOTS**: it reaches
   `HERMIT-QEMU-BUSYBOX-PASS` and `reboot: Power down` at guest ts 1.903 — slow
   (crawls to `hpet0` by ~3 min) but successful; the `status=124` was a
   post-power-down teardown hang, not an HPET wedge. So `SleepUntil(0)`-dominance,
   `step2d`=0, and the racing clock all occur in a HEALTHY `--no-rcb-time` boot.

   **Minimal isolation repro of the true trigger**
   (`scratch/demo5-icount-sleep/boot_qemu_sock.sh` vs `boot_qemu_off.sh` —
   single variable: injected host-pollable listening sockets). Identical bare
   busybox kernel/initrd, identical `-icount shift=0,sleep=off`, console held
   observable via `-serial file:`; the sock variant ADDS
   `-serial unix:…,server,nowait` + `-qmp unix:…,server,nowait`:
   - **`boot_qemu_off.sh` (no sockets): BOOTS to PASS (guest ts 1.903).**
   - **`boot_qemu_sock.sh` (+2 listening sockets): WEDGES — frozen at
     `hpet0` (guest ts 0.715845), zero guest output for 77+ s while the
     scheduler burns ~174k turns / 20 s (`out/wedge-sock-run1`), never reaching
     PASS within the 300 s budget.**
   This is the same freeze point as the parent controller harness
   (`demos/05-qemu-boot.py`, 237's aa5258b trace: "timed out waiting for
   qmp.sock"). The parent demo's own comment (`05-qemu-boot.py:94-97`) already
   names this: a socket chardev is a host-timing-driven pollable fd that starves
   the `-icount` vCPU under `--no-rcb-time`.

   **CAVEAT 1 — the `dtid_activity` STARVED-TAIL flag is NOT a wedge
   discriminator.** Running `log-science/dtid_activity.rs` on BOTH traces shows a
   large starvation tail in EACH:
   - wedge (sock): dtid 7 starved 84.7% of the run, clock +2222 s.
   - **SUCCESS (bare, `wedge-off-run1`, reaches PASS): dtid 5 starved 76.2% of
     the run, clock +1232 s — yet the boot completes.**
   A thread that legitimately finishes its work and is never needed again trips
   the same heuristic as a genuinely starved one. So "a starvation tail exists"
   proves nothing; the only sound discriminator is the OUTCOME — the bare boot
   reaches `HERMIT-QEMU-BUSYBOX-PASS` / `Power down` (guest ts 1.903) whereas the
   sock boot stays frozen at `hpet0` (guest ts 0.715) for 7+ min / 3 M+ turns and
   never PASSes. (This over-fire is a real gap in `dtid_activity.rs`; it needs a
   "did the guest reach a terminal marker / did every thread's last state = parked
   vs exited" refinement before its STARVED-TAIL flag can be trusted.)

   **CAVEAT 2 — console confound, closed by a dedicated control.**
   `boot_qemu_off.sh` used `-serial stdio` while `boot_qemu_sock.sh` used
   `-serial file:` console PLUS the two sockets, so off→sock changed two things.
   `boot_qemu_filecon.sh` is the single-variable control: `-serial file:` console
   and NO extra sockets. **RESULT: it BOOTS** — crawls at `hpet0` (~0.7167) for
   ~75 s then breaks through to `HERMIT-QEMU-BUSYBOX-PASS` / `reboot: Power down`
   (guest ts 1.903269), `out/wedge-filecon-run1`. So the `-serial file:` console
   is NOT the cause; the two host-pollable listening sockets are the sole
   remaining variable ⇒ **the host-pollable listening socket fd is the confirmed
   single-variable wedge trigger.**

   **Controlled A/B (identical bare busybox, same `--no-rcb-time`/`-icount`
   flags; the ONLY variable is the injected listening sockets):**

   | variant | console | listening sockets | outcome |
   |---|---|---|---|
   | `boot_qemu_off.sh` | `-serial stdio` | none | BOOTS — PASS at guest ts 1.903 |
   | `boot_qemu_filecon.sh` | `-serial file:` | none | BOOTS — PASS at guest ts 1.903 |
   | `boot_qemu_sock.sh` | `-serial file:` | +2 (serial unix + QMP) | **WEDGES — frozen at `hpet0` ts 0.716, 3 M+ turns / 7+ min, never PASS** |

⇒ **Refined mechanism.** The deadline-less unproductive-poller steady state
(`SleepUntil(0)`, run_queue never empty, step2d=0, committed_time racing via the
500× syscall multiplier + per-turn `NANOS_PER_SCHED` tick) is real but on its own
a bare guest still makes forward progress and boots. Adding a **host-pollable
listening socket fd** introduces a chardev poller whose readiness is
host-timing-driven; under `!use_rcb_time()` (no PMU preemption) that poller
monopolizes turns and the productive `-icount` vCPU thread is starved of
re-selection — the guest freezes at `hpet0` while committed_time races past. Plain
`--strict` (PMU armed) preempts the poller and keeps the vCPU getting turns, so it
boots regardless of the socket. (Q3 load-independence is unaffected: WHICH thread
is starved is a deterministic function of the flag/fd set — see
`load-independence.md`; only wall-clock duration and the verify-excluded
committed_time value vary with host load.)

## Why plain `--strict` (the crawl) boots

No `--no-rcb-time`, no `--max-timeslice disabled` → `max_timeslice` keeps default
`200000000` (`config.rs:396-403`) and `no_rcb_time == false` ⇒
**`use_rcb_time() == true`**. The decisive difference is the **PMU preemption
timer**: it IS armed (`lib.rs:529-639` install path, `guest.set_timer_precise(
TimerSchedule::Rcbs(...))`), so the QEMU vCPU/TCG thread is forcibly preempted
after a bounded number of retired branches and returns to the scheduler as a
productive checkin — it can no longer be starved indefinitely by the pollers, and
RCB retirement also folds into its logical time. The guest therefore keeps getting
turns and advances through HPET init — slowly (~325s), hence the crawl. (Note the
crawl is not primarily "RCB creeps the global clock across a deadline"; global
committed_time races ahead in BOTH configs. What rcb-time restores is bounded
preemption/fair turns for the productive guest thread.)

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
