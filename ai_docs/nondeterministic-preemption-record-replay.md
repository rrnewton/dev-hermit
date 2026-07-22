# Nondeterministic preemption recording and exact replay

Status: design recommendation, research only

Date: 2026-07-21

## Decision

Hermit should separate the policy used to discover a preemption from the
mechanism used to reproduce it:

1. During `hermit record`, keep Detcore's single-thread run token, but arm an
   imprecise PMU timer. When the interrupt actually stops the tracee, record the
   resulting execution point rather than pretending that the requested RCB was
   reached exactly.
2. Make the recorded execution point the canonical event. At minimum it
   contains stable thread identity, cumulative per-thread RCB count, RIP,
   global schedule sequence, code identity, and a register fingerprint.
3. During replay, run only the recorded thread and locate the recorded
   `(RCB, RIP, registers)` point. Approach it with the PMU, use an internal
   execution breakpoint at RIP, reject false dynamic instances of the same
   RIP, and fail closed on overshoot or state mismatch.

The important semantic change is that a requested timeout and an observed
preemption are no longer conflated. If a timer requested for RCB 500 stops the
guest at RCB 501 and RIP `X`, the recording says `(501, X)`. Replaying at 500
would be a different execution.

This design makes recording fast and removes the precise-timer skid margin
from the recording correctness path. Replay remains the expensive phase. A
correct implementation should follow rr's repeated-breakpoint algorithm, not
simply accept the first occurrence of the recorded RIP after an early PMU
interrupt.

## Scope

### Goals

- Allow fast, imprecise PMU preemption during `hermit record`.
- Reproduce the execution point where each recorded timer interrupt actually
  landed.
- Preserve Detcore's serialized guest-thread model and global scheduling
  order.
- Make corrupt, incompatible, or unreplayable recordings fail explicitly.
- Keep precise replay as a correctness feature even if the recording itself
  was nondeterministic.

### Non-goals for the first version

- Running guest threads concurrently during recording. Only the interrupt
  landing is nondeterministic; one guest thread still owns the run token.
- Cross-architecture replay.
- Portable replay across arbitrary CPU PMUs or changed binaries.
- Complete asynchronous-signal and external-I/O replay in the first milestone.
  Those events need the same global tape eventually, but they are separable
  from validating preemption capture.
- Replacing `hermit run --record-preemptions-to` and its schedule-analysis
  format immediately. That format can be migrated after the record/replay
  execution-point format is proven.

## Current implementation

### `hermit record`

`hermit record start` launches `Detcore<Recorder>`. Its hard-coded Detcore
configuration serializes threads and uses the default preemption timeout. If
perf is unavailable, the timeout is disabled with a warning. The Recorder
itself writes per-thread syscall and nondeterministic-input streams; it does
not write a global scheduler tape.

Record and replay currently share the same Detcore configuration. The
`recordreplay_modes` scheduler path also uses an eager policy for external I/O
because its readiness decisions are not recorded yet.

Relevant code:

- `hermit-cli/src/record.rs`
- `hermit-cli/src/replay.rs`
- `hermit-cli/src/metadata.rs::record_or_replay_config`
- `hermit-cli/src/recorder/mod.rs`
- `hermit-cli/src/replayer/mod.rs`

### Detcore preemption

For each running thread, `ThreadState::next_timeslice` chooses an absolute
logical end time. `post_handler_hook` converts the remaining logical time to
RCBs and asks Reverie for either:

- `set_timer_precise(Rcbs(n))`, the normal path; or
- `set_timer(Rcbs(n))`, when `imprecise_timers` is enabled.

On a timer event, `handle_timer_event` invokes the common pre-hook. The
pre-hook reads Reverie's non-resetting per-thread RCB clock, commits the delta
to thread logical time, and ends the timeslice if its limit was reached.

The existing schedule artifacts are close but not sufficient:

- `PreemptionRecord.per_thread` stores logical end times and priorities.
- `PreemptionRecord.global` stores `SchedEvent`s with branch run lengths and
  optional start/end RIPs.
- Schedule replay uses branch counts to arm timeslices. RIP is used to detect a
  desynchronization, not to position execution.
- The `hermit record` command does not currently enable this writer.

Relevant code:

- `detcore/src/lib.rs::{update_logical_time_rcbs,post_handler_hook,handle_timer_event}`
- `detcore/src/tool_local.rs::ThreadState::next_timeslice`
- `detcore/src/preemptions.rs`
- `detcore/src/scheduler/replayer.rs`
- `detcore-model/src/schedule.rs`

