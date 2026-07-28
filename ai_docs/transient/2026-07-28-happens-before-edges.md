# Design Pitch: Custom Happens-Before Edges for Deterministic Race Construction

**Status:** design pitch for owner review — *design only, no implementation.*
**Date:** 2026-07-28
**Author:** impl agent (opus-4.8), task `design-custom-happens-before-edges`
**Grounding:** `hermit/detcore/src/scheduler.rs`, `.../scheduler/{runqueue,replayer,timed_waiters}.rs`,
`hermit/detcore/src/preemptions.rs`, `hermit/detcore-model/src/schedule.rs`,
`hermit/detcore-model/src/config.rs`, `hermit/detcore/src/tool_global.rs`,
`hermit/reverie/reverie-ptrace/src/{perf.rs,gdbstub/breakpoint.rs}`.

---

## 1. Motivation

Today, to reproduce a concurrency bug under Hermit you either (a) already possess
a recorded schedule and replay it, or (b) *search* the schedule space —
`hermit analyze` (`hermit-cli/src/bin/hermit/analyze/mod.rs`: "A mode for
analyzing a hermit run to detect concurrency bugs") drives repeated runs under
`--chaos` with different `--sched-seed` values, perturbing thread priorities
until a divergence appears. That is *blind seed-fuzzing*: it hunts for an
interleaving without being told what the interleaving is.

The owner's vision inverts this. For a *post-facto repro* we frequently already
know the interleaving diagram — "to hit the bad condition, thread A must run its
buffer-free before thread B's use-after-free read." That knowledge is a set of
**happens-before relations** over named dynamic events. We want to inject those
relations directly into Hermit's scheduler and thereby **construct** the race
deterministically, rather than stumble onto it via random seeds.

Concretely the capability is:

> Name dynamic events precisely — *"the 342nd invocation of function `X` on
> thread T1"* — and place ordering constraints — *"`X_342` happens-before
> `Y_97`"* — that the scheduler is guaranteed to honor, so the target race is
> witnessed on the very next run.

This document proposes how to name such events, how the existing deterministic
scheduler enforces the edges, the user-facing API/DSL, the instrumentation
needed to count invocations, the interaction with `--strict`, the risks, an
incremental implementation plan, and future MCP-server exposure.

The good news, established below: **Hermit already has every primitive this
needs.** It has a global deterministic scheduler with per-thread priorities and
turn-granting gates, a serializable event vocabulary, record/replay of exact
schedules, and — critically — the ability to stop a thread at a precise dynamic
instruction (PMU-RCB counting + single-step, plus hardware breakpoints). The
feature is largely a matter of *composing and exposing* existing machinery, not
building a new engine.

---

## 2. Background: What the Scheduler Already Provides

### 2.1 One CPU, deterministic turn-granting

Under `--sequentialize-threads` (the default; required for `--strict`, DBI, and
record/replay — `detcore-model/src/config.rs:158`, `hermit-cli/src/lib.rs:1117`)
Detcore serializes all guest threads onto one logical CPU and picks the next
runnable thread deterministically. A guest thread does not run freely; at each
scheduling point it issues a **resource request** and *awaits a turn*:

- `tool_global::resource_request` (`detcore/src/tool_global.rs:1611`) is the
  choke point where a thread asks to proceed and blocks until granted.
- The grant is delivered through an `Ivar<SchedResponse>` — a single-assignment
  future the scheduler fills to release the thread
  (`scheduler.rs`: `SchedResponse::Go(Option<SchedValue>)` at line 104;
  `unblock_guest` at `scheduler.rs:2247`).

This "thread parks until the global scheduler hands it a turn" structure is the
single most important fact for this design: **an ordering edge is just a
condition on when that turn is granted.**

### 2.2 Priorities decide who runs next

The runnable set is a priority queue (`scheduler/runqueue.rs`):

- `pub type Priority = u64` — "Lowest runs first" (`runqueue.rs:63`).
  `FIRST_PRIORITY = 1`, `DEFAULT_PRIORITY = 1000`, `LAST_PRIORITY = 10000`
  (`runqueue.rs:68,81,71`).
- The queue is a `BTreeMap<PrioritizedOrder, QueueValue>` (`runqueue.rs:159`),
  so selection is a deterministic min-priority pick with round-robin tie-breaks.
- **Chaos mode** replaces priorities with seeded-random ones
  (`entropy_to_priority`, `runqueue.rs:91`; `prng: Pcg64Mcg`, `runqueue.rs:180`).
  This is exactly the "blind" knob we are replacing with explicit edges.
- **Replay** uses reserved priorities (`REPLAY_FOREGROUND_PRIORITY`,
  `REPLAY_DEFERRED_PRIORITY`, `runqueue.rs:74,77`) to force a recorded order.

Priorities are therefore an existing, first-class, *deterministic* lever for
"run A before B." But priorities alone are *soft* (they only matter when both
threads are runnable). Hard ordering also needs the *gate* below.

### 2.3 Blocking and waking are already first-class

The scheduler already parks and wakes threads on synchronization events:

- `wake_futex_waiter` / `wake_futex_waiters` (`scheduler.rs:1197,1248`),
  `wake_futex_child_cleartid` (`1294`), `wake_timed_event` (`1452`).
- Timed/blocked waiters live in `scheduler/timed_waiters.rs`.

A happens-before edge `X → Y` is structurally identical to a futex: **Y's thread
blocks on a synthetic condition that X's occurrence signals.** We do not need a
new blocking mechanism; we need a new *reason* to block and a new *event* to
signal the wake.

### 2.4 A serializable event vocabulary already exists

`detcore-model/src/schedule.rs` defines the atom the whole feature can be built
on:

```rust
pub struct SchedEvent {
    pub dettid: DetTid,               // which thread
    pub op: Op,                       // what happened
    pub count: u32,                   // run-length-encoded repeat count
    pub start_rip: Option<InstructionPointer>,
    pub end_rip: Option<InstructionPointer>,
    pub end_time: Option<LogicalTime>,
}
pub enum Op {
    Branch,                           // one retired conditional branch (RCB)
    Rdtsc, Cpuid,
    Syscall(Sysno, SyscallPhase),     // Prehook | Polling | Posthook
    OtherInstructions,
    SignalReceived(SigWrapper),
}
```

Every observable event already carries **(thread, operation, instruction
pointer, logical time)**. These are precisely the coordinates we need to *name*
an event.

### 2.5 Record/replay of exact schedules already exists

`detcore/src/preemptions.rs` defines `PreemptionRecord` — a serializable list of
`SchedEvent`s plus a per-thread `(LogicalTime, Priority)` history — with
`write_to_disk`, `validate`, and `normalize` (`preemptions.rs:174,194,227`).
Driven by config flags (`detcore-model/src/config.rs`):

- `record_preemptions` / `record_preemptions_to` (`config.rs:205,209`)
- `replay_schedule_from` (`config.rs:223`), `replay_exhausted_panic` (`228`)
- `sched_seed` (`366`), `chaos` (`186`), `chaos_target_races` (`200`)
- `stop_after_turn` / `stop_after_iter` (`387,392`) — bounded execution
- `preemption_stacktrace[_log_file]` (`253,258`) — provenance of a preemption

The replayer (`scheduler/replayer.rs`) consumes a recorded event stream and
forces the same interleaving. **A recorded schedule is a *total* order.
Happens-before edges are a *partial* order** — strictly more general and easier
for a human/agent to author, because you specify only the few edges that matter
and let the scheduler pick a valid completion for everything else.

### 2.6 Precise dynamic-instruction addressing already exists

The hardest-sounding requirement — "stop exactly at the 342nd instance" — is
already solved for preemption placement:

- **PMU retired-conditional-branch (RCB) counting** via `perf_event_open`
  (`reverie/reverie-ptrace/src/perf.rs`), with `PERF_EVENT_IOC_PERIOD/REFRESH/
  RESET` to arm a counter to fire after N branches, then Detcore single-steps to
  the exact instruction (`detcore/src/lib.rs:220-245` handles RCB overshoot and
  computes `interrupt_rcbs`). This is how deterministic preemptions land on an
  exact dynamic instruction today.
- **Hardware breakpoints** (`reverie/reverie-ptrace/src/perf.rs` — "Hardware
  breakpoints"; `reverie/reverie-ptrace/src/gdbstub/breakpoint.rs`,
  `Breakpoint`/`BreakpointType`). Hermit can set a breakpoint at an arbitrary
  RIP (e.g., a function entry) and trap every time control reaches it.

So "reach and stop at a specific dynamic event on a specific thread" is a
shipped, battle-tested capability. The new work is *counting* occurrences and
*gating* on them, not *reaching* them.

---

## 3. Naming Dynamic Events

An edge is a relation between two *named event instances*. Naming must be:
(a) deterministic (the same name refers to the same instant on every run under
the same inputs), (b) authorable by a human or agent from a repro diagram, and
(c) resolvable by the runtime to a concrete stop-point. We propose a **layered
addressing scheme**, from zero-instrumentation to fully general, so early phases
ship value before the hardest piece lands.

An event name is a tuple:

```
Event ::= (Thread, Anchor, Ordinal)
```

- **Thread** — a `DetTid`. Because thread creation is deterministic under
  sequentialization, `DetTid`s are stable across runs. For ergonomics we also
  support symbolic thread labels resolved at first-fork (see §5).
- **Anchor** — *what kind* of event, one of the addressing modes below.
- **Ordinal** — *which occurrence*: the Nth time this (Thread, Anchor) has
  occurred, 1-based. This is the "invocation count" — the `_342` in `X_342`.

### 3.1 Addressing modes (in increasing power / cost)

1. **Syscall-anchored** — `(T, syscall=write, n=342)`: the 342nd `write` syscall
   on thread T, optionally qualified by phase (Prehook/Posthook). *Zero new
   instrumentation:* syscalls are already `SchedEvent`s with a count. This is the
   MVP anchor and already covers a large class of real races (I/O, locking via
   futex, mmap, file ops).

2. **RIP-anchored (instruction)** — `(T, rip=0x…, n=97)`: the 97th time thread T
   is about to execute the instruction at a given RIP. Resolved with a hardware
   breakpoint at that RIP (§2.6) plus a per-thread counter. This addresses
   *any* instruction, including a lock acquisition or a specific load/store, not
   just syscalls.

3. **Function-invocation-anchored** — `(T, func="X", n=342)`: the 342nd *entry*
   to function `X`. This is the owner's `X_342` notation. It is RIP-anchoring
   where the RIP is the symbol's entry address (resolved from the binary's
   symbol table / DWARF, or an address supplied directly). Counting is per-thread
   at the entry breakpoint (§6).

