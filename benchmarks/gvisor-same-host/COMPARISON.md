# Same-host comparison notes

The [mini-paper](README.md) is the primary result. The rounded tables in
[`HEAD_TO_HEAD.md`](HEAD_TO_HEAD.md) provide the expanded numeric appendix, and
[`SCORECARD.tsv`](SCORECARD.tsv) retains full precision for recomputation.

## What the measurements support

- **Interception microbenchmark:** runsc KVM is **~12.4x**, systrap **~40.2x**,
  and ptrace **~70.5x** the runsc collection's **100.789 ns native getpid
  anchor**. This ordering is specific to the tight syscall loop.
- **Syscall-heavy application:** Redis is **~11.2x-20.5x** the runsc
  collection's **1.128 s native anchor**, depending on platform. The metric is
  `250000 / QPS`, not end-to-end wall time.
- **CPU-heavy application:** all runsc ffmpeg platforms remain
  **~1.04x-1.05x** the **24.682 s native anchor**, consistent with codec work
  amortizing interception overhead.
- **Long mixed workloads:** ABSL ranges from **~1.12x to ~2.03x** its
  **91.774 s native anchor**. Completed TensorFlow engines are **~2.47x-2.61x**
  its **162.182 s native anchor**, while ptrace times out above **5.55x**. These
  are one-sample observations.

Getpid does not predict whole-application ordering. Systrap is closest to
native for this bounded ABSL build; KVM is slightly faster than systrap for the
TensorFlow suite; all three ffmpeg platforms cluster together.

## What the measurements do not support

- They do not explain why local systrap getpid is **~40.2x** its
  **100.789 ns native anchor** while the 2023 blog reports **~4.26x** its
  **239 ns blog-native anchor**. Instruction shape, fast-path coverage,
  runtime/kernel/hardware differences, harness accounting, and shared-host load
  are hypotheses, not established causes.
- They do not establish that the Redis cell reproduces the blog metric. This
  harness derives `250000 / QPS` and records a **1.128 s native anchor**; the
  blog describes median request latency scaled by 250,000 and shows a 17.250 s
  native bar. The aggregation may differ.
- They do not rank ABSL across the runsc and counter2 collections because the
  parallelism bounds differ.
- They do not rank the TensorFlow collections because no completed matching
  eight-program Hermit/Reverie aggregate exists.
- They do not establish semantic equivalence. Counter2, deterministic Hermit,
  and runsc enforce different execution and isolation properties.

## Hermit/Reverie interpretation

The earlier same-machine rows are useful for locating overhead, not declaring a
single winner. For example, counter2 LiteInst is **~7.84x** and e9patch is
**~9.55x** their **91.715 ns native getpid anchor**, while strict DBI is
**~16.9x** its separate **276.045 ns strict-native anchor**. For Redis,
counter2 SaBRe is **~1.37x** and DBI **~4.12x** their **1.158 s native anchor**.
Adding syscall determinization and deterministic scheduling changes both the
cost and the guarantee, so comparisons should remain within the named tier.