### Reverie precise timers

Reverie has a continuously enabled RCB clock and a second perf counter used to
request timer signals. A precise request programs the signal early by a
CPU-specific skid margin, then ptrace-single-steps the stopped tracee until the
requested RCB/instruction coordinate is reached. An imprecise request only
guarantees that delivery is not early.

This is why precise recording is expensive and why a too-small margin is a
correctness problem: if the signal arrives after the target, a stopped tracee
cannot be moved backward without checkpoint/rollback.

Relevant code:

- `reverie-ptrace/src/timer.rs`
- `reverie-ptrace/src/task.rs::{set_timer,set_timer_precise}`
- `reverie/src/timer.rs::TimerSchedule`

## Lessons from scx-sim and rr

### scx-sim

The inspected scx-sim implementation records, per worker:

- actual signal RIP;
- the PMU count for that slice;
- cumulative RCB count;
- global sequence number;
- scheduler callback context and counts;
- instruction bytes and binary metadata.

Its replay backend uses a hybrid scheme. A PMU signal fires before the
cumulative target, then a hardware execution breakpoint is armed at the
recorded RIP. It also has a breakpoint-only mode and detects PMU overshoot.
This is the correct high-level split between nondeterministic capture and
targeted replay.

Hermit should not copy two simplifying assumptions as correctness rules:

1. A fixed 200-RCB approach margin is not a proven skid bound on every PMU.
2. The first breakpoint hit at the target RIP is not necessarily the recorded
   dynamic instruction. A loop can execute the same RIP many times within the
   approach window.

### rr

rr handles the repeated-RIP case explicitly. Replay first approaches the
recorded tick count. It then installs an internal breakpoint at the recorded
IP. On each hit it checks whether the full recorded execution point has been
reached. A false hit is stepped over with the breakpoint temporarily removed,
then execution continues toward the next occurrence. Overshooting the recorded
tick count is fatal.

That algorithm should be Hermit's correctness reference. RIP is an efficient
search key, while RCB count and register state decide whether a breakpoint hit
is the right dynamic instance.

## Required semantic model

### The canonical coordinate

Define a recorded preemption point as:

```text
ExecutionPoint =
  (global_sequence,
   stable_thread_id,
   thread_rcb,
   instruction_pointer,
   register_fingerprint,
   code_identity)
```

`thread_rcb` is cumulative for one deterministic thread identity from a
defined epoch, preferably the first post-exec stop. It is not the requested
timer delta and not global wall-clock time.

RIP and RCB are both necessary:

- RCB alone identifies the last retired conditional branch, but an imprecise
  signal can land later in a branch-free instruction region.
- RIP alone is repeated by loops, functions called more than once, and shared
  library code.
- `(RCB, RIP)` is the fast coordinate. A register fingerprint proves that the
  state is the recorded dynamic execution point.

The register fingerprint should initially contain all x86-64 general-purpose
registers, RFLAGS, FS/GS base, and the syscall-origin register. Storing a
versioned digest as well as selected fields keeps comparison inexpensive while
allowing useful mismatch diagnostics. SIMD state can be added if testing shows
that it is needed to distinguish otherwise identical control states.

### Stable thread identity

Raw host TIDs are not durable identifiers. Use Detcore's deterministic thread
identity plus pedigree/clone sequence. The trace header must define the root
identity and the event stream must record thread creation before a child can
appear in a preemption event.

### RIP identity

Reverie disables ASLR today, so raw RIP is usually stable within one recording
and replay. The format should still avoid making that an undocumented
requirement. Store:

- raw RIP for diagnostics;
- executable mapping identity;
- offset within that mapping;
- build ID or content digest for file-backed code; and
- a small byte window at the target instruction.

Replay resolves mapping plus offset and verifies the digest/bytes before
arming a breakpoint. The first version may reject JIT or self-modifying code
when it cannot prove that the target bytes match.

## Recording design

### Configuration

Add an internal mode enum rather than another combination of booleans:

```text
PreemptionMode::PreciseCanonical
PreemptionMode::CaptureActual
PreemptionMode::ReplayRecorded
```

Expose `CaptureActual` as an opt-in record option first, for example
`hermit record start --preemption=recorded`. Record the chosen mode and all
effective parameters in `metadata.json`. Replay takes its mode from the
recording rather than requiring matching command-line flags.

