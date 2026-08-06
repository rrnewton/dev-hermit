# DAG-runner parallelism surface — a three-axis design proposal

**Task:** `design-the-dag-runner-parallelism-surface-three-axes-not-one-j` (P0, sentinel-owned)
**Date:** 2026-08-05
**Bound to:** agent-utils `570e7865`, hermit `b64d893a`
**Mode:** local design + code reading. No validate-run, no egress, nothing mutated.

> **This is a PROPOSAL. The owner explicitly said he does not know the best design yet and that
> agents must propose, not build.** Nothing here is implemented and no flag was touched. Each option
> below is presented with its own footgun analysis, including the one I recommend.

---

## The headline, before any design

**The axis the owner wants to control (A2, total concurrent parallelism) already has an enforcement
point — and that enforcement point is blind. It can currently see the true width of 2 of 55 nodes.**

Any intent-based surface built on top of it today would derive confident numbers from an input that
is wrong by one to two orders of magnitude. **Fixing the blindness is a prerequisite for the
surface, not a companion to it.** That is the single most important thing this design has to say.

---

## 1. The three axes, bound to code

| Axis | Meaning | Where it lives today | Status |
|---|---|---|---|
| **A1** outer DAG width | how many *steps* run concurrently | `scheduler.py:335` — `if len(self.running) >= self.jobs` | **this is the entire meaning of `-j` today** |
| **A2** total concurrent parallelism | Σ inner widths across running steps → maps to the box | `scheduler.py:198,210,262-270` `core_budget` + `cores_used` (`:344` add, `:534` release) | **exists, dormant** (`None` ⇒ gate always True, `:266-267`) |
| **A3** max width per step | inner fan-out of one step | `model.py:157-161` `preferred_inner_jobs`; `jobs_flag` | per-step data, **not settable from outside** |

`self.jobs` has exactly one use — the A1 comparison at `:335`. So today's `-j` sets A1 and **nothing
else**. It neither sets A3 nor bounds A2.

## 2. Why A2's gate is blind — the load-bearing measurement

The gate sums `_step_width` (`scheduler.py:256-260`):

```python
width = preferred_inner_jobs(step)
return width if (width is not None and width > 0) else 1     # <- undeclared ⇒ counted as 1
```

**Only 2 of 55 nodes declare `preferred_inner_jobs`:**

| Lane | declares | values |
|---|---:|---|
| portable | **2 / 47** | `build.workspace` = 32, `build.runtime_release` = 32 |
| privileged | **0 / 8** | — |

Inner width actually lives in **three** places, and the gate reads only one:

| Where the real width lives | Nodes | Gate sees it? |
|---|---:|---|
| `hint.preferred_inner_jobs` | 2 | **yes** |
| `CARGO_BUILD_JOBS=N` baked into the cmd string | 8 | **no** — counted as 1 |
| nothing declared ⇒ inherits `NUM_JOBS` ≈ nproc (284–316 here) | 17 | **no** — counted as 1 |

So enabling `--cores P` today would admit a node that really forks ~284 compile processes while
charging the budget **1**. A gate that under-counts by ~284× is worse than no gate: it reports a
safety property it does not provide. This is the same Proxy Binding failure as a memory cap without
its `{j}` — the value does not carry its condition.

**Corroborating mechanism:** `effective_cpu_count` (`model.py:171-176`) documents that it *"Bounds
ONLY the cgroup `cpu.max`, never the command's inner `-j` flag."* So an undeclared node gets a small
CPU box but still **spawns** its full process fan-out. `cpu.max` throttles rate; it does not reduce
process count — so memory and linker contention scale with the real `-j` regardless. That is exactly
the `#1592` failure (cargo-lock contention at `-j 16`, passing *and faster* at `-j 4`) and the
compile-node OOM chain.

## 3. Why renaming `-j` cannot fix this

From the task's evidence base (inherited, see Provenance):

- DAG intrinsic ceiling **4.24×** (total_work 5360 s / critical path 1265 s); realized ceiling with
  deps + `hermit_guest=1` is **1.78×**.
- The j-sweep **goes flat at j ≥ 5**.
- Shipped default was **`-j 2` on a 316-core box**.
- `-j 16` produced a **false red** that read as a code defect.

So the flag's useful range is ~1–5, most of its accepted range is inert, and part of it actively
manufactures false reds. **A better name relabels the trap; it does not remove it.** The reason is
structural: `-j` moves A1, but what breaks is A2. The user is handed the wrong knob for the failure
they will experience.

## 4. Design options considered

**Option A — rename `-j` to `--dag-width`.**
Honest about A1, cheap, no behaviour change. But it leaves A2 unbounded and A3 unsettable, so the
`#1592` failure mode is untouched. *Rejected as sufficient; adopt only as part of B.*

**Option B — expose all three axes as explicit numbers** (`--dag-width`, `--core-budget`,
`--step-width-max`).
Complete and honest. **But it triples the footgun surface**: an agent that cannot correctly answer
"what should `-j` be?" will not do better with three coupled numbers, and the coupling is
non-obvious (raising A1 raises A2 implicitly). *Rejected as the primary surface; keep as the expert
escape hatch.*

