# Dual-verify: portable-DAG per-node memory caps

Task: `memory-caps-must-scale-with-job-count-not-be-constants`
Hermit @ 0f891e43 (== origin/main). Box: devbig014, 316 cores.

## Defect fixed
10 cpu-bound portable-DAG nodes ran `CARGO_BUILD_JOBS=${THIRD_PARTY_BUILD_JOBS:-$(nproc)}`
(job count SCALES with the machine: 32 third-party or nproc=316) against a CONSTANT
`hard_mem_max_bytes`. On a large box, parallelism outgrew the cap -> deterministic OOM.
`build.dbi_release` (release C++ of drcachesim, ~522 MiB/proc) OOMed EVERY full-validate PR.

## Fix
Each node: pin `CARGO_BUILD_JOBS=8` LITERAL in the cmd (memory saturates by j8 per the
per-width model; the cap and the parallelism can no longer drift), and set
`hard_mem_max_bytes` from the cgroup-RECORDED peak@j8 with in-file derivation
(rss_baseline / method / date / SAMPLED-vs-RECORDED). Peaks: results.csv.

## Enforcement mechanism (verified against the runner, not inferred)
`agent-utils safe_ci_dag_runner`: `hard_mem_max_bytes` is written VERBATIM (no factor, no
floor) to the step's cgroup-v2 `memory.max`, with `memory.swap.max=0` -> exceed = kernel
OOM-kill. `systemd-run --user -p MemoryMax=<cap> -p MemorySwapMax=0` writes those identical
two cgroup-v2 files, so the verification exercises the SAME kernel primitive.
(cgroup.py:1001-1008, sizing.py:20-26, scheduler.py:361.)

## NEGATIVE leg — plant workload > cap, confirm KILL  (verify-negative.csv)
Boxed a page-touching allocator at each node's exact cap, target = cap+512MiB. ALL 10
OOM-killed (rc137). The mechanism is not permissive and each cap value actually bites.

## POSITIVE leg — genuine workload at cap, confirm NO KILL  (verify-positive.csv)
Each genuine node cmd re-run boxed at its NEW cap against the warm shared target
(/tmp/mc-target-j4hi; DAG-representative — every node depends on build.workspace which
warms the target). ALL 10 completed Result=success, NO OOM-kill, incl. the flagship
build.dbi_release at 8.5 GiB. Positive-leg peaks are warm/incremental lower bounds; the
cold-headroom argument rests on the sweep's uncapped cgroup-RECORDED peaks < cap (every
node: cap > measured peak, min +25% on dbi_release).

## Caps changed (peak -> cap, from results.csv sweep)
| node | peak@j8 | old cap | new cap | why |
|---|---|---|---|---|
| build.workspace | 6.0G | 8G | 8.0G | kept at floor; true-cold root |
| build.dbi_release | 6.8G | 8G | 8.5G | RAISED: 6.8*1.25=8.16 > 8 -> old cap BOUND = the OOM |
| build.sabre_release | 0.92G | 6G | 2.5G | lowered (over-provisioned) |
| lint.clippy | 4.0G | 8G | 5.5G | lowered |
| doc.doctests | 0.20G | 5G | 2.0G | lowered |
| doc.rustdoc | 1.1G | 5G | 3.0G | lowered |
| test.regular_crates | 2.0G | 6G | 3.5G | lowered |
| build.flaky_harnesses | 0.14G | 5G | 2.0G | lowered |
| test.hermit_unit | 4.6G | 5G | 6.5G | RAISED: old 5G left only 0.4G over peak = too tight |
| test.detcore_unit | 1.8G | 5G | 3.5G | lowered |

Two caps RAISED because the old constant BOUND (dbi_release OOMed; hermit_unit had 0.4G
headroom); the rest LOWERED to cut over-provisioning. Lowering only makes a cap catch a
regression SOONER, never hides one; positive leg confirms none newly binds the real workload.

## Out of scope (not in the sweep; caps unchanged)
- `build.liteinst_runtime_release`, `build.manifest_guests`: script-wrapped, no visible
  nproc-scaled cargo jobs; not measured -> no cgroup-RECORDED peak to back a cap.
- `test.detcore_parallel`, `test.detcore_misc`: execution already thread-capped (--test-threads
  4 / 1); build phase is incremental on the warm workspace target. `detcore_misc` currently
  LIVELOCKS (reverie#355 unfixed) -> any constant derived from its present behaviour would be
  calibrated against a defect. cpu_timeout derivation is a separate task.
- `privileged.json build.privileged_tests` shares the shape but is PRIVILEGED-lane (needs
  /dev/kvm) -> unmeasurable in this sandbox; note separately.

Reproduce: `bash dual_verify.sh` (negative), `bash verify_positive.sh` (positive).