Keep the old mode as the default until replay fidelity and performance gates
pass.

### Timer path

In `CaptureActual` mode:

1. Detcore chooses a nominal timeslice as it does today.
2. `post_handler_hook` calls `set_timer`, not `set_timer_precise`.
3. Reverie resumes the one runnable guest thread. PMU skid and signal latency
   determine the actual stop.
4. Before Detcore mutates scheduling state, read the RCB clock and registers
   from the stopped tracee.
5. Commit the actual RCB delta to logical time and append a preemption event
   containing the canonical execution point.
6. End the timeslice and append the scheduler decision that identifies the
   next runnable thread.

The nominal timeout should also be logged for telemetry, but replay must never
use it as the target.

### Global schedule tape

Do not extend `PreemptionRecord`'s current double-duty JSON indefinitely.
Introduce a versioned record/replay schedule tape in the recording directory,
for example `schedule.v1.jsonl` or a length-delimited binary equivalent.

The header should include:

- schema version and endianness;
- Hermit/Reverie build identifiers;
- architecture;
- resolved PMU type, raw event, precise-IP setting, CPU vendor/family/model,
  and pinned CPU/core type;
- executable and shared-object identities;
- counter epoch rules; and
- effective scheduler and preemption configuration.

The event enum should cover at least:

```text
ThreadStart { seq, thread, parent, clone_ordinal }
ThreadExit  { seq, thread, status }
Preempt     { seq, thread, requested_delta, point, next_thread }
Yield       { seq, thread, reason, next_thread }
Block       { seq, thread, resource }
Unblock     { seq, thread, reason }
Signal      { seq, thread, signal, point }
```

The initial milestone can emit only the events needed by supported test
workloads, but unknown or missing event classes must make replay fail closed.
The trace needs one global sequence even if per-thread target queues are built
at load time.

### Atomicity and finalization

Write a temporary tape, include checksums and an explicit complete footer, then
rename it into the recording directory only after Recorder and Detcore both
finish successfully. A truncated tape must never be accepted as a shorter
valid schedule.

## Replay design

### Ownership boundary

Execution-point location belongs in Reverie, not Detcore. Reverie owns ptrace
stops, perf counters, signal suppression, register access, and breakpoint
step-over. Detcore should request a recorded target and receive the ordinary
timer callback only after Reverie has reached it.

Prefer an API such as:

```text
Guest::set_execution_point(RecordedExecutionPoint)
```

or a new `TimerSchedule::ExecutionPoint` variant. Do not expose raw breakpoint
mutation to Detcore.

### Correctness-first locator

The first implementation should reuse the existing precise RCB timer as an
oracle:

1. Compute the remaining per-thread RCBs from current committed time to the
   recorded cumulative target.
2. Use the existing precise timer machinery to stop exactly at that RCB.
3. Arm an internal execution breakpoint at the recorded RIP and continue.
4. At every breakpoint hit, compare current RCB and the register fingerprint.
   Normalize tracer artifacts first, including software-breakpoint RIP
   adjustment and any tracer-owned Trap Flag.
5. If the hit is before the recorded point, remove/disable the breakpoint,
   single-step one instruction, re-arm it, and continue.
6. If it matches, suppress the internal trap and emit the Reverie timer event
   to Detcore without executing the target instruction.
7. If RCB is greater than the target, code bytes differ, or state cannot match,
   report a structured replay desynchronization and stop.

Starting with the existing precise counter minimizes new correctness surface.
It already preserves the core benefit: recording no longer single-steps.

### Optimized locator

After the baseline passes, replace the exact-RCB approach phase with a hybrid
similar to scx-sim and rr:

1. Program an imprecise PMU interrupt before `target_rcb` by a validated guard.
2. On the approach stop, read the clock. Overshoot is fatal for that replay
   attempt.
3. Arm the target RIP breakpoint and resume with the clock still counting.
4. Reject and step over early dynamic RIP instances until the full execution
   point matches.

The guard must come from the resolved PMU configuration and measured/certified
skid behavior. A fixed universal margin is not a correctness boundary. Since
there is no rollback inside one replay, an overshoot requires restarting from
the beginning with a larger guard or selecting the correctness-first engine.

### Breakpoint implementation

Use a backend-neutral internal-breakpoint abstraction:

- A per-thread perf hardware execution breakpoint is fast, invisible to guest
  code, and matches the scx-sim prototype. It consumes scarce debug resources
  and may be unavailable in VMs or under restrictive perf policy.
