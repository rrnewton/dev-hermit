# Hermit Runtime Overhead Profile

Date: 2026-07-21

## Executive summary

A short-lived-thread workload is about 260x slower under Hermit than natively
when both are pinned to one CPU. The workload creates 1,000 threads in batches
of 32; every thread calls `sched_yield` and exits, and the parent joins it.
This deliberately stresses clone, ptrace stop delivery, deterministic
scheduling, exit notification, and thread teardown.

The top five flat CPU-time symbols are not Detcore syscall handlers. They are:

1. vDSO clock reads made mostly by Tokio scheduling and metrics;
2. the futures `AtomicWaker::register` used by safeptrace notification;
3. Tokio `AtomicWaker::register_by_ref`;
4. Tokio current-thread waker `Arc` cloning;
5. safeptrace `ExitFuture::poll`.

Together these five account for 21.1% of sampled on-CPU time. Cutting each by
half would improve CPU time by at most about 1.12x. That is useful, but it
cannot explain the 260x wall-time ratio. The run spends 5.77 of 8.88 seconds
in system CPU and makes about 71,000 context switches. The dominant product
problem is the stop/wake/schedule lifecycle around ptrace, not one expensive
Detcore function.

The highest-leverage experiment is to replace or specialize safeptrace's
one-native-thread-per-guest notifier for Hermit's controlled process tree.
The second is to cache per-task notifier state so repeated polls avoid a
global PID hash map, mutex, and waker registration. Optimizing syscall
emulation or resource hashing should come later for this workload.

## Revisions and environment

The task started from clean Hermit `main`:

- Hermit source: `ffdc1280e33c1c7d0f905ee630865e1b30d63e05`
- Reverie dependency: `96693397`
- rust-shed dependency: `84a82026`
- release Hermit SHA-256:
  `5f64026edf930450ef685c7025eba2833cb8278214fa3e460739d2fb8bfbe3b4`
- workload SHA-256:
  `42bbfb80e42194668ded0ea2a78d0d4188e45f35a5e6c0c07ab8910dfdb03d4a`

The primary checkout was switched concurrently during the investigation.
To avoid disturbing that work, the final binary was built from a read-only
`git archive` of the revision above in an isolated temporary target
directory. Cargo resolved the unlocked public dependencies on 2026-07-21;
the binary hash is the definitive artifact identity for these measurements.

Host:

- AMD EPYC 9D85, family 26 model 17
- Linux `6.13.2-0_fbk13_hardened_0_g02230262e956`
- perf `6.19.0-rc6`
- CPU 24 selected with `taskset -c 24`
- `perf_event_paranoid=1`, `kptr_restrict=0`
- CPUID faulting unavailable; Hermit printed the expected host warning

The profile used the software `cpu-clock` event, so PMU availability does
not affect the hotspot ranking.

## Workload

`stress-ng` was not installed. A temporary 18 KiB C helper already present
in the shared build cache was used instead. Its `thread COUNT` mode is
equivalent to:

```c
for (size_t base = 0; base < count; base += 32) {
    size_t n = min(32, count - base);
    for (size_t i = 0; i < n; i++)
        pthread_create(&threads[i], NULL, thread_main,
                       (void *)(base + i));
    for (size_t i = 0; i < n; i++)
        pthread_join(threads[i], &result);
}

static void *thread_main(void *arg) {
    sched_yield();
    return (void *)((uintptr_t)arg + 1);
}
```

The binary also verifies every return value. It does almost no guest
computation, so results are intentionally specific to thread lifecycle and
ptrace control-plane overhead. The helper is not a product test and was not
committed.

## Timing

Both modes ran the same 1,000-thread workload on CPU 24. Timing used
`date +%s%N` around otherwise uninstrumented commands. The final adjacent
steady-state batches were ten native runs and three Hermit runs.

| Mode | Mean | Observed range | Relative |
| --- | ---: | ---: | ---: |
| Native | 34.014 ms | 29.611-40.693 ms | 1.0x |
| Hermit run | 8.854 s | 8.836-8.880 s | 260.3x |

One separate `/usr/bin/time` Hermit run reported:

```text
wall=8.88 user=3.09 sys=5.77 maxrss_kb=5096 invol_cs=15149 vol_cs=56218
```

This comparison pins the native workload too, so the ratio does not include
native parallel speedup. It measures instrumentation, serialization, and
control-plane cost for the same single-CPU budget.

## Profiling method

