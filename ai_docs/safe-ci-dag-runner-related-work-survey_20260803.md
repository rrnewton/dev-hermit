# Survey: Resource-Aware DAG Scheduling and Resource Containment

_Updated 2026-08-03. Claims about the local prototype are bound to dev-hermit
`c5f57f7`, agent-utils `0eb4203`, and the Hermit gitlink `3e4367e`._

The Hermit project is about to invest substantially in `safe-ci-dag-runner`:
using it as the common execution layer for CI, validation, experiments, and
benchmarks. The project has surveyed deterministic Linux execution, virtual
time, and syscall-interception systems, but not resource-aware build/test
scheduling. This survey fills that gap and asks one blunt question: **which
mature systems already implement the scheduling, learning, and
resource-control algorithms that Hermit needs?**

Two different problems are in scope:

1. **DAG scheduling:** choose which dependency-ready step runs next, subject to
   finite CPU, memory, and named resources.
2. **Resource containment:** keep trusted but potentially faulty project code
   from running forever, leaking all memory, or creating an unbounded number of
   processes.

Resource containment is not a security sandbox. The code being run is trusted;
only its resource use is not. This survey therefore does not treat filesystem,
network, namespace, or syscall isolation as requirements. Tools such as nsjail,
bubblewrap, and gVisor solve a broader security problem.

The systems compared are BuildXL, Bazel, Buck2, cargo-nextest, Pants, Nix,
Please, and Taskcluster. Linux cgroup v2 and systemd are considered separately
as enforcement mechanisms rather than schedulers.

## 1. Terms and evaluation questions

A **directed acyclic graph (DAG)** contains steps and dependency edges. A step
is **ready** when all its dependencies have completed. A scheduler then applies
two kinds of policy:

- **Priority:** which ready step should run first?
- **Admission:** does that step fit the resources still available?

The resource terms used below are deliberately distinct:

- **Outer width:** the maximum number of steps running concurrently.
- **Step demand:** the CPU, memory, or named-resource amount a step claims.
- **Resource capacity:** the total amount of one resource available to the DAG.
- **Inner width:** the parallelism used inside a step, such as `cargo -j 8`.
- **Outer resource envelope:** the CPU and memory assigned to an entire DAG.

For each scheduler, the central question is not whether it can emit a profile.
It is whether the next run **learns from prior runs**:

1. What measurements persist?
2. How are those measurements converted into an expected cost?
3. Which scheduling decision changes because of that expectation?

Build caches are not a learned cost model. A cache can remove completed work
from the next run without predicting the cost of the work that remains.

## 2. Scheduling algorithms and cross-run learning

### 2.1 Comparison

