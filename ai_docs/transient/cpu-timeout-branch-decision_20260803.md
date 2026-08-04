# Decision: which `cpu_timeout` branch for safe-ci-dag-runner

**Task:** `resolve-two-competing-cpu-timeout-branches` (P0). **Author:** impl agent, opus-4.8.
**Date:** 2026-08-03. **Scope:** recommendation only — do NOT land either branch.
All claims are `git diff`/`git show` against agent-utils pin `1c0e9c3`.

## Recommendation (one line)

**Adopt the cgroup-poll design (`codex/cpu-time-timeout`) as the single
mechanism, port its enforcement into the Rust engine's existing per-step monitor
loop, and salvage two parts from the rlimit branch (the `default_cpu_timeout`
config knob + its real enforcement test). Retire `origin/ci/cpu-time-rlimit-timeout`.**

## What each branch ACTUALLY delivers (not what its name suggests)

Both add a `Step.cpu_timeout` field (seconds; `0` = off) and serialize it only
when non-default, so existing DAGs stay byte-identical. Beyond that they diverge.

| Aspect | `codex/cpu-time-timeout` (f43c3ea) | `origin/ci/cpu-time-rlimit-timeout` (f1a61a1) |
|---|---|---|
| Enforcement mechanism | Poll the step's cgroup `cpu.stat usage_usec` (user+sys) at 1 Hz in the existing `_monitor` loop; on breach `reap()` the whole subtree via `cgroup.kill` | Wrap the step as `prlimit --cpu=soft:hard -- bash -c …`; kernel raises `SIGXCPU` at soft=`cpu_timeout`, `SIGKILL` at hard=`cpu_timeout+5` |
| Which engine enforces | **Python only.** Rust carries the field for schema/round-trip parity and explicitly does NOT enforce (`model.rs`: "the Rust scheduler does not enforce CPU-time budgets yet") | **Rust only.** Python is **completely untouched** (0 files) — it parses/ignores `cpu_timeout` |
| Accounting scope | **Aggregate subtree** — cgroup sums all processes in the step's cgroup, so a multi-process fan-out (make -jN, cargo test) IS bounded | **Per-process** — its own doc admits: "catches the dominant-CPU process; a multi-process fan-out is still backstopped by the wall-clock `timeout`" |
| Breach reason string | `CPU-TIMEOUT >{N}s cpu` | `CPU-TIMEOUT >{N}s cpu-time (RLIMIT_CPU)`, returncode `-24` |
| Precedence vs OOM/wall | Explicit + tested: OOM > CPU-timeout > timeout > … | CPU-timeout only when wall didn't fire; SIGXCPU(-24) ≠ OOM(SIGKILL) so no conflict |
| DAG-level default | none (per-step only) | **`default_cpu_timeout`** DAG-wide knob |
| External dependency | none (reads a file the runner already reads every second) | **`prlimit(1)`** (util-linux) must be on PATH |
| Kill robustness | `cgroup.kill` = unconditional SIGKILL to whole subtree (no signal-catch escape) | soft SIGXCPU catchable; hard SIGKILL +5s CPU later |
| Enforcement granularity | 1 Hz poll → up to ~1 poll-interval overshoot | kernel-exact at the CPU-second boundary |
| Test coverage | round-trip/serialization test only (enforcement untested — needs cgroups in CI) | **real functional test** `cpu_budget_trips_before_generous_wall` (busy-loop, cpu=1s, wall=60s, asserts SIGXCPU + reason + wall<30s) |
| perflog | adds `cpu_timed_out` column | no perflog change |

**Both currently produce engine divergence, in opposite directions** — codex
enforces in Python but not Rust; rlimit enforces in Rust but not Python. Neither
is landable as-is for a repo whose correctness model is "two engines,
differentially identical." The real question is which mechanism becomes THE
mechanism in BOTH engines.

## The task's central premise was stale — and it flips the tradeoff

The task frames the choice as *"commit to cgroups-everywhere (implement them in
Rust first) vs. want enforcement on the current Rust path immediately, because
**the Rust engine does not implement cgroups**."* That premise is **false at this
pin** (established by `rust-runner-lacks-cgroups-and-perf` / memory
`safe-ci-dag-runner-cgroups-perf-premise-stale`):

- The Rust engine **boxes every step in a per-step child cgroup by default**
  (`cgroup.rs`, boxing ON by default).
- Its `CgroupManager` trait **already exposes `cpu_stats(tag)` reading `cpu.stat`**
  (trait lines 72-73; impl 512-517) and already calls it at step end.
- It **already runs a 1 Hz per-step monitor loop** (`MONITOR_INTERVAL`, polling
  `thread_count`) — the exact place codex's Python enforcement lives.
