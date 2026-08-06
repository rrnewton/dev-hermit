# DAG memory caps as a set — status audit against the owner's directive

**Task:** `derive-all-dag-memory-caps-as-a-set-not-reactively` (P0)
**Date:** 2026-08-05
**Bound to:** hermit main **`b64d893ae9ea6404472eae9cb86102d91ec642ef`**, agent-utils `570e7865`
**Host:** devbig014, MemTotal **754.8 GiB**
**Mode:** local read only. **No validate launched**, no egress, nothing mutated.

---

## First: the dispatch instruction contradicts this task's own owner directive

The dispatch asked me to *"DERIVE all DAG per-node memory caps as a coherent SET from measured
peaks."* The task's title and description say the opposite, in the owner's words (2026-08-04):

> **"JUST INCREASE THE MEMORY CAP… THIS IS NOT OUR HIGHEST PRIORITY right now to absolutely
> minimize how tight the memory caps in these cgroups are."**
> **"DO NOT RE-OPEN THE PRECISION DERIVATION. If a cap ever actually binds, measure THEN."**

The task's `local_id` is a stale name from *before* the overrule. **I did not re-open the
derivation.** I audited the set as it currently stands against the four things the owner actually
asked for, using only existing data.

## Verdict: 3 of 4 owner items are DONE. The one that is not is the outer cgroup.

| # | Owner item | Status |
|---|---|---|
| 1 | Raise `test.strict_compat` generously — "24 GiB+" | **DONE — 24.00 GiB** (was 6.0, OOMing) |
| 3 | Raise every other tight node — `hermit_unit` 4.6G, `clippy` 4G | **DONE — both 16.00 GiB** |
| 2 | **Raise the outer cgroup if that is what trips** | **NOT DONE — `outer_mem_safety_factor = 1.0`** |
| 4 | Still record `{j, bytes}` so values stay qualified | **PARTIAL — 17 of 25 compile nodes carry no `{j}`** |

---

## A correction to my own first reading

My initial pass flagged *"33 of 55 nodes are capped below the declared 8 GiB
`mem_cap_floor_bytes` — the set is incoherent."* **That was wrong, and the mechanism disproves it.**

Traced end to end in `agent-utils/py/safe_ci_dag_runner/sizing.py`:

```python
# sizing.py:26-39 — the PER-STEP enforced cap
def step_mem_cap_bytes(step, *, mem_cap_factor, default_cap_bytes=None):
    if step.hint.hard_mem_max_bytes is not None:
        return step.hint.hard_mem_max_bytes      # explicit hard cap WINS, verbatim
    base = step.hint.rss_baseline_bytes
    if base:
        return int(base * mem_cap_factor)
    return default_cap_bytes
    # <- no floor is applied anywhere in this function

# sizing.py:131-134 — the OUTER footprint model
def jobs_footprint_bytes(cfg, jobs, inner_jobs=None):
    peak, _ = schedulable_peak_mem_bytes(cfg, jobs, inner_jobs)
    return max(cfg.mem_cap_floor_bytes, int(peak * cfg.outer_mem_safety_factor))
```

`mem_cap_floor_bytes` has **exactly one arithmetic consumer**: the outer `-j` footprint model. The
scheduler calls `step_mem_cap_bytes` with only `mem_cap_factor` and `default_cap_bytes`
(`scheduler.py:365-369`) — **the floor is never passed to per-step enforcement.** So a 0.5 GiB
`check.reverie_pin` cap is not a floor violation; the floor was never meant to reach it. Nodes below
8 GiB are correct, not incoherent.

## Two real findings from the same trace

**F1 — `mem_cap_factor = 1.25` is INERT for every node in production.**
All **55/55** nodes (47 PORT + 8 PRIV) declare an explicit `hint.hard_mem_max_bytes`, and
`sizing.py:34-35` returns it verbatim before the factor is ever reached. The declared 1.25×
"headroom factor" multiplies nothing. It only applies to a node characterized by
`rss_baseline_bytes` alone — of which there are none. Anyone reasoning "the caps already carry 25%
headroom" is wrong: the caps are exactly the literals in `ci/dag/*.json`.

**F2 — the outer cgroup has zero safety margin, and that is owner item (2).**
`outer_mem_safety_factor = 1.0` in **both** lanes ⇒ the outer footprint is
`max(8 GiB, modelled_peak × 1.0)` — sized at *exactly* the modelled peak. Every other knob was
loosened generously; this one still says "no margin." Given the owner's framing — caps exist to stop
runaway, and the box has ~754 GiB — a 1.0 factor on the outer scope is the remaining tight
constraint and the direct answer to *"if it's the outer cgroup that's tripping, increase that."*
Raising `outer_mem_safety_factor` (e.g. 1.5–2.0) is a **one-field, set-level** change, not a
per-node campaign.

> This also composes with a known correctness bug that is *not* a tightness question: the runner's
> outer scope is memory-**unboxed** and the `oom.group` write is silent, so a kill can land on a
> neighbour. That is wrong at any cap size and **harder to notice once caps are generous**
> (hermit-ci owns it).