| System | Current-run scheduling algorithm | Learns cost from prior runs? | Persisted model and scheduling effect |
|---|---|---:|---|
| **BuildXL** | Assigns each process, called a *pip*, a bottom-level priority: its expected duration plus the longest expected downstream path. The highest-priority ready pip runs first. Admission also observes process slots, pip weights, semaphores, and projected memory. | **Yes** | Persists per-pip execution/run duration, maximum duration, CPU utilization, peak/average working set, and disk I/O. New observations are merged 50:50 with the old average and entries have a time-to-live. Historical duration replaces the file-count heuristic in critical-path priority; historical CPU becomes a process-slot weight; historical RAM throttles admission. |
| **Bazel** | Executes dependency-ready actions subject to a fixed job count and declared local resource pools. Rule implementations provide action resource estimates. Dynamic execution can race a local and remote copy of the same action. | **No documented cross-run cost feedback** | The JSON trace records one invocation for post-run diagnosis: action durations, critical path, concurrent actions, CPU, memory, load, and optional network data. Bazel's documentation tells the operator to inspect or compare traces; the next scheduler invocation does not read the trace to predict action cost. |
| **Buck2** | Uses the current dependency graph, executor choice, a machine-permit semaphore, optional percentage weights, named mutual-exclusion tokens, and exclusive-host requests. Event logs can reconstruct the critical path after a build. | **No documented cross-run cost feedback** | Event logs and `buck2 log` are observability inputs. Public Buck2 does not document a persisted per-action duration or memory model that changes the next run's ready queue or permit request. Caches and incremental computation reduce the work set, but do not estimate the remaining actions' cost. |
| **cargo-nextest** | Runs ready test binaries under a global test-thread budget. A test may require multiple threads and may also belong to a test group with its own concurrency ceiling. | **No** | Reports test duration and can emit JUnit data, but does not use a retained duration history to prioritize the next run. Thread demands and group membership come from configuration. |
| **Pants** | The rule engine schedules available processes. Local parallelism is a configured global capacity; a process may request an exact/ranged concurrency value or exclusive access. | **No documented cross-run cost feedback** | Execution-log entries, called *workunits*, support diagnosis. Process concurrency is declared for the current run, not learned from retained duration or memory samples. Pants' caches remove work rather than reprioritize remaining work by learned cost. |
| **Nix** | Builds dependency-ready build units, called *derivations*, up to `max-jobs`. Each derivation receives a `cores` hint for its own internal build, but Nix does not sum those hints into a host CPU budget. | **No** | The Nix store and cached results, called *substitutes*, avoid already-built derivations. Nix does not retain per-derivation duration or memory measurements to order uncached derivations. |
| **Please** | Walks the current build graph with a fixed worker-thread count. | **No documented cross-run cost feedback** | Build caches avoid repeated work. No public profile-driven duration, memory, or critical-path model changes the next schedule. |
| **Taskcluster** | Queues independent tasks by priority/deadline and assigns them to compatible worker pools. Worker Manager can grow or shrink a pool from queue demand. | **Not at per-task DAG-cost level** | Operational history can drive pool autoscaling, but Taskcluster does not learn a per-node cost model for one DAG. Scaling changes worker count; it does not reprioritize a DAG using learned step duration or memory. |

### 2.2 BuildXL: the clearest profile-driven scheduler

BuildXL answers the profile-driven scheduling question directly. Before
execution it computes a priority for every pip. That priority represents the
expected time from the pip through the longest downstream chain. When multiple
pips are ready, the highest value runs first. Without history, BuildXL estimates
duration from declared input and output counts. After a build, it serializes
actual per-pip runtimes; future builds use those runtimes instead.

The retained record is broader than duration. `ProcessPipHistoricPerfData`
stores execution and total run duration, maximum observed duration, CPU use as
a percentage of one processor, peak and average working set, and disk I/O.
Each new record is merged with the old record using equal old/new weight. An
entry also ages out through a time-to-live counter.

That history changes three decisions:

1. **Ready-queue priority:** historical duration weights the downstream
   critical-path calculation.
2. **CPU admission:** historical average CPU use can become the pip's `weight`;
   concurrent weights may not exceed the process-slot budget.
3. **Memory admission:** projected historical working sets can stop new pips
   from launching before the machine reaches its RAM threshold.