- The **rlimit branch's own Rust code proves this**: line 477 — "prepare_command
  has created the step's child cgroup, so cpu.pressure is readable." The rlimit
  branch runs `prlimit` *inside* a cgroup that already carries the CPU accounting
  the codex approach uses. It layers a second enforcement mechanism on top of the
  first.

So rlimit's entire reason to exist ("works WITHOUT cgroups, gives enforcement on
the Rust path today") collapses: the Rust path already has cgroups, and porting
codex's `usage_usec` check into the existing Rust monitor loop is a few lines —
yielding ONE mechanism, identical across both engines, which is the LINEAR-repo
ideal.

## Against the owner's breach-error spec (the task called this "close to decisive")

Spec: name the limit + value, report BOTH clocks, peak-vs-cap, the two actions,
the specific step+graph, non-zero exit.

- **Attribution (which step):** both satisfy it — each step has its own cgroup
  (codex) / its own prlimit wrap (rlimit); a breach maps unambiguously to the step tag.
- **Report both clocks + peak-vs-cap:** this is where they part.
  - codex **has the data in hand**: at the trip it reads `cpu_used_s =
    usage_usec/1e6` (observed CPU peak) alongside wall `elapsed_s`, and the same
    cgroup already surfaces `memory.peak` / `cpu.pressure`. Today it stores only a
    bool, but enriching the reason to "used Xs CPU of Ns budget (wall Ys)" is a
    few lines on data it already collects.
  - rlimit **structurally cannot** report observed CPU used: `prlimit` enforces
    in-kernel and the runner sees only exit `-24`. Reporting peak-vs-cap would
    require adding `getrusage`/`/proc` readback it does not have.
- Net: the cgroup design is architecturally aligned with the owner's richer
  breach report; rlimit is architecturally opposed to it. This confirms the
  task's hypothesis.

## Honest steelman of rlimit (where it genuinely wins)

- **Kernel-exact precision** vs codex's ~1s poll overshoot. Real, but for CI CPU
  budgets (typically ≥10s) the overshoot is ≤10% and the interval is tunable; the
  aggressive-cpu/generous-wall policy tolerates it. Not decisive.
- **Ships a real enforcement test**; codex ships only a round-trip test. Real
  quality point — but a test-coverage artifact, not an architectural advantage.
  Salvage it (below).
- **Works when boxing is off.** But boxing is ON by default in both engines now;
  the unboxed path is the explicit `--allow-cgroup-failure` escape hatch on which
  the user has already opted out of resource enforcement. Enforcing a budget there
  is arguably wrong, not a feature.

None outweighs: false founding premise, per-process (vs aggregate) accounting that
misses the multi-process CI steps that matter most, no breach-telemetry readback,
a new external dependency, and a second enforcement mechanism where one suffices.

## The single merged design (what should actually land, later, by ONE owner)

1. codex's cgroup `cpu.stat usage_usec` poll as the mechanism, in **both** engines
   — port enforcement into the Rust `run_step` monitor loop (reuse existing
   `cpu_stats` + cgroup kill; ~a dozen lines).
2. Add rlimit's **`default_cpu_timeout`** DAG-level knob (codex lacks it).
3. Enrich the breach reason to the owner's spec: observed CPU used vs cap + wall
   clock (codex already reads both).
4. Adapt rlimit's enforcement test to the cgroup path (boxed test env).
5. Keep the `cpu_timed_out` perflog column + OOM > CPU-timeout > timeout precedence.

Result: one mechanism, symmetric across engines, differential-safe, breach-spec
compliant — landed as a single linear change.

## How the losing branch is retired (so it does not rot into a third stranded branch)

- **`origin/ci/cpu-time-rlimit-timeout`**: close/abandon with a note pointing to
  this decision and to the chosen codex line; after its `default_cpu_timeout`
  plumbing and its enforcement test are salvaged into the merged change, delete
  the remote branch. Its author should be told the RLIMIT_CPU *mechanism* is not
  adopted and why (stale premise + per-process accounting + no breach readback),
  but that two of its contributions survive.
- **`codex/cpu-time-timeout`**: becomes the base for the merged change (mechanism
  kept; Rust enforcement + default knob + richer reason + test added). Do not land
  it as-is either — as-is it is Python-only and would diverge from Rust.
- Note the two diverged/adjacent branches so they are not mistaken for a third
  option: `origin/feat/rust-cgroups-and-defaults` (−25k lines, NOT a clean delta —
  a diverged base) and `feat/cgroups-defaults-cpuset-231b` (currently just a label
  at the pin, no committed delta).

## Sequencing note

This does NOT require "implement cgroups in Rust first" — they already exist. It
folds cleanly into `enable-cgroups-and-cpu-timeouts-across-dag-nodes`
(hermit-231b) as the cpu_timeout half, and unblocks
`per-platform-cpu-timeout-multipliers` and the breach-table work on a single
enforcement path. One owner, one linear change.
