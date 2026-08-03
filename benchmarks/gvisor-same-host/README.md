# Same-host gVisor platform overhead

## Introduction

gVisor is an application-kernel sandbox whose `runsc` runtime can intercept
guest system calls through systrap, KVM, or ptrace. This experiment runs five
workloads under those three platforms and native Linux on one machine. It asks
whether a current official runsc release reproduces the performance reported in
gVisor's 2023 [systrap release post][systrap-blog]. Every local slowdown uses the
native result from the same collection as its denominator.

**If we trust our gVisor numbers on this machine, Reverie achieves better
performance with its best available thin `counter2` backends[^hermit-cost] than
the fastest runsc platform on two of the three directly comparable workloads.**
For `getpid`, `counter2` LiteInst
at **719 ns/call** (counter2 native: **91.7 ns/call**) is **~1.74x faster** than
runsc KVM at **1.25 us/call** (runsc native: **101 ns/call**) and **~5.64x
faster** than runsc systrap at **4.06 us/call**. For Redis, `counter2` SaBRe at
**1.59 s** (counter2 native: **1.16 s**) is **~8.0x faster** than the fastest
runsc result, systrap at **12.7 s** (runsc native: **1.13 s**). For ffmpeg,
`counter2` ptrace at **26.9 s** (counter2 native: **24.1 s**) is within **~4.6%**
of the fastest runsc result, KVM at **25.7 s** (runsc native: **24.7 s**), but
is slightly slower. These are performance comparisons, not claims of semantic
equivalence: `counter2` counts interceptions, whereas runsc provides an
application-kernel sandbox.

The two collections ran on the same host but at different times. The
`counter2` `getpid` cells used 200,000 calls per sample and the runsc cells used
1,000,000; both report three-sample medians, and runsc also used one warmup.
Both Redis collections report `250000 / QPS` for 250,000 SET operations and
five clients, and the ffmpeg comparison uses the same input with three measured
samples per engine. ABSL is not ranked because the `counter2` build used
unbounded jobs while runsc used 16; TensorFlow is not ranked because no matching
eight-program Reverie result completed. An earlier, different 40-million-call
steady-state study on this host (two warmups and nine measured samples) found
**1.01 us/call** for gVisor KVM, **8.08 us/call** for gVisor systrap, **29.3
us/call** for Reverie KVM, and **40.5 us/call** for Reverie ptrace. That study is
a qualitative sanity check, not pooled evidence for the ratios above.[^v2-check]

It does not reproduce the blog's gVisor results. The clearest mismatch is
`getpid`: local systrap is **~40.2x** its native anchor, while the post reports
**~4.26x**. The local **100.789 ns/call native anchor** is not a direct timing of
one syscall. Each sample launches a program that issues exactly 1,000,000 raw
`getpid` syscalls, measures the complete process wall time, and divides by
1,000,000; the reported anchor is the median of three such quotients. The runner
checks process completion every 50 ms, so this is a coarse batch-average metric
that includes launch cost amortized over the batch, not a Criterion-style
line-fit or a precise single-call latency.

Redis also does not establish a reproduction: the local metric is
`250000 / QPS`, whereas the blog describes scaled median request latency. The
QPS-derived native anchor is **1.128 s**, but the full native sample, including
Redis server startup, readiness, benchmark execution, and shutdown, ran for a
median **1.302 s**. Full sample medians were **12.917 s** for systrap,
**14.520 s** for KVM, and **23.328 s** for ptrace. The remaining workloads show
that local runsc overhead varies sharply with workload, from about
**1.04x-1.05x** native for ffmpeg to multi-fold slowdowns for ABSL and
TensorFlow. Hermit/Reverie measurements later in this report are separate
same-host context; they do not change the finding that the gVisor numbers here
failed to reproduce the blog.

[^hermit-cost]: `counter2` is not Hermit. Hermit is currently much slower on
    these cells even in relaxed mode. The best completed `getpid` result in
    **Hermit relaxed** mode is KVM at **21.3 us/call** (relaxed native: **91.7
    ns/call**), **~17x slower** than runsc KVM at **1.25 us/call**; the best
    **Hermit strict** result is DBI at **4.66 us/call** (strict native: **276
    ns/call**), **~3.72x slower** than that same runsc result. For Redis, the
    best relaxed result is e9patch at **38.1 s** and the best strict result is
    DBI at **93.7 s** (Hermit native: **1.16 s**), respectively **~3.01x** and
    **~7.40x slower** than runsc systrap at **12.7 s**. For ffmpeg, relaxed
    ptrace takes **112 s** (Hermit native: **24.1 s**), **~4.38x slower** than
    runsc KVM at **25.7 s**; no strict ffmpeg cell completed. Relaxed mode
    determinizes syscalls without deterministic thread scheduling; strict mode
    adds deterministic scheduling. The best available backend differs by tier,
    so none of these relaxed measurements is presented as strict. No comparable
    relaxed or strict ABSL or TensorFlow cell completed.

