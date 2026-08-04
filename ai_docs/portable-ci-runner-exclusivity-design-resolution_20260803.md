# Portable CI under safe-ci-dag-runner: leaf/sub-DAG vs full exclusivity

Date: 2026-08-03
Task: `github-ci-use-dag-runner-exclusively`
Author: impl agent (opus-4.8)
Consumer PRs: rrnewton/hermit#1548, rrnewton/agent-utils#6

## Question

Should GitHub's authoritative **portable** CI run all compute under
`safe-ci-dag-runner` as a single **outer** scheduler (FULL exclusivity, one
runner owning the whole DAG on one machine), or should GitHub Actions stay the
**cross-machine** scheduler with the runner owning execution **within** each
shard (leaf/sub-DAG)? Decide with numbers, reporting **wall time AND
runner-minutes, before and after**.

## Answer: leaf/sub-DAG. Full exclusivity is a ~4.8x wall-time regression for
isolation the fan-out already gets for free.

### Measured baseline (green portable run 30814445191)

| metric | value |
|---|---|
| jobs (shards) | 35 |
| runner-minutes | ~118 |
| wall time | ~24.6 min |
| avg machine-parallelism (runner-min / wall) | ~4.82x |

### The two options

**Leaf/sub-DAG (chosen).** GitHub Actions schedules across ephemeral VMs exactly
as today; each shard calls `run-node.sh <lane> <nodes>` which execs
`safe-ci-dag-runner run --dag ci/dag/<lane>.json --only <nodes>`. `run --only`
runs exactly the named nodes, drops edges to steps outside the selection (their
outputs come from an upstream build job / restored artifact), and honors
intra-selection edges. Same nodes, same fan-out:

| metric | before | after |
|---|---|---|
| runner-minutes | ~118 | ~118 (unchanged; same nodes) |
| wall time | ~24.6 min | ~24.6 min (+ negligible per-shard runner startup) |
| per-node profiling | no | **yes** |
| per-node wall-clock timeout | no | **yes** |
| setsid-proof teardown | no | **yes** |

**Full exclusivity (rejected).** One runner as the outer scheduler serializes the
~4.8x of concurrently-fanned-out work onto a single 4-core hosted VM:

| metric | before | after (est.) |
|---|---|---|
| runner-minutes | ~118 | ~118 (same total compute) |
| wall time | ~24.6 min | **~2h (~4.8x regression)** |

### Why the isolation argument does not rescue full exclusivity

Each `ubuntu-latest` job runs in its **own ephemeral, isolated VM**. The job
boundary *is* the containment box: a runaway step can only harm its throwaway
machine, and GitHub's per-job timeout + VM teardown kill it cleanly. cgroup
boxing **within** a hosted VM is redundant for isolation. So full exclusivity
would pay ~4.8x wall time to gain isolation the fan-out already has for free.

cgroup boxing is load-bearing only on **shared, long-lived machines**
(self-hosted privileged CI, dev-box `validate.sh`) where multiple steps share a
host and a systemd `--user` scope exists. There the runner boxes each node and
fails closed if boxing is misconfigured.

### Boxing behavior across environments

The runner treats cgroup boxing as mandatory and FAILS CLOSED (exit 3) rather
than run advisory-only. On hosted runners there is no per-user systemd scope, so
it self-skips the cgroup re-exec; `run-node.sh` passes `--allow-cgroup-failure`
under `GITHUB_ACTIONS`/`CI` to acknowledge UNBOXED-within-VM explicitly (profiling
+ timeouts + teardown still apply). On shared machines it does NOT pass the flag,
so boxing is enforced and a misconfiguration is surfaced, not swallowed.

## Library gap fixed (not bypassed)

The only genuine library gap was a top-level `import yaml` in the Python runner's
`io.py` that made even JSON-DAG use require PyYAML — which the portable shards do
not install. Fixed by making PyYAML lazy behind cached loader/dumper factories
(agent-utils#6, `08fc605`); JSON output byte-identical, Rust unaffected, YAML
paths unchanged. Everything else the task needed (`run --only`, `--perf-dir`
profiling, cgroup boxing, `--allow-cgroup-failure`) already shipped in the runner.

## Profiling → ci-hub

Each runner shard writes per-node profiles to `$RUNNER_TEMP/hermit-ci-perf`
(`--perf-dir`). Per-step columns: `step, classification, returncode, ok,
elapsed_s, git_sha, profile_base_sha, enforcement_kind, runner_name`. The
workflow uploads them as `ci-perf-<job>[-<slug>]` artifacts (7-day retention) so
the coordinator's ci-hub poller can pull them (`gh run download -p 'ci-perf-*'`)
and append to the history store keyed by `git_sha`.