- A tracer-managed software breakpoint reuses machinery already present for
  GDB and works without a hardware debug register. It must preserve original
  instruction bytes, adjust RIP after `int3`, serialize shared-address-space
  step-over, and coexist with user breakpoints.
- Single-stepping is the fail-safe fallback for unsupported writable/shared
  mappings, but not the normal path.

Hermit's one-thread run token makes software-breakpoint step-over much simpler
than a native concurrent debugger, but signal delivery and debugger use still
need explicit arbitration.

### Scheduler integration

Replay loads the global tape and per-thread target queues before the root
exec. The scheduler grants the run token only to the thread named by the next
global event. A recorded `Preempt` target is armed whenever that thread resumes.

If a syscall, signal, block, or thread exit occurs before the target, it must
match the next tape event. Reverie's existing timer cancellation on observable
events must cancel only the active approach attempt, not consume the recorded
target; the remaining target is re-armed on resume.

After Reverie reports a matched preemption point, Detcore:

1. commits the observed clock to the same cumulative RCB;
2. validates stable thread identity and event sequence;
3. ends the timeslice; and
4. switches to the recorded next thread.

Replay succeeds only when the syscall streams and the global schedule tape are
both exhausted exactly.

## Fail-closed checks

Replay must stop on any of these conditions:

- wrong architecture, PMU event semantics, or unsupported CPU/core type;
- executable mapping or instruction-byte mismatch;
- missing or ambiguous deterministic thread identity;
- non-monotonic per-thread RCB target;
- unexpected syscall, signal, block, exit, or runnable-thread choice;
- PMU approach overshoot;
- target RIP hit with RCB greater than target;
- register fingerprint mismatch at an otherwise matching `(RCB, RIP)`;
- tape checksum failure, truncation, or unconsumed events; or
- inability to install a required breakpoint without an enabled safe fallback.

Warnings and "closest event" behavior are unsuitable here. A replay that
continues after missing the recorded point is not reproducing the recording.

## Alternatives considered

### Replay only the requested RCB

Rejected. The actual recording may have executed more branches and different
instructions before the imprecise signal arrived.

### Replay only the actual RCB

Incomplete. The signal may have landed in a branch-free region after the last
counted branch. Exact RCB replay would stop earlier than the recorded RIP.

### Accept the first target RIP after an early PMU stop

Rejected as a correctness rule. A hot loop may hit the target RIP at the wrong
RCB. Every breakpoint hit needs count/state validation and false-hit step-over.

### Record instructions-since-last-RCB

Potentially sufficient with `RcbsAndInstructions`, but cheaply obtaining a
reliable instruction count during recording requires another portable precise
counter or instruction instrumentation. That erodes the fast-recording goal.
RIP plus breakpoint search derives the same position during replay instead.

### Single-step replay from every preceding observable event

Correct but too slow. Retain it only as a diagnostic fallback for short
unsupported regions.

### Make recording natively multithreaded

Out of scope and unsafe for this milestone. It would require recording memory
races and weak-memory behavior, not just scheduling decisions. Keep Detcore's
serialized-thread model.

## Performance expectations

Recording removes the ptrace single-step tail from every precise preemption.
Existing local chaos measurements found a synthetic 10-million-RCB search was
about 12 times faster with imprecise timers than precise timers, while exposing
the same five failing seeds. That is evidence for the direction, not a general
speedup claim for `hermit record`.

Replay cost becomes workload-dependent:

- one PMU approach stop plus one breakpoint hit is the fast case;
- repeated target RIPs add breakpoint and step-over stops;
- AMD's larger skid guard increases the search window;
- breakpoint-only replay can be pathological for a very hot RIP; and
- the correctness-first precise-RCB locator retains today's replay cost but
  still gives fast recording.

Measure record wall time, replay wall time, ptrace stop counts, false
breakpoint hits, single steps, overshoots, and trace bytes per preemption.

## Implementation plan

### Phase 0: format and invariants

- Add a versioned execution-point and schedule-tape model with round-trip and
  corruption tests.
- Add resolved PMU/CPU/code identity to recording metadata.
- Define the counter epoch and deterministic thread identity contract.

### Phase 1: imprecise capture, precise-RCB replay oracle

- Add `CaptureActual` to record configuration.
- Capture actual clock, RIP, code bytes, and registers at the timer stop.
- Add a Reverie execution-point request that first uses the existing precise
  RCB machinery, then searches for RIP.
