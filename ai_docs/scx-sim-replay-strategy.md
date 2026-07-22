# scx-sim Replay Strategy and Applicability to Hermit

Date: 2026-07-21

## Scope

This note studies the replay implementation in scx-sim revision
`d64779a5902a778afd8cd60694c3f236f62702ec`, with emphasis on exact
preemption placement, e9patch, and the contrast with Hermit's Reverie precise
timer. The Hermit comparison uses the checked-out Hermit and Reverie sources;
it is an architectural comparison, not a performance benchmark.

The most important finding is that three mechanisms must be kept distinct:

1. scx-sim's normal replay uses a PMU approach timer followed by a hardware
   execute breakpoint at the recorded RIP.
2. scx-sim's `--no-pmu-signal` replay arms the hardware breakpoint
   immediately, accepts or rejects each dynamic execution of that RIP by
   reading the branch count, and re-arms until the recorded instance is
   reached.
3. scx-sim's e9patch replay does not implement that breakpoint loop. Its
   branch mode counts instrumented conditional branches in process; its RIP
   mode patches recorded addresses and yields at the first execution of the
   currently armed address.

That distinction matters for both correctness and any proposed Hermit port.

## What the trace identifies

A `PreemptionRecord` contains more than a program counter:

- the event-period count and cumulative per-worker `structop_rbc`;
- the absolute instruction pointer;
- worker, CPU, sequence, struct-ops, and kfunc context;
- five instruction bytes for binary-mismatch detection.

The serialized trace also carries the PMU event type, scenario parameters, a
scheduler shared-object hash, and a scheduler base-relative RIP offset. On
replay, scx-sim reconstructs addresses for the new mapping and rejects an
unexpected scheduler binary.

The cumulative branch count distinguishes dynamic executions of the same RIP.
The RIP identifies the exact static instruction. Instruction bytes and
struct-op context detect several forms of divergence. These coordinates are
why the hardware-breakpoint-only path can stop at a frequently executed
instruction without accepting the wrong occurrence.

## Hardware replay

### PMU approach plus RIP breakpoint

The default `ReplayBackend` keeps a passive PMU counter and a hardware
execute breakpoint for each worker:

1. It programs the PMU to interrupt approximately
   `target.structop_rbc - REPLAY_MARGIN`. The margin is 200 events.
2. The PMU handler reads the counter and aborts on overshoot.
3. The handler disables the PMU and arms the target RIP as an execute
   breakpoint.
4. The breakpoint handler validates the recorded context and instruction
   bytes, yields to the engine, advances the trace cursor, and arms the next
   target.

Targets too close to the current counter bypass the PMU approach interrupt
and arm the breakpoint directly. In the ordinary PMU path the branch counter
is frozen near the target, so the breakpoint handler deliberately does not
compare it with the final count.

This is a two-stage run-forward strategy: hardware counting gets near the
point, and a static instruction address catches the final location. It does
not single-step the instruction stream.

### Breakpoint-only RIP plus branch-count matching

With `--no-pmu-signal`, scx-sim removes PMU overflow delivery but still
creates and reads a PMU counter. It directly arms the target RIP:

1. Every dynamic execution of the target instruction raises `SIGTRAP`.
2. The handler disables the breakpoint and reads the cumulative counter.
3. If `current_rbc < target.structop_rbc`, it re-arms the same RIP and
   resumes without advancing the trace.
4. When the count reaches the target, it validates context and instruction
   bytes, yields, advances, and arms the next target.

This is the requested "RIP breakpoint plus branch-count matching" loop. Its
cost is proportional to how often the chosen static instruction executes
before the desired dynamic instance, not to the number of all intervening
instructions. It still depends on a readable compatible PMU event and incurs
a signal/context-switch cycle for every rejected hit.

## e9patch replay

scx-sim uses e9patch only on the scheduler shared object. It is not rewriting
the simulator, arbitrary processes, or a changing set of executable mappings.
The build compiles small C trampolines with `e9compile.sh`, then invokes
`e9tool` to create instrumented shared objects.

### Software branch-count mode

The standard `_e9.so` instruments every x86 conditional branch matched by
`-M jcc`, including Jcc, LOOP-family, and JCXZ-family instructions. Before
each original branch, `rbc_trampoline`:

1. reads state at a fixed shared virtual address;
2. decrements a 64-bit counter;
3. returns on the fast path while the counter is positive;
4. calls `e9_replay_yield` when the armed counter expires;
5. installs the delta returned for the next recorded target.

The replay backend converts cumulative trace counts to deltas. There is no
PMU interrupt, skid margin, breakpoint retry, or ptrace stop between replay
points. The engine handoff is an in-process Rust call followed by the
simulator's futex/token protocol.