**Option C — intent, with the axes derived** (`--contention=solo|shared|ci`).
The owner's candidate. An agent *can* reliably answer "am I alone on this box?" — it demonstrably
cannot answer "what should `-j` be?". Derivation uses measured node footprints and the real box
size. *Recommended as the primary surface — conditional on §2 being fixed first.*

**Option D — full auto, no knob** (derive everything from live box state).
Most agent-proof, but non-reproducible: two runs of the same DAG on a differently-loaded box give
different widths, and a receipt cannot then be compared against another. *Rejected — it breaks the
"carry the conditions" requirement by construction.*

## 5. Recommended surface

**Primary (what agents use):**

```
--contention=solo     # I have the box
--contention=shared   # other agents/validates are running   [DEFAULT]
--contention=ci       # hosted runner, small fixed box
```

One argument, answerable from fact rather than judgement. It derives all three axes:

| intent | A1 dag-width | A2 core budget | A3 per-step ceiling |
|---|---|---|---|
| `solo` | min(realized-ceiling ≈ 5, ready-set) | large fraction of box cores | node's declared width |
| `shared` | small (≈ 2–4) | **conservative slice of box** | clamped to fit A2 |
| `ci` | small | runner core count | clamped |

Derivation must be **from measured footprints**, and the resolved triple must be **printed at
start** — an intent that silently resolves to a number is the same opacity in a nicer wrapper.

**Escape hatch (expert/experiment only):** `--dag-width N`, `--core-budget P`, `--step-width-max W`.
Each overrides one axis of the derived triple; each is recorded as an override in the receipt.
Deliberately verbose so it cannot be reached for casually.

**Retire the bare `-j`.** Not renamed silently — removed, with an error that names the replacement.
A flag whose meaning is "A1 only" while users believe it means "how much machine to use" should not
survive under any spelling.

## 6. The receipt must carry the conditions

Per the owner: a bare wall time from an unstated width is uninterpretable. Every run record must
carry the resolved triple **and its provenance**:

```json
"parallelism": {
  "intent": "shared",
  "dag_width": 3,              "dag_width_src": "derived",
  "core_budget": 64,           "core_budget_src": "derived",
  "step_width_max": 8,         "step_width_max_src": "derived",
  "box_cores": 316,
  "width_visibility": {"declared": 2, "cmd_baked": 8, "inherited_nproc": 17}
}
```

`width_visibility` is the honesty field: it states how much of A2 the gate could actually see for
*this* run. While it reads `2 / 8 / 17`, any A2 claim in that receipt is explicitly a lower bound —
and that is exactly what a reader needs to know.

## 7. Sequencing — the prerequisite is not optional

1. **Surface inner width as data (PREREQUISITE).** Move `CARGO_BUILD_JOBS=N` out of the 8 cmd
   strings into `hint.preferred_inner_jobs`, and give the 17 inheriting nodes a declared width.
   Until then A2 cannot be enforced honestly and **no intent surface can be built on it**.
2. **Add `width_visibility` to the receipt.** Cheap, immediately makes the blindness measurable
   rather than invisible — and it is useful even if the rest is never built.
3. **Turn on the A2 gate** (`core_budget`) once it can see real widths. The enforcement code already
   exists.
4. **Then add `--contention`**, deriving from now-trustworthy inputs.
5. **Then retire `-j`.**

Steps 1–2 are worth doing regardless of which surface the owner picks; they are correctness, not
design. Steps 4–5 are the design decision and should not start until the owner rules.

## 8. Open questions for the owner (not for an agent to settle)

1. Is `shared` the right default? It is the safe answer but leaves throughput on the floor when an
   agent genuinely is alone.
2. Should `solo` be *verified* rather than *asserted* — e.g. refuse it if the validate-lock is held
   or other DAG scopes are live? That converts a claim into a checked fact, at the cost of a
   coupling.
3. Should the escape hatch exist at all, or does its presence guarantee agents use it and rebuild
   the footgun?
4. Do `sabre`/`liteinst`/`kvm`-style per-class `resource_caps` fold into A2 as one budget, or stay a
   separate dimension?

## Provenance

| Claim | Source | Status |
|---|---|---|
| `-j` ⇒ A1 only (`scheduler.py:335`) | agent-utils `570e7865` | **read this session** |
| `core_budget` dormant A2 gate (`:198,210,262-270,344,534`) | same | **read this session** |
| `_step_width` undeclared ⇒ 1 (`:256-260`) | same | **read this session** |
| **2/55 nodes declare `preferred_inner_jobs`** | `hermit/ci/dag/*.json` @ `b64d893a` | **measured this session** |
| 8 cmd-baked / 17 nproc-inheriting compile nodes | same, cmd-field scan | **measured this session** (also in `ai_docs/dag-memory-caps-set-audit-20260805.md`) |
| `cpu.max` bounds the box, not the command's `-j` (`model.py:171-176`) | agent-utils | **read this session** |
| 4.24× / 1.78× ceilings; j-sweep flat ≥ 5; `-j 2` default; `#1592` `-j16` false red (589 s → 492 s at `-j 4`) | task notes, 2026-08-04 | **inherited, not re-measured** |
