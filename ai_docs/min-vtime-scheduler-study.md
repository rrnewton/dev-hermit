# Min-Vtime Scheduler Study for Detcore

**Task:** `study-min-vtime-scheduler-alternatives` (P2) · **Author:** impl agent, opus-4.8 · **Date:** 2026-07-29
**Status:** RESEARCH + BRANCH PROTOTYPE. Branch-only, exploratory. **NOT for landing without owner discussion + approval** (core DetCore scheduling change, post-facto-human-review trigger #4).
**Prototype branch:** `study/min-vtime-scheduler-prototype` (slot `makedet`) @ `d2dc977e`, 3 files changed in `hermit/`. Two opt-in heuristics: `minvtime` (deterministic) and `minvtime-allturns` (the control variant added to isolate the livelock cause — see §4.2).

This answers three questions the owner posed:
1. Why did Detcore use a priority-turn-FIFO run queue instead of a direct MIN-VTIME algorithm that other deterministic threading systems use?
2. What does the prior art actually do (dettrace, DThreads, CoreDet, Kendo, DMP, rr, Determinator)?
3. Does a min-vtime variant, prototyped on a branch, improve determinism / fairness / perf — in particular does it structurally fix the #1157 starvation class?

---

## 0. Executive summary

- **Detcore is priority-turn-FIFO, not min-vtime, by design.** Priority is the *single actuator* that chaos mode, record/replay, and schedule/race search use to reorder threads. Virtual (logical) time in Detcore is a scheduling **output** (one global serialized clock advanced by the scheduler), so using it as the selection **input** is circular. The scoped min-vtime structure that *does* exist — `timed_waiters` — is deliberately limited to deadline events (sleeps/timeouts/alarms), not general thread selection.
- **Only Kendo (ASPLOS'09) is a true "run the lowest logical clock" scheduler.** DMP/CoreDet/DThreads are token/quantum round-robin; dettrace (Hermit's direct lineage) is fair round-robin over run-queues; rr is record/replay (records a schedule, does not deterministically pick one); Determinator sidesteps interleaving with fork/join. So "other deterministic systems use min-vtime" is really "Kendo does"; the dominant family is round-robin/token.
- **The prototype works and is L2-deterministic on lock-based multithreaded guests, with performance identical to the default** — AND it provides structural starvation-freedom for the `sched_yield` class (GH #81) as an algorithmic property. But it **livelocks on `make -jN`**, and a control experiment (§4.2) shows this livelock is a property of min-vtime *selection order against blocking-via-polling*, **independent of how polling turns are charged** — so it is not a tuning artifact that a smarter charging rule can remove. That incompatibility is the concrete modern reason not to switch Detcore's core selection to min-vtime.
- **Min-vtime does NOT fix #1157.** #1157 is an *admission-timing* bug (a SIGCHLD-blocked parent isn't in the run queue to be selected); min-vtime governs *selection ordering among threads already runnable*. They are complementary. The targeted SIGCHLD `timed_waiters` fix remains the near-term lever for #1157.

---

## 1. Why Detcore is priority-turn-FIFO, not min-vtime (design rationale)

Source of rationale: code + comments in `detcore/src/scheduler/runqueue.rs`, `detcore/src/scheduler.rs`, `detcore/src/scheduler/timed_waiters.rs`, and `USER_GUIDE.md`. The initial import was squashed (`c6d05ef2`), so the rationale lives in code/docs, not commit messages.

### 1.1 The run queue is priority-then-turn, collapsing to FIFO in non-chaos mode
`RunQueue` is a `BTreeMap<PrioritizedOrder, QueueValue>` where `PrioritizedOrder { priority: Priority(u64), turn: RoundRobinTurn(i64) }` and `Ord = priority.then(turn)`. In non-chaos mode every thread shares `DEFAULT_PRIORITY = 1000`, so selection is pure FIFO by insertion turn. Priority is the knob; turn is the deterministic tie-break.

### 1.2 Priority is the single actuator for chaos / replay / race-search
- **Chaos mode** perturbs the schedule by *randomizing priority* (and re-randomizing at `sched_yield` and timer-preemption points). A one-dimensional priority key is exactly the minimal, replayable perturbation surface a race search wants: reorder by changing one scalar per thread.
- **Record/replay** records/reissues scheduling decisions through the same priority mechanism (`REPLAY_FOREGROUND_PRIORITY`, `REPLAY_DEFERRED_PRIORITY`).
- If selection were driven by accumulated virtual time, the search/replay engine would have to perturb *time*, which also drives timeouts, virtual clocks read by the guest, and epoch scheduling — entangling the perturbation with observable guest state.

### 1.3 Virtual time is a scheduling OUTPUT, so it is circular as an INPUT
Detcore runs a **single global logical clock** (`committed_time`), serialized to one logical CPU — fundamentally unlike Kendo/CoreDet per-thread parallel clocks. The clock is *advanced by the scheduler* on committed turns (`bump_global_time` → `add_scheduler_time()`, a fixed tick). Feeding that output back in as the primary selection key is circular; a min-vtime Detcore therefore has to synthesize a *separate* per-thread accumulator (a CFS-style `vruntime`), which is what the prototype does.

### 1.4 A scoped min-vtime heap already exists — `timed_waiters`
`timed_waiters` is a real min-vtime min-heap (`BTreeMap<LogicalTime, BTreeSet<TimedEvent>>`, `pop_if_before(current_time)`), but scoped to **deadline events** (nanosleep/timeouts/alarms). Woken deadline threads are pushed to the *front* of the priority queue. So Detcore already uses min-vtime exactly where time is a genuine input (deadlines) and avoids it where time would be circular (general selection).

---

## 2. Prior-art survey (condensed; full survey with verbatim quotes + PDF URLs archived with the task)

| System | Venue | Family | "Next thread" rule | Time unit |
|---|---|---|---|---|
| **Kendo** | ASPLOS'09 | **MIN-VTIME** (canonical) | thread whose deterministic clock is the unique global minimum; tie-break by thread ID | retired **stores** (perf counter) |
| **DMP** | ASPLOS'09 | token + quantum | round-robin deterministic token; block until you hold it | fixed instruction-count quantum |
| **CoreDet** | ASPLOS'10 | token + quantum (SW DMP) | round-robin token; parallel then serial commit | instruction-count quantum |
| **DThreads** | SOSP'11 | token + parallel/serial phases | single global token passed in fixed thread order | synchronization ops (not insns) |
| **Determinator** | OSDI'10 | structured fork/join | no interleaving; deterministic copy-in/out at join | N/A (space, not time) |
| **dettrace** | ASPLOS'20 | **fair round-robin** over run-queues | iterate fairly over Runnable/Blocked/Parallel | count of `time()` calls (for time, not scheduling) |
| **rr** | ATC'17 | record/replay | serialize on 1 core, record the schedule, replay it exactly | retired conditional branches ("ticks") |

Key points for this decision:
- **"Other systems use min-vtime" ≈ "Kendo does."** The dominant deterministic-scheduler family is token/quantum round-robin; a priority-turn-FIFO queue is a close relative of that family, not an outlier.
- **dettrace is Hermit's direct lineage (same authors)** and is fair round-robin, *not* min-vtime. Critically, it makes blocking deterministic by **rewriting blocking syscalls into non-blocking polls** (`wait4` → `WNOHANG`, retry at the back of a queue). Hermit inherits this **blocking-via-polling** model — which §4 shows is what breaks min-vtime.
- **Kendo's own paper documents the exact pitfall the prototype hit** (Fig. 3): naive min-vtime *deadlocks* under nested locks unless "a thread increments its logical clock as it spins on a contested lock." I.e. a waiting thread must advance its clock or it stays the minimum forever. Kendo also notes the raw turn rule is *unfair* (low thread-ID always wins ties) and needs a per-lock FCFS queue. Both caveats reproduced in the prototype.
- **rr does not deterministically schedule** — it records nondeterminism and replays it, giving replay-determinism, not run-to-run determinism. Its chaos mode randomizes the *recorder*; Detcore's chaos perturbs the *deterministic schedule* directly (via priority), which composes better with "localize a race to an event + stack".

---

## 3. The prototype

`SchedHeuristic::MinVtime` (opt-in via `--sched-heuristic=minvtime`; default path untouched). Three files:
- `detcore-model/src/config.rs` — new enum variant + FromStr (`minvtime`/`minvruntime`/`cfs`) + Display.
- `detcore/src/scheduler/runqueue.rs` — match-exhaustiveness for the new variant (safe fallbacks; MinVtime selects at the scheduler level).
- `detcore/src/scheduler.rs` — a `vruntime: BTreeMap<DetTid, LogicalTime>` accumulator; charge one fixed deterministic tick per committed **non-polling** turn in `step6_reenquue`; select via `min_vtime_pick` (argmin vruntime, tie-break DetTid) in `step3_peek`; vfork barriers still take precedence.

**Design choices that matter for determinism:**
- vruntime is charged a **fixed unit per committed turn**, NOT the raw `committed_time` delta. The delta absorbs host-timing-perturbed IO-polling retry advances (deliberately excluded from DETLOG), which would leak nondeterminism into selection.
- **Polling turns are excluded from charging** (the `is_polling` guard). Their *count* is host-timing nondeterministic; charging them would make selection host-dependent. (The `minvtime-allturns` control in §4.2 charges them anyway, to test whether this exclusion is what causes the `make` livelock — it is not.)
- **Newcomer freeze:** a thread's vruntime is snapshotted to the current minimum **exactly once** at first sighting. This was essential — see §3.1.

**Control variant `minvtime-allturns`** (`--sched-heuristic=minvtime-allturns`): identical argmin-vruntime selection, but charges *every* committed turn including `InternalIOPolling` retries. It exists only to bracket the determinism-vs-liveness tradeoff (§4.2). It is expected to be nondeterministic (poll counts are host-timed) and is not a landing candidate.

### 3.1 Two livelocks found and one fixed (the instructive part)
The first two prototype builds **livelocked** on both `sched_yield_progress` and `make`. Root causes, both matching prior-art warnings:

1. **Newcomer tie-loss (fixed).** A not-yet-run thread was re-evaluated as `base` (current min) on every selection, and `base` tracks the *running* thread's ever-growing vruntime — so the newcomer stayed perpetually tied with the runner and lost the DetTid tie-break forever. Trace evidence: on `sched_yield_progress`, dtid 3 was committed **286,230 times and the worker never ran once**. Fix: persist a newcomer's vruntime at the min *once* (CFS new-task placement); the runner's clock then climbs past the frozen newcomer, which becomes the strict minimum and is scheduled. This is Kendo's "advance the waiter's clock" lesson in a different guise.
2. **Poller domination (fundamental, NOT fixed — see §4.2).** Under `make -jN`, selection collapses onto the pipe-polling threads and starves the work threads. §4.2's control experiment shows this is caused by min-vtime *selection*, not by the polling-charge policy.

---

## 4. Measurements (ptrace backend, `--strict --verify` = L2, default log level, no relaxations)

Binary: `study/min-vtime-scheduler-prototype` release build. Host: devbig (this workspace). Repro: `ignored/makedet-repro/` (small multi-target C Makefile). Raw logs: `ignored/minvtime-results/`.

### 4.1 Determinism + perf on lock/CPU-bound multithreaded guests

| Guest | Default (None/FIFO) | MinVtime | Notes |
|---|---|---|---|
| `mt_perf` (4 CPU threads + shared mutex) | **5/5 CLEAN**, avg 0.070s | **5/5 CLEAN**, avg 0.070s | identical determinism + perf |
| `printf_with_threads` | **5/5 CLEAN**, avg 0.048s | **5/5 CLEAN**, avg 0.045s | identical |
| `sched_yield_progress` (GH #81 starvation) | **5/5 CLEAN**, avg 0.049s | **5/5 CLEAN**, avg 0.051s | min-vtime **structurally** fixes the starvation class |

On well-behaved threads, min-vtime is **L2-deterministic (5/5) with no measurable perf cost**, and it fixes the `sched_yield` starvation *by construction*: the spinning main thread accrues vruntime, the frozen worker becomes the minimum, and is scheduled — no chaos-reprioritization special-case needed.

### 4.2 The `make -j8` livelock and the control experiment (heavy polling-based parallelism)

| `make -j8 --strict --verify` | Default (None/FIFO) | MinVtime (exclude polling) | MinVtimeAllTurns (charge polling) |
|---|---|---|---|
| Outcome | full 2-run verify in **4.25s** (CLEAN this run; historical baseline ~5/6 CLEAN, residual channel #4) | **rc=124, timed out 300s+, never finished Run1** (≥70×; livelock) | **rc=124, timed out (330s, 5:30 wall, 0% CPU, 11 voluntary ctx-switches), never finished Run1** — also a livelock |

The `minvtime-allturns` control was added precisely to test the earlier hypothesis that *excluding polling turns* is what causes the livelock (a poller's frozen clock stays the global minimum and monopolizes selection). If that were the whole story, charging polling turns should restore liveness. **It does not** — the build still livelocks. The failure mode only *shifts*:

| Trace (scheduler debug, one build attempt) | MinVtime | MinVtimeAllTurns |
|---|---|---|
| Top committer | dtid 3 = **254,860** turns | dtid 3 = **107,874** turns |
| Runner-up | dtid 5 = **2** turns | dtid 9 = **107,776** turns |
| Polling share of committed turns | 254,824 of dtid 3's turns are polling non-commits | **215,558 / 216,808 = 99.4%** are `InternalIOPolling` retries |
| Work threads (cc1/as children) | starved | starved to ~**105** turns each |

So under plain `minvtime` **one** poller monopolizes (frozen at the minimum); under `minvtime-allturns` **two** pipe-pollers alternate *fairly* (nearly equal counts, because now they are charged) — but they simply ping-pong on the jobserver pipe while the compiler work threads that would supply the awaited tokens are starved. In both cases ~all committed turns are polling retries and the build makes no forward progress.

**Corrected mechanism:** the livelock is a property of **min-vtime selection order against Detcore's blocking-via-polling model**, *independent of the polling-charge policy*. Detcore expresses a blocked read/wait as a host-timed poll loop (inherited from dettrace). Min-vtime keeps re-selecting whichever runnable threads have the least accumulated vruntime; in a jobserver producer/consumer, those are the cheap-to-run pipe-pollers, so selection concentrates on them and rarely reaches the work threads whose completion would let a poll succeed. Charging polling changes *which* pollers win and how the turns divide, but not the fact that selection stays trapped among pollers.

This also means the pure determinism-vs-liveness tradeoff I set out to demonstrate could **not** be exhibited on `make`: `minvtime-allturns` is nondeterministic *in principle* (poll counts are host-timed), but it livelocks before a `--verify` second run can even begin, so I have **no direct divergence witness** for it — I report the livelock, not a divergence. (On the small guests in §4.1, `minvtime-allturns` is 3/3 CLEAN, because their poll-retry counts happen to be stable run-to-run; the nondeterminism only bites under genuinely contended, host-timed polling like the jobserver.)

The Kendo Fig. 3 lesson still frames the root tension: min-vtime needs every waiting thread to advance its clock, but a Detcore poll-waiter can only advance its clock via host-timed retries — advancing it (allturns) reintroduces nondeterminism, not advancing it (minvtime) freezes it at the minimum. Neither branch yields deterministic *and* live scheduling of polling-based blocking.

### 4.3 Fairness / starvation summary
- **Structural starvation-freedom for the `sched_yield` / GH #81 class — as an algorithmic property, not (yet) an empirically differentiated win.** By construction, a spinning thread accrues vruntime while a passed-over runnable thread's frozen clock becomes the minimum and must be scheduled. *Caveat, stated honestly:* in the §4.1 measurements both Default and MinVtime pass `sched_yield_progress` 5/5 in non-chaos `--strict --verify`, so these runs do **not** empirically separate the two — the #81 starvation manifests under `--chaos` (randomized priority), and min-vtime is deliberately **not wired into chaos** in this prototype. The claim here is the algorithmic guarantee plus non-chaos determinism/perf parity; a head-to-head starvation win would require wiring min-vtime into (or alongside) the chaos actuator, which §1.2 argues against on other grounds.
- **Does NOT fix #1157.** #1157 is *admission* starvation: a SIGCHLD-blocked parent is not in the run queue at all, so no selection policy can pick it. Min-vtime is complementary to, not a replacement for, the SIGCHLD `timed_waiters` admission fix.
- **Introduces a NEW starvation mode that no charging policy removes:** min-vtime selection concentrates on pipe-pollers and starves the work threads that would unblock them (§4.2), shown for both the exclude-polling and charge-polling variants. For a syscall-heavy, polling-based runtime this is worse than the problem it solves.

---

## 5. Recommendation

**Do not adopt min-vtime as Detcore's core selection policy.** The priority-turn-FIFO design is well-matched to Detcore's actual constraints: a single global clock (time is an output), priority as the sole chaos/replay/search actuator, and blocking-via-polling. Min-vtime's one real advantage — structural starvation-freedom for runnable-but-passed-over threads — is worth capturing, but its incompatibility with polling-based blocking is disqualifying for a general switch.

Directions worth discussing with the owner (all additive, none requiring a core switch):
1. **Keep `--sched-heuristic=minvtime` as a research/diagnostic heuristic** for lock-based workloads and for exercising the fairness property in tests. It is deterministic and cheap there.
2. **Borrow the anti-starvation property narrowly**: a bounded priority-aging term on the existing FIFO turn (age a long-un-run runnable thread's effective priority) would give the `sched_yield`-class starvation-freedom *without* touching the polling model or the chaos actuator. The §4.2 control clarifies *why* this is safe where min-vtime is not: aging is a bounded perturbation layered **on top of** the FIFO turn re-queuing, which advances a poller's position on *every* turn (polls included) and so preserves poller liveness; min-vtime instead *replaces* selection with an accumulated-progress key that structurally cannot advance on host-timed polls. Aging keeps FIFO's poller liveness and adds only a starvation backstop. This is the minimal, in-model way to get min-vtime's benefit and is the recommended next prototype if the owner wants to pursue the anti-starvation win.
3. **If a poller-safe min-vtime is ever wanted**, it requires first replacing blocking-via-polling with a deterministic readiness/wakeup model (so waiters need not spin and need not advance their clocks) — a much larger effort touching Reverie's blocking semantics, out of scope here.

---

## 6. Reproduction

```bash
# On branch study/min-vtime-scheduler-prototype @ d2dc977e in the makedet slot:
cargo build --release --bin hermit          # NOTE: build WITHOUT with-proxy (BPFJailer blocks cc1)
H=./target/release/hermit
# Lock-based guest (deterministic + fast under both heuristics):
$H run --strict --verify --sched-heuristic=minvtime          -- <mt_perf|sched_yield_progress>
$H run --strict --verify --sched-heuristic=minvtime-allturns -- <mt_perf|sched_yield_progress>   # also CLEAN here
# make -j8 (livelocks under BOTH minvtime variants, completes under default):
$H run --strict --verify [--sched-heuristic=minvtime|--sched-heuristic=minvtime-allturns] -- /bin/sh -c \
  'cd ignored/makedet-repro && make clean >/dev/null 2>&1 && make -j8 >/dev/null 2>&1'
# Failure-mode trace (per-dtid committed turns; look for InternalIOPolling share):
RUST_LOG=detcore::scheduler=debug $H run --strict --sched-heuristic=minvtime-allturns -- ... 2>trace.err
grep -oE '^ COMMIT turn [0-9]+, dettid [0-9]+' trace.err | grep -oE 'dettid [0-9]+' | sort | uniq -c | sort -rn
```
Guests + raw logs: `ignored/minvtime-results/` (mt_perf.c, mtp, syp, pwt, make-trace.err, make-allturns-verify.log, *-allturns-*.log, *-run*.err). Traces are multi-hundred-MB; do not commit them (they are under the gitignored `ignored/`).
