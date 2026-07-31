# SIGCHLD Timed-Waiters Determinization — Design (PREP)

Status: **PREP / design-frozen, code in progress, validation HELD** pending the
ptrace `--strict --verify` baseline (hermit-210). Branch-only until owner
reviews the PR. This is a **core DetCore scheduling change** →
`post-facto-human-review` trigger #4, and per owner direction is published as a
draft PR tagged `pre-land-human-review` (owner reviews on the PR *before*
landing — an explicit override of the standing "never apply pre-land" rule).

Base: `rrnewton/hermit` `origin/main` @ `37ea6bce` (already contains the merged
partial fix PR #1157). Related: [[make-jn-sigchld-nondeterminism-root-cause]],
[[min-vtime-scheduler-study]].

Role tag for all PR/notes: `[impl agent, opus-4.8]`.

---

## 1. Problem

`make -jN --strict --verify` diverges (non-log-identical across the two verify
runs). Root cause class: **child-exit `SIGCHLD` admission is driven by
host-async signal-arrival timing**, not by the deterministic logical clock.

When a guest child process exits:

1. `handle_exit_group` (syscalls/threads.rs:404) requests
   `ResourceID::Exit{group:true, process, mm}` then `tail_inject`s the real
   `exit_group`. The **Exit grant is scheduler-ordered and deterministic**; its
   `committed_time` is a deterministic logical instant — call it `t_exit`.
2. The **kernel** then raises a real `SIGCHLD` on the parent process at a
   host-decided wall-clock moment.
3. The parent traps it → `handle_signal_event` (lib.rs:1046) → requests
   `ResourceID::InboundSignal(SigWrapper(SIGCHLD))`.
4. Scheduler grant path (scheduler.rs:2137) — the PR #1157 machinery — defers
   the parent into `blocked.sigchld_deferred` when other ordinary work is
   runnable, and `step2e_process_signal_deferred` (scheduler.rs:1430) re-admits
   it at "deterministic-work-first" quiescence.

**Why #1157 is only partial AND introduced a regression:**

- *Partial determinism:* step2e fixes the *re-admission ordering* (fires only at
  quiescence, in sorted `DetTid` order) but the *entry* into the deferred set
  still happens whenever the host-async signal physically lands. The logical
  instant at which the parent's `InboundSignal` request first reaches the
  scheduler is a function of host timing, so which scheduler step observes the
  signal — and the interleaving relative to sibling jobserver `pselect6`/pipe
  continuations — is not pinned to the logical clock. PR #1157 reached 5/6 clean;
  the residual is this host-timed entry (memory CH4,
  task `fix-execd-sibling-admission-quiescence`).
- *Liveness regression (now on main):* step2e's quiescence gate ("re-admit only
  when no ordinary work is runnable") **starves the parent forever** when a
  sibling never quiesces. Confirmed with `redis_deep`: redis-cli exits, parent
  `dtid21` enters SIGCHLD handling, but redis-server `dtid9` continuously cycles
  `clock`/`gettimeofday`/`epoll_wait`, so the run_queue is never empty of
  ordinary work and the deferred SIGCHLD is only re-admitted at the SIGTERM
  timeout. Base `eb76b3a0` verifies in ~57s; the #1157 head cannot finish a
  single strict Run1 in 45s. **This deadlock is the strongest motivation for the
  timed-waiters fix** — it removes the runnability-conditioned gate entirely.

## 2. Mechanism (the fix)

Make `SIGCHLD` a **scheduler-synthesized timed event at the deterministic
`t_exit`**, exactly like alarms/POSIX timers already are, instead of a
host-async inbound signal that the scheduler reacts to.

Three coupled parts:

### 2a. Deterministic registration (child-exit side)
At the moment the child's `Exit{group:true}` (and the last-thread `Exit`) is
**granted** — a deterministic logical instant `t_exit` under scheduler control —
look up the parent `P` via `thread_tree`, and if `P` is a live guest that has
not set `SIGCHLD` to `SIG_IGN`/`SA_NOCLDWAIT`, insert
`TimedEvent::SignalEvt(ChildExit(child_pid), P_tid, SIGCHLD)` into
`blocked.timed_waiters` with **deadline `t_exit`**.

Requires a new `SignalTimerId::ChildExit(DetPid)` variant (timed_waiters.rs:35)
so the coalescing key is unique per exiting child (Alarm/Posix keys would
collide and hit the `insert_signal_timer` "already in set" panic when a process
both has a timer and reaps children).

### 2b. Deterministic firing (already exists)
`step2b_process_timed` (scheduler.rs:1489) pops the event once
`committed_time >= t_exit` and calls `fire_alarm(P_pid, P_tid, SIGCHLD)`
(scheduler.rs:1504) → `signal_guest(P, SIGCHLD)` (scheduler.rs:1607), which
physically `kill`s the parent and makes it runnable. **No new firing code.**

### 2c. Suppression of the real host-async SIGCHLD (the hard part — see §5)
The kernel *still* delivers a real `SIGCHLD` to `P`. That real signal must not
*also* drive admission, or we double-count and reintroduce the host-timed
channel. Options under evaluation in §5.

## 3. Model correction (must appear verbatim-in-spirit in the PR)

Two distinct scheduler structures, previously conflated in discussion:

- **`run_queue` = priority-turn FIFO.** `BTreeMap<PrioritizedOrder,
  QueueValue>`, `PrioritizedOrder{priority: Priority(u64), turn:
  RoundRobinTurn(i64)}`, `Ord = priority.then(turn)`. In non-chaos every thread
  is `DEFAULT_PRIORITY` so it degenerates to FIFO-by-insertion. Priority is the
  single actuator chaos/replay/race-search use. It is **not** a min-vtime heap.
- **`timed_waiters` = a genuine min-vtime min-heap.** `BTreeMap<LogicalTime,
  BTreeSet<TimedEvent>>` with `pop_if_before(committed_time)`
  (timed_waiters.rs). It is deliberately scoped to *deadline* events
  (sleeps/timeouts/alarms/POSIX timers), and woken `ThreadEvt`s are pushed to
  the **front** of the priority queue (`wake_timed_event` →
  `runqueue_push_front`, scheduler.rs:1575/1601).

The fix routes child-exit `SIGCHLD` through the **timed_waiters min-vtime heap**
(the correct structure for "an event that must occur at logical time T"), not
through run_queue admission heuristics. This is why it is deterministic where
the #1157 quiescence gate was only partial.

## 4. Liveness proof (no #1157-style starvation)

Claim: once the event is committed at deadline `t_exit`, the parent `P` is
delivered `SIGCHLD` within a bounded number of scheduler turns, **regardless of
whether any sibling is runnable**.

- `committed_time` is monotonically non-decreasing and is advanced by the
  scheduler on every committed turn (`bump_global_time` → `add_scheduler_time`).
- `step2b_process_timed` runs at the top of `step2_process_blocked` on every
  scheduling pass and pops **every** event with deadline `<= committed_time`.
- Therefore on the first pass after `committed_time` reaches `t_exit`, the
  `SignalEvt` fires unconditionally. It does **not** consult run_queue
  occupancy or sibling runnability (contrast step2e's quiescence gate). So the
  make-jN livelock class — parent never selected because pollers dominate the
  run_queue — cannot occur: the timed heap fires the parent independent of the
  run_queue.
- `step2d_handle_empty_queue` guarantees the clock keeps advancing when only
  blocked/timed work remains, so `committed_time` will reach `t_exit` even if
  every guest thread is currently blocked.

This is the same liveness the existing alarm/timer path already relies on;
child-exit `SIGCHLD` simply joins it.

## 5. Open questions / risks (MUST be resolved in code before validation)

0. **The trigger must be the child-exit grant, not the parent-signal arrival
   (settled).** A tempting cheaper variant — "when the parent's real `SIGCHLD`
   `InboundSignal` arrives, park it in `timed_waiters` until the recorded
   `t_exit`" — is provably a **no-op**. Between the child's Exit grant (at
   `t_exit`) and the parent's host-async `SIGCHLD` arrival, the scheduler commits
   sibling turns, so `committed_time` has already advanced **past** `t_exit` by
   the time the signal enters the scheduler. Parking until an already-passed
   deadline grants immediately and re-exposes the host-timed entry. Therefore the
   registration MUST happen at the deterministic child-exit grant (§2a), which is
   exactly the owner's "route child-exit SIGCHLD as `TimedEvent::SignalEvt` at
   deterministic `t_exit`."

1. **Double delivery / suppression (2c) — the central remaining risk.** Because
   the trigger is the child-exit grant, the scheduler *synthesizes* the `SIGCHLD`
   via `signal_guest`, and the kernel *also* raises a real one at `P`. The real
   host-async one must not additionally drive admission. Resolution:
   **swallow the real `SIGCHLD` in `handle_signal_event`** (lib.rs:1046) /
   at the `InboundSignal` grant (scheduler.rs:2137) when it is attributable to a
   child exit detcore has already synthesized. Gate the swallow on matching
   accounting — a per-parent count of synthesized-but-not-yet-consumed child-exit
   `SIGCHLD`s — never on the bare signal number, so a `SIGCHLD` from a
   non-exit source (stop/continue, `SI_USER` kill) is still delivered. detcore
   already tracks exited children (`exited_children` /
   `record_exited_child_process_cpu_time`, tool_local.rs:1122-1569), giving the
   accounting hook. Deadline is `t_exit + one scheduler tick` so the synthesized
   event is strictly ordered after the exit that produced it.
1a. **Parent-of-process plumbing (new, required for §2a).** At the child's Exit
   grant the scheduler must know the parent `DetPid` to target. Today detcore has
   no direct parent-of-process map at that site: `ThreadTree` stores
   parent→children (`tree`) and thread→leader (`thread_to_leader`) but not a
   process→parent-process reverse map, and the child only holds a
   `parent_process_cpu_time: Arc<Mutex<ProcessCpuTime>>` accounting handle
   (tool_local.rs:1260, set at clone; child-exit records via lib.rs:2215), which
   does **not** carry the parent's `DetPid`. Options: (i) reverse-search `tree`
   for the process whose child set contains the exiting leader (O(n), but n is
   small and the search is deterministic); (ii) add an explicit
   `process_parent: HashMap<DetPid, DetPid>` populated at clone/fork alongside
   `add_edge`. (ii) is cleaner and O(1); prefer it. Must handle re-parenting to
   init on parent pre-death (orphan → no signal target, mirror
   `select_signal_target`'s `None` path at scheduler.rs:1505).

2. **Reapability ordering.** When `P`'s `wait4`/`waitid` runs after admission,
   the child must be host-reapable. `t_exit` is taken at the Exit *grant*, but
   the child's physical `tail_inject(exit_group)` completes slightly later.
   `signal_guest`'s `backend_reports_physical_process_exits` /
   `complete_physical_process_exit` (tool_global.rs:397) barrier is the existing
   mechanism ensuring physical waitability; must confirm the timed firing is
   ordered no earlier than that barrier for ptrace (barrier is a no-op for
   ptrace/DBI/KVM per the doc comment — so ptrace relies on the child having
   physically exited by the time the parent's wait4 is *granted*, which the
   existing wait4 emulation already handles). Validate with `redis_deep`/wait4
   guests.
3. **Multi-child coalescing.** N children exiting → N `ChildExit(pid)` events
   (distinct keys, no panic). Linux coalesces `SIGCHLD`, but each child must
   still be individually reapable. Approach (b) naturally preserves this (one
   real signal per exit, each admitted at its own `t_exit`); approach (a) would
   need explicit per-child accounting.
4. **Interaction with #1157 machinery.** If (b) is chosen, `sigchld_deferred` /
   `step2e_process_signal_deferred` may become redundant for the child-exit case
   but should be *retained* (smaller diff, defense-in-depth for any SIGCHLD not
   matched to a `t_exit`). Do not delete #1157 in the same PR unless validation
   shows it is fully subsumed; note the relationship explicitly.

## 6. Integration points (origin/main @ 37ea6bce)

| Concern | Location |
| --- | --- |
| `TimedEvent`/`SignalEvt`/`SignalTimerId` enum + insert/pop | `detcore/src/scheduler/timed_waiters.rs:34-114,228` |
| Timed firing dispatch | `detcore/src/scheduler.rs:1489-1517` (`step2b_process_timed`, `fire_alarm`) |
| Physical signal delivery + requeue | `detcore/src/scheduler.rs:1607` (`signal_guest`) |
| Parent lookup / thread group | `detcore/src/scheduler.rs:429,445,562` (`thread_tree`) |
| Child-exit grant (register `t_exit`) | `detcore/src/syscalls/threads.rs:385-420` (`handle_exit`, `handle_exit_group`) |
| Host-async SIGCHLD → InboundSignal | `detcore/src/lib.rs:1046` (`handle_signal_event`) |
| #1157 deferral / re-admit | `detcore/src/scheduler.rs:1430,2137` (`step2e`, InboundSignal grant) |
| Physical-exit waitability barrier | `detcore/src/tool_global.rs:397` (`complete_physical_process_exit`) |

## 7. Validation plan (HELD until ptrace baseline confirmed)

- `hermit run --strict --verify -- make -jN` **log-identical ≥3 runs** (ptrace).
- `redis_deep` + an HTTP-server liveness guest (parent reaps workers) —
  no hang, correct reaping.
- Existing suites: `cargo test -p detcore` (timed_waiters + scheduler unit
  tests), `cargo test -p detcore --test tests_time`, workspace fmt+clippy.
- Add a `timed_waiters` unit test for the `ChildExit` key
  (register/pop/coalesce with a concurrent Alarm on the same process — the
  panic the new key prevents).
- Report exactly per Communication Precision: backend=ptrace, level (target
  L2), programs, mode=`--strict --verify`, and any relaxations (none intended).

Build **without** `with-proxy` (BPFJailer blocks cc1); use `with-proxy` only for
networked git/gh.