4. **RCB-anchored (logical clock)** — `(T, rcb=123456)`: thread T at its
   123,456th retired conditional branch. This is Hermit's finest-grain
   deterministic per-thread clock and is exactly what preemption records use. It
   is the fallback that can name *any* point, including inside branch-free code
   regions, at the cost of being less human-legible than a function name.

5. **Marker-anchored (cooperative)** — `(T, mark="phase2", n=1)`: a point where
   the guest itself calls a Hermit marker hypercall (§6.3). Most precise and
   most legible, but requires touching guest source. Ideal for instrumented
   test harnesses.

All five modes reduce to the same internal representation: *a predicate over the
event stream + a per-(thread,anchor) occurrence counter*, resolved to a concrete
"trap here on the Nth hit" stop-point.

### 3.2 Why ordinals are deterministic

Under `--sequentialize-threads`, a given thread's instruction/syscall/branch
sequence up to any scheduling point is a deterministic function of program input
and the *scheduling decisions already made*. Provided the anchors we count
(syscalls, RCBs, RIP hits) are on the deterministic path, the Nth occurrence is
well-defined and repeatable — the same property that lets record/replay work at
all (§2.5). Non-determinism only enters through *inter-thread* ordering, which is
exactly what edges pin down. (See §7 for the subtlety when an ordinal depends on
an ordering the edges themselves change.)

