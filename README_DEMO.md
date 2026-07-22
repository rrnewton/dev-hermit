# Hermit Demo Walkthrough

This walkthrough demonstrates reproducible Linux execution with Hermit from the
`dev-hermit` workspace. Hermit runs unmodified x86-64 Linux programs under the
[Reverie](https://github.com/facebookexperimental/reverie) ptrace backend and
controls common sources of nondeterminism, including thread scheduling, time,
random data, CPUID results, address layout, and selected file metadata.

The demo materials live entirely in this parent repository. The pinned
`hermit/` submodule is unmodified: the outer workspace only adds to it. The
walkthrough covers four working workflows:

1. repeat an execution with stable guest-visible inputs;
2. record an execution and replay it, with or without GDB;
3. search seeded thread schedules for a concurrency failure; and
4. bisect two schedules to identify the events that change the outcome.

> [!WARNING]
>
> Hermit is in maintenance mode. It is not a security boundary, and it does not
> make changing files or external network responses deterministic. Record/replay
> support is experimental and narrower than `hermit run` compatibility.

## Requirements

Use an x86-64 Linux host with Rust nightly (selected by the submodule's
`rust-toolchain.toml`), libunwind and LZMA development libraries, Linux
user/PID namespaces, and parent-child ptrace and seccomp support. GDB is needed
for the record/replay section, and the Python demo uses `/usr/bin/python3`. The
`--verify` step in demo 1 and the schedule-bisection demo (demo 4) both need
user-accessible CPU performance counters (PMU).

The demos use private temporary and ignored build-artifact directories and
require no external network access.

## Layout

```text
README_DEMO.md              # this walkthrough
demos/
  common.sh                 # shared setup: builds hermit/, defines helpers
  01-deterministic-run.sh   # stable inputs, --verify
  02-record-replay.sh       # record, list, replay, replay under GDB
  03-chaos-concurrency.sh   # seeded schedules, save/replay a failing schedule
  04-schedule-bisection.sh  # hermit analyze (requires PMU)
  run-all.sh                # runs demos 1-3 (add --with-analyze for demo 4)
```

Each script sources `demos/common.sh`, which locates the `hermit/` submodule,
builds the release and debug binaries, and defines the shared `run_hermit`,
`verify_hermit`, and `chaos_run` wrappers. `run_hermit` and `chaos_run`
deliberately disable CPUID virtualization and PMU timer preemption so the short
examples also run on hosts without those features; CPUID is therefore a host
input in those commands, and CPU-bound guests receive fewer preemption
opportunities. `verify_hermit` is different: it keeps PMU-based preemption on
(the racy verify guest is only reliably determinized with real preemption) and
raises the log level to `info` (at `--log=error` the execution log that
`--verify` compares is empty). Demo 4 and the demo-1 `--verify` step are the
exceptions that require the PMU.

## Quick Start

From the workspace root, make sure the submodule is populated, then run the
portable demos:

```bash
git submodule update --init hermit
./demos/run-all.sh
```

Include the slow PMU-based analysis at the end with:

```bash
./demos/run-all.sh --with-analyze
```

Run an individual step directly, for example:

```bash
./demos/01-deterministic-run.sh
```

Set `DEMO_SKIP_BUILD=1` to reuse an existing `hermit/target` build, or export
`HERMIT`, `HELLO_RACE`, and `HEAP_PTRS` to point at prebuilt binaries.

## What Each Demo Shows

### 1. Deterministic Run

Hermit preserves the guest exit status and output while making random bytes,
wall-clock time, Python hash seeding, and heap address layout stable across
runs. It then determinizes `examples/race.sh` -- two shells whose output
interleaves differently on every native run -- and `verify_hermit` runs it twice
under Hermit, comparing exit status, output, and thousands of DETLOG/scheduler
messages in the deterministic execution log. The guest must be idempotent: a
first run that changes a file, database, cache, or external service can
legitimately change the second run.

### 2. Record And Replay

Hermit records an execution into an isolated data directory, lists the recording
in text and JSON, and replays it to completion with `--autopilot`. It can also
record and immediately verify a replay. Without `--autopilot`, `hermit replay`
starts a replay gdbserver and GDB client; the demo drives a noninteractive GDB
session that continues the guest to completion. Keep the recording directory,
executable, inputs, and Hermit revision unchanged between recording and replay.

### 3. Chaos Concurrency Testing

`hello_race` contains an intentional data race. Chaos mode makes scheduler
choices with a seeded PRNG, so different seeds explore different interleavings
and the same seed reproduces the same result. Seed 1 passes; seed 0 reaches the
antagonistic schedule and returns the guest's expected failure status. The demo
surveys seeds 0-15, then records a failing schedule to an artifact and replays
that exact schedule, confirming the outputs match.

### 4. Schedule Bisection

`hermit analyze` first finds passing and failing schedules, then bisects their
event streams to identify the ordering that changes the outcome. It builds a
debug guest so the report can resolve source locations. This is intentionally
the slow finale: it runs the guest many times, requires PMU access, and can emit
scheduler-desynchronization diagnostics while converging. A successful run ends
with `Completed analysis successfully`. On the verified host, the report
identified two adjacent events in different `hello_race` threads and resolved
both stacks to the intentional racy access in `flaky-tests/hello_race.rs`. Event
numbers can vary with the binary and Hermit revision; the source-level diagnosis
is the durable result.

## Scope And Next Steps

- Keep file contents and mount layouts fixed, prefer a minimal environment, and
  avoid external networking when asserting reproducibility.
- Use PMU timer preemption when exploring CPU-bound races. The portable chaos
  commands still find this syscall-rich demo failure without it.
- Treat version probes as launch coverage, not proof that every workflow of a
  program works.
- Benchmark the real workload; ptrace overhead varies with syscall frequency,
  thread count, scheduling, and logging.

For full option and troubleshooting coverage, see the Hermit product
documentation under `hermit/docs/`. Hermit is BSD-licensed; see
`hermit/LICENSE`.
