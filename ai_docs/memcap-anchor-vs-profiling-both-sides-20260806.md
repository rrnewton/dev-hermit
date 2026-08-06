# Memory caps: anchor+model vs. inherit-outer — both sides

**Task:** `memory-cap-anchor-plus-scaling-model-explore-both-sides` · **Date:** 2026-08-06
**Bound to:** agent-utils `570e7865`, hermit `b64d893a` · **Mode:** local read. No egress, nothing changed.
**Owner asked to explore, not to pick. This does not pick.**

## Precondition: the runner already has Side A, and it is doubly inert

The owner said *"was supposed to have some support."* It does — `sizing.py:42-57`:

```python
def step_mem_cap_for_inner_jobs(step, inner_jobs, *, mem_cap_factor) -> int:
    """Conservative ``P x J`` model pending measured matrices: ... CPU-bound steps
       scale linearly above J=4."""
    cap = step_mem_cap_bytes(step, mem_cap_factor=mem_cap_factor) or 0
    if (step.hint.hard_mem_max_bytes is not None      # <-- kills it for all 55 nodes
        or inner_jobs is None
        or step_classification(step) is not StepClass.CPU_BOUND):
        return cap
    return max(cap, int(cap * inner_jobs / 4))
```

That **is** Side A, dumb-linear fallback included. It fires for nothing:

1. **No production caller.** `scheduler.py:78,366` imports and calls `step_mem_cap_bytes` — the
   *flat* one. The scaling function appears only as a re-export (`__init__.py:84,142`).
2. **Even if called, the first guard returns the base cap for all 55/55 nodes**, because every node
   declares `hard_mem_max_bytes`.

Eleventh instance this session of a mechanism that exists and does not fire. **Side A is not a
proposal to build; it is a proposal to *wire*** — which materially changes its cost.

## The `{j}` provenance audit — the owner's core insight, quantified

| Fact | Count |
|---|---|
| nodes with a cap | **55 / 55** |
| caps carrying **any** stated `-j` (`preferred_inner_jobs`) | **2 / 55** — `build.workspace`=32, `build.runtime_release`=32 |
| compile nodes pinning `CARGO_BUILD_JOBS` | 8 / 25 |
| compile nodes inheriting `NUM_JOBS ≈ nproc` (284–316 here) | **17 / 25** |

**53 of 55 caps are numbers with no stated parallelism**, and 17 of them are measured against an
*unbounded* `j`. The owner's claim ("wrong on every machine except the one it was measured on") is
if anything understated: 17 are wrong on *this* machine whenever `nproc` differs from the run that
set them.

Two further inert knobs found earlier this session: `mem_cap_factor = 1.25` never applies (explicit
cap wins first), and `mem_cap_floor_bytes = 8 GiB` governs only the outer `-j` footprint model, not
per-step enforcement.

## Side A — anchor at a stated `-j`, scaled by a model

**Shape:** `mem_at_j = {j: 32, bytes: N}` in the DAG; runner scales by profiled CPU→memory
high-water, dumb-linear when no model exists.

| | |
|---|---|
| **profiling ABSENT** | **Works.** Linear fallback gives a bound from day one. This is its decisive advantage. |
| **anchor STALE** | Scales a wrong base — "a stale anchor scaled by a good model is still wrong." But wrong *within a bounded factor*, and the anchor is visible in the file for audit. |
| **fails** | **SAFE.** A too-low scaled cap kills the offending step at its own `memory.max`, with `oom.group=1` confining the kill. Loud, attributable, retryable. |
| **cost to adopt** | Re-express 55 caps as `{j, bytes}` — the 2 that already state `j` are free; the 17 nproc-inheriting nodes need a pinned `j` *first*, which is the same prerequisite the parallelism-surface design identified. Plus wire the existing function. |

## Side B — no static cap, inherit the outer cgroup, rely on profiling

| | |
|---|---|
| **profiling ABSENT** | **No bound at all.** Today `ci-hub/history/` is empty and no validate perf CSVs exist; the task records 1 of 132 runs carrying DAG profiling. So Side B is *currently* unbounded-in-practice, not bounded-by-outer. |
| **anchor STALE** | Immune — nothing to go stale. Its real advantage. |
| **fails** | **SILENT, and it fails onto neighbours.** With no per-step `memory.max`, a runaway grows until the *outer* scope's limit, where `oom.group=1` at scope level kills **every concurrent step**. The offender and the bystanders are indistinguishable in the ledger. |
| **cost to adopt** | Zero to configure, high to make safe: needs a profiling producer that runs every time, plus per-step attribution that currently doesn't exist. |

**The asymmetry is the finding.** Side A's failure is a *false negative on one node*; Side B's failure
is a *true positive attributed to the wrong node*. A cap that kills the wrong job is worse than a cap
that is merely mis-sized, because the mis-sized one is self-correcting on inspection and the
misattributed one teaches you the wrong lesson. This is the
`oom-blast-radius-hits-neighbours-not-the-offender` shape.

## Does the hybrid collapse the tension?

The owner's own sketch — **anchor as FLOOR, profiled model as operative value, outer cgroup as hard
ceiling** — dissolves rather than adjudicates it:

```
effective_cap = clamp( profiled_model(j)  if a model exists  else  anchor × (j / j_anchor),
                       low  = anchor_floor,
                       high = outer_scope_limit )
```

- **Profiling absent** → falls back to Side A's linear scaling. No unbounded window.
- **Anchor stale-low** → the profiled model overrides it. Staleness stops mattering as soon as data exists.
- **Anchor stale-high** → the outer ceiling still bounds blast radius.
- **Fails safe**, because a per-step `memory.max` always exists.

Cost: it needs *both* the `{j, bytes}` re-expression and the profiling producer — it is the union of
the two adoption costs, not a shortcut. Worth stating plainly rather than presenting the hybrid as
free.

## Negative test to require of whichever is chosen

Per the task, and reusable from this session's boxing experiments: **a node that exceeds its scaled
cap must DIE, and its neighbours must SURVIVE.** Run two steps concurrently, one ballooning past its
cap; assert the offender is killed, the neighbour exits 0, and the ledger attributes the kill to the
offender. The `experiments/pids_axis_cgroup_enforcement_20260805/` and
`boxing_coverage_gap_layer_and_reap_20260805/` harnesses already demonstrate `cgroup.kill` confining
a subtree with a no-kill control, which is the same bracket shape.

**Side B cannot pass this test today** — with no per-step cap there is no per-step kill to attribute.
That is not an argument against Side B; it is the concrete precondition Side B must buy first.

## Provenance — every number with its `{j}`

| Number | `{j}` | Source |
|---|---|---|
| 55/55 caps; 2/55 with stated `j`; 8/25 vs 17/25 `CARGO_BUILD_JOBS` | the point is that 53 have **none** | `hermit/ci/dag/*.json` @ `b64d893a` — **measured this session** |
| `step_mem_cap_for_inner_jobs` inert (no caller + guard) | n/a | `sizing.py:42-57`, `scheduler.py:78,366`, `__init__.py:84,142` — **read this session** |
| `mem_cap_factor` 1.25 inert; floor is outer-only; `outer_mem_safety_factor` 1.0 | n/a | earlier this session, `ai_docs/dag-memory-caps-set-audit-20260805.md` |
| Σ portable caps 451 GiB = 59.8 % of 754.8 GiB | at full concurrency | **measured this session** |
| 1 of 132 runs carried DAG profiling | n/a | task description — **inherited, not re-measured** |
