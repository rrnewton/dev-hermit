# Same-host gVisor runsc benchmark

This benchmark corrects the invalid cross-host comparison in the earlier
gVisor systrap blog reproduction. It runs the same getpid, Redis SET 250k/c5,
ffmpeg, ABSL, and TensorFlow workloads on this host under:

- native execution (`podman` for image workloads, direct for getpid);
- `runsc --platform=systrap`;
- `runsc --platform=kvm`; and
- `runsc --platform=ptrace`.

Run the entire workflow through the external-network proxy:

```bash
with-proxy benchmarks/gvisor-same-host/run.rs
```

The rust-script self-provisions official runsc release `20260727.0`, verifies
its pinned SHA-512, pulls four immutable benchmark image digests, exports their
root filesystems, downloads and SHA-256-verifies the ABSL build archives,
compiles the tracked getpid guest, executes bounded samples, and writes small
text results under `results/`. Large binaries, downloads, image roots,
container exports, state directories, and raw logs live under
`ignored/gvisor-runsc-same-host/`.

Useful narrower runs:

```bash
with-proxy benchmarks/gvisor-same-host/run.rs --provision-only
benchmarks/gvisor-same-host/run.rs --workloads getpid,redis \
  --platforms systrap,kvm,ptrace
```

The default methodology uses one warmup plus three measured samples for
getpid, Redis, and ffmpeg. ABSL and TensorFlow use no warmup and one measured
sample because each cell is expensive. Every cell has a workload-specific
success marker. Redis reports `250000 / QPS`, excluding server startup as the
blog does. Other application rows report end-to-end wall time, including
runsc/container startup; getpid reports wall time divided by one million raw
syscalls.

ABSL runs offline with Bzlmod disabled and 16 Bazel build/loading threads. The
fixed bound avoids exhausting the process limit on this shared 316-CPU host and
is applied identically to native, systrap, KVM, and ptrace.

Published local evidence:

- [`HEAD_TO_HEAD.md`](HEAD_TO_HEAD.md) is the clean same-host review table;
- [`COMPARISON.md`](COMPARISON.md) is the human-readable same-host table;
- [`SCORECARD.tsv`](SCORECARD.tsv) is the machine-readable cross-backend table;
- [`results/20260802-short-matrix`](results/20260802-short-matrix) contains the
  three-repetition getpid, Redis, and ffmpeg run;
- [`results/20260802-absl-matrix`](results/20260802-absl-matrix) contains the
  offline ABSL build; and
- [`results/20260802-tensorflow-matrix`](results/20260802-tensorflow-matrix)
  contains TensorFlow-8, including the explicit ptrace timeout.

Blog numbers are historical context only. They are never used to rank a local
backend. Only same-host rows are compared. Earlier Hermit/Reverie rows were
collected at different times, so the scorecard uses each collection's own
native baseline and states remaining command/coverage differences explicitly.
