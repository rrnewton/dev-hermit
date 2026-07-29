# Reproducible Benchmarks

> **Repository-maintainer evidence:** this suite depends on the repository's
> private pod compiler and runtime, which are deliberately absent from the
> crates.io package. This document and its relative commands must remain outside
> the published package; crate consumers should use the public examples instead.

The benchmark suite measures the costs and contention shapes that matter to a
shared-memory pod on one controlled host. It is an experiment harness, not a
leaderboard. Every result is bound to an immutable snapshot of every
non-ignored v2 input, the Cargo lockfile, toolchain and build flags, kernel, CPU
model, exact harness/compiler binaries, compiler evidence, executable-pod
digest, and explicit workload counts.

## Run It

Run every workload with tiny counts as a build and correctness smoke test:

```console
./scripts/run-benchmarks.sh --smoke
```

Run the bounded standard configuration:

```console
./scripts/run-benchmarks.sh --output target/my-benchmark-run
```

Make a comparison configuration explicit when collecting evidence:

```console
./scripts/run-benchmarks.sh \
  --output target/benchmark-8x200k \
  --warmup 20000 \
  --iterations 200000 \
  --samples 7 \
  --workers 8 \
  --timeout 3600
```

`--help` lists all options. Standard defaults are 5,000 untimed operations per
worker, 50,000 timed operations per worker, five samples, and at most eight
workers. Smoke defaults are 8, 64, one, and at most two respectively. The
harness per-command deadline is 30 minutes for a standard run and five minutes
for smoke.
All counts can be overridden; zero warmup is permitted and recorded.
`--output` must name a directory which does not exist. The runner atomically
claims that directory and removes only that newly claimed directory if the run,
source-stability check, or output validation fails. This prevents a failed rerun
from leaving an older completion marker in place.

The current executable-image format and raw process harness are audited only
for Linux x86-64, so the runner rejects other targets rather than silently
substituting a different workload.

`RUSTUP_TOOLCHAIN` is the only inherited build selector. For example, the MSRV
smoke is `RUSTUP_TOOLCHAIN=1.85.0 ./scripts/run-benchmarks.sh --smoke`.
The runner rejects inherited Cargo, rustc/rustdoc, wrapper, target, compiler,
linker, and compiler-flag controls before it claims an output directory. This
includes target-specific variables such as
`CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS` and
`CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER`. Do not use `RUSTC_WRAPPER`,
`RUSTC_WORKSPACE_WRAPPER`, `CC`, `RUSTFLAGS`, or `CARGO_HOME` for an evidence
run; they are intentionally unsupported rather than incompletely recorded.

## Outputs

Each successful run creates one self-contained, run-owned bundle:

- `environment.json` is the completion marker. It records the run ID, source
  SHA and dirty state, exact initial-live and retained-snapshot manifest
  digests, whole-bundle inventory digest, workspace and normalized harness
  lockfile digests, exact runner, harness source/binary/report, harness
  manifest, compiler binary/manifest/cross-check, result-file digests,
  toolchain and build-affecting environment, host/kernel/CPU/NUMA metadata,
  observed CPU and memory affinity, inherited cgroup-v2 limits and effective
  CPU/memory sets, pod artifact digest, timer, timeout, and all workload counts.
- `results.jsonl` contains one
  `shmem-pod-benchmark-result-v1` object per timing sample.
- `results.csv` contains the same fields with a header for statistical tools.
- `artifacts/` retains the pod binary, ELF, object, manifest, SDK rlib, and
  dependency evidence produced for this run.
- `bin/` retains the exact compiler and benchmark harness executables.
- `provenance/source/` is the read-only source snapshot used to compile the
  compiler, runtime, pod, and harness. It contains every tracked or untracked,
  non-ignored file under v2, not a hand-selected subset.
- `provenance/source-live-manifest.tsv` records the initial live file type,
  mode, size, SHA-256, and hex-encoded path. `provenance/source-manifest.tsv`
  records those fields again for the retained read-only snapshot.
