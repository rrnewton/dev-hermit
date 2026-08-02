# Same-host head-to-head appendix

This appendix expands the comparison summarized in the
[colleague-ready mini-paper](README.md). All rows were measured on `devbig014`,
but the runsc and Hermit/Reverie collections were not simultaneous. Every
slowdown is rounded and paired with the native denominator from its own
collection.

The systems also provide different semantics. Counter2 counts interceptions,
Hermit relaxed adds syscall determinization, Hermit strict adds deterministic
thread scheduling, and runsc supplies an application-kernel sandbox.

## getpid

| Collection/tier | Native anchor | systrap | KVM | ptrace | LiteInst | DBI | SaBRe | e9patch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| runsc | **100.789 ns** | **~40.2x** | **~12.4x** | **~70.5x** | n/a | n/a | n/a | n/a |
| counter2 | **91.715 ns** | n/a | **~157x** | **~187x** | **~7.84x** | **~13.7x** | **~28.3x** | **~9.55x** |
| relaxed | **91.715 ns** | n/a | **~233x** | **~434x** | **~1,010x** | n/a | **~641x** | **~421x** |
| strict | **276.045 ns** | n/a | **~486x** | **~3,700x** | **~2,090x** | **~16.9x** | **~259x** | **~1,840x** |

Runsc used one million calls per sample. Counter2 and relaxed used 200,000;
strict used 20,000. Runsc reports three measured samples after one warmup.
Hermit/Reverie backend medians use three samples and their native anchors use
five. These are steady-state per-call costs, not equal-duration processes.

## Redis SET 250k/c5

| Collection/tier | Native anchor | systrap | KVM | ptrace | DBI | SaBRe | e9patch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| runsc | **1.128 s** | **~11.2x** | **~12.5x** | **~20.5x** | n/a | n/a | n/a |
| counter2 | **1.158 s** | n/a | n/a | **~14.6x** | **~4.12x** | **~1.37x** | n/a |
| relaxed | **1.158 s** | n/a | n/a | **~35.3x** | n/a | **~75.5x** | **~32.9x** |
| strict | **1.158 s** | n/a | n/a | n/a | **~80.9x** | n/a | n/a |

All local rows invoke 250,000 SET operations with five clients and report three
measured samples. The runsc harness and earlier Hermit harness each derive a
nominal duration from QPS. As discussed in the main paper, matching the 2023
blog's scaled-latency metric has not been established.

## ffmpeg

| Collection/tier | Native anchor | systrap | KVM | ptrace | DBI |
| --- | ---: | ---: | ---: | ---: | ---: |
| runsc | **24.682 s** | **~1.05x** | **~1.04x** | **~1.05x** | n/a |
| counter2 | **24.070 s** | n/a | n/a | **~1.12x** | timeout **>37.4x** |
| relaxed | **24.070 s** | n/a | n/a | **~4.67x** | n/a |

All successful rows produced the expected output file. Runsc has three measured
samples; the earlier Hermit application cells have one.

## Coverage-limited applications

| Workload/collection | Native anchor | Results | Status |
| --- | ---: | --- | --- |
| Build ABSL, runsc | **91.774 s** | systrap **~1.12x**, KVM **~2.03x**, ptrace **~1.33x** | One sample per engine. |
| Build ABSL, counter2 ptrace | **12.110 s** | **~16.4x** | Not ranked against runsc: this older build was unbounded, while runsc used 16 Bazel jobs/loading threads. |
| TensorFlow full eight, runsc | **162.182 s** | systrap **~2.61x**, KVM **~2.47x**, ptrace timeout **>5.55x** | One sample per engine; ptrace did not complete. |
| TensorFlow basic five, counter2 ptrace | **105.010 s** | **~8.13x** | Not ranked: runsc measured the full eight-program suite. |
| TensorFlow convolutional, counter2 ptrace | **26.470 s** | timeout **>34.0x** | Not ranked: no completed matching aggregate. |

Exact absolute measurements, statuses, repetition counts, and source paths are
in [`SCORECARD.tsv`](SCORECARD.tsv). The primary paper explains the workload
definitions, uncertainty, and cross-study hypotheses.
