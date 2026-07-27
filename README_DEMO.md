# Hermit Demo Walkthrough

This walkthrough demonstrates reproducible Linux execution with Hermit from the
`dev-hermit` workspace. Hermit runs unmodified x86-64 Linux programs under the
[Reverie](https://github.com/facebookexperimental/reverie) ptrace backend and
controls common sources of nondeterminism, including thread scheduling, time,
random data, CPUID results, address layout, and selected file metadata.

The demo materials live entirely in this parent repository. The pinned
`hermit/` submodule is unmodified: the outer workspace only adds to it. The
walkthrough covers six working workflows:

1. repeat an execution with stable guest-visible inputs;
2. record an execution and replay it, with or without GDB;
3. search seeded thread schedules for a concurrency failure;
4. bisect two schedules to identify the events that change the outcome;
5. boot Linux in QEMU and save a live snapshot under Hermit's strict profile;
6. resume that snapshot and inject repeatable commands over its serial port.

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
`--verify` step in demo 1 needs user-accessible CPU performance counters (PMU).
Demo 4 defaults to syscall-boundary schedule exploration; set
`ANALYZE_PREEMPTION_TIMEOUT=400000` to add precise PMU preemption.

The demos use private temporary and ignored build-artifact directories. Demos 5
and 6 additionally need `qemu-system-x86_64`, `qemu-img`, the Meta `manifold`
CLI, Ncat (`nc`), static BusyBox, `cpio`, and `gzip`. Demo 5 downloads a fixed
kernel from Manifold, verifies its SHA-256, and caches it with the generated
initramfs and qcow2 snapshot disk under `ignored/qemu-linux`.

## Layout

```text
README_DEMO.md              # this walkthrough
demos/
  common.sh                 # shared setup: builds hermit/, defines helpers
  01-deterministic-run.sh   # stable inputs, --verify
  02-record-replay.sh       # record, list, replay, replay under GDB
  03-chaos-concurrency.sh   # seeded schedules, save/replay a failing schedule
  04-schedule-bisection.sh  # portable syscall-boundary hermit analyze
  05-qemu-boot.sh           # strict QEMU/Linux boot and snapshot
  06-qemu-resume.sh         # resume snapshot and inject a serial command
  lib/
    qemu-assets.sh          # internal first-run kernel/initramfs helper
    qemu-snapshot.sh        # QMP, snapshot, and stable-log helpers
  run-all.sh                # demos 1-3; demo 4 and QEMU pair are opt-in
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
`--verify` compares is empty). The demo-1 `--verify` step requires the PMU;
demo 4 only requires it when `ANALYZE_PREEMPTION_TIMEOUT` enables preemption.

## Quick Start

From the workspace root, make sure the submodule is populated, then run the
portable demos:

```bash
git submodule update --init hermit
./demos/run-all.sh
```

Include the slow schedule analysis at the end with:

```bash
./demos/run-all.sh --with-analyze
```

Include the QEMU boot-and-resume pair with `--with-qemu`, or use `--all` for
all optional demos:

```bash
./demos/run-all.sh --with-qemu
./demos/run-all.sh --all
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
the slow finale: it runs the guest many times and can emit convergence
diagnostics. A successful run ends
with `Completed analysis successfully`. On the verified host, the report
identified two adjacent syscall events in different `hello_race` threads whose
order changes the outcome. Event numbers can vary with the binary and Hermit
revision. The default uses portable syscall-boundary chaos. Set
`ANALYZE_PREEMPTION_TIMEOUT=400000` to add precise PMU preemption and obtain
finer-grained source localization on a validated host.

### 5. QEMU Linux Snapshot

Hermit runs `qemu-system-x86_64`, which boots a real x86-64 Linux kernel under
TCG and reaches the initramfs serial shell. The guest RTC is checked against
Hermit's fixed 2022 virtual-time epoch rather than host wall time. Here RTC
means the guest's Real-Time Clock; the demo explicitly starts it at
`2022-01-01T00:00:00` on QEMU's VM clock.

At the shell, Demo 5 asks QEMU's QMP control socket to run `savevm hermit-boot`.
The resulting internal snapshot is stored in the ignored
`hermit-snapshot.qcow2` disk. The disk is deliberately not attached to the
guest; it exists only as QEMU's VM-state store. Demo 5 then exits QEMU over QMP
and prints pastable Demo 6 commands.

On first use, `demos/lib/qemu-assets.sh` downloads this content-addressed
kernel and verifies it before atomically populating the cache:

```text
manifold://test/tree/dev-hermit/qemu-kernels/e4b1c0248a31c7e1f7cb31d82a1a03d4e7cab408ee1b8e622dd897c17eae46a2/bzImage
sha256: e4b1c0248a31c7e1f7cb31d82a1a03d4e7cab408ee1b8e622dd897c17eae46a2
```

The helper also builds a small static BusyBox initramfs. Later runs reuse the
kernel only after checking its SHA again, so stale or host-specific cache
contents are replaced. `KERNEL_IMAGE` supplies a local copy of the same fixed
kernel for offline testing. `QEMU_KERNEL_MANIFOLD_PATH` and
`QEMU_KERNEL_SHA256` provide an explicit paired override for intentional kernel
updates; `BUSYBOX` and `QEMU_ASSETS` retain their existing overrides.

The boot and every resume use `--strict`, `--target-timeslice 100000`, and
`--max-timeslice 2000000000`. Strict mode fails closed on unsupported
operations; the timeslice settings retain deterministic scheduling while
keeping the VM practical. To avoid writing millions of per-syscall lines, the
scripts enable stable scheduler/run lifecycle INFO targets and print a concise,
timestamp-free tail.

### 6. QEMU Snapshot Resume

Demo 6 starts the same QEMU machine with `-loadvm hermit-boot`, connects to its
Unix serial socket, and injects one shell command. It prints the guest output,
powers the guest off, and prints the normalized Hermit INFO tail. For example:

```bash
./demos/06-qemu-resume.sh 'ls /'
./demos/06-qemu-resume.sh 'cat /proc/cpuinfo'
./demos/06-qemu-resume.sh 'uname -a'
./demos/06-qemu-resume.sh 'echo hello'
```

The first run of each command stores its INFO tail under the ignored QEMU asset
directory, keyed by the snapshot image and command. Repeating the same command
from the same snapshot must match that tail byte for byte; Demo 6 reports the
match or fails with a diff.

## Scope And Next Steps

- Keep file contents and mount layouts fixed, prefer a minimal environment, and
  avoid external networking when asserting reproducibility.
- Use PMU timer preemption when exploring CPU-bound races. The portable chaos
  commands still find this syscall-rich demo failure without it.
- Treat version probes as launch coverage, not proof that every workflow of a
  program works.
- Benchmark the real workload; ptrace overhead varies with syscall frequency,
  thread count, scheduling, and logging.
- Demos 5 and 6 run inside the strict deterministic boundary and show their
  INFO-log evidence. Demo 6 performs the paired comparison for repeated guest
  commands without paying for a second Linux boot.

For full option and troubleshooting coverage, see the Hermit product
documentation under `hermit/docs/`. Hermit is BSD-licensed; see
`hermit/LICENSE`.
