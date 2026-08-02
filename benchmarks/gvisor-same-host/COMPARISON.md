# Same-host backend comparison

All rows below were measured on `devbig014.atn7.facebook.com`. The gVisor rows
use official runsc release `20260727.0`; no gVisor blog value participates in a
ratio or ranking.

## Local runsc results

| Workload | Native | systrap | KVM | ptrace |
| --- | ---: | ---: | ---: | ---: |
| getpid | 100.789 ns | 4,056.209 ns (40.245x) | 1,252.276 ns (12.425x) | 7,109.977 ns (70.543x) |
| Redis SET 250k/c5 | 1,128.000 ms | 12,668.003 ms (11.230x) | 14,089.000 ms (12.490x) | 23,075.993 ms (20.457x) |
| ffmpeg | 24,681.582 ms | 25,933.676 ms (1.051x) | 25,686.832 ms (1.041x) | 25,941.522 ms (1.051x) |
| Build ABSL | 91,773.778 ms | 103,183.537 ms (1.124x) | 185,941.937 ms (2.026x) | 121,825.586 ms (1.327x) |
| TensorFlow-8 | 162,181.537 ms | 423,369.988 ms (2.610x) | 400,047.639 ms (2.467x) | timeout >900,000 ms (>5.549x) |

getpid, Redis, and ffmpeg are medians of three measured repetitions after one
warmup. ABSL and TensorFlow are one measured repetition. A TensorFlow result is
accepted only after all eight programs emit the final `TF_OK` marker, so the
ptrace timeout is not summarized as a completed time.

## Hermit and Reverie context

The existing Hermit/Reverie evidence was also collected on this host, but in
earlier runs. The ratios therefore normalize each row to the native sample from
its own run; they remove the invalid cross-machine comparison but do not make
the runs simultaneous or the systems semantically equivalent.

| Workload | Tier/backend | Local-native slowdown |
| --- | --- | ---: |
| getpid | counter2 ptrace / KVM | 186.743x / 157.428x |
| getpid | counter2 LiteInst / DBI / SaBRe / e9patch | 7.838x / 13.725x / 28.272x / 9.547x |
| getpid | relaxed ptrace / KVM / LiteInst / SaBRe / e9patch | 434.364x / 232.564x / 1,010.829x / 641.102x / 421.328x |
| getpid | strict ptrace / KVM / LiteInst / DBI / SaBRe / e9patch | 3,697.296x / 485.569x / 2,089.840x / 16.879x / 259.142x / 1,839.454x |
| Redis SET 250k/c5 | counter2 ptrace / DBI / SaBRe | 14.611x / 4.118x / 1.370x |
| Redis SET 250k/c5 | relaxed ptrace / SaBRe / e9patch | 35.273x / 75.478x / 32.915x |
| Redis SET 250k/c5 | strict DBI | 80.943x |
| ffmpeg | counter2 ptrace / relaxed ptrace | 1.116x / 4.672x |
| ffmpeg | counter2 DBI | timeout >37.391x |
| Build ABSL | counter2 ptrace | 16.387x |
| TensorFlow basic five | counter2 ptrace | 8.127x |
| TensorFlow convolutional | counter2 ptrace | timeout >34.001x |

The same-host correction changes the conclusions. For getpid, counter2
LiteInst and e9patch have lower normalized overhead than local runsc KVM,
systrap, and ptrace; DBI is close to runsc KVM. For Redis, counter2 SaBRe and
DBI have lower normalized overhead than local runsc systrap, while counter2
ptrace is between local runsc KVM and ptrace. For ffmpeg, all three runsc
platforms are near 1.05x and counter2 ptrace is 1.116x.

ABSL is not ranked across the two collections: the new harness pins 16 Bazel
jobs/loading threads to avoid exhausting this shared 316-CPU host, while the
older counter2 row was unbounded. TensorFlow also has no completed full
eight-program counter2 row, so only the local runsc matrix is comparable for
that complete suite. `SCORECARD.tsv` retains every numeric row and its source.
