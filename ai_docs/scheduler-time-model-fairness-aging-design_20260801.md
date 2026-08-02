# Detcore fairness/aging design: bounded service lead, not min-vtime

Date: 2026-08-01

Task: `scheduler-time-model-fairness-design`

Status: design for owner review; no scheduler code changed

## Decision in one paragraph

Keep Detcore's priority-then-FIFO run queue and continuous guest-visible logical
time. Add a **separate, deterministic fairness-service counter** per runnable
thread and make threads temporarily ineligible when their service gets more
than a bounded amount ahead of the least-served runnable thread. Selection
inside the eligible band remains the existing priority/FIFO policy. Every
completed scheduling opportunity costs at least one service unit, so a cheap
poll/yield loop consumes its budget and self-deprioritizes without the scheduler
ever recognizing it as a poller. Do not select the global minimum counter: that
was already prototyped and livelocked. Fairness only covers runnable threads;
it cannot run a thread that is absent from the run queue because it is blocked.

## Stop sign: the failed Kendo-style branch

**Recover this before proposing another min-vtime design.**

- Local Hermit branch: `study/min-vtime-scheduler-prototype`
- Final local SHA: `d2dc977e9e71c70c0da79562962b0a516b2bb233`
- Durable, immutable writeup: [min-vtime scheduler study at parent commit
  `babd90ae`](https://github.com/rrnewton/dev-hermit/blob/babd90aeeb517682bee90ad5b8585d68b02b70b8/ai_docs/min-vtime-scheduler-study.md)
- Publication status: the remote branch was deleted and GitHub's commit API no
  longer resolves the SHA. The writeup above is the public evidence link; do
  not publish a dead commit URL.

### TLDR: why it failed

The branch replaced priority/FIFO selection with Kendo-style
`argmin(per_thread_vruntime)` and ran the decisive `make -j8` control both ways:

| Charging rule | Observed failure |
| --- | --- |
| Exclude polling turns | One waiter stayed at minimum vtime: **254,860 turns**, runner-up **2**. |
| Charge every polling turn | Two pipe waiters alternated almost perfectly: **107,874 vs 107,776**, but **99.4%** of all turns were `InternalIOPolling`; productive `cc1` threads received only about **105** turns each. |

Both variants timed out after 300 seconds while default priority/FIFO completed
in 4.25 seconds. Charging the pollers changed one monopolist into two fair
monopolists; it did not schedule the producers that would satisfy their waits.

**Conclusion:** wholesale min-vtime is incompatible with Detcore's
blocking-via-polling selection dynamics. The failure is not a missing poll
charge or a tuning problem. The successor must bound how far any runnable task
may lead while preserving a broad FIFO eligibility band; it must not choose the
single least-charged task.

This prior-art warning was also posted to hermit-237's active
`build-debug-episode-cli-and-migrate` task for the debug/lab notebook.

## What Hermit is modeling

### Explicit answer

Hermit is **not modeling a particular Linux scheduler** such as CFS or EEVDF.
Linux still owns the host tasks, but Detcore stops them and deterministically
chooses which one may continue at an intercepted event or RCB preemption
boundary. The default model is a **single logical execution resource** with a
serialized commit order, not `N` continuously running hardware CPUs.

It is also **not currently modeling `N` smooth virtual CPUs plus injected
chaos**. Guest-visible time is continuous and deterministic, but `GlobalTime`
is the starting epoch plus the **sum** of each thread's local work plus
scheduler-generated extra time. That is serialized-work time, not the maximum
elapsed time of `N` parallel CPUs. Chaos injects seeded choices of priority and
RCB timeslice into this abstract machine; it is schedule exploration, not a
simulation of Linux's scheduler or physical CPU latency.

Source anchors:

- The architecture describes cooperative sequentialization with RCB
  preemption, where Detcore chooses one stopped Linux task to continue:
  [ARCHITECTURE.md lines 404-446](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/docs/ARCHITECTURE.md#L404-L446).
- `GlobalTime` explicitly sums the per-thread time vector and `extra_time`:
  [time.rs lines 705-725](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore-model/src/time.rs#L705-L725) and
  [lines 781-808](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore-model/src/time.rs#L781-L808).
- Chaos uses deterministic streams for priorities, RCB timeslices, and queue
  choices: [ARCHITECTURE.md lines 755-790](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/docs/ARCHITECTURE.md#L755-L790).
- `--no-sequentialize-threads` delegates concurrent execution back to Linux and
  explicitly weakens schedule reproducibility:
  [USER_GUIDE.md lines 383-402](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/docs/USER_GUIDE.md#L383-L402).

If the desired normative model changes to `N` deterministic CPUs, that is a
separate DMP/CoreDet-style phase-and-commit design. It needs CPU assignment,
parallel phases, deterministic conflict serialization, and a decision whether
elapsed time is a maximum or another aggregate. A fairness overlay must not
silently make that model change.

## Current mechanism and constraints

The present queue key is `(priority, round_robin_turn)`, lower first. Equal
priority therefore has a concrete `N - 1` runnable-turn bound, but strict
priority can starve lower-priority tasks:

- [runqueue.rs queue contract and polling strategy](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore/src/scheduler/runqueue.rs#L9-L46)
- [priority/FIFO ordering](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore/src/scheduler/runqueue.rs#L62-L116)
- [documented same-priority fairness bound](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/docs/USER_GUIDE.md#L404-L420)

The current exponential poller backoff and periodic promotion explicitly
classify pollers. It is a useful performance heuristic, but cannot be the
correctness argument:
[runqueue.rs lines 275-297](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore/src/scheduler/runqueue.rs#L275-L297).

Guest-visible time must continue to advance on polling retries so finite
timeouts remain finite. Poll retry counts can be host-timing-sensitive, which
is why their advances are normalized out of verification logs:
[scheduler.rs lines 2433-2441](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore/src/scheduler.rs#L2433-L2441) and
[lines 2470-2537](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore/src/scheduler.rs#L2470-L2537).
The fairness currency therefore must not be an alias for `GlobalTime` or
`committed_time`.

Finally, queue selection is transactional: a tentative choice may be committed
or undone. Fairness state may change only with the corresponding committed
scheduling event:
[runqueue.rs lines 408-558](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore/src/scheduler/runqueue.rs#L408-L558).

## Literature survey and transfer

| Work | Relevant mechanism | What Detcore should borrow | What it must not copy |
| --- | --- | --- | --- |
| Linux EEVDF | Runnable tasks have lag; only nonnegative-lag tasks are eligible, then earliest virtual deadline wins. Sleeping lag decays. [Pinned Linux documentation](https://github.com/torvalds/linux/blob/02dc699f83d04069fdabc996fc22d47cda47a4a9/Documentation/scheduler/sched-eevdf.rst#L13-L32). | Separate service accounting from wall time; eligibility as a fairness gate; cap/decay sleeper credit. | EEVDF is Linux SMP proportional-share scheduling, not a deterministic execution model. Earliest-deadline replacement selection would discard Detcore's priority/chaos actuator. |
| Linux `sched_ext` and virtual-time DSQs | A DSQ can be FIFO or vtime-ordered. [Pinned scheduler documentation](https://github.com/torvalds/linux/blob/02dc699f83d04069fdabc996fc22d47cda47a4a9/Documentation/scheduler/sched-ext.rst#L244-L250). `scx_simple` charges used slice/weight and clamps idle credit to one slice. [Pinned example](https://github.com/sched-ext/scx-c-examples/blob/82c692afe32ed4e79fd047a93d3ff316bf399287/scheds/c/scx_simple.bpf.c#L69-L129). | A service counter and bounded wakeup credit are useful data structures. | `sched_ext` is an experimentation API, not a fairness proof. `scx_simple` explicitly tolerates a racy multicore global vtime; Detcore cannot. A vtime-priority queue is not itself a suitable policy. |
| EEVDF original | Stoica and Abdel-Wahab, *Earliest Eligible Virtual Deadline First* (1995). [Paper linked by Linux](https://citeseerx.ist.psu.edu/document?repid=rep1&type=pdf&doi=805acf7726282721504c8f00575d91ebfd750564). | Bound service lead with eligibility before using a secondary policy. | Real-time deadline semantics are not required for Hermit's fairness floor. |
| Kendo | Olszewski, Ansel, and Amarasinghe, *Kendo* (ASPLOS 2009), [DOI](https://doi.org/10.1145/1508244.1508256). It orders synchronization by per-thread deterministic clocks and must advance a spinner's clock. | Independent deterministic service clocks and explicit handling of waiting credit. | Global `argmin(clock)`. The recovered Hermit branch reproduces its waiter/tie pitfalls and fails `make -j8`. |
| DMP | Devietti et al., *DMP* (ASPLOS 2009), [DOI](https://doi.org/10.1145/1508244.1508255). | Finite quanta and deterministic token handoff provide a clearer bounded-service argument than minimum-vtime selection. | Its parallel hardware model cannot be imported without defining Detcore's `N`-CPU semantics. |
| CoreDet | Bergan et al., *CoreDet* (ASPLOS 2010), [DOI](https://doi.org/10.1145/1736020.1736029). | Parallel/serial phases and deterministic commit are the right reference if Hermit later adopts `N` logical CPUs. | It is not a small run-queue fairness patch. |
| DThreads | Liu, Curtsinger, and Berger, *Dthreads* (SOSP 2011), [DOI](https://doi.org/10.1145/2043556.2043587). | Fixed-order token/phase designs show that deterministic multithreading generally uses bounded quanta rather than Kendo-style minimum clocks. | Process isolation and synchronization phases do not map directly onto today's syscall-interception queue. |
| dettrace | Hermit's direct lineage uses fair round-robin and converts blocking operations to nonblocking polls. [Repository](https://github.com/dettrace/dettrace). | Preserve FIFO rotation and make fairness robust to polling-based blocking. | Do not assume a poll classification can serve as the liveness proof. |

The common transferable idea is **eligibility plus bounded service**, not a
virtual-deadline queue by itself. EEVDF supplies lag/eligibility and sleeper
credit discipline; deterministic-multithreading systems supply finite quanta
and fixed commit order; Detcore retains priority/FIFO as the secondary policy.

## Requirements

1. **Runnable fairness:** a continuously runnable thread gets a scheduling
   opportunity after a deterministic finite number of other runnable turns,
   even across priority levels.
2. **Poller-agnostic correctness:** the proof may not inspect `ResourceID`,
   syscall number, readiness, progress, or a `poll_attempt` label.
3. **Continuous time:** never freeze, reset, round, or coarsen `DetTime`,
   `GlobalTime`, or `committed_time` to create fairness.
4. **Work conserving:** if any runnable task exists, at least one is eligible.
5. **Determinism:** all state transitions use integer arithmetic and committed
   scheduler events. External readiness remains an explicit record/replay or
   environmental boundary; fairness must not add a new host clock.
6. **Composition:** priority remains chaos/replay's ordering actuator inside the
   fair band. Vfork barriers and exact replay are explicit forced-order cases.
7. **No invented wakeups:** a blocked task stays blocked until a modeled wake,
   timeout, signal, or recorded external completion admits it.

## Candidate designs

Quality scores are 1 (poor) to 5 (strong); risk is 1 (low) to 5 (high).

| Candidate | Injection point | Determinism | Liveness | Compatibility | Risk | Decision |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Bounded wait-age priority boost | Selection: reduce effective numeric priority after `A` passed-over turns. | 5 | 4 | 5 | 2 | Viable minimal prototype, but priority arithmetic and repeated boosts make the service bound less direct. |
| **Bounded service-lead eligibility** | Admission + selection + committed requeue. | 5 | 5 | 5 | 3 | **Recommended.** It preserves the queue order and gives a short proof. |
| Per-epoch budgets | Give each runnable task `B` tokens; defer exhausted tasks until every current runnable task is exhausted. | 5 | 5 | 4 | 3 | Good implementation experiment; epoch rollover and wake/exit membership need careful definitions. |
| EEVDF/virtual-deadline replacement | Replace queue selection with eligible earliest deadline. | 4 | 3 | 1 | 5 | Reject. It replaces the chaos actuator and is too close to the failed minimum-key family. |
| DMP/CoreDet `N`-CPU phases | Admission, parallel execution, deterministic commit. | 4 | 5 | 1 | 5 | Separate architectural project, only if the owner changes the machine model. |

The first three are overlays. They leave the priority/FIFO queue and logical
clock intact. The latter two change the scheduler model and should not be mixed
into the Demo5 experiment.

## Recommended design: bounded service-lead eligibility

### State and ordering

For each thread `i`, maintain an unsigned scheduler-internal counter `S[i]`.
Call it **fair service**, not virtual time, because it is not guest-visible and
does not drive timers.

For the current runnable set `R`:

```text
floor    F = min(S[j] for j in R)
lead     L[i] = S[i] - F
eligible(i) = L[i] < B
next = minimum existing (priority, fifo_turn) among eligible threads
```

`B > 0` is a fixed service-lead budget. A minimum-service task is always
eligible, so the rule is work conserving. This is deliberately a **band**, not
`argmin(S)`: all tasks less than `B` ahead compete under today's queue policy.

This is the decisive difference from the failed branch. If two cheap waiters
alternate, both counters rise. After each has consumed its finite lead, neither
can exclude an eligible producer merely by having a better persistent priority.
If the producer begins more than `B` ahead, the waiters can catch up only until
the producer re-enters the band; they cannot remain the unique minimum winners
forever. Existing FIFO rotation then orders all eligible peers.

### Charging

The correctness version uses only deterministic committed opportunities:

```text
base_cost = 1
step = min(MAX_SHIFT, L[i] / LEAD_STEP)
cost = saturating_shift_left(base_cost, step)
S[i] = saturating_add(S[i], cost)
```

Phase 1 should use `base_cost = 1` and no escalation (`MAX_SHIFT = 0`). That is
enough for the proof and isolates policy from time accounting. A later measured
variant may make `base_cost` the capped delta of the selected thread's local
RCB/syscall-derived `DetTime`, then apply the lead multiplier. It must never use
the raw `GlobalTime`/`committed_time` delta, which includes scheduler extra time
and host-sensitive poll retry counts.

Charge after a completed guest scheduling opportunity and before requeue. A
task moved to a precise blocked pool is no longer runnable and needs no charge;
an undone tentative selection or scheduler-only bookkeeping does not charge.
An immediately retrying poll/yield turn does charge the same base unit as every
other completed turn. Therefore pollers self-deprioritize by behavior, without
classification.

Cost escalation is an optimization, not part of the safety argument. It makes
a task already far ahead burn its remaining lead budget faster. The minimum
one-unit charge and finite `B` provide liveness even with escalation disabled.

### Admission, blocking, and wakeup credit

- New thread: initialize `S[new] = F`, or zero if it is the first runnable
  thread. This is neutral placement, not minimum preference.
- Block: remove the task from `R` but retain its `S` value.
- Wake: let `F` be the current runnable floor and clamp only stale credit:
  `S[i] = max(S[i], F.saturating_sub(WAKE_CREDIT))`, with `WAKE_CREDIT` at most one base
  slice. A short sleep cannot erase over-service debt; a long sleeper cannot
  accumulate unlimited credit.
- Exit: remove its accounting after it can no longer rejoin.
- Empty-to-nonempty transition: retain a monotonic remembered floor so a task
  cannot reset service merely by making the queue empty.

This borrows EEVDF/scx's sleeper-credit discipline without importing their
deadline selection.

### Scheduler injection points

1. **IP1, admission:** initialize or clamp service when clone/start/wake moves a
   thread into the runnable queue.
2. **IP2, selection:** filter `tentative_pop_next` candidates by bounded lead,
   then apply the unchanged heuristic and `(priority, turn)` order.
3. **IP3, preemption/commit:** charge only when the tentative choice becomes a
   completed guest turn. Keep exact replay and vfork forced selections explicit.
4. **IP4, time:** no change. Continuous local/global time remains authoritative
   for clocks, deadlines, and RCB timeslices.
5. **IP5, blocking:** exclude blocked tasks from the runnable floor; preserve
   their accounting for clamped readmission.

The existing completion point is
[`step6_reenquue`](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore/src/scheduler.rs#L2662-L2680).
The selection point and vfork override are
[`step3_peek`](https://github.com/rrnewton/hermit/blob/0da50ed8abae8a06f0b0954d202c3a4bdb42ff76/detcore/src/scheduler.rs#L2053-L2104).

### Runnable liveness argument

Assume a finite runnable set during the interval, `B` finite, and every
completed opportunity costs at least one. First take a continuously runnable
minimum-service thread `x`. While `x` is not selected, its `S[x]` and therefore
the floor do not increase. Every other task can be selected at most `B` more
one-unit turns before reaching the lead limit and becoming ineligible. Strict
priority cannot defeat this gate, so some minimum-service thread is selected
after at most roughly `(N - 1) * B` other completed opportunities.

For an arbitrary continuously runnable thread `y`, any initial service excess
is finite. Repeated application of the minimum-thread bound advances every
lower-service runnable peer until `y` is back inside the eligibility band; the
same finite bound then forces a turn for `y`. A loose bound includes the initial
excess plus `N * B`; an implementation should state and test the exact bound
for its tie and wake rules. New runnable arrivals add a finite `B` term each; an
unbounded arrival stream requires a separately stated population bound.

The guarantee excludes:

- a thread blocked outside `R`;
- a nonpreemptible CPU loop when RCB preemption is disabled;
- an explicit vfork/replay forced-order interval;
- an external event that never arrives.

These exclusions are admission/preemption facts, not reasons to recognize
pollers.

## Demo5: what this can and cannot claim

The latest good-vs-broken trace analysis reports that terminal Demo5 has four
runnable threads `{3,5,11,13}` already cycling fairly, while vCPU `dtid 7` is
absent from the run queue: its seventh untimed futex wait at turn 171,416 is
never woken. See the workspace evidence
[demo5-green-vs-broken-vcpu-condvar-starvation_20260731.md](demo5-green-vs-broken-vcpu-condvar-starvation_20260731.md).

Therefore **runnable fairness cannot repair the terminal state**. It cannot
select `dtid 7` or synthesize the missing condition-variable wake. The proposed
overlay can still fix Demo5 by changing the earlier serialized interleaving so
the seventh wait is never lost, but that is a hypothesis to test, not a liveness
theorem. A Demo5 success claim requires trace evidence that the relevant wake
occurs (or the bad wait is avoided), the vCPU becomes runnable, and guest RCB
progress resumes. Merely showing balanced turn counts among `{3,5,11,13}` is a
failure.

This distinction also prevents poller recognition from becoming a hidden
correctness dependency: blocked admission must be explained by modeled wake
semantics; runnable service must be explained by the bounded-lead invariant.

## Prototype and validation plan

No production default should change in the first patch. Prototype the overlay
behind a research-only scheduler option, with counters sufficient to audit the
proof (`S`, floor, lead, eligibility, selection, wake clamp).

### Model/unit tests

1. Two permanently higher-priority burners plus one low-priority worker: assert
   the worker's maximum wait is bounded by the configured `B` and population.
2. Two alternating cheap waiters plus productive workers, shaped like the
   recovered `make -j8` trace: assert producer turns occur within the bound.
3. Equal-priority tasks: assert current FIFO order is unchanged while all are
   inside the band.
4. Sleep/wake gaming: a task cannot reset over-service debt by sleeping; a long
   sleeper receives at most one slice of credit.
5. Tentative-pop undo: no service charge; commit charges exactly once.
6. Blocked task: excluded from floor and never selected until a modeled wake.
7. Counter overflow/renormalization: use checked wide integers and, before a
   threshold, deterministically clamp stale blocked credit and subtract one
   common minimum from every live internal counter. Preserve ordering and lead;
   never saturate silently and never renormalize guest time.
8. Vfork and replay: forced selection remains exact and accounting cannot make
   a recorded choice fail eligibility.

### Workload gates

1. Re-run the recovered `make -j8 --strict --verify` case. It must complete and
   must show bounded producer wait; this is the anti-deja-vu gate.
2. Run `sched_yield_progress`, the existing four-thread fairness test, bounded
   buffer, `RwLock` writer completion, and CPU-only RCB-preemption cases.
3. Run Demo5 repeatedly with the exact good/broken trace probes. Require vCPU
   wake/readmission and RCB progress, not only boot success.
4. Repeat strict verification across supported backends. Separate failures
   caused by recorded external readiness from failures caused by fairness-state
   divergence.
5. Compare default versus overlay on syscall-heavy, build, and CPU-heavy
   workloads. Record turns, fairness deferrals, wall time, and virtual-time
   deltas. Guest-visible time must remain continuous in both configurations.

### Rejection conditions

Reject or redesign the overlay if any of these occurs:

- it needs `is_polling_turn` or a syscall/resource classifier for liveness;
- `GlobalTime` is frozen, rounded, reset, or used as the primary selection key;
- `make -j8` returns to the two-poller/zero-producer pattern;
- exact replay choices become ineligible without an explicit bypass contract;
- Demo5 boots without an explainable vCPU wake/readmission transition;
- the fairness guarantee disappears under chaos priority differences.

## Owner decisions before implementation

1. Approve **bounded service-lead eligibility** as the primary prototype, with
   per-epoch budgets retained only as a comparison implementation.
2. Choose the first `B` experimentally; do not encode a latency promise until
   traces establish the service/overhead curve.
3. Keep phase 1 turn-based. Evaluate local `DetTime`-weighted cost only after the
   turn-only version passes determinism and `make -j8` gates.
4. Decide exact replay semantics: bypass the eligibility gate while replaying
   recorded selections, or record enough fairness state to prove every replayed
   choice eligible. The former is simpler and keeps replay authoritative.
5. Treat any implementation as core Detcore scheduling change, requiring the
   repository's post-facto human-review trigger 4. This document itself changes
   no scheduler code.