This mode is exact relative to the instrumented branch stream, assuming the
rewrite preserves the relevant control flow and the recorded and replayed
scheduler binary are identical. Its steady cost is paid at every conditional
branch, whether or not a replay event is nearby.

### Prepatched RIP mode

For instruction-retired traces, scx-sim constructs an `_e9rip.so` at replay
time:

- it deduplicates recorded target RIPs;
- converts them to scheduler-object-relative addresses;
- instruments all Jcc sites with the branch trampoline;
- additionally instruments each target address with
  `rip_trampoline(addr)`;
- composes both trampolines when a target itself is a Jcc.

At runtime a fixed shared page contains one `armed_rip`. Each patched target
does a cheap compare and returns unless it is the current target. A match
calls `e9_replay_yield`, which advances the trace cursor and arms the next
target. The branch counter is set to `i64::MAX` and marked unarmed, so it
does not select the dynamic instance in RIP mode.

This leads to a material correctness limitation: current e9patch RIP replay
accepts the first execution of the armed static address. It does not compare
the live branch count with `target.structop_rbc`; instead, after a hit it
sets its accumulated value to the recorded target count. A loop or repeated
call that reaches the same patched address before the recorded occurrence can
therefore be ambiguous. The hardware breakpoint-only path has the dynamic
instance check that the e9patch RIP path lacks.

Any Hermit design based on e9patch RIP targets should match at least
`(module identity, module-relative RIP, RCB, instruction offset)` and treat
an early RIP hit as a rejected occurrence rather than advancing the trace.

## Hermit's precise timer

Detcore expresses remaining deterministic time as RCBs and normally calls
`Guest::set_timer_precise(TimerSchedule::Rcbs(...))`. Reverie's ptrace
backend then provides exact delivery:

1. A PMU overflow gets control near the target.
2. Reverie disables the timer and reads the counter.
3. It tracks a coordinate `(target_rcb, target_instr)`, where the
   instruction offset disambiguates progress after the final counted branch.
4. While behind the coordinate, it issues `ptrace::step`
   (`PTRACE_SINGLESTEP`), waits for the resulting `SIGTRAP`, and reads the
   counter again.

The configured skid bound is roughly 100-125 RCBs for listed Intel CPUs and
10,000 RCBs for listed AMD Zen families. The latter can make the worst-case
single-step tail particularly expensive.

This really is instruction-by-instruction ptrace stepping. Each step includes
tracee stop/resume and wait handling, unlike scx-sim's hardware-breakpoint
run-forward path and unlike e9patch's in-process fast path.

Hermit's scope is also much broader. Reverie follows arbitrary guest
executables and libraries through processes, threads, syscalls, signals, and
exec. scx-sim controls one known scheduler shared object and its calling
environment. e9patch only addresses exact preemption delivery; it does not
replace Hermit's syscall, signal, filesystem, or record/replay machinery.

## Cost comparison

| Mechanism | Work between targets | Expensive transitions | PMU required | Binary rewriting |
| --- | --- | --- | --- | --- |
| Hermit/Reverie precise timer | Native execution, then instruction steps inside skid window | One ptrace stop/resume per final instruction | Yes | No |
| scx-sim PMU + breakpoint | Native execution to PMU approach point, then native execution to target RIP | PMU signal plus one breakpoint signal | Yes | No |
| scx-sim breakpoint-only | Native execution between executions of target RIP | One breakpoint signal per candidate occurrence | Counter read only; no overflow signal | No |
| e9patch branch mode | Counter decrement and branch at every Jcc | In-process callback/futex handoff only at target | No | All Jcc in scheduler object |
| e9patch RIP mode | Compare at each execution of a recorded static target | In-process callback/futex handoff on armed RIP | No | Recorded RIPs, plus current all-Jcc pass |

No trustworthy "Nx faster" result is available from this checkout. The source
tree contains qualitative claims but no benchmark comparing e9patch with
Hermit/Reverie precise timers. No numeric speedup should be inferred from the
qualitative design.

The source-level cost model predicts:

- e9patch should win when the precise-timer single-step tail is long or when a
  breakpoint target executes many instructions apart;
- hardware RIP breakpoints should usually beat per-instruction ptrace
  stepping when the target RIP is not extremely hot;
- e9patch branch mode can lose when preemptions are sparse but the guest
  executes many conditional branches, because instrumentation is always on;
- the relative result depends strongly on CPU skid, target-RIP frequency,
  branch density, and replay-point density.

A defensible speedup claim requires a benchmark recording wall time, retired
instructions/branches, ptrace single-step count, breakpoint-hit count, and
e9 trampoline calls on the same workloads and trace.