---

## 4. How the Scheduler Enforces Edges

### 4.1 The gate model

An edge `A → B` ("A happens-before B") compiles to a **gate**: thread(B) may not
be granted the turn that would execute event B until event A has been observed.

This maps directly onto existing machinery (§2.1, §2.3):

1. **Arm anchors.** Before the run, for every event named by any edge, install
   its detector: a syscall-count watch (mode 1), a hardware breakpoint + counter
   (modes 2/3), an RCB target (mode 4), or a marker hook (mode 5).
2. **Fire on source.** When source event `A` is observed, mark it *satisfied* in
   a global `EdgeState` table and wake any threads parked on gates whose
   preconditions are now all met (reuse the `wake_*` path, `scheduler.rs:1197+`).
3. **Block on sink.** When thread(B) reaches the stop-point for `B` and B has an
   inbound edge whose source is not yet satisfied, the scheduler withholds B's
   `SchedResponse` `Ivar` — i.e., B parks in a new `BlockedOnEdge` reason —
   instead of granting the turn (`resource_request` / `unblock_guest`,
   `tool_global.rs:1611`, `scheduler.rs:2247`).
4. **Deterministic completion.** All *other* threads and all *unconstrained*
   choices continue to be scheduled by the normal deterministic policy
   (priority + round-robin, `runqueue.rs`), so a partial order of a few edges
   still yields a single, reproducible total order.

