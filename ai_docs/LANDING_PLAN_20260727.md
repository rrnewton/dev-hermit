# P0 Landing Plan — 105 open PRs (2026-07-27)

Source: `with-proxy ./scripts/pr_status.py` + `gh pr list --json files,statusCheckRollup`
for `rrnewton/hermit` (93 open) and `rrnewton/reverie` (12 open).
Analysis scripts: `/tmp/analyze.py`, `/tmp/analyze2.py`.

## 1. CI tally

| state | count | meaning |
|-------|------:|---------|
| green   | 69 | GitHub-hosted "Regular tests" SUCCESS (self-hosted SKIPPED) |
| red     | 27 | at least one failing check |
| none    | 9  | no CI has run (mostly reverie drafts; undraft does NOT trigger CI) |
| **total** | **105** | all "free to land" per repo policy once green |

Only **1 reverie PR is green** (rev#172). The other 11 reverie PRs are none/red.

## 2. The crux: 67 of 69 green PRs are ONE conflict cluster

Nearly every green PR is a "determinize X" change that edits a tiny set of hot files:

| hot file | # green PRs touching |
|----------|--------------------:|
| `detcore/src/procfs.rs`               | 29 |
| `detcore/src/lib.rs`                  | 24 |
| `detcore/src/syscall_classification.rs` | 22 |
| `detcore/src/syscalls/files.rs`       | 7  |

Because these PRs append arms to the same match/dispatch blocks, **same-file ==
near-certain textual merge conflict**. GitHub merges one PR at a time and each
merge to `main` invalidates every other PR's merge-base (merge-gate stale-fires,
per `pr-landing-mechanics-merge-gate-uptodate-chase`). So the cluster is **not
parallel-mergeable** — it must land as a **speculative rebase chain** (exactly
the pipeline `vision-landing-sprint` describes).

Greedy disjoint-file coloring needs **29 sequential rounds** (bounded by
procfs.rs's 29 PRs). That's the theoretical floor for "simultaneous batch merge"
— but GitHub can't batch-merge, so treat 29 as a lower bound, not a plan.

## 3. Actionable partition: 2 parallel chains + 1 free batch

The hot files split cleanly, so we can run **two speculative pipelines in
parallel** (their hot files are disjoint; they share only `validate.sh` and
`command_strict_verify.rs`, both trivially mergeable):

- **Chain A — procfs.rs (29 PRs)**: 861 903 905 907 909 910 913 914 916 917 918
  922 923 926 927 928 931 932 933 934 935 937 939 941 944 945 949 950 951
- **Chain B — lib.rs / syscall_classification.rs (25 PRs)**: 839 841 847 848 852
  853 855 857 859 860 862 869 874 876 877 881 882 887 889 890 892 895 899 901 912
- **Free — none of the 3 hot files (15 PRs)**: rev#172 868 872 880 886 894 896
  898 904 908 919 924 943 946 952

## 4. Landing batches (ordered)

### Batch 0 — land now, maximally parallel (Free set)
Up to **7 mutually file-disjoint** PRs merge with zero rebase interference:
`rev#172, her#868, her#872, her#896, her#919, her#943, her#946`.
(rev#172 is a different repo — always parallel to hermit.)
Then drain the remaining free PRs respecting their small overlap groups:
- `files.rs` group (serialize): 880 → 886 → 894 → 898 → 904
- `Cargo.toml/Cargo.lock` trio (serialize): 896 → 908 → 952
- `detcore/tests/misc/mod.rs`: 872 / 880 / 924 overlap
- singleton: 924 (also shares `syscalls/misc.rs` with 919)

### Batches 1..N — the two hot chains, run CONCURRENTLY
- Run Chain A and Chain B as two independent speculative pipelines (disjoint hot
  files → they never conflict with each other).
- **Within each chain, land oldest-PR-first** and speculatively rebase the next
  PR onto the current tip while the current PR's CI runs. Conflicts are almost
  always mechanical append-arm resolutions.
- Effective wall time ≈ `max(len A, len B)` = **29 CI cycles** if rebases stay
  clean, vs. 54 if run as a single chain.

## 5. Local validation per PR (before/at each land)
Self-hosted CI is SKIPPED in practice, so validate locally on the devserver.
Run the hosted DAG or its targeted subset:
- Fast gate: `cargo build --workspace && cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings`
- Chain A PRs: `cargo test -p hermit --test procfs_determinism` (+ the PR's added test)
- Chain B PRs: `cargo test -p hermit --test command_strict_verify -- --ignored`
  and `cargo test -p detcore --lib` (classification)
- Full hosted parity: `ci/run-dag.sh hosted -v` (reproduces the GitHub gate)
- Note: `validate.sh` cannot go fully green on a devserver
  (`validate-sh-cannot-be-green-on-devserver`); rely on hosted CI + targeted local tests.

## 6. CI wall time & test-case counts

**GitHub-hosted "Regular tests" (the real gate):**
- Wall clock: **~10–17 min** (median ~13 min). Measured: 952=13.5m, 951=10.4m,
  950=10.6m, 874=16.6m, 908=14.7m, 946=16.2m.
- Structure: `ci/dag/hosted.json` = **25 DAG steps** (~19 test/doc steps),
  ~70 min serial compressed to ~13 min by DAG parallelism.
- Test steps: regular_crates (workspace nextest), hermit_unit, detcore_unit,
  detcore_misc, detcore_parallel, hermit_integration, arbitrary_binaries, cli,
  hermit_modes, app_strict_verify, command_strict_verify,
  ignored_syscall_regressions, rr_suite_contract, dbi_parity, envelope_levels,
  strict_compat (600s cap), doctests, rustdoc.

**Self-hosted "PMU and CPUID tests" + "QEMU strict L2 boot":**
- **Consistently SKIPPED** on every sampled PR (single PMU runner bottleneck,
  `ci-capacity-single-pmu-runner-bottleneck`). Not gating in practice; `main`
  is unprotected (`self-hosted-ci-sigsegv-blocks-all-prs`).
- Structure: `ci/dag/hardware.json` = **26 DAG steps** (cpuid, pmu, kvm, rr,
  leveldb, redis, python stdlib, debugger, ptrace parity). Heavyweight; effective
  wall time in CI = 0 because skipped.

**Aggregate wall-time estimates:**
- Naive single sequential chain (54 hot PRs × 13 min): **~11.7 hrs** of CI.
- Two parallel chains (max 29 × 13 min): **~6.3 hrs** of CI.
- Plus Batch 0 free drain: ~30–45 min.
- Speculative rebasing overlaps rebase/build work with CI but does not reduce
  the merge-gate serialization floor; realistic target **~6–7 hrs** for all 69.

## 7. Not-now buckets
- **27 red PRs** need CI repair before entering any pipeline (out of immediate
  scope). Includes rev#175/#163/#156 and hermit#968/#794/#775 (which already
  carry post-facto-review) plus 21 draft reds.
- **9 none-CI PRs** (mostly reverie#173–182 drafts): CI must be triggered by a
  push (undraft does NOT trigger CI, `undraft-does-not-trigger-ci`).