Naively running `perf record -- hermit ...` inherits perf events into every
tracee thread. On a clone-heavy benchmark that adds substantial
`perf_event_init_task` work and changes the workload. Conversely,
`--no-inherit` on the original CLI PID misses nearly all work because the
CLI forks a runtime process and safeptrace uses native `guest-N` notifier
threads.

The final collection used this sequence:

1. Start Hermit with a guest shell that sleeps for two seconds before
   executing the helper.
2. Discover the Hermit runtime PID, its already-running tracee PID, and the
   stable `guest-3` notifier TID.
3. Attach perf only to the runtime and notifier TIDs after the tracee exists.
4. Leave inheritance enabled so later Hermit notifier threads are sampled.
   The existing tracee has no perf event, so its pthread children do not
   inherit one.
5. Record `cpu-clock` at 999 Hz, once flat and once with
   `--call-graph dwarf,8192`.

The flat run had 3,679 samples; the call-graph run had 3,778. Both reported
zero lost samples. The call-graph sample window was 11.495 seconds. In the
flat run, sample ownership was:

| Object | CPU samples |
| --- | ---: |
| Hermit executable | 69.72% |
| Linux kernel | 17.75% |
| libc | 6.80% |
| vDSO | 5.71% |

`guest-3` in the command breakdown is a safeptrace notifier thread inside
the Hermit runtime process, not the tracee. It held 2.12% of flat samples.

Perf data and expanded reports were kept in `/tmp`; binary perf data is not
a durable repository artifact.

## Top five flat hotspots

Percentages are self time from two independent exact-binary runs. The mean is
used for ranking.

| Rank | Symbol | Flat | Call-graph | Mean | Category |
| ---: | --- | ---: | ---: | ---: | --- |
| 1 | `__vdso_clock_gettime` | 5.71% | 5.19% | 5.45% | scheduling/bookkeeping |
| 2 | futures `AtomicWaker::register` | 4.35% | 4.37% | 4.36% | ptrace notification/scheduling |
| 3 | Tokio `AtomicWaker::register_by_ref` | 4.02% | 3.84% | 3.93% | scheduling |
| 4 | Tokio `wake::clone_arc_raw` | 3.97% | 3.60% | 3.79% | scheduling/bookkeeping |
| 5 | safeptrace `ExitFuture::poll` | 3.45% | 3.68% | 3.57% | ptrace overhead |

Tokio `wake::drop_arc_raw` is immediately below the top five at a 3.40%
mean. Clone and drop are one 7.18% reference-counting family and should be
treated as one optimization opportunity.

### 1. Clock reads

The call graph attributes most `__vdso_clock_gettime` samples to
`std::time::Instant` from:

- Tokio runtime metrics around scheduled-task processing;
- Tokio's time driver and `Clock::now`;
- park and orphan-reaping paths.

This is scheduling/bookkeeping, not guest time virtualization. Replacing the
vDSO clock itself is unlikely to help. Reducing redundant task
poll/park/unpark cycles should reduce these reads as a consequence. A focused
experiment can also test whether Hermit needs Tokio's time driver in every
runtime involved in tracing.

### 2. futures atomic waker registration

safeptrace's `Event::poll_status` and `Event::poll_exit` register an
`AtomicWaker` before every status check. This is required to avoid missed
wakeups, but the same task's waker is frequently registered again.

This cost belongs to ptrace notification and async scheduling. Prototype a
per-event cached waker identity and avoid replacement when
`Waker::will_wake` is true. The race proof must be explicit; a missed exit
notification is a correctness bug.

### 3. Tokio atomic waker registration

`tokio::sync::Notify` and runtime wake paths account for the second atomic
waker implementation. Reverie's `cancellable` races handler futures against
an exit notification, and Detcore also wakes scheduling/RPC work.

This is scheduling overhead. Measure poll and wake counts per ptrace event,
then consolidate duplicate notifications or keep a task runnable across a
burst of already-available events instead of parking it after each one.

### 4. Tokio waker Arc cloning

`clone_arc_raw` and `drop_arc_raw` indicate repeated creation and
destruction of current-thread runtime wakers. Together they consume more CPU
than any individual symbol in the table.

This is scheduling/bookkeeping. The actionable target is fewer wake and poll
cycles, not unsafe custom reference counting. A borrowed or cached waker may
be useful only if Tokio's ownership contract can be preserved.

### 5. safeptrace exit polling