- Keep the feature opt-in and restrict initial tests to no external async
  signals and deterministic file/network inputs.

### Phase 2: global scheduling fidelity

- Record and force thread starts, exits, yields, blocks, and scheduler choices.
- Integrate external-I/O readiness and asynchronous-signal ordering.
- Remove the record/replay scheduler's eager-I/O workaround only after those
  events are replayed from the tape.

### Phase 3: optimized hybrid replay

- Add the early PMU approach plus internal-breakpoint engine.
- Use per-PMU validated guards and structured overshoot restart/fallback.
- Add hardware breakpoint acceleration while retaining software breakpoint or
  precise-RCB fallback.

### Phase 4: consolidation

- Compare the new tape with `--record-preemptions-to` and schedule-analysis
  needs.
- Migrate shared data types only after compatibility and minimization tools can
  consume the new schema.
- Consider making captured preemption the default for `hermit record` after
  replay gates are consistently green.

## Validation plan

### Unit tests

- Trace round trip, checksum, truncation, and version rejection.
- Stable thread identity through clone/exec.
- Monotonic per-thread RCB and global sequence validation.
- RIP relocation and code-byte mismatch.
- Replayer rejection of wrong next thread and unconsumed events.

### Reverie tests

- A loop that reaches the same RIP many times before the target RCB. Verify
  false hits are stepped over and only the recorded dynamic instance fires.
- A straight-line branch-free region after the target RCB. Verify replay stops
  at recorded RIP, not merely at the branch count.
- Target RIP equal to current RIP, requiring one step before re-arming.
- PMU approach overshoot and larger-guard/precise fallback.
- Software and hardware breakpoint coexistence with guest SIGTRAP and GDB.
- Writable/JIT code mismatch fails closed.

### Hermit integration tests

- Record a racy multi-thread guest with imprecise timers, then replay it many
  times and compare output, syscall stream, schedule tape, and exit status.
- Verify two recordings may have different landing points while each replay is
  stable.
- Exercise preemption across syscalls, clone, exec, blocking I/O, and thread
  exit.
- Corrupt RCB, RIP, thread ID, code bytes, and next-thread fields separately;
  each must fail with a targeted diagnostic.
- Run on the rootful self-hosted PMU lane and archive CPU/PMU metadata.

### Acceptance gates

- Zero silent resynchronization in record/replay mode.
- At least 100 successful replays for each stress fixture at the exact recorded
  execution points.
- No accepted PMU overshoot.
- Demonstrated record-time improvement on PMU-heavy workloads.
- Both existing precise record/replay and new captured-preemption modes remain
  covered until migration is complete.

## Open questions

1. Should the trace store full registers or a digest plus a diagnostic subset?
2. Is a perf hardware execution breakpoint reliable on every supported CI
   host, or should software breakpoint be the baseline?
3. What is the supported policy for JIT/self-modifying code in version 1?
4. Should replay restart automatically with a larger guard after overshoot, or
   immediately select the precise-RCB engine?
5. Which asynchronous signals must be supported before the option can leave
   experimental status?
6. Can the new schedule tape replace both per-thread preemption histories and
   global `SchedEvent`s without breaking analyzer/minimizer workflows?

## Source baseline

Local source revisions inspected:

- Hermit `origin/main` `3f3c31c45b1d6a750b716bc3efd96efb2a575e76`
  (the inspected feature worktree differed only in unrelated no-namespace CLI
  files)
- Reverie `96693397ed60aa07c59ffeed4df3deed89b183e2`
- scx-sim `9e6194f0ae9250395d765c9b8fa1d335c7410c15`

External reference:

- scx-sim preemption recording and replay backend at the inspected commit:
  <https://github.com/rrnewton/sched-test/tree/9e6194f0ae9250395d765c9b8fa1d335c7410c15/scx-sim>
- rr `ReplaySession.cc` at
  `39e5c18e7e43236b7ca0fb1eb647fe9c93e3934e`, especially the two-stage
  `advance_to` logic that approaches a tick target, repeatedly breaks on the
  recorded IP, validates the execution point, and steps over false hits:
  <https://github.com/rr-debugger/rr/blob/39e5c18e7e43236b7ca0fb1eb647fe9c93e3934e/src/ReplaySession.cc>

Related local research:

- `ai_docs/intel-pmu-analysis.md`
- `ai_docs/chaos-effectiveness.md`
- `ai_docs/sabre-determinism-analysis.md`