[^v2-check]: See the tracked
    [steady-state v2 benchmark](../../experiments/gvisor-reverie-benchmark_20260725/README.md).
    Its `getpid` native median was **4.14 s** for 40,000,000 calls; the quoted
    values are `(backend median - native median) / 40,000,000`.

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

| Workload | Metric and workload | Resource and duration intuition |
| --- | --- | --- |
| getpid | End-to-end wall time for one process issuing 1,000,000 raw syscalls, divided by 1,000,000; a checksum gates completion. | Almost no useful work per call; emphasizes interception and dispatch. Complete batches take about 0.10 s native and 1.25-7.11 s under runsc. |
| Redis SET 250k/c5 | `redis-benchmark -t set -n 250000 -c 5`; the reported value is `250000 / QPS`, not process wall time. | Event-loop, futex, timer, loopback, and syscall-heavy throughput. Complete samples run for 1.30-23.33 s depending on engine. |
| ffmpeg | End-to-end wall time to transcode the pinned video with libx264 `veryslow`; output size gates completion. | CPU-heavy codec work amortizes syscall overhead; completed samples run for about 25-26 s. |
| Build ABSL | End-to-end wall time for offline `bazel build //...`, with build jobs and loading threads fixed at 16. | Process creation, compiler execution, filesystem metadata, and bounded parallelism; samples run for about 92-186 s. |
| TensorFlow-8 | End-to-end wall time to execute eight TensorFlow 1.x examples sequentially and emit `TF_OK`. | Python startup, threads, filesystem activity, and CPU numerical kernels; completed samples run for about 162-423 s. |

## Results and discussion

### Local runsc matrix

**Reproduction result: failed.** The local systrap `getpid` ratio is about ten
times the blog's ratio, and the Redis measurements do not use a demonstrably
equivalent metric. The table below is valid as a same-run local comparison, but
not as a reproduction of the blog's absolute values or ratios.

Absolute medians are shown before slowdown. The **native anchor column is the
denominator for every ratio in its row**.

| Workload | Native anchor | systrap | KVM | ptrace | Samples |
| --- | ---: | ---: | ---: | ---: | ---: |
| getpid | **100.789 ns/call (batched)** | 4,056.209 ns (**~40.2x**) | 1,252.276 ns (**~12.4x**) | 7,109.977 ns (**~70.5x**) | 3 |
| Redis SET 250k/c5 | **1.128 s (250k/QPS)** | 12.668 s (**~11.2x**) | 14.089 s (**~12.5x**) | 23.076 s (**~20.5x**) | 3 |
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

### getpid does not reproduce the blog

This collection records 4,056.209 ns/call for systrap against its batched
**100.789 ns/call native anchor** (**~40.2x**). The 2023 post reports 1,017 ns
against a **239 ns blog-native anchor** (**~4.26x**). Both the absolute values
and the ratio differ substantially.

The experiment does not isolate why. Candidate hypotheses are different guest
instruction shapes and syscall-patching coverage; changes in runsc, kernels,
compilers, or hardware; different accounting between this fixed-count,
50-ms-polled batch harness and the blog's benchmark framework; and shared-host
load. These are follow-up hypotheses, not explanations established by this
dataset.

### Does the Redis cell reproduce the blog workload?

It is not established. This harness definitely invokes
`redis-benchmark -t set -n 250000 -c 5`, derives `250000 / QPS`, and records a
**1.128 s local-native anchor**. The full native sample actually ran for a
median **1.302 s** including server lifecycle; the corresponding systrap, KVM,
and ptrace wall durations were **12.917 s**, **14.520 s**, and **23.328 s**. The
blog describes five clients but says it reports median per-request latency
scaled by 250,000; its native bar is 17.250 s. Those descriptions may not define
the same metric even though both mention 250,000 and five clients.

The leading hypothesis is a metric mismatch: aggregate QPS-derived completion
time here versus scaled median request latency in the blog. Redis/image
versions, command details, server configuration, and host differences are
additional hypotheses. Until the original command and aggregation are matched,
the local Redis ratios are internally valid but should not be compared to the
blog bars as a reproduction.

### Hermit/Reverie context

Earlier measurements on `devbig014` are useful context but are not simultaneous
with the runsc matrix and are not part of the gVisor reproduction result. They
measure different execution semantics. Each ratio below names its own native
denominator.

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
