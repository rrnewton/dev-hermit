# Same-host gVisor platform overhead

## Introduction

This experiment measures five workloads on one machine under native Linux and
official gVisor `runsc` with the systrap, KVM, and ptrace platforms. The goal is
to establish a same-host overhead comparison: every slowdown in the primary
table is computed against the native result from this collection, not against a
number from another machine.

The main result is workload-dependent. A tight `getpid` loop magnifies
interception cost: relative to the **100.789 ns native anchor**, runsc KVM is
about **12.4x**, systrap **40.2x**, and ptrace **70.5x**. Redis remains sensitive
to kernel-facing work at roughly **11.2x-20.5x** its **1.128 s native anchor**.
The CPU-heavy ffmpeg transcode is only **1.04x-1.05x** its **24.682 s native
anchor**. The longer ABSL and TensorFlow cells are directional one-sample
measurements, not distribution estimates.

### Related work

The motivating comparison is the gVisor team's April 2023 post,
["Releasing Systrap - A high-performance gVisor platform"][systrap-blog]. It
explains systrap's shared-memory and syscall-patching design and reports both a
tight `getpid` microbenchmark and application workloads. Its optimized systrap
`getpid` result was 1,017 ns against a **239 ns blog-native anchor**, or about
**4.26x**. Those results were collected on a four-vCPU GCE `n2-standard-4` VM
with a 2023 gVisor revision. They are historical context, not inputs to any
local ratio or ranking in this report.

Earlier Hermit/Reverie measurements from `devbig014` provide additional
same-machine context in [`SCORECARD.tsv`](SCORECARD.tsv) and
[`HEAD_TO_HEAD.md`](HEAD_TO_HEAD.md). They were collected at a different time
and provide different semantics: counter2 counts interceptions, Hermit relaxed
determinizes syscalls without deterministic thread scheduling, Hermit strict
adds deterministic scheduling, and runsc supplies an application-kernel
sandbox. Similar timing does not imply equivalent behavior.

[systrap-blog]: https://gvisor.dev/blog/2023/04/28/systrap-release/

## Methods

### Provenance

- **Run date:** 2026-08-02.
- **Source:** then-latest parent `main` at `3f391f8`.
- **Machine:** `devbig014`, a shared 316-logical-CPU AMD EPYC host.
- **Runtime:** official runsc release `20260727.0`.
- **Run IDs:** `20260802T094424Z` (ABSL), `20260802T095303Z`
  (TensorFlow), and `20260802T102453Z` (getpid, Redis, and ffmpeg).

See the run-specific `metadata.json` files for full immutable SHAs, kernel and
runtime versions, image digests, producing-script hashes, load averages,
timeouts, and exact configuration:

- [ABSL metadata](results/20260802-absl-matrix/metadata.json)
- [TensorFlow metadata](results/20260802-tensorflow-matrix/metadata.json)
- [getpid/Redis/ffmpeg metadata](results/20260802-short-matrix/metadata.json)

This is a writeup-only revision. No benchmark was rerun or regenerated.

### Procedure

The complete collection was invoked as:

```bash
with-proxy benchmarks/gvisor-same-host/run.rs
```

The runner downloaded and hash-verified the pinned runsc binary, pulled four
immutable benchmark image digests, exported their root filesystems, verified
the ABSL input archives, and compiled the tracked `getpid` guest. Native
`getpid` ran directly. Native application workloads ran in Podman; runsc used
the same image roots and workload commands with only
`--platform={systrap,kvm,ptrace}` changed. Large provisioned inputs, runtime
state, binaries, and raw logs remained under ignored storage.

For getpid, Redis, and ffmpeg, each engine received one warmup followed by
three measured repetitions; the reported statistic is the median. Engine order
rotated between phases. ABSL and TensorFlow received no warmup and one measured
repetition per engine because each cell took minutes. Every sample had a
900-second process-group timeout. Failed and timed-out samples remain in
`samples.tsv` and do not contribute a completed median.

Each slowdown is:

```text
engine median / native median from the same run directory
```

The runner required workload-specific completion evidence: a getpid checksum,
a Redis SET CSV row, a minimum-size ffmpeg output, `BUILD_OK` for ABSL, and
`TF_OK` after all eight TensorFlow programs. Redis is special: its reported
metric is the nominal time `250000 / QPS`, derived from `redis-benchmark`, and
excludes server startup; it is not end-to-end wall time.