`ExitFuture::poll` calls the global notifier. `Notifier::poll_exit` locks a
global `Mutex<HashMap<Pid, Arc<Event>>>`, performs a PID lookup, and
registers the exit waker. Every guest task also gets a native notifier thread
that blocks in `waitpid(pid)`.

This is direct ptrace overhead. Cache the `Arc<Event>` in each
`TracedTask` after first registration so normal polling bypasses the global
map and lock. A larger Hermit-specific experiment should replace the
one-thread-per-guest design with a central waiter when the tracer owns the
whole child tree. It must retain out-of-band `PTRACE_EVENT_EXIT` delivery
and ptrace's thread-ownership requirements.

## Speedup bounds

The table applies Amdahl's law to sampled CPU time. "50% cut" is a useful
prototype target; "ceiling" unrealistically removes the symbol completely.

| Hotspot | Mean share | 50% cut | Zero-cost ceiling |
| --- | ---: | ---: | ---: |
| vDSO clock reads | 5.45% | 1.028x | 1.058x |
| futures waker registration | 4.36% | 1.022x | 1.046x |
| Tokio waker registration | 3.93% | 1.020x | 1.041x |
| Tokio waker clone | 3.79% | 1.019x | 1.039x |
| safeptrace exit polling | 3.57% | 1.018x | 1.037x |

Cutting all five by half has a CPU-time bound of about 1.12x. Including the
paired waker-drop cost raises it to about 1.14x. These are not wall-time
predictions: CPU-clock does not sample time blocked in `waitpid`, epoll, or
ptrace stops, and Hermit intentionally serializes guest execution.

## Supporting findings

The cumulative call graph gives useful context:

- kernel syscall entry: 17.65%;
- kernel ptrace subtree: 4.42%;
- epoll wait subtree: 2.30%;
- eventfd write subtree: 1.85%;
- clone3 subtree: 1.16%, including about 0.56% profiler event inheritance.

Direct flat costs were smaller:

- Detcore global RPC receive: 2.72% and 3.23%;
- kernel `ptrace_request`: 1.03% and 1.46%;
- Detcore syscall-event handler: 0.69% in the call-graph run;
- Detcore thread-start handler: 0.63% and 0.71%;
- resource-map insert: 0.90% and 1.06%.

Therefore:

- ptrace is expensive as a transition and wait protocol, but no single kernel
  ptrace function dominates on-CPU time;
- syscall interception is not a top-five self-time cost for this workload;
- deterministic scheduling and async wake bookkeeping dominate the named
  user-space symbols;
- resource bookkeeping is measurable but is not the first target.

## Recommended optimization order

1. Instrument counts per guest event: notifier-thread creation, wait events,
   future polls, waker registrations, task wakes, ptrace calls, and scheduler
   RPCs. CPU samples alone cannot expose amplification factors.
2. Cache notifier `Event` handles in `TracedTask` and avoid the global PID
   map on steady-state exit/status polls.
3. Prototype a Hermit-owned central wait dispatcher. Safeptrace's source
   rejects unrestricted `waitpid(-1)` for a general library because it may
   steal unrelated child events; Hermit's dedicated runtime/container may
   provide a narrower ownership boundary where it is safe.
4. Coalesce wakeups and keep runnable tasks active while queued ptrace events
   are available. Re-profile clock reads and Arc waker traffic after this
   change.
5. Only then specialize singleton `Resources` requests or reduce Detcore
   RPC/hash-map allocation.

Success should be judged on both CPU time and wall time. For this benchmark,
also require fewer notifier threads and context switches, not merely a
different flat-symbol ranking.

## Limitations

- This is a pathological thread-lifecycle microbenchmark, not a broad
  application suite. Repeat with CPU-bound, syscall-heavy, futex-heavy, and
  long-lived-thread workloads before setting priorities for all of Hermit.
- The helper is a temporary artifact. Its hash and effective algorithm are
  recorded, but it is not a maintained repository benchmark.
- Sampling new Hermit notifier threads necessarily inherits perf events into
  those threads. The measured clone subtree includes a small amount of perf
  bookkeeping.
- CPU-clock profiles on-CPU work only. Off-CPU tracing latency, guest
  serialization, and scheduler handoff latency require sched tracepoints,
  eBPF, or explicit timestamps.
- The host cannot fault CPUID. This workload does not exercise CPUID after
  startup, but the environment differs from a fully supported host.
- Percent-level differences between symbols should not be overinterpreted;
  use the two-run agreement and optimize families, not a single instruction.