- `provenance/compiler-crosscheck.json`, the harness manifest/lockfile, and
  `harness-report.json` retain the independent build and execution evidence.
- `provenance/host-linker-manifest.tsv` records canonical paths and digests for
  the explicitly selected host linker driver, its `collect2`, `ld`, assembler,
  LTO/plugin executables, selected startup/library inputs, and the dynamic
  libraries observed for Cargo, rustc, rust-lld, and the linker tools.
  `provenance/host-linker-config.txt` records the driver's specs probe, built-in
  specs, target, version, and search directories. The runner re-resolves and
  revalidates this host evidence through completion.
- `provenance/control-tools.tsv` records and revalidates `/bin/bash` plus every
  root-owned `/usr/bin` executable used to discover sources, hash files, parse
  JSON, build inventories, and publish completion. The runner rejects exported
  shell functions with those names before its first external command and does
  not use the caller's PATH for integrity decisions.
- `provenance/vendor/` retains the exact registry package trees used by every
  successful Cargo compilation. `provenance/vendor-manifest.tsv` binds every
  vendored file's type, mode, size, and digest, and is revalidated through
  completion.
- `bundle-inventory.tsv` records the type, exact mode, size, SHA-256, and
  hex-encoded path of every retained file except itself and the self-describing
  `environment.json` completion marker. The environment binds its digest and
  entry count.
- `runner-owner` and `harness-owner.json` bind the directory and result files to
  this run and its random owner token. Both the runner and direct harness
  reject reuse independently.

The runner first manifests the live v2 tree, copies that exact path set, proves
that the snapshot equals the initial manifest and that the live tree did not
drift during the copy, and makes the snapshot read-only. All four builds then
consume only snapshot paths; Cargo also runs from a temporary directory rather
than discovering configuration from the live checkout. The retained snapshot
is re-manifested after hardening and checked throughout the run. Source
symlinks are rejected: retaining only link text would not bind bytes read from
an external target by Cargo, rustc, or an `include_*` macro.

All Cargo and direct compiler executions run under `env -i`, not the calling
shell's environment. They receive direct, hashed rustc and Cargo paths, a
direct, hashed target linker, a controlled PATH containing only the bound
linker, assembler, and `uname` queried by Cargo's version reporting, fixed
locale/time/source-date settings, a run-private
HOME/TMPDIR, and offline/incremental-disabled Cargo settings. Cargo uses a
run-private vendor-only `CARGO_HOME` which exposes the local registry/git cache
only while `cargo vendor` copies it into the retained bundle. Compilation uses
a separate empty `CARGO_HOME` and command-line source replacement pointing at
that read-only vendor tree; it cannot see user config, credentials, aliases,
wrappers, or mutable registry sources. Cargo.lock authenticates registry
packages, and the runner verifies
that the temporary working directory and every ancestor have no `.cargo/config`
or `.cargo/config.toml`. Cargo configuration discovery starts from that
temporary working directory, not from the retained manifest's directory.
The harness itself receives the same stripped environment.

The runner's own control plane is separate from the build environment. Its
interpreter is `/bin/bash`; after resolving the selected rustup launchers it
sets PATH to `/usr/bin:/bin`, fixes locale/timezone, and gives Git an empty HOME
with global and system configuration disabled. Exported `cargo`, `rustc`,
`git`, `jq`, `sha256sum`, `timeout`, or other required-tool shell functions are
rejected. Thus a caller cannot make the verifier bless a bundle by interposing
a wrapper through PATH or an exported Bash function. The root-owned control
binaries remain host evidence; their retained path/digest manifest is included
in the bundle inventory and completion bindings.

This is hermetic against inherited build variables, Cargo configuration, and
mutable registry-source discovery; it is not a relocatable Linux sysroot or a
container image. The selected GCC driver, system startup objects, dynamic
loader, shared libraries, rustup toolchain and kernel remain host inputs. Their
observed paths/configuration and bytes are hashed and revalidated, but they are
not copied and executed from the bundle. A rebuild-independent compiler/sysroot
closure would require a pinned toolchain image or a fully retained sysroot.