### Experimental limits

The machine was shared, with recorded load averages ranging from tens to
hundreds. The experiment did not record a dedicated cgroup, CPU set, or host
exclusivity. Three repetitions give only a narrow view of variance, while the
one-sample ABSL and TensorFlow cells provide none. Native applications used
Podman whereas sandboxed applications used runsc, so the comparison includes
their respective runtime setup. These constraints support an overhead
snapshot, not a hardware-independent performance claim.

## Evaluation

| Workload | What runs | Resource and duration intuition |
| --- | --- | --- |
| getpid | One million raw `getpid` syscalls in a tight C loop, with a checksum gate. | Almost no useful work per call; isolates interception/dispatch. Native completes in about 0.10 s, while runsc engines take about 1.25-7.11 s. |
| Redis SET 250k/c5 | An in-sandbox Redis server and `redis-benchmark -t set -n 250000 -c 5`; the metric is `250000 / QPS`. | Event-loop, futex, timer, loopback, and syscall-heavy throughput. The native derived metric is about 1.13 s. |
| ffmpeg | Transcode the pinned video with libx264 `veryslow`; output size is checked. | CPU-heavy codec work amortizes syscall overhead; all completed cells take about 25-26 s. |
| Build ABSL | Offline `bazel build //...`, with build jobs and loading threads both fixed at 16. | Process creation, compiler execution, filesystem metadata, and bounded parallelism; cells take about 92-186 s. |
| TensorFlow-8 | Sequentially execute eight TensorFlow 1.x examples and require the final `TF_OK`. | Python startup, threads, filesystem activity, and CPU numerical kernels; completed cells take about 162-423 s. |

## Results and discussion

### Local runsc matrix

Absolute medians are shown before slowdown. The **native anchor column is the
denominator for every ratio in its row**.

| Workload | Native anchor | systrap | KVM | ptrace | Samples |
| --- | ---: | ---: | ---: | ---: | ---: |
| getpid | **100.789 ns** | 4,056.209 ns (**~40.2x**) | 1,252.276 ns (**~12.4x**) | 7,109.977 ns (**~70.5x**) | 3 |
| Redis SET 250k/c5 | **1.128 s** | 12.668 s (**~11.2x**) | 14.089 s (**~12.5x**) | 23.076 s (**~20.5x**) | 3 |
| ffmpeg | **24.682 s** | 25.934 s (**~1.05x**) | 25.687 s (**~1.04x**) | 25.942 s (**~1.05x**) | 3 |
| Build ABSL | **91.774 s** | 103.184 s (**~1.12x**) | 185.942 s (**~2.03x**) | 121.826 s (**~1.33x**) | 1 |
| TensorFlow-8 | **162.182 s** | 423.370 s (**~2.61x**) | 400.048 s (**~2.47x**) | timeout >900 s (**>5.55x**) | 1 |

For the three-sample workloads, the observed min-max ranges were:

| Workload | Native | systrap | KVM | ptrace |
| --- | ---: | ---: | ---: | ---: |
| getpid | 100.683-100.861 ns | 4,055.812-4,112.542 ns | 1,252.165-1,258.244 ns | 7,018.551-7,160.184 ns |
| Redis | 1.113-1.200 s | 12.363-12.909 s | 13.938-14.208 s | 22.442-23.149 s |
| ffmpeg | 24.588-24.781 s | 25.933-26.090 s | 25.681-25.732 s | 25.834-26.033 s |

The getpid ordering shows platform mechanism most directly: KVM has the lowest
local runsc interception cost, followed by systrap and ptrace. Redis still
pays large overhead, consistent with frequent kernel-facing work. The ffmpeg
rows cluster near native because codec computation dominates. ABSL and
TensorFlow show why a syscall microbenchmark does not predict whole-application
ordering: systrap is closest to native for this ABSL cell, while KVM is slightly
faster than systrap for TensorFlow. The latter two conclusions are directional
because each engine has only one sample.

### Why does systrap rise from ~4x (239 ns blog native) to ~40x (100.789 ns local native)?

The measured discrepancy is real in the recorded numbers: this collection has
4,056.209 ns systrap against a **100.789 ns native anchor** (**~40.2x**), while
the 2023 post has 1,017 ns systrap against a **239 ns blog-native anchor**
(**~4.26x**). The experiment does **not** isolate a cause. Plausible hypotheses
include:

- the two getpid binaries may expose different instruction shapes and therefore
  different coverage of systrap's syscall-patching fast path;
- runsc, kernel, compiler, and hardware changes between the 2023 GCE VM and the
  2026 shared AMD host may change both absolute paths;
- the fixed-count harness and the blog's benchmark framework may account for
  setup and timing differently; and
- shared-host load may add noise, although the tight three-sample range suggests
  it is unlikely to explain the full tenfold ratio gap by itself.

These are hypotheses for follow-up, not findings from this dataset. The
absolute values show that both sides move: local native is 100.789 ns versus
239 ns in the blog, while local systrap is 4,056.209 ns versus 1,017 ns. No
single denominator effect explains the whole gap.

### Does the Redis cell reproduce the blog workload?

It is not established. This harness definitely invokes
`redis-benchmark -t set -n 250000 -c 5`, derives `250000 / QPS`, and records a
**1.128 s local-native anchor**. The blog describes five clients but says it
reports median per-request latency scaled by 250,000; its native bar is 17.250
s. Those descriptions may not define the same metric even though both mention
250,000 and five clients.

The leading hypothesis is a metric mismatch: aggregate QPS-derived completion
time here versus scaled median request latency in the blog. Redis/image
versions, command details, server configuration, and host differences are
additional hypotheses. Until the original command and aggregation are matched,
the local Redis ratios are internally valid but should not be compared to the
blog bars as a reproduction.

### Hermit/Reverie context

Earlier measurements on `devbig014` are useful context but are not simultaneous
with the runsc matrix. Each ratio below names its own native denominator.

| Workload/tier | Native anchor | Selected backend slowdowns |
| --- | ---: | --- |
| getpid counter2 | **91.715 ns** | LiteInst **~7.84x**, e9patch **~9.55x**, DBI **~13.7x**, SaBRe **~28.3x**, KVM **~157x**, ptrace **~187x** |
| getpid relaxed | **91.715 ns** | KVM **~233x**, e9patch **~421x**, ptrace **~434x**, SaBRe **~641x**, LiteInst **~1,010x** |
| getpid strict | **276.045 ns** | DBI **~16.9x**, SaBRe **~259x**, KVM **~486x**, e9patch **~1,840x**, LiteInst **~2,090x**, ptrace **~3,700x** |
| Redis counter2 | **1.158 s** | SaBRe **~1.37x**, DBI **~4.12x**, ptrace **~14.6x** |
| Redis relaxed | **1.158 s** | e9patch **~32.9x**, ptrace **~35.3x**, SaBRe **~75.5x** |
| Redis strict | **1.158 s** | DBI **~80.9x** |
| ffmpeg counter2/relaxed | **24.070 s** | counter2 ptrace **~1.12x**, relaxed ptrace **~4.67x**, counter2 DBI timeout **>37.4x** |

The counter2 numbers isolate a thinner interception/counting path than either
runsc or deterministic Hermit. Relaxed and strict add progressively more
determinization. The table can support overhead discussion within each stated
semantic tier; it cannot establish that one system provides another's
guarantees. ABSL is not ranked across collections because the older counter2
build was unbounded while the runsc build uses 16 jobs. TensorFlow is not
ranked because the older evidence lacks a completed matching eight-program
suite.

## Artifacts

- [`SCORECARD.tsv`](SCORECARD.tsv): machine-readable runsc and Hermit/Reverie
  rows with status, sample count, native ratio, and source.
- [`HEAD_TO_HEAD.md`](HEAD_TO_HEAD.md): expanded same-host review tables.
- [`COMPARISON.md`](COMPARISON.md): supporting qualitative comparison.
- [`results/20260802-short-matrix`](results/20260802-short-matrix): three-sample
  getpid, Redis, and ffmpeg rows.
- [`results/20260802-absl-matrix`](results/20260802-absl-matrix): one-sample
  offline ABSL build.
- [`results/20260802-tensorflow-matrix`](results/20260802-tensorflow-matrix):
  one-sample TensorFlow-8 rows and the explicit ptrace timeout.

All local and Hermit/Reverie values in the prose are derived from these tracked
artifacts. Full calculator-precision values remain in TSV for recomputation;
the narrative uses only the significant figures supported by the experiment.