## Applicability to Hermit

### What transfers cleanly

The strongest near-term idea is the hardware breakpoint assist, not wholesale
e9patch adoption. Hermit already records deterministic progress and Reverie
already owns ptrace and PMU delivery. A prototype could record a target
coordinate containing:

- executable mapping identity or build ID;
- mapping-relative RIP;
- cumulative RCB and instruction offset;
- instruction bytes for mismatch detection.

On replay, Reverie could arm one execute breakpoint for the next target, read
progress at each hit, reject early occurrences, and fall back to the existing
precise timer on divergence or unsupported mappings. One next-target
breakpoint fits the small x86 debug-register budget.

This directly tests whether native run-forward removes enough
`PTRACE_SINGLESTEP` traffic to matter without changing guest code.

### Why general e9patch adoption is harder

scx-sim benefits from constraints Hermit does not have:

- one known x86-64 scheduler shared object;
- a stable scheduler hash and base-relative relocation model;
- control over build, load order, and replay setup;
- fixed shared virtual addresses for trampoline state;
- no need to rewrite arbitrary executables, all DSOs, JIT code, or later
  `exec` mappings.

For Hermit, rewriting guest code can change binary hashes, mappings, unwind
and debugger behavior, self-inspection, instruction addresses, and timing. It
must coexist with PIE/ASLR, `dlopen`, `exec`, JIT/W^X policy, signals,
CET/IBT, and user breakpoints. The trampoline and its control flow must not
pollute the guest-visible branch counter or deterministic state. A fixed
`MAP_FIXED` control page also creates address-collision and compatibility
risk.

Instrumentation could therefore be an explicit, narrow optimization for
immutable, allowlisted x86-64 ELF mappings, not Hermit's default execution
model.

## Recommended experiment sequence

1. Add measurement to the existing precise timer: single steps per event,
   PMU skid, elapsed replay time, and fallback reasons. Establish Intel and
   AMD baselines before changing delivery.
2. Prototype one-next-target hardware execute breakpoints in Reverie. Match
   the full dynamic coordinate, reject early RIP hits, and retain
   `PTRACE_SINGLESTEP` as the correctness fallback.
3. Benchmark native, precise-timer, and breakpoint-assisted replay across
   hot loops, recursion, shared-library code, multithreaded programs,
   `exec`, and signals.
4. Only if breakpoint assistance leaves a measured bottleneck, prototype
   e9patch for one immutable module behind an opt-in flag. Cache rewritten
   artifacts by exact build ID/hash and keep traces module-relative.
5. Before expanding coverage, test repeated target RIPs, Jcc-at-target,
   dlopen/exec, self-checking code, unwinding, debugger breakpoints, CET/IBT,
   and trampoline exclusion from RCB accounting.

The adoption criterion should be a measured replay-speed improvement without
changing output or the accepted `(RCB, instruction-offset, RIP)` sequence.
If a target cannot be resolved or validated, the existing precise-timer path
must remain authoritative.

## Source map

scx-sim:

- `crates/scx_simulator/src/unsafe_impl/backend/replay.rs`: replay backend
  setup, target construction, and PMU versus breakpoint-only arming.
- `crates/scx_simulator/src/unsafe_impl/preempt/mod.rs`: PMU and breakpoint
  handlers, repeated RIP/RBC matching, and `e9_replay_yield`.
- `crates/scx_simulator/src/unsafe_impl/backend/e9patch.rs`: branch and RIP
  replay modes, e9tool command construction, and runtime patched-object
  creation.
- `crates/scx_simulator/src/unsafe_impl/preempt/trace.rs`: trace metadata,
  serialization, ASLR-relative addresses, and replay grouping.
- `csrc/e9_rbc_trampoline.c`, `csrc/e9_rip_trampoline.c`, and
  `csrc/sim_rbc_trampoline.c`: injected fast paths and fixed-address shared
  state.
- `schedulers/Makefile`: e9compile/e9tool build pipeline.
- `crates/scx_simulator/src/bin/scxsim/main.rs`: replay-mode selection,
  address relocation, object generation, and loading.

Hermit/Reverie:

- `detcore/src/lib.rs`: Detcore logical-time accounting and precise timer
  requests.
- `reverie-ptrace/src/timer.rs`: PMU configuration, skid bounds,
  `ClockCounter`, and exact single-step loop.
- `safeptrace/src/lib.rs`: `Stopped::step` mapping to
  `ptrace::step`.

Two scx-sim AI notes predate the current source implementation and describe
e9patch replay as absent or branch-only. The current code and commits
`92bbeb5`, `0cc78ee`, and `025877a` supersede those statements.