This is genuine closed-loop scheduling, not merely profile visualization.
Primary sources: [schedule prioritization](https://github.com/microsoft/BuildXL/blob/main/Documentation/Wiki/Advanced-Features/Scheduler-Prioritization.md),
[pip weight](https://github.com/microsoft/BuildXL/blob/main/Documentation/Wiki/Advanced-Features/Pip-Weight.md),
[historical record](https://github.com/microsoft/BuildXL/blob/main/Public/Src/Engine/Scheduler/ProcessPipHistoricPerfData.cs),
and [memory throttling](https://github.com/microsoft/BuildXL/blob/main/Documentation/Wiki/Advanced-Features/Performance-Tuning.md#memory-throttling).

### 2.3 Bazel: profiling is diagnostic, not scheduler feedback

Bazel's profile is easy to misread as an input to its scheduler. It is not.
`--profile` writes a trace of the current invocation. The trace helps a human or
an external tool identify the critical path, insufficient parallelism, expensive
actions, CPU pressure, memory growth, worker behavior, and network use. Bazel's
own performance guide recommends collecting and comparing traces when wall time
regresses.

For the next invocation, local scheduling still uses the current build graph,
`--jobs`, configured local capacities, and resource estimates supplied by rules
or test tags. Bazel does not document reading the previous JSON trace to infer a
duration distribution, memory percentile, or priority. Its strong incremental
cache can make the next build much smaller, but the remaining actions are not
ordered by a learned profile.

Primary sources: [JSON trace profile](https://bazel.build/advanced/performance/json-trace-profile),
[performance analysis](https://bazel.build/advanced/performance/build-performance-breakdown),
and [`ResourceManager`](https://github.com/bazelbuild/bazel/blob/master/src/main/java/com/google/devtools/build/lib/actions/ResourceManager.java).

### 2.4 Buck2: strong current-run resource arbitration, no public feedback loop

Buck2 has a useful host-sharing abstraction. A command can request a fixed
number of permits, a percentage of machine permits, exclusive access, or one of
several named tokens. A semaphore admits commands under a fixed permit count.
This is a current-run capacity model; the request is supplied by the command,
not learned from its earlier CPU or memory use.

Buck2 event logs preserve enough information for `buck2 log critical-path` and
other post-run analysis. The public implementation and documentation do not
describe feeding those event logs back into the next run's permit request or
ready-queue priority. This conclusion is deliberately limited to public Buck2;
it makes no claim about private deployment systems.

Primary sources: [Buck2 overview and installation](https://github.com/facebook/buck2),
[`HostSharingBroker`](https://github.com/facebook/buck2/blob/main/host_sharing/src/host_sharing.rs),
and [the host-sharing protocol](https://github.com/facebook/buck2/blob/main/app/buck2_host_sharing_proto/host_sharing.proto).

## 3. Resource containment

Resource containment needs four explicit axes:

A Linux **control group (cgroup)** is a process hierarchy to which the kernel
applies and accounts resource limits. The controller files below are part of
the cgroup-v2 interface.

| Axis | Failure being bounded | Kernel/runner mechanism |
|---|---|---|
| **CPU** | A CPU-bound command runs forever or consumes more CPU than assigned | `cpu.max` limits rate; a separate cumulative CPU-time budget terminates work after a fixed amount of CPU service. A rate limit alone does not stop an infinite job. |
| **Memory** | A process leaks or allocates until the host becomes unusable | `memory.high` applies reclaim pressure; `memory.max` is the hard ceiling and may invoke the cgroup OOM killer. |
| **PIDs** | A command fork-bombs the host | `pids.max` rejects further forks in the cgroup. Merely enabling the pids controller is not a ceiling. |
| **Wall time** | A command blocks, deadlocks, sleeps, or otherwise consumes little CPU forever | A generous deadline kills the full process subtree. This is a defense-in-depth backstop, not a substitute for CPU accounting. |

CPU rate and CPU placement are separate controls. `cpu.max` limits aggregate
CPU service but lets the Linux scheduler move work across every permitted core.
CPU affinity (`sched_setaffinity`) or `cpuset.cpus` instead keeps work on named
cores. Pinning is necessary for a same-core contention experiment; it is not
implied by a quota.

Applying these limits is mechanically straightforward: cgroup v2 exposes the
control files, and `systemd-run --user --scope -p Delegate=yes` is a standard
way to place a transient process tree inside a delegated cgroup. The difficult
part is **coverage**: every local command, CI shard, workflow, experiment, and
agent-spawned child must actually pass through the containment entry point.

The distinction matters. Adding a `memory.max` writer to a library does not
contain a workflow that bypasses the library and invokes `bash` directly. A
deprecated `--cgroups` flag also proves nothing by its presence or absence when
the CLI already enables containment by default. Coverage must be established by
following each launch path and reading back the applied limits.

The relevant mechanisms are:

- **cgroup v2:** the enforcement substrate. `cgroup.kill` also terminates a
  whole descendant tree, including processes that changed session or process
  group. [Kernel documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).
- **systemd transient scopes:** create and delegate the outer cgroup without a
  custom privileged daemon. [systemd resource control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html).
- **nsjail and Firejail:** demonstrate CPU-time rlimits and other useful
  mechanics, but their security-isolation surface is outside this project's
  resource-containment goal.
- **bubblewrap and gVisor:** security-isolation systems, not replacements for a
  resource-aware DAG scheduler. Both ultimately rely on host cgroups for CPU and
  memory ceilings.

## 4. Integration cost

The table separates “can run locally” from “can be embedded as a Cargo
dependency.” They are not the same.

| Candidate | Build/runtime dependencies | Standalone local use without remote execution | Practical integration assessment |
|---|---|---:|---|
| **BuildXL** | Large .NET/C# application; building requires the .NET toolchain and BuildXL's bootstrap. Runtime is a separate CLI/server ecosystem. | **Yes** on supported Windows and Linux distributions. | **High cost.** Not a Cargo library. Adopting it means translating the project into a BuildXL frontend or driving a separate process and accepting its cache/configuration model. Best used as an algorithm source. |
| **Bazel** | Native launcher plus a Java server and Bazel rule/toolchain ecosystem; normally installed through Bazelisk or a release binary. | **Yes.** Remote execution is optional. | **High cost.** Not a Cargo library. Hermit's CI DAG would need to become Bazel targets/actions, and the repository would acquire Bazel configuration and toolchains. |
| **Buck2** | A large Rust workspace with many internal path crates, generated code, and a *prelude* (its standard rule library). Public releases provide a standalone binary; source builds require Buck2's documented Rust/build prerequisites. | **Yes.** Remote execution is optional, although the public README states local-only actions are not currently hermetic. | **Medium-to-high as a CLI; unsuitable as a small Cargo dependency.** There is no stable embedded scheduler API, no stable release line, and the workspace is an application rather than one reusable crate. Running the binary locally is viable; embedding it would import a build system. |
| **cargo-nextest** | Published Rust crates and a standalone Cargo subcommand. `nextest-runner` exposes the core runner but has a broad dependency set. | **Yes.** | **Low for Rust test execution, high mismatch for an arbitrary CI DAG.** It is the easiest Rust dependency here, but its unit is a test binary, not an arbitrary dependency graph of shell/build steps. |
| **Pants** | Python launcher, Pants engine, plugins, build metadata, and usually a persistent daemon. | **Yes.** Remote execution is optional. | **High cost.** Integration means expressing Hermit work as Pants targets/rules or maintaining a process boundary. Not a Cargo dependency. |
| **Nix** | Nix store, evaluator, daemon or single-user install, derivation language, and sandbox/store conventions. | **Yes.** | **High operational cost.** Strong reproducibility and caching, but adopting Nix to schedule this DAG changes package/build ownership rather than adding a library. |
| **Please** | Standalone Go binary and Please build definitions. | **Yes.** | **Medium cost.** Simple local deployment, but fixed-width scheduling and no learned model provide little reason to migrate. Not a Cargo dependency. |
| **Taskcluster** | Queue, Worker Manager, authentication, workers, backing cloud/provider services, and operational storage. | **No, not as an in-process/local DAG runner.** | **Very high cost.** Appropriate for a CI service fleet, not a library inside a developer command. |

**Direct answer for Buck2:** it can run builds locally without remote execution
or Meta infrastructure. It cannot reasonably be added as a small Cargo
dependency that supplies only its scheduler. The practical adoption boundary is
the Buck2 executable plus Buck2 project definitions and prelude.

## 5. Dynamic outer resource scaling

Consider two independent DAGs on one machine. The first initially receives most
of the machine. When the second arrives, an outer coordinator wants to reduce
the first DAG's envelope so both make progress while each scheduler still sees
a defined capacity.

CPU and memory behave differently:

- **CPU is work-conserving.** Oversubscribing runnable work lets the kernel share
  CPU. Updating `cpu.max` or CPU weights can make the shares explicit, but it
  does not require each running process to resize its thread pool immediately.
- **Memory is not work-conserving in the same way.** A process that already owns
  20 GiB does not give back 10 GiB because its scheduler capacity changed.
  Lowering `memory.high` induces reclaim and throttling. Lowering `memory.max`
  below current use can reclaim or OOM-kill work, and the kernel documents that
  convergence may take an indefinite amount of time. Safe shrinkage normally
  means stopping admission, waiting for memory-heavy steps to finish, or
  canceling/suspending/restarting selected steps.
- **Most inner steps are not moldable.** A compiler launched at `-j 32` usually
  cannot become `-j 16` in place. A new envelope changes the next admissions;
  full effect arrives only as existing steps turn over.

### 5.1 What the surveyed systems do

| System | Can it rescale one running DAG's outer envelope? | Memory behavior |
|---|---:|---|
| **BuildXL** | **Partial, pressure-reactive rather than externally resizable.** It monitors actual/projected machine RAM and stops admitting pips. | When a threshold is exceeded it can cancel and retry processes; on Windows it can instead empty working sets or suspend/resume processes. This is the closest surveyed response to memory shrink, but it does not expose fair, explicit envelopes across independent BuildXL invocations. |
| **Bazel** | **No.** `--jobs` and local resource capacities are fixed for an invocation. An external cgroup may change CPU/memory limits, but Bazel does not replan its declared capacity from that change. | No scheduler-level turnover protocol for shrinking a running invocation. |
| **Buck2** | **No public live-resize API.** `HostSharingBroker` is created with a fixed machine-permit count. Percentage requests are percentages of that fixed count. | The source explicitly describes memory-aware weighting as future work. No memory-reclaim/turnover protocol is documented. |
| **cargo-nextest, Pants, Nix, Please** | **No.** Their local concurrency capacities are fixed for the run. | External pressure may slow or kill work; the scheduler does not renegotiate an outer memory envelope. |
| **Taskcluster** | **Scales the worker pool, not a running task/DAG envelope.** New workers help queued work; draining workers removes capacity after tasks turn over. | Does not reclaim memory from a running task to admit another task on the same worker. |

No surveyed DAG scheduler fully implements the stated two-DAG contract. Linux
provides live cgroup knobs, but a higher-level broker must connect them to
scheduler admission and step turnover.

### 5.2 Recommended outer broker

A minimal design should keep one machine-wide broker above all DAG runs:

1. Give each DAG an explicit CPU share and memory ceiling.
2. On a new arrival, change CPU shares immediately; allow normal CPU
   oversubscription rather than waiting for steps to resize.
3. For memory, first lower the DAG scheduler's capacity so it launches no new
   steps that exceed the new envelope.
4. Wait a bounded interval for running steps to turn over. Use `memory.high` to
   signal pressure, not as proof that memory has been reclaimed.
5. If capacity is still required, apply a declared policy: cancel/retry a
   restartable step, suspend a chosen process tree, or reject/delay the new DAG.
   Do not silently lower `memory.max` and call the result graceful scaling.
6. Read back cgroup usage and expose the transition state so the outer planner
   knows the requested envelope is not yet the effective footprint.

BuildXL's stop-admitting plus cancel/suspend policy is the best algorithmic
reference for the memory half. cgroup v2 supplies the enforcement and
measurement, but not the turnover policy.

## 6. Recommendation

Do not adopt a full build system solely to replace `safe-ci-dag-runner`. Buck2
can run locally, but importing Buck2 as a Cargo dependency is not a realistic
small integration. Bazel, BuildXL, Pants, Nix, and Taskcluster have still larger
repository or operational ownership costs.

Continue the focused runner, but copy the mature algorithms rather than merely
their vocabulary:

1. **Use BuildXL's closed loop:** learned per-step duration for bottom-level
   priority, learned CPU demand for admission weight, and a conservative memory
   estimate for admission.
2. **Keep Bazel-style profiles as evidence, not as a model by themselves:** the
   document must state exactly how retained measurements become estimates and
   which decision consumes them.
3. **Use BuildXL's memory-pressure response as the starting point for outer
   rescaling:** stop admission first; then explicitly cancel, suspend, or wait.
4. **Keep resource containment narrow:** CPU, memory, PIDs, cumulative CPU time,
   wall backstop, full-tree teardown, and attribution. Do not grow namespace or
   syscall isolation unless the trust model changes.
5. **Prove launch-path coverage:** a feature is deployed only when every claimed
   path invokes it and the applied cgroup limits are read back.

The distinctive project-specific work is the combination of retained
resource-attribution data with deterministic validation, matched-load flaky
testing, and rate-aware multisect. The basic scheduler and cgroup mechanisms
are established prior art.

## 7. Current prototype status

[`safe-ci-dag-runner`](https://github.com/rrnewton/agent-utils/tree/0eb4203ae59aa006c6382d50c3cdc43b10be3fed/rs/safe-ci-dag-runner)
is a small DAG runner in the
[`rrnewton/agent-utils`](https://github.com/rrnewton/agent-utils) repository. It
ships both a Rust crate/binary and a
[Python package](https://github.com/rrnewton/agent-utils/tree/0eb4203ae59aa006c6382d50c3cdc43b10be3fed/py/safe_ci_dag_runner).
A DAG declares commands, dependencies, duration and memory hints, inner
parallelism, and named-resource demands. The runner executes ready steps
concurrently and records per-step resource data. The
[`USER_GUIDE`](https://github.com/rrnewton/agent-utils/blob/0eb4203ae59aa006c6382d50c3cdc43b10be3fed/common/docs/safe-ci-dag-runner/USER_GUIDE.md)
is the source-level interface description.

Its scheduling algorithms are real, not aspirational:

- `greedy-lpt` applies longest-processing-time-first ordering: the ready step
  with the longest estimated duration is considered first.
- `critical-path` computes each step's longest expected downstream path and
  orders ready work by that bottom-level value.
- `cpa` (critical-path-and-area planning) uses measured speedup curves to choose
  inner widths, then applies critical-path list scheduling under CPU, memory,
  and named-resource limits.

It also has a genuine cross-run feedback model. For each step and inner width,
the profile store records wall time, contention, CPU seconds, effective cores,
throttling, and peak memory. Duration is a contention-adjusted robust median
(the minimum while fewer than three samples exist, then a median trimmed by
median absolute deviation, or MAD).
Memory is the nearest-rank 90th percentile. Multi-width samples form a speedup
curve with work-conservation guards. Learned duration changes LPT/critical-path
priority; when a memory budget is supplied, learned memory changes
memory-aware sizing and admission; speedup curves change the CPA inner-width
allocation. A bounded 64-sample-per-bucket summary can be merged and
synchronized across machines.

The remaining gap is deployment coverage, not absence of scheduling code:

- At agent-utils `0eb4203`, both Rust and Python implement default-on cgroup CPU
  rate and memory containment plus performance logging. `--cgroups` is a
  deprecated no-op because the CLI already attempts containment; its absence at
  a call site is not evidence that containment is off. Library callers can
  still select the no-op cgroup implementation explicitly.
- Both engines enforce the per-step cumulative `cpu_timeout` budget and the
  differential test cross-checks their timeout outcomes.
- Neither engine pins work to cores at this revision. Python reads an ambient
  `cpuset.cpus.effective` and both engines write `cpu.max`, but neither writes a
  cpuset or invokes `taskset`/`sched_setaffinity`. There is no stateful registry
  assigning disjoint cores across runner processes.
- A [standalone Python allocator prototype](https://github.com/rrnewton/agent-utils/blob/15dbf8091647e9861ee4b3b415e04ddadf23442e/py/safe_ci_dag_runner/coreallocator.py)
  can lease *K* disjoint cores and pin a process tree with
  `sched_setaffinity`; a live child was measured on its assigned core. It is
  pushed but not merged, is not wired into either DAG scheduler, has no Rust
  counterpart, and its kernel-thread confound ranking still has a known
  `kworker/N:M` false positive. The current runner therefore remains unpinned.
- The Hermit tree at `3e4367e` has 53 DAG nodes. All 53 declare wall timeouts and
  hard-memory hints; **0 of 53 declare `cpu_timeout`**.
- Authoritative portable GitHub CI still runs those nodes through
  `ci/run-node.sh`, which extracts commands and invokes `bash` directly. That
  path bypasses runner scheduling, containment, wall deadlines, and profiling.
  Privileged CI and local full validation do invoke the runner.
- Neither implementation writes `pids.max`. The pids controller is enabled and
  failure-reporting types mention a PID guard, but the scheduler currently
  passes `pids_guard_tripped=false`. Fork-bomb containment is therefore absent.
- The local profile reader/writer and shared-summary synchronization mechanisms
  exist, but the Hermit CI paths do not yet provide a complete persistent
  feedback loop for every run.

The current prototype is therefore a capable learned DAG scheduler and partial
resource-containment engine with incomplete launch-path coverage. It is **not a
security sandbox**, and it should not be presented as one.
