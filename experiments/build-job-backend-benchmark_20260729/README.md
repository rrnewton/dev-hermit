# Build-job backend benchmark

Date: 2026-07-29
Task: `rb-build-benchmark-harness`

This experiment measures a real parallel GCC build under Hermit while holding
the workload constant across backends. It extracts pinned zlib 1.3.1 into the
guest's private `/tmp`, runs its configure script and `make -jN`, hashes the
static library and two linked executables, and runs zlib's example program.

The default `verify` mode wraps a complete
`hermit run --backend BACKEND --strict --verify` command in
`scripts/detached-verify.rs verify-twice`. That gives two independent wall-clock
measurements and four clean package builds, while keeping compiler and Hermit
logs detached. Change only `--backend` for a head-to-head comparison.

## Reproduce

```bash
experiments/build-job-backend-benchmark_20260729/prepare.sh
experiments/build-job-backend-benchmark_20260729/run.sh \
  --hermit worktrees/274/hermit/target/release/hermit \
  --backend ptrace --jobs 8 --mode verify
```

Use `--mode evidence` for one strict run that prints the artifact hashes which
Hermit's verify summary otherwise suppresses.

## PTRACE baseline

Inputs:

- Hermit `e3067d69f972a57247a72c3cfa2624691e8439a7`, release binary SHA-256
  `caa5fdc1da5585025beadc34a468b33f95d2bff88e28e918a17c27e2b2d1a86b`.
- PTRACE backend, default log level, `--strict`, no relaxations, `make -j8`.
- GCC 11.5.0, GNU Make 4.3, GNU tar 1.35.
- zlib 1.3.1 release archive SHA-256
  `9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23`.
- Host `devbig014`, AMD EPYC 9D85, Linux
  `6.18.39-0_fbk0_hardened_0_ga43d5727b443`.

| Mode | Independent samples | Wall time (ms) | Result |
| --- | ---: | --- | --- |
| Native reference | 2 | 720, 681 | output logs and all artifacts identical |
| PTRACE strict | 2 | 13,164, 13,089 | both exit 0; all artifacts identical to each other and native |
| PTRACE strict verify | 2 (two builds each) | 32,888, 38,817 | both exit 1: deterministic-log scheduling divergence |

The two-sample median strict-build time is 13,126.5 ms versus 700.5 ms native,
an initial PTRACE/native wall-time ratio of **18.74x**. This is a baseline, not
a stable performance claim: it has only two samples and no CPU pinning. Verify
times include two package builds plus log comparison and are not throughput
samples.

Bitwise artifact determinism passes. Both independent native and PTRACE strict
builds produced:

| Artifact | SHA-256 |
| --- | --- |
| `libz.a` | `390ca410224ff94c45d7bd898791f9ad5d85e023e754a7be149668f6cdde0242` |
| `example` | `9faddacffe88f2e16f13273a7e91576fe90ae0bbbd78df30d977ff03b62a1d88` |
| `minigzip` | `3efe47d7bece7712646f7671c6311ab14eee5a4b4864f4af7594e0afac5c05df` |
| `example` output | `f310cbe7fd9b19e6a39db1d8085481e067f8fed6966519e4c192ae9a868da8c6` |

L2 execution determinism does not pass. The first verify invocation diverged at
scheduler turn 8,249 between make's `BlockedExternalContinue` jobserver path
and a compiler-child `SIGCHLD`; the second independently reproduced the same
class at turn 5,637 with the sides reversed. The nearby syscalls are GCC
`vfork` and make `pselect6`/`wait4(WNOHANG)`. This matches the earlier small
parallel-build frontier but now on a real package. PTRACE therefore establishes
the timing and artifact baseline, but does not yet meet the parent epic's
strict-verify correctness gate.

The build emits make's known clock-skew warning because extracted source mtimes
can lead Hermit's guest-visible clock by about one second. Every run starts
with no targets, exits successfully, and produces the identical complete
artifact set; the warning is preserved in the evidence rather than filtered.

Raw bounded summaries are listed in `results.csv`; full command output remains
under ignored `ignored/logs/` as required by the flood-safe runner.
