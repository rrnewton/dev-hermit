# Reproducible Benchmarks

The benchmark suite measures the costs and contention shapes that matter to a
shared-memory pod on one controlled host. It is an experiment harness, not a
leaderboard. Every result is bound to its source revision, Cargo lockfile,
toolchain, kernel, CPU model, executable-pod digest, and explicit workload
counts.

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
harness deadline is 30 minutes for a standard run and five minutes for smoke.
All counts can be overridden; zero warmup is permitted and recorded.

The current executable-image format and raw process harness are audited only
for Linux x86-64, so the runner rejects other targets rather than silently
substituting a different workload.

## Outputs

Each run creates three text files:

- `environment.json` records the run ID, source SHA and dirty state, workspace
  and normalized harness lockfile SHA-256 values, host/kernel/CPU/toolchain,
  pod artifact SHA-256, profile, timer, and all workload counts.
- `results.jsonl` contains one
  `shmem-pod-benchmark-result-v1` object per timing sample.
- `results.csv` contains the same fields with a header for statistical tools.

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

The runner validates both JSON schemas and requires at least one CSV result row
before reporting `PASS`.

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
| `fine_grained_futex_locks` | throughput, forked processes | Update a hot or process-sharded key under that key's lock |
| `atomic_fetch_add` | throughput, forked processes | Update a hot or process-sharded atomic key |
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
all children exit. Process workers run before any benchmark helper thread is
created.

The Unix stream responder is a thread in the same process. That row measures a
kernel IPC round trip without scheduler-independent claims about a separate
process topology. Use it as a syscall/copy/wakeup reference within the same run.

## Correctness Checks

The suite fails instead of emitting a successful run when any of these checks
does not hold:

- direct, pod, lock, coarse, fine, and atomic counter totals equal the exact
  warmup-plus-timed count;
- every Unix stream reply matches its request and the responder consumes the
  exact exchange count;
- every SNZI worker completes the exact timed count, ordinary SNZI is healthy
  and quiescent, and closeable variants reach terminal drained state;
- allocator occupancy returns to zero, every destroyed box returns its value,
  and every vector pop returns the just-pushed value;
- every forked child exits successfully; and
- the pod loader verifies runtime permissions and validates pod state after the
  final call.

Warmup mutates the same state but is excluded from timing and included in total
validation. Setup, mapping, image compilation, thread/process creation, terminal
close, and teardown are excluded unless the operation definition explicitly
includes allocation or destruction.

## Interpret Results Carefully

Compare latency only with latency and throughput only with throughput. Even
within a category, consult the operation table: an IPC round trip, an atomic
increment, and an enter/depart pair do different work.

Results from one machine do not establish portable rankings. CPU frequency,
SMT placement, NUMA placement, scheduler activity, cgroup CPU quota, memory
pressure, kernel mitigations, and power policy can dominate small differences.
The runner does not pin CPUs or disable system services. For a serious study,
hold those variables constant, collect multiple independent runs, retain every
environment file, report distributions rather than only minima, and test each
topology and worker count relevant to the deployment.

`ProcessFutexMutex` timeout is cancellation, not recovery, and neither pod
mutex is robust against owner death. The suite therefore has no
"recoverable shmem-pod mutex" row: the library exposes no such primitive.
Benchmarking a pthread robust mutex would be a distinct kernel baseline and
must not be mislabeled as behavior provided by this crate.

Do not compare a dirty-tree run to a clean revision without preserving the
diff. `source_dirty: true` is evidence that the SHA alone is insufficient to
reconstruct that run.