Because the gate reuses the futex-style block/wake, it inherits Detcore's
existing deadlock detection and logical-time accounting for blocked threads
(`timed_waiters.rs`).

### 4.2 Two enforcement strengths

- **Soft (priority nudge).** Realize `A → B` by keeping thread(B) at
  `LAST_PRIORITY` until A fires, then restoring it. Cheap, no new block state,
  and composes with chaos for "mostly-constrained" search. Weakness: if B's
  thread is the only runnable one, it still runs — the order is a *preference*,
  not a guarantee.
- **Hard (true gate).** Park thread(B) in `BlockedOnEdge` regardless of runnable
  set; only A's firing releases it. This is the guarantee the owner wants and the
  default for constructed repros. It requires the deadlock/liveness checks in §8.

We propose implementing **hard gates** as the primary semantics, with soft
priority-nudging as an optimization/compatibility mode that layers cleanly on the
existing priority system.

### 4.3 Relationship to record/replay

Edges are complementary to `replay_schedule_from` (§2.5), not a replacement:

- **Replay** = a *complete* total order captured from a prior run. Brittle:
  requires having already produced the interleaving.
- **Edges** = a *sparse* partial order authored from knowledge of the bug.
  Robust: specify only what matters; the scheduler fills the rest deterministically.

An attractive hybrid: replay a known-good schedule *and* impose a few extra edges
to perturb it toward a suspected race — "same as last run, but force `X_342`
before `Y_97`." The `PreemptionRecord` format (§2.5) already carries per-thread
priority histories, giving a natural place to splice edge-derived priorities.

---

## 5. API / DSL for Specifying Edges

### 5.1 File format (declarative, primary surface)

A JSON/TOML edges file, passed via a new flag
`hermit run --happens-before edges.json -- <prog>` (sibling to
`--replay-schedule-from`). Example:

```json
{
  "version": 1,
  "threads": { "writer": {"label": "writer"}, "reader": {"label": "reader"} },
  "events": {
    "X_342": {"thread": "writer", "func": "free_buffer", "nth": 342},
    "Y_97":  {"thread": "reader", "func": "read_buffer", "nth": 97},
    "lockA":  {"thread": "writer", "syscall": "futex", "phase": "posthook", "nth": 5},
    "storeB": {"thread": "reader", "rip": "0x401f3c", "nth": 1}
  },
  "edges": [
    {"before": "X_342", "after": "Y_97", "strength": "hard"},
    {"before": "lockA", "after": "storeB"}
  ]
}
```

- **`events`** is a name table (each entry an §3 addressing tuple). Naming events
  separately from edges lets one event participate in several edges and keeps the
  edge list readable.
- **`edges`** is the partial order; each edge optionally sets `strength`
  (`hard` default / `soft`).
- **`threads`** maps symbolic labels to a resolution rule (spawn ordinal, entry
  symbol, or explicit `DetTid`) so authors need not know raw `DetTid`s.