The harness cannot create completion metadata. It requires the exact run ID,
runner-owner token, canonical bundle and artifact paths, the retained harness
executable, and `--defer-completion 1`; it emits only synchronized results and a
report of its own runtime observations. The runner independently observes and
requires the exact same runtime/configuration object, cross-checks every path
and file digest pair in the compiler manifest, validates its dependency
closures and the exact result matrix and byte-equivalent CSV/JSON rows, freezes
every payload file, and inventories the whole bundle. It regenerates the
canonical environment deterministically and rechecks the immutable snapshot
and inventory immediately before atomically publishing `environment.json`. A
failure removes only the directory claimed by that run. A directory without
`environment.json` is not a completed bundle.

Read-only file modes are accidental-mutation hardening, not an adversarial
immutability boundary. A process running as the bundle owner can restore write
permission. A consumer must treat a trusted copy or externally recorded digest
of `environment.json` as the trust anchor, verify its
`bundle.inventory.sha256` and entry count against `bundle-inventory.tsv`, then
recompute every inventory row's type, exact mode, size, and digest before using
any result or artifact. The environment also names and hashes the compiler
manifest and cross-check, retained source manifests, harness report/binary, and
result files; consumers should require those exact paths rather than searching
for substitutes. Without an external signature, checksum, or read-only storage
boundary, no bundle format can authenticate a wholesale same-UID rewrite of
both the completion record and its payload.

Every result row carries raw `elapsed_ns` and `operations`. The integer
`operations_per_second` is a convenience derived from those fields. Retain the
raw pair when computing distributions or confidence intervals. A row is emitted
only after its workload-specific total and terminal state have been checked;
`verified: true` means that check passed, not that the host was noise-free.

For example:

```console
jq -s 'group_by(.variant) | map({variant: .[0].variant, samples: length})' \
  target/my-benchmark-run/results.jsonl
```

Before reporting `PASS`, the runner first applies jq's streaming parser to each
physical JSONL line. Streaming events preserve repeated object members, so the
runner rejects duplicate keys before jq's ordinary object representation can
discard them; it also requires the exact flat 12-key schema with no extra,
missing, or nested member. It then requires exactly 22 unique rows for every
sample, the one expected run ID, exact sample indices, variants, topologies,
worker counts and operation denominators, an exactly recomputed rate, full
field equality between every JSONL and CSV row, and unchanged compiler and
bundle evidence.

## What Is Measured

Latency rows use one calling thread unless the topology says otherwise.
Throughput rows use the recorded worker count. The operation unit is deliberately
explicit because unlike logical operations should not be compared as if they
were interchangeable.

| Variant | Category and topology | One recorded operation |
| --- | --- | --- |
| `direct_rust_atomic_increment` | latency, one thread | One non-inlined Rust call containing `AtomicU64::fetch_add` |
| `authenticated_executable_pod_upsert` | latency, one process | One checked runtime dispatch into the freshly compiled RX pod image and one shared-table upsert |
| `gettid_syscall` | latency, one thread | One raw `gettid` system call |
| `unix_stream_8_byte_round_trip` | latency, two threads | One 8-byte request plus 8-byte reply through a Unix stream socket |
| `process_spin_mutex` | latency or throughput | One lock, increment, and unlock cycle |
| `process_futex_mutex` | latency or throughput | One lock, increment, and unlock cycle; contended waiters may sleep with futex |
| `coarse_futex_lock` | throughput, forked processes | Update one process-specific key while holding the single table lock |
| `fine_grained_futex_locks` | throughput, forked processes | Update a hot key or a process-sharded, cache-line-padded key under that key's lock |
| `atomic_fetch_add` | throughput, forked processes | Update a hot atomic key or a process-sharded, cache-line-padded atomic key |
| `snzi`, `closeable_snzi`, `csnzi` | throughput, hot and sharded thread leaves | One successful enter/arrive plus matching depart cycle |
| `shared_box_allocate_destroy_pair` | latency, exclusive thread | Two operations: one `SharedBox` allocation and one checked destruction/free |
| `checked_get` | latency, exclusive thread | One checked `SharedBox` descriptor resolution |
| `checked_push_pop_pair` | latency, exclusive thread | Two operations: one checked `SharedVec` push and one pop |

