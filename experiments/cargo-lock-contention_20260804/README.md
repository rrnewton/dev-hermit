# Cargo lock contention in the portable DAG

## Result

Cargo target locking materially suppresses outer-DAG scaling in the compile/test
fan-out, but the lock also deduplicates compilation. On the seven nodes valid in
both layouts, a shared target scaled only **1.25x** from width 1 to 7; per-node
targets scaled **2.90x** and reduced width-7 wall by **42.5%**, at the cost of
**78% more CPU**.

The whole-DAG **4.24x** figure remains a topology-derived upper bound. It was not
a measured lock-free ceiling. This experiment explains why measured scaling can
fall well below that bound; it does not erase the later serial guest-test tail.

## Provenance

- Date: 2026-08-04
- Controlled A/B Hermit head: `451f9953b5b84c0cba52c6cb57c2614256fdfdb3`
- Runner gitlink: agent-utils `089e4b47563c6ec2d46555dcdbd7b027de6a5b1a`
- Inner build width: `CARGO_BUILD_JOBS=8`
- Host: 316 logical CPUs; load-probe before the experiment reported 18.4% CPU
  executing and 441.8 GiB available memory.

The original observation came from PR #1592 at
`8078d089e3c8c968e9f416b215f027a342b847ac`, retained in
`/tmp/hermit-validate.Xci2Xl.log`. That run did not configure either Cargo path;
its paths were:

- `CARGO_HOME=/home/newton/.cargo` (default), package lock
  `/home/newton/.cargo/.package-cache`.
- `CARGO_TARGET_DIR=/home/newton/work/dev-hermit/worktrees/e9patch/hermit/target`
  (default), build lock `target/debug/.cargo-build-lock`.

## Method

The retained PR #1592 full-profile log named nine concurrent portable nodes with
a package-cache wait and seven with a build-directory wait. The controlled A/B,
run later at the separately recorded Hermit head above, uses the seven Cargo
nodes whose behavior remains valid when `CARGO_TARGET_DIR` changes:
`test.rr_suite_contract`, `doc.doctests`, `test.detcore_unit`,
`build.flaky_harnesses`, `lint.clippy`, `doc.rustdoc`, and
`test.regular_crates`.

Each arm starts from an identical Btrfs snapshot of a target aligned to the exact
Hermit SHA. All arms share one experiment-private clone of Cargo home, excluding
other agents while retaining intra-arm package-cache contention. Shared arms use
one target snapshot; isolated arms use one snapshot per node. The tracked
[`cargo`](cargo) wrapper runs the real Cargo under `strace -f -T -y`, making lock
paths and blocked syscall durations observable. Widths are outer DAG widths;
every node retains inner `K=8`.

`blocked_pct` is occupancy-weighted: summed completed blocking `flock` duration
divided by summed node wall. Lock waits overlap, so summing them and dividing by
lane wall would be invalid. The direct lane-wall effect is the width-7 A/B.

## Results

| layout | width | wall | speedup vs own w1 | CPU (user+sys) | lock-blocked node wall |
|---|---:|---:|---:|---:|---:|
| shared target | 1 | 272.3 s | 1.00x | 1425.2 s | 0.0% |
| shared target | 7 | 217.7 s | 1.25x | 1031.3 s | 75.2% |
| per-node target | 1 | 361.7 s | 1.00x | 1851.0 s | 0.0% |
| per-node target | 4 | 124.9 s | 2.90x | 1851.4 s | 0.7% |
| per-node target | 7 | 125.1 s | 2.89x | 1836.6 s | 2.1% |

At shared width 7, `strace` measured 665.6 node-seconds waiting on
`.cargo-build-lock` and 7.84 node-seconds on `.package-cache`, out of 895.1
aggregate node-seconds. Per-node targets eliminated the build-lock wait; the
shared package cache remained and contributed only 7.51 node-seconds.

Two shared-width-4 attempts are retained but excluded: unrelated fleet work
raised median external demand to 106-114 cores and CPU PSI to 51-71%. Reporting
either as the curve midpoint would conflate host contention with Cargo locking.

## Interpretation

The hypothesis is confirmed for this fan-out: shared target locking, not graph
shape alone, prevents width from turning into parallel work. Per-node target
isolation moves the measured ceiling, but it is not free: it recompiles artifacts
that the shared target would reuse. A production design should therefore group
compatible nodes or build once and run prebuilt test artifacts, rather than give
all 47 nodes independent cold targets.

Raw logs, per-step profiles, and syscall traces were retained locally under
`/tmp/cargo-lock-contention-8078d089-20260804/`; the compact measurements are in
[`results.tsv`](results.tsv).