### 5.2 Terse DSL (ergonomic sugar)

For humans and CLI one-liners, a line-oriented sugar that desugars to the file
format:

```
writer:free_buffer#342  <  reader:read_buffer#97
writer:futex@post#5     <  reader:@0x401f3c#1
```

`<` = happens-before. `thread:anchor#ordinal`, `@rip` for raw addresses,
`syscall@phase`. This is the surface most natural to expose to an agent.

### 5.3 Discovery: you must be able to *list* events before you can name them

Authoring edges requires knowing what events exist and their ordinals. We expose
a companion read-only mode that reuses `record_preemptions` (§2.5):

```
hermit run --list-events[=filter] -- <prog>   # dumps SchedEvents as named tuples
```

This prints the `SchedEvent` stream already recorded today, rendered in the §3
naming scheme (thread, anchor, running ordinal). The workflow becomes:
*run once to enumerate → pick the two events → author the edge → re-run with
`--happens-before`.* This "enumerate then constrain" loop is the core UX and the
natural thing to wrap in MCP (§10).

---

## 6. Instrumentation to Count Invocations

Modes 1 and 4 need **no new instrumentation** (syscalls and RCBs are already
observed and counted). Modes 2, 3, 5 do:

### 6.1 RIP / function-entry counting via hardware breakpoints (modes 2, 3)

- Resolve the function symbol → entry RIP from the ELF symbol table / DWARF, or
  accept a raw address. (A small symbolization helper; Hermit already reads guest
  memory and maps, and the gdbstub path already deals in RIPs.)
- Install a hardware breakpoint at that RIP (`reverie-ptrace` breakpoint API,
  §2.6). On each trap, increment a **per-`DetTid` counter** held in the global
  tool state. When the counter reaches the target ordinal for any armed event,
  emit the internal "event A observed" signal (§4.1 step 2) or engage the gate
  (step 3).
- Cost: hardware breakpoints are limited in number (4 debug registers on x86).
  For more simultaneous anchors than registers, fall back to software
  breakpoints (int3 patching, already used conceptually in the gdbstub) or to
  RCB-anchoring. The design should treat "number of concurrently armed
  RIP anchors" as a documented resource limit, not silently drop anchors.

### 6.2 Counting must be per-thread and determinism-safe

The counter is keyed by `DetTid` so "342nd invocation *on thread T1*" is exact.
Counters live in the global scheduler state (single-threaded logical CPU), so
increments are already serialized and deterministic — no cross-thread counter
races.

### 6.3 Cooperative markers (mode 5)

A guest-visible no-op hypercall — e.g., a reserved `prctl`/`madvise` subcode or a
dedicated ioctl on a `/dev/hermit` control fd — that Detcore intercepts as a
named marker event. Precise and legible, requires editing guest source; ideal for
test harnesses and for the QEMU/kernel repro workflows where we already control
the guest. This reuses the existing syscall-interception path; the marker just
becomes another `Op` variant (or a recognized `Syscall` subcase) in the event
stream.

### 6.4 Reuse, don't reinvent

Every counting mechanism above feeds the *same* `SchedEvent`/`EdgeState`
pipeline. Instrumentation differences are confined to "how do we detect event
occurrence"; the naming, gating, and enforcement are shared.

---

## 7. Interaction With `--strict`