## The set, as it stands

| | PORT | PRIV |
|---|---:|---:|
| nodes | 47 | 8 |
| explicit `hard_mem_max_bytes` | 47/47 | 8/8 |
| Σ caps | **451.0 GiB** | 76.5 GiB |
| largest single cap | 64 GiB | 16 GiB |

Cap distribution (GiB → nodes): 0.5→5 · 1→4 · 2→2 · 3→19 · 3.5→2 · 5→1 · 8→1 · **16→18** · **24→1** · **64→2**

**Scarcity check — this is the number that settles the argument.** If all 47 portable nodes ran
*simultaneously*, each at its full cap, the worst case is **451.0 GiB = 59.8 % of the 754.8 GiB
box**. Full-DAG concurrency cannot exhaust memory even at these generous caps. The owner's position
is quantitatively correct: there is no scarcity here, and further tightening buys nothing.

## Item 4 — `{j, bytes}` qualification is the real remaining gap

A cap without its `{j}` is unqualified: compile peak scales with cargo fan-out, so "16 GiB" means
nothing unless you say at what `-j`. Counting cargo/`validate.sh`-bearing nodes by whether the cmd
pins `CARGO_BUILD_JOBS`:

| Lane | compile-bearing | **pinned `{j}`** | **unpinned** |
|---|---:|---:|---:|
| PORT | 23 | 7 | **16** |
| PRIV | 2 | 1 | **1** |
| **total** | **25** | **8** | **17** |

Pinned: `lint.clippy`, `doc.doctests`, `doc.rustdoc`, `test.regular_crates`, `test.hermit_unit`,
`test.detcore_unit`, `test.rr_suite_contract`, `build.privileged_tests`.

Unpinned (inherit `NUM_JOBS` ≈ nproc, ~284–316 on this box): `setup.nextest`, `build.workspace`,
`build.runtime_release`, `lint.rustfmt`, `test.detcore_misc`, `test.detcore_parallel`,
`test.hermit_integration`, `test.arbitrary_binaries`, `test.cli`, `test.liteinst_strict`,
`test.sabre_examples`, `test.hermit_modes`, `test.app_strict_verify`, `test.command_strict_verify`,
`test.ignored_syscall_regressions`, `test.strict_compat`, `cpuid.faulting`.

`test.strict_compat` is the notable one: it is both the largest cap (24 GiB) **and** unpinned, and
its cmd is a *nested* `./validate.sh` whose inner build an outer `CARGO_BUILD_JOBS` does not reach.
Its 24 GiB is therefore a generous cap over an **unbounded** `{j}` — which is exactly the right
trade under the owner's directive (stop runaway, don't pack), but it should be recorded as
`{j: unbounded/nproc, bytes: 24 GiB}`, not as a characterized value.

**This is the surviving correctness item the owner explicitly preserved** ("the safe-ci CPU quota
LEAKS INTO CARGO and races the linker… `THIRD_PARTY_BUILD_JOBS` covers SELECTED DAG commands only,
not the set" — hermit-238b owns it). It is a *qualification and linker-race* fix, **not** a reason
to re-derive caps.

## Recommendation — one field, then stop

1. **Raise `outer_mem_safety_factor` from 1.0** in both `ci/dag/portable.json` and
   `ci/dag/privileged.json` (suggest 1.5–2.0). One field per lane; closes owner item (2). Worst-case
   outer footprint even at 2.0 stays far inside 754.8 GiB.
2. **Record `{j, bytes}` for the 17 unpinned nodes** as `{j: nproc-inherited}` — a documentation and
   pinning task owned by hermit-238b, not a measurement campaign.
3. **Change nothing else.** Per-node caps are done and provably non-binding in aggregate (59.8 %).
4. **Drop `mem_cap_factor` from the mental model**, or note it as inert while every node declares an
   explicit hard cap (F1).

**Not done here:** no JSON was edited. Egress is down (no push/PR), the change belongs on a feature
branch rather than the hermit primary, and the dispatch scoped this to local analysis.

## Provenance

| Number | Source | Status |
|---|---|---|
| All 55 caps, `mem_cap_factor`, `mem_cap_floor_bytes`, `outer_mem_safety_factor` | `hermit/ci/dag/{portable,privileged}.json` @ `b64d893a` | **read this session** |
| Cap arithmetic (floor applies to outer only; explicit cap wins) | `agent-utils/py/safe_ci_dag_runner/sizing.py:26-39,131-134`; `scheduler.py:365-369` @ `570e7865` | **read this session** |
| `{j}` pinning counts | `cmd` field of every step, both lanes | **derived this session** |
| MemTotal 754.8 GiB | `/proc/meminfo` | **read this session** |
| Historical peaks (6.0 G strict_compat, 4.6 G hermit_unit, 4 G clippy) | task notes / `experiments/dag-memcaps-as-a-set_20260804/` | inherited; **not re-measured** |
