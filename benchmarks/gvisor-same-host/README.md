# Same-host gVisor runsc benchmark

This experiment measures the same getpid, Redis, ffmpeg, ABSL, and TensorFlow
workloads on one machine under native Linux and official gVisor runsc with the
systrap, KVM, and ptrace platforms. It replaces an invalid comparison against
numbers from the gVisor blog's different host. Blog results are historical
context only and never participate in a ratio or ranking here.

## Provenance

- **Run date:** 2026-08-02.
- **Source:** latest parent `main` at `3f391f8`; the initial experiment
  publication is `da400db`.
- **Machine:** `devbig014` (short hostname), shared 316-CPU AMD EPYC host.
- **Runtime:** official runsc release `20260727.0`.
- **Runs:** `20260802T094424Z` (ABSL), `20260802T095303Z` (TensorFlow), and
  `20260802T102453Z` (getpid, Redis, and ffmpeg).

See each result directory's `metadata.json` for the full repository SHA,
kernel, runsc URL and SHA-512, immutable image digests, runner/source SHA-256
values, load averages, timeouts, platforms, repetitions, and warmups.

## Methods

Run the complete experiment through the external-network proxy:

```bash
with-proxy benchmarks/gvisor-same-host/run.rs
```

The rust-script runner self-provisions the pinned runsc binary, verifies its
SHA-512, pulls four immutable benchmark image digests, exports their root
filesystems, downloads and SHA-256-verifies the ABSL build archives, and
compiles the tracked getpid guest. Large binaries, downloads, image roots,
container exports, runtime state, and raw logs remain under the ignored
`ignored/gvisor-runsc-same-host/` directory.

Native getpid executes directly. Native application workloads run in Podman;
runsc workloads use the same extracted image roots and commands with only
`--platform={systrap,kvm,ptrace}` changed. Networking is disabled except where
the workload itself uses an in-sandbox loopback connection. Each sample has a
900-second process-group timeout and a workload-specific correctness gate.
Failed or timed-out samples stay in `samples.tsv` and never contribute to a
median.

Getpid, Redis, and ffmpeg use one warmup followed by three measured samples per
engine. The engine order rotates for every phase so one engine does not always
run first. Their reported value is the median. ABSL and TensorFlow use no
warmup and one measured sample per engine because every cell takes minutes;
these rows have materially weaker statistical confidence.

Redis reports `250000 / QPS`, so the metric covers exactly 250,000 SET requests
from five clients and excludes server startup. Other application rows report
end-to-end wall time, including runsc/container startup. Getpid reports total
wall time divided by one million raw syscalls. ABSL runs offline with Bzlmod
disabled and both Bazel job and loading-thread parallelism fixed at 16 for all
engines, avoiding process-limit exhaustion on the shared host.

Useful narrower runs are:

```bash
with-proxy benchmarks/gvisor-same-host/run.rs --provision-only
benchmarks/gvisor-same-host/run.rs --workloads getpid,redis \
  --platforms systrap,kvm,ptrace --skip-provision
```

## Evaluation

| Workload | Operation | What it emphasizes |
| --- | --- | --- |
| getpid | One million direct `getpid` calls in a tight C loop | Interception and syscall-dispatch cost with almost no useful work per call. |
| Redis SET 250k/c5 | In-sandbox Redis server plus five clients issuing 250,000 SETs | Event-loop, loopback, futex, timer, and high-rate syscall overhead; long enough to expose throughput effects. |
| ffmpeg | Transcode the pinned input video with libx264 `veryslow` | CPU-heavy media processing plus file I/O; syscall overhead should be amortized by codec work. |
| Build ABSL | Offline `bazel build //...` with 16 build/loading threads | Process creation, filesystem metadata, compiler execution, and bounded build parallelism. |
| TensorFlow-8 | Sequentially run eight TensorFlow 1.x example programs | Python startup, numerical kernels, threads, filesystem access, and a long mixed application suite. |

The getpid checksum, Redis CSV row, ffmpeg output size, ABSL `BUILD_OK`, and
TensorFlow `TF_OK` markers prevent incomplete work from being reported as a
fast result.

## Results

### Local runsc

| Workload | Native | systrap | KVM | ptrace |
| --- | ---: | ---: | ---: | ---: |
| getpid | 100.789 ns | 4,056.209 ns (40.245x) | 1,252.276 ns (12.425x) | 7,109.977 ns (70.543x) |
| Redis SET 250k/c5 | 1,128.000 ms | 12,668.003 ms (11.230x) | 14,089.000 ms (12.490x) | 23,075.993 ms (20.457x) |
| ffmpeg | 24,681.582 ms | 25,933.676 ms (1.051x) | 25,686.832 ms (1.041x) | 25,941.522 ms (1.051x) |
| Build ABSL | 91,773.778 ms | 103,183.537 ms (1.124x) | 185,941.937 ms (2.026x) | 121,825.586 ms (1.327x) |
| TensorFlow-8 | 162,181.537 ms | 423,369.988 ms (2.610x) | 400,047.639 ms (2.467x) | timeout >900,000 ms (>5.549x) |

The microbenchmark shows the platform mechanism clearly: local runsc KVM is
the lowest-overhead getpid path, systrap is next, and ptrace is slowest. Redis
still pays large platform overhead because it performs frequent kernel-facing
operations. In contrast, all three ffmpeg rows stay near 1.05x because codec
work dominates the intercepted operations.

ABSL and TensorFlow show that application behavior is not predicted by getpid
alone. Systrap is closest to native for this bounded ABSL build, while KVM is
slowest. TensorFlow costs roughly 2.5x under systrap/KVM, and ptrace does not
finish within 900 seconds. Because ABSL and TensorFlow have only one sample,
these observations are directional rather than distribution estimates.

### Hermit/Reverie context

The clean same-host table in [`HEAD_TO_HEAD.md`](HEAD_TO_HEAD.md) places the
local runsc ratios beside counter2, Hermit relaxed, and Hermit strict rows.
Counter2 LiteInst and e9patch have lower normalized getpid overhead than all
three local runsc platforms; counter2 DBI is close to runsc KVM. For Redis,
counter2 SaBRe and DBI have lower normalized overhead than local runsc systrap.
These are overhead comparisons, not semantic equivalence: counter2 counts
interceptions, relaxed adds syscall determinization, strict adds deterministic
thread scheduling, and runsc provides a sandbox.

ABSL is not ranked across collections because the older counter2 build did not
use the new 16-job bound. TensorFlow is also not ranked because the older
counter2 evidence does not contain one completed matching eight-program suite.

## Artifacts

- [`HEAD_TO_HEAD.md`](HEAD_TO_HEAD.md): concise same-host review table.
- [`COMPARISON.md`](COMPARISON.md): expanded qualitative comparison.
- [`SCORECARD.tsv`](SCORECARD.tsv): machine-readable runsc and Hermit/Reverie
  rows with status and source.
- [`results/20260802-short-matrix`](results/20260802-short-matrix): three-rep
  getpid, Redis, and ffmpeg results.
- [`results/20260802-absl-matrix`](results/20260802-absl-matrix): one-sample
  offline ABSL build.
- [`results/20260802-tensorflow-matrix`](results/20260802-tensorflow-matrix):
  one-sample TensorFlow-8 results and the explicit ptrace timeout.