`--strict` fail-closes on any nondeterminism (memory: strict fail-closes
post-#644). Edges must *preserve* that guarantee, and in fact *strengthen* it:

- **Edges are a deterministic function of a deterministic execution.** Under
  `--sequentialize-threads` (mandatory for `--strict`), the scheduler is already
  deterministic. Adding gates only *removes* schedules from the feasible set; it
  never introduces a nondeterministic choice. So `--strict --verify` should
  remain L2 (bitwise-identical repeat) with edges applied.
- **Edges compose with `--verify`.** The two-run verify (memory: verify runs the
  guest twice) should produce identical logs *including* the edge-constrained
  interleaving. This is a strong self-check: if applying an edge set makes a
  previously-L2 program fail `--verify`, that is a real bug in the edge
  machinery, not benign.
- **Ordinal/edge circular-dependency subtlety.** If an event's *ordinal* depends
  on an ordering that an edge itself changes (e.g., counting interleaved shared
  events across threads), the name could shift between the enumerate run and the
  constrained run. Mitigation: (a) ordinals are **per-thread** (§3.2), so a
  thread-local count is invariant to inter-thread ordering in the common case;
  (b) `--list-events` is re-run *under the same edge set* to confirm names are
  stable (a fixpoint check); (c) `--strict` flags any drift as nondeterminism.
- **Unsatisfiable edges must fail closed.** If `X_342` never occurs (X is called
  only 300 times) or a cycle makes an edge unsatisfiable, the run must abort with
  a precise diagnostic under `--strict`, never hang silently or silently drop the
  edge. This mirrors `replay_exhausted_panic` (`config.rs:228`).

Net: edges are *inside* the determinism envelope. They are best used *with*
`--strict`, and `--strict --verify` becomes the oracle that the constructed
schedule is exactly reproducible.

---

## 8. Feasibility and Risks

**Feasibility: high.** Every primitive exists — deterministic scheduler with
turn-gates (§2.1), per-thread priorities (§2.2), block/wake (§2.3), serializable
event vocabulary (§2.4), record/replay (§2.5), and precise dynamic-instruction
stop via PMU-RCB + hardware breakpoints (§2.6). The feature is composition +
exposure, not a new engine.

**Risks / open questions:**

1. **Deadlock from ill-formed edge sets.** A cycle (`A→B`, `B→A`) or an edge
   whose source can never fire deadlocks the gate. *Mitigation:* static cycle
   detection at load time (topological sort of the edge DAG); runtime liveness
   watchdog that reports "all threads blocked on edges, none satisfiable" using
   the existing blocked-thread bookkeeping (`timed_waiters.rs`); fail-closed
   under `--strict`.
2. **Ordinal instability across runs.** §7 subtlety. *Mitigation:* per-thread
   ordinals, fixpoint `--list-events`, verify oracle.
3. **Hardware-breakpoint scarcity.** Only 4 debug registers. *Mitigation:*
   software breakpoints / RCB-anchoring fallback; document the concurrent-anchor
   limit; never silently drop an anchor.
4. **PMU availability.** RCB anchoring needs working perf counters, which some
   VMs/containers lack (memory: PMU counters unreliable in some environments,
   `perf.rs:259`). *Mitigation:* syscall- and RIP-anchoring (modes 1–3) do not
   need RCBs; degrade gracefully and report the limitation, don't fake it.
5. **Symbolization gaps.** Stripped binaries have no function symbols.
   *Mitigation:* accept raw RIPs (mode 2); optional DWARF/symbol-table lookup as
   a convenience.
6. **Overhead of breakpoint counting.** A breakpoint on a hot function traps on
   every call. *Mitigation:* prefer RCB/syscall anchors for hot paths; scope
   counting to the target thread; document cost.
7. **Multi-process / backend coverage.** Ptrace is the reference backend with the
   richest introspection. DBI/KVM/SaBRe/LiteInst have varying breakpoint/PMU
   support (memory: cross-backend gaps). *Mitigation:* ship ptrace first; treat
   other backends as follow-on, reporting per-backend capability honestly (never
   claim a backend-wide result from a ptrace-only run — per Hermit's Backend
   Definition rules).
8. **Interaction with signals and `OtherInstructions`.** Some events (§2.4) are
   zero-instruction markers or uninterceptable "dark matter." Edges should be
   restricted to *observable* anchors; naming a point inside `OtherInstructions`
   requires RCB/single-step and should be flagged as expensive.

---

## 9. Incremental Implementation Plan

Each phase is independently useful and testable; none requires the next.

- **Phase 0 — Enumerate (read-only, tiny).** Add `--list-events[=filter]` that
  renders the already-recorded `SchedEvent` stream (§2.5) in the §3 naming
  scheme (thread, anchor, per-thread ordinal). No scheduler change. Delivers the
  "what can I name?" half of the loop immediately and validates the naming
  vocabulary against real programs.

- **Phase 1 — Syscall-anchored hard gates (MVP).** Implement `EdgeState` +
  `BlockedOnEdge` block reason + the gate wiring in `resource_request` /
  `unblock_guest`, restricted to **mode-1 (syscall) anchors** — zero new
  instrumentation. Add `--happens-before <file>` (JSON) and cycle detection.
  Validate: author `futex#n before write#m` on a small two-thread test; confirm
  `--strict --verify` stays L2. This proves the core mechanism end-to-end.

- **Phase 2 — RIP / function-invocation anchors (the `X_342` vision).** Add
  hardware-breakpoint arming + per-`DetTid` occurrence counters (§6.1) feeding
  the Phase-1 `EdgeState`. Add symbol→RIP resolution. Add the terse DSL (§5.2).
  Now full function-invocation edges work.

- **Phase 3 — RCB anchors + soft/priority mode + hybrid replay.** RCB-target
  anchoring (mode 4) reusing preemption machinery; soft priority-nudge strength
  (§4.2); splice edges onto a replayed `PreemptionRecord` (§4.3) for
  "replay-plus-perturbation."

- **Phase 4 — Cooperative markers + MCP exposure.** Marker hypercall (§6.3); MCP
  server tools (§10). Optional broader backend coverage.

Testing throughout: every phase adds a small `tests/`-style guest with a known
race, asserts the constructed edge deterministically witnesses (or provably
excludes) the bug, and asserts `--strict --verify` L2 stability of the
constrained schedule.

---

## 10. Future MCP-Server Exposure

The enumerate→constrain→run loop is exactly an agent tool loop, so the natural
end-state is an MCP server wrapping Hermit:

- `list_events(program, args, filter) -> [EventName]` — Phase 0 machinery; lets
  an agent see the namable events and their ordinals.
- `place_edge(before, after, strength) -> EdgeId` / `remove_edge(EdgeId)` —
  build the partial order incrementally.
- `run_with_edges(program, args, edges) -> {witnessed, divergence, schedule,
  logs}` — execute under `--happens-before --strict --verify` and report whether
  the target condition was hit, with the exact resulting schedule.
- `explain_deadlock() -> {cycle | unsatisfiable_event}` — surface §8 diagnostics
  in a machine-actionable form.

This turns "construct a race" into a closed agent loop: an agent reads a bug
report / interleaving diagram, enumerates events, places the handful of edges the
diagram implies, runs, and either witnesses the race (done) or gets a precise
reason it could not (which edge is unsatisfiable, where the cycle is) and
iterates — *without ever resorting to random seed search.* That is the
capability the owner is after: deterministic, directed race construction as a
first-class, agent-drivable operation.

---

## Appendix: Grounding Citations (as of hermit main, 2026-07-28)

| Claim | Location |
| --- | --- |
| Turn-granting choke point | `detcore/src/tool_global.rs:1611` (`resource_request`) |
| Turn delivered via `Ivar<SchedResponse>` | `detcore/src/scheduler.rs:104`, `:2247` (`unblock_guest`) |
| Priority queue, lowest-first | `detcore/src/scheduler/runqueue.rs:63,68,71,81,159` |
| Chaos = seeded-random priorities | `runqueue.rs:91` (`entropy_to_priority`), `:180` (`Pcg64Mcg`) |
| Replay priorities | `runqueue.rs:74,77` |
| Block/wake machinery | `scheduler.rs:1197,1248,1294,1452`; `scheduler/timed_waiters.rs` |
| Event vocabulary | `detcore-model/src/schedule.rs` (`SchedEvent`, `Op`) |
| Serializable schedule record | `detcore/src/preemptions.rs:174,194,227` (`PreemptionRecord`) |
| Scheduler config flags | `detcore-model/src/config.rs:158,186,200,205,209,223,228,366,387,392` |
| PMU RCB counting + precise stop | `reverie/reverie-ptrace/src/perf.rs`; `detcore/src/lib.rs:220-245` |
| Hardware/software breakpoints | `reverie/reverie-ptrace/src/perf.rs`; `.../gdbstub/breakpoint.rs` |
| Blind schedule search (to complement) | `hermit-cli/src/bin/hermit/analyze/mod.rs` |