The pod baseline is not a function-pointer imitation: the script invokes the
project's pod compiler, authenticates the resulting image, maps code RX and
state RW/NX through `PodImage`, and calls the generated binding. Its timed
`upsert` includes the public runtime wrapper, ABI dispatch, pod table lookup,
and synchronization. The direct call is intentionally much smaller and exists
as a lower-bound reference, not an apples-to-apples implementation comparison.

The process contention cases construct the synchronization objects in an
anonymous `MAP_SHARED` mapping, fork workers before starting the timer, warm the
exact path, and release all workers through shared atomics. Fork, process exit,
and `waitpid` are outside the timed interval. The parent validates the sum after
all children exit; sharded cases additionally validate every worker's exact key
and every unused key. Coordination words, locks, fine-grained slots, and atomic
slots occupy separate cache lines. Process workers run before any benchmark
helper thread is created. Their internal warmup/completion wait derives from the
recorded `--timeout` rather than imposing a hidden shorter deadline.

The Unix stream responder is a thread in the same process. That row measures a
kernel IPC round trip without scheduler-independent claims about a separate
process topology. Use it as a syscall/copy/wakeup reference within the same run.

## Correctness Checks

The suite fails instead of emitting a successful run when any of these checks
does not hold:

- direct, pod, lock, coarse, fine, and atomic counter totals equal the exact
  warmup-plus-timed count;
- every sharded process key equals its per-worker count and unused keys remain
  zero;
- every Unix stream reply matches its request inside the measured loop and the
  responder consumes the exact exchange count;
- every SNZI worker completes the exact timed count, ordinary SNZI is healthy
  and quiescent, and closeable variants reach terminal drained state;
- allocator occupancy returns to zero, every timed box/get returns its exact
  expected value, and every vector pop returns the immediately preceding value;
- every forked child exits successfully; and
- the pod loader verifies runtime permissions and validates pod state after the
  final call.

Warmup is excluded from timing and included in total validation. Persistent
single-thread states (direct call, pod, uncontended locks, IPC, and relocatable
collections) warm once before sample zero and then produce ordered repeated
intervals. Process contention and presence workloads construct fresh state and
warm it once for every sample. This policy is recorded in `environment.json`;
the runner does not call repeated intervals statistically independent. Setup,
mapping, image compilation, thread/process creation, terminal close, and
teardown are excluded unless the operation definition explicitly includes
allocation or destruction.

## Interpret Results Carefully

Compare latency only with latency and throughput only with throughput. Even
within a category, consult the operation table: an IPC round trip, an atomic
increment, and an enter/depart pair do different work.

Results from one machine do not establish portable rankings. CPU frequency,
SMT placement, NUMA placement, scheduler activity, cgroup CPU quota, memory
pressure, kernel mitigations, and power policy can dominate small differences.
The runner does not pin CPUs or disable system services. It records allowed CPU
and memory sets and the online/possible NUMA nodes, but not each scheduler
migration. For a serious study, hold those variables constant, collect multiple
independent runs, retain every complete bundle, report distributions rather than
only minima, and test each topology and worker count relevant to the deployment.

`ProcessFutexMutex` timeout is cancellation, not recovery, and neither pod
mutex is robust against owner death. The suite therefore has no
"recoverable shmem-pod mutex" row: the library exposes no such primitive.
Benchmarking a pthread robust mutex would be a distinct kernel baseline and
must not be mislabeled as behavior provided by this crate.

Do not compare a dirty-tree run to a clean revision by SHA alone.
`source.dirty: true` means the revision is not a sufficient reconstruction key;
the retained full snapshot is authoritative for that run. The initial-live and
retained-snapshot manifests prove which bytes and modes were copied and used.
