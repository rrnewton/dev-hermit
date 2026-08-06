# Does `memory.oom.group=1` kill the offender and spare the neighbours? — a real boxed run

**Task:** `verify-oom-group-real-boxed-run` · **Date:** 2026-08-05 · Local, no egress, no validate run.

## The gap this closes

`adv-review-boxing` could not confirm that `memory.oom.group=1` takes effect in a **real
boxed run** — the property was **self-reported only** (the runner prints
`memory.oom.group=1 (enabled)` in its own audit line, which is the runner attesting to
itself). This plants an actual OOM and observes the outcome from outside that claim.

## Verdict

> **CONFIRMED, both directions, in a real `safe-ci-dag-runner` run.**
>
> **Confinement holds:** the offending step was OOM-killed at its own cap while three
> concurrent neighbour steps ran to completion, `oom_kills = 0`, sentinels written —
> including one neighbour actively holding 143 MiB at the moment of the kill.
>
> **Group-kill fires:** an innocent, non-allocating process in the **same** step cgroup was
> killed along with the allocator — proven by wall time (1.3 s, not the ~20 s it would have
> taken had only the allocator died), not by an absent file.

## Setup

`safe-ci-dag-runner run --dag dag.json -j 4 -k`, cgroup boxing **ACTIVE** (verified in the
run header, not assumed). Per-step cap via `hint.hard_mem_max_bytes = 268435456` (256 MiB)
→ inner cgroup `memory.max`, swap disabled.

**Safety:** the plant is confined by construction — a 256 MiB cap and a ≤4 GiB allocation
attempt on a box with a ~524 GiB outer scope cap. The cap was verified **bound before the
allocation ran** (the step read its own `memory.max` and printed it, below), so this could
not escalate into host pressure on a shared machine.

## Evidence 1 — the cap is real, read by the step from inside its own cgroup

```
PATH=/sys/fs/cgroup/user.slice/.../safe-ci.slice/safe-ci-434854.scope/step-probe.cgattrs
memory.max=268435456
memory.swap.max=0
memory.oom.group=1        <-- the property under test, on the STEP cgroup
PARENT_oom_group=1
```

Each step gets its **own** cgroup (`step-<group>.<job>`), and `memory.oom.group=1` is set
there, not only on the shared outer scope. Retained: `out/probe-cgattrs.txt`.

## Evidence 2 — confinement: offender dies, neighbours complete

`-j 4`, all four steps started together, `--keep-going` so the scheduler does not abort the
neighbours (see *Confound* below):

| step | role | ok | oom_kills | peak_bytes | elapsed_s |
| --- | --- | --- | --- | --- | --- |
| `offender.oomer` | offender | **False** | **4** | 268 435 456 (= its cap exactly) | 0.683 |
| `neighbour.n1` | neighbour cgroup | True | 0 | 1 245 184 | 12.036 |
| `neighbour.n2` | neighbour cgroup | True | 0 | 1 318 912 | 12.086 |
| `neighbour.n3` | neighbour cgroup | True | 0 | **150 429 696** | 12.137 |

Runner verdict: `FAIL - 3 passed, 1 failed, 0 aborted`.

The offender's own trace shows it climbing to the wall and dying there:

```
offender memory.max=268435456
offender memory.swap.max=0
alloc 64 MiB
alloc 128 MiB
alloc 192 MiB
✗ FAIL  (1s, OOM-KILLED (hit inner MemoryMax; 4 oom_kill event(s)))
```

**Completion sentinels** — "survived" means *finished its work*, not merely *was not killed*:

```
offender.started    PRESENT
offender.completed  ABSENT   <- killed mid-allocation, as intended
n1.completed        PRESENT
n2.completed        PRESENT
n3.completed        PRESENT
```

`neighbour.n3` is the sharpest control: it was **holding 143 MiB of its own** when the
offender OOMed, and was untouched (`oom_kills = 0`, full 12 s, sentinel written). A
machine-wide or slice-wide OOM would have been very likely to take it.

## Evidence 3 — group-kill semantics: the innocent sibling dies too

Confinement alone does not prove `oom.group`; it would also hold with `oom.group=0`
(kernel kills just the biggest process). The discriminator is what happens to a
**non-allocating process in the same cgroup**.

Step `groupkill.sibling` starts `( sleep 20; touch sibling.survived ) &`, then allocates
until it hits the cap, then `wait`s for the sleeper.

| | `oom.group=0` would give | **observed** |
| --- | --- | --- |
| step wall | ~20 s (bash waits out the sleeper) | **1.299 s** |
| `sibling.survived` | PRESENT | **ABSENT** |
| `sibling.allocret` | PRESENT (allocator returns, bash continues) | **ABSENT** |
| oom_kills | 1 | **6** |

**The wall time is the positive measurement** — 1.3 s against a 20 s alternative is
unmistakable, and it does not rest on inferring anything from a missing file. The whole
cgroup was torn down at the OOM, which is exactly `memory.oom.group=1`.

The oom_kill count rising 4 → 6 between the two plants tracks the extra processes present
in the cgroup (subshell + `sleep`), consistent with group-kill rather than single-victim
kill.

## Confound found and corrected

The **first** run of `dag.json` (without `-k`) is retained as `runner.log` and is **not
valid evidence**: all three neighbours show `⊘ ABORT ... eager-exit after another step
failed`. They were killed by the *scheduler's* eager-exit policy, not spared or taken by
the OOM — so that run cannot distinguish "survived" from "aborted for an unrelated reason".
`runner-keepgoing.log` is the corrected run and the one the table above reports. Recording
this because the first run's `oom_kills = 0` on the neighbours looks like a pass and is not
one.

## Files

- `dag.json`, `groupkill.json`, `probe.json` — the three planted DAGs
- `runner.log` — first run, **confounded** (eager-exit aborted the neighbours)
- `runner-keepgoing.log` — the valid confinement run
- `groupkill.log` — the same-cgroup sibling run
- `out/` — completion sentinels + `probe-cgattrs.txt`
- `perf/step_profiles_*.csv` — per-step `oom_kills`, `peak_bytes`, `elapsed_s` (raw)
- `results.csv` — the distilled table above
- `metadata.json` — host, runner version, caps, command lines

## Reproduction

```bash
cd ~/work/dev-hermit/experiments/oom-group-confinement_20260805
R=../../agent-utils/py/bin/safe-ci-dag-runner
python3 $R run --dag dag.json       -j 4 -k --profile --perf-dir $PWD/perf   # confinement
python3 $R run --dag groupkill.json      --profile --perf-dir $PWD/perf      # group-kill
python3 $R run --dag probe.json          --no-profile                        # cgroup attrs
```

## Limitations

- **Python engine only.** `safe-ci-dag-runner` has a Rust parity engine; this exercises the
  Python one (the default, and what local `validate.sh` resolves to). The Rust engine's
  boxing is **not** covered here.
- Single host (`AMD EPYC 9D85`, cgroup-v2, systemd user scope). Behaviour under a different
  cgroup driver or a v1 hierarchy is untested.
- The neighbours are cheap (`sleep`/small alloc). This shows the OOM does not collaterally
  kill them; it does not characterise behaviour when *several* steps approach their caps at
  once, nor when the **outer** scope cap is hit rather than a per-step cap.
- `oom_group_kill` from `memory.events` was not read for the offender's cgroup directly —
  the cgroup is destroyed at step teardown. The group-kill conclusion rests on the wall-time
  discriminator (Evidence 3), which is independent of that counter.
- N=1 per configuration. The signals are categorical (killed / completed, 1.3 s / 12 s), so
  repetition adds little, but no flakiness assessment was made.
