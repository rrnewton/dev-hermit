# DAG per-node memory cap: static anchor vs. profiled model — tradeoff write-up

Date: 2026-08-04
Status: DESIGN / decision-support. **No verdict — both sides laid out for the owner to decide.**
Scope: `safe-ci-dag-runner` per-DAG-node memory caps (`agent-utils`, dual impl py+rs).
Owner premise (GChat 2026-08-04): a bare `hard_mem_max_bytes` is a number **without a stated
parallelism**. It is only meaningful if you know the `-j` / inner-jobs it was measured at. ~10
DAG nodes carry a fixed cap against an nproc-scaled (or otherwise machine-dependent) job count, so
the cap is correct only on the machine it was measured on. "More parallelism needs more memory."

Sibling task: `oom-blast-radius` / `memory.oom.group` (the negative-test section ties to it).

---

## Question

Should each DAG node's inner-cgroup memory cap be:

- **Side A — ANCHOR + MODEL:** a static, *qualified* anchor `mem_at_j = {j: N_j, bytes: B}` in the
  DAG file, which the runner SCALES by a profiled CPU→memory high-water function, with a dumb
  linear fallback when no profile exists; or
- **Side B — NO STATIC CAP:** drop the per-node cap entirely, inherit the outer box cgroup (all
  memory), and rely purely on the profile store for any inner bound.

The tension is real because each side's failure mode is the *complement* of the other's, and one of
those failure modes (unbounded) is how a single node OOMs its neighbours. Below: what already
exists, the two sides costed against `profiling-absent` and `anchor-stale`, the owner's hybrid, a
failure-mode matrix, and a negative test for whatever is adopted.

---

## Existing support (verified against current source)

The owner recalled "was supposed to have some support." There is. But the width-aware piece is
**recorded but not carried onto the model** — the model collapses it. Precise state:

### What EXISTS

1. **Base per-step cap resolution.** `py/safe_ci_dag_runner/sizing.py:20-26`
   `step_mem_cap_bytes(step, *, mem_cap_factor)`: an explicit `hard_mem_max_bytes` wins verbatim;
   otherwise `rss_baseline_bytes * mem_cap_factor`. `mem_cap_factor` default `1.25`
   (`model.py:218`); `mem_cap_floor_bytes` default `8 GiB` (`model.py:221`);
   `outer_mem_safety_factor` default `1.0` (`model.py:223`). Rust parity: `rs/.../sizing.rs:17`.

2. **A P×J inner-jobs scaler ALREADY EXISTS — as a "dumb linear" model.**
   `sizing.py:29-44` `step_mem_cap_for_inner_jobs(step, inner_jobs, *, mem_cap_factor)`:
   > "Conservative `P x J` model pending measured matrices: an explicit hard cap and non-CPU-bound
   > steps keep the base cap; CPU-bound steps scale linearly above J=4."
   The scaling rule is literally `max(cap, int(cap * inner_jobs / 4))` (`sizing.py:44`). This is
   exactly Side A's "dumb linear fallback" — it is already written. Rust parity: `sizing.rs:38`.

3. **Per-width memory IS in the raw profile store.** A `Sample` carries `peak_bytes`
   (`estimates.py:166`, parsed `estimates.py:419-420`), and rows are bucketed by
   `(step, inner_jobs)` (`BucketKey` `estimates.py:395`; `bucketize_rows` `estimates.py:437-452`).
   `perflog.py:128` logs `peak_bytes` per run, and real boxed rows always resolve `inner_jobs >= 1`.
   So the store has `{step, inner_jobs, peak_bytes}` triples — the raw material for a width→memory
   curve.

4. **Cgroup enforcement + blast-radius primitive.** `cgroup.py:493-503` writes
   `memory.swap.max=0`, `memory.max=<cap>`, and **`memory.oom.group=1`** on the step child cgroup;
   `scheduler.py:472,496` reads `memory.events` `oom_kill` count and classifies the step OOM-KILLED
   (`model.py:182`). `memory.oom.group=1` already means the whole step subtree dies together — the
   sibling `oom-blast-radius` primitive is present.

### What is MISSING (the actual gap)

5. **The speedup/estimate model COLLAPSES per-width memory to one number.**
   `step_samples_from_buckets` (`estimates.py:455-483`) aggregates across **all** of a step's widths
   into a single high-percentile `peak_bytes` — its own docstring (`estimates.py:458-459`): *"the
   memory + duration model does not distinguish inner_jobs"*. The high-water is
   `_high_percentile(step_peaks)` at `estimates.py:481` (90th pct nearest-rank, `_RSS_PCTL_*`
   `estimates.py:89-90`).

6. **The speedup curve has NO memory dimension.** `SpeedupLevel` (`estimates.py:519-536`) carries
   `inner_jobs, samples, wall_s, cpu_s, effective_cores, throttled_s, speedup` — **no `peak_bytes`
   field.** `_build_step_speedup` (`estimates.py:555-607`) fits a knee on *wall + CPU-work-growth*
   only. So the width→wall curve is learned; the width→memory curve is discarded, even though the
   raw samples in (3) contain it.

7. **The runtime cgroup write is UNSCALED.** The actual `memory.max` written to the kernel comes
   from `scheduler.py:361` → `step_mem_cap_bytes(...)` — the **base, un-inner-jobs-scaled** value.
   The scaler in (2) is only consumed by `schedulable_peak_mem_bytes` (`sizing.py:98`) for
   **`-j` budget *planning*** (`jobs_for_budget`, `sizing.py:124`), never by the enforcement path.
   **Consequence:** the number the kernel enforces is exactly the bare, parallelism-unqualified
   `hard_mem_max_bytes` the owner is complaining about. Even the linear scaler that exists does not
   touch the cap that OOM-kills a step.

**Summary of existing support:** the *ingredients* for a width-aware cap exist (per-width peaks in
the store; a linear P×J scaler; oom.group isolation), but they are not assembled: the model throws
away width when summarizing memory, `SpeedupLevel` has no memory axis, and the enforcement path
writes the unqualified static number. Any adopted design is mostly *wiring existing pieces together*,
not new machinery.

### The nodes in question

`hermit/ci/dag/portable.json` carries `hard_mem_max_bytes` on ~40 nodes; the load-bearing
CPU-bound builds are the risk. The canonical offender, `build.dbi_release`
(`portable.json:46-52`): `hard_mem_max_bytes = 8589934592` (8 GiB), `rss_baseline = 5368709120`
(5 GiB), `classification: cpu-bound`, and its command runs
`CARGO_BUILD_JOBS=${THIRD_PARTY_BUILD_JOBS:-$(nproc)} cargo build --release ... -p hermit
--features third-party-backends ...`. So the **jobs are nproc-scaled** while the **cap is a fixed
8 GiB** — the mismatch made concrete: on a big box the build fans out to 32-way and blows the cap
that was implicitly measured at some smaller width.

---

## Live curve data (owner-supplied — do NOT re-measure)

- **{j=32, peak > 8 GiB}** — PR #1584 `build.dbi_release` was **OOM-KILLED at the 8 GiB cap under a
  32-way build** (`oom_kill events = 2`). One hard point: at j=32 this node's peak exceeds 8 GiB.
- **{j=8, peak = measuring}** — sibling run `hermit-238b` is capturing `jobs=8 MemoryPeak` for the
  same node. Second point on the curve (pending).
- **Clean DEBUG `cargo build -p hermit` peak RSS:** `2.5 GiB @ j1 → 3.2-3.3 GiB by j8`, then
  **FLAT to j316**. rustc's crate-DAG width saturates around ~27, so memory does **not** grow
  linearly with `-j` forever — it *knees and plateaus* at the crate concurrency width. (Different
  build than dbi_release — debug vs release+third-party — so absolute bytes differ; the **shape** is
  the transferable lesson.)

**Why the shape matters for BOTH sides:**
- A **dumb-linear** model (`cap * j / 4`, `sizing.py:44`) keeps climbing past the knee: at j=32 it
  predicts `8x` the j=4 cap, but real memory flattened at ~j8-27. Linear **over-provisions** the
  cap past the knee — safe against OOM (cap too generous) but useless as a tight bound and it will
  hand out too much headroom in `-j` planning, under-parallelizing.
- The **anchor's stated `j`** must sit **at or above the knee** to be meaningful. An anchor stated
  at `j=1` (2.5 GiB) scaled up is guessing; an anchor stated at `j=8` (past the debug knee) captures
  the plateau and needs little scaling. Choosing the anchor's `j` is itself the crux of Side A: pick
  a `j` on the plateau and the "scale by model" step becomes nearly a no-op (good); pick `j=1` and
  you are back to trusting the scaler.

---

## Side A — ANCHOR + MODEL

Static file holds `mem_at_j = {j: N_j, bytes: B}` (a cap **qualified** by the `-j` it was measured
at). At runtime the runner scales `B` by a profiled CPU→memory high-water function keyed to the
target machine's actual width; when no profile exists it falls back to the dumb-linear scaler that
already ships (`sizing.py:29-44`).

- **PRO:** works with **zero** profiling history (linear fallback is deterministic and present
  today). Degrades **predictably** — you can read the cap that will be enforced straight from the
  file and the fallback formula. The anchor is **auditable in-file**: a reviewer sees
  `{j:8, bytes:8G}` and can sanity-check it. Directly answers the "carry the condition with the
  value" review axis — the `j` travels with the bytes.
- **CON:** the anchor **still has to be derived** (someone measures `B` at `N_j`), and it can go
  stale as the build's memory grows. **A stale anchor scaled by a good model is still wrong** —
  the model faithfully scales a wrong base. The linear fallback **over-provisions past the knee**
  (see curve shape), so with no profile the cap is loose.

### Costed cases

- **(a) profiling ABSENT:** falls back to `cap * j / 4` (`sizing.py:44`) from the anchor. Produces a
  **bounded** cap on every machine — possibly too generous past the knee, but never unbounded. The
  step that exceeds it **dies alone** (oom.group). **Fails SAFE.**
- **(b) anchor STALE (real memory grew above `B`):** the scaled cap sits below real usage; the node
  OOM-kills **itself** repeatedly at a too-tight cap. Loud, attributable (`oom_kill` count,
  `model.py:182`), **local to the node** — it does not take out neighbours. Painful but **bounded /
  fail-SAFE-ish**: the failure is a self-inflicted false-kill, not an unbounded blast. (Symmetric
  risk: a stale anchor that is too *high* relative to a shrunk build wastes headroom but is
  harmless.)

---

## Side B — NO STATIC CAP

Drop `hard_mem_max_bytes`. The step inherits the outer box cgroup (all the machine's memory), and
the only inner bound is whatever the profile store supplies via `rss_estimate_bytes`
(`estimates.py:481`, currently width-collapsed).

- **PRO:** **nothing to go stale** — there is no static number to drift. The machine's **real
  memory is the real bound**, which is the honest physical limit. No per-machine wrongness by
  construction, because there is no per-machine-fixed number.
- **CON:** depends on the profile **existing**. Tonight **only 1 of 132** validate runs carried DAG
  profiling. A **missing model means NO inner bound at all** — the node can consume the entire box.
  That is precisely how one node OOMs its neighbours: with no inner `memory.max`, the kernel OOM
  killer fires at the **outer** box level and picks a victim by heuristic, which may be an innocent
  neighbour, not the greedy node. `memory.oom.group` only isolates a subtree **if that subtree has
  its own cgroup with a cap**; inheriting the outer cgroup defeats it.

### Costed cases

- **(a) profiling ABSENT:** **no inner cap at all.** Unbounded. First node to spike triggers an
  outer-level OOM that can kill a neighbour. **Fails SILENT / UNBOUNDED** — this is the dangerous
  quadrant. (And "profiling absent" is the *current* empirical norm: 1/132.)
- **(b) anchor STALE:** **N/A** — Side B has no anchor. This is Side B's structural advantage:
  the entire "stale" failure column is empty. Its cost is concentrated entirely in the
  profiling-absent column.

---

## Hybrid (owner-floated): anchor = FLOOR, profiled model = operative, outer cgroup = CEILING

Rule: enforced cap = `min( max(anchor_floor, profiled_value), outer_box_ceiling )`.
- The **anchor** is a conservative **floor** — a small, rarely-wrong lower bound (e.g. the node's
  known single-width baseline), NOT a precise target. It only has to be "at least this much."
- The **profiled model** is the **operative** value when it exists — the learned width-aware
  high-water.
- The **outer box cgroup** is a hard **ceiling** that always protects neighbours regardless of the
  other two.

### Does it collapse the tension rather than adjudicate it?

Largely **yes** — because it assigns each input the job it is actually good at, instead of forcing
one number to be simultaneously always-present, never-stale, and neighbour-safe:

- **profiling ABSENT** → `max(floor, ∅) = floor`, clamped by ceiling. This is **Side-A-lite**: a
  bounded cap with no profile, so it does NOT fall into Side B's unbounded quadrant. The floor need
  only be a *loose lower bound*, so it is far less prone to staleness than a precise anchor (Side A's
  stale-anchor risk is muted because the floor is deliberately conservative, not tight).
- **anchor/floor STALE (too low)** → the **profiled model overrides upward** to the real learned
  value, so a stale-low floor is corrected by the profile. A stale floor only bites when profiling
  is *also* absent, and even then it fails safe-ish (self-kill at a tight cap, not a neighbour
  blast).
- **anchor/floor STALE (too high)** → the **ceiling still protects neighbours**: the box-level cap
  bounds the worst case even if both floor and model are wrong-high.
- **profiling PRESENT** → operative value is the learned width-aware number, exactly what Side A
  wanted the model to produce and what Side B wanted the profile to supply.

**What it costs:** it needs **all three** wired — an anchor floor in the DAG file, the profiled
operative value (which requires closing gaps 5-6-7 above: carry `peak_bytes` per width onto
`SpeedupLevel`, stop collapsing widths for the memory model, and route the width-scaled value into
the `scheduler.py:361` enforcement write), and the outer-cgroup ceiling read. It also introduces a
`min/max` policy that must be tested from both sides (a floor that never lets the model *lower* the
cap; a ceiling that always wins). It does not eliminate the need to derive a floor value — but the
floor is a much easier number to get right than a precise anchor, because "conservative lower bound"
tolerates error in the safe direction.

**Where it does NOT fully collapse the tension:** if the floor is set too *high* (over-conservative)
and profiling is absent, you over-provision and under-parallelize — the same knee-overshoot the
linear fallback has. And the ceiling only protects neighbours if the step genuinely runs in its own
capped child cgroup (it does today, `cgroup.py:493-503`); if any path inherits the outer cgroup the
ceiling degenerates to Side B's unbounded case.

---

## Failure-mode matrix

| Design | profiling ABSENT | anchor/floor STALE | Net |
|---|---|---|---|
| **Side A (anchor+model)** | linear fallback → **bounded** cap (loose past knee); step dies alone via oom.group → **FAIL-SAFE** | scaled cap may be too tight → node **self-OOMs**, loud+local, no neighbour blast → **FAIL-SAFE(-ish)** | never unbounded; can false-kill or over-provision |
| **Side B (no static cap)** | **no inner bound → UNBOUNDED**; outer-OOM can kill a **neighbour** → **FAIL-SILENT** | N/A — no anchor to go stale (structural advantage) | safe *only when* a profile exists (today 1/132) |
| **Hybrid (floor/model/ceiling)** | `= floor`, ceiling-clamped → **Side-A-lite, bounded** → **FAIL-SAFE** | model overrides a low floor upward; ceiling caps a high floor → **FAIL-SAFE** | bounded in every quadrant *iff* all three wired + step keeps its own capped cgroup |

The single decisive asymmetry: **Side A / Hybrid fail SAFE (bounded, predictable, self-local) when
profiling is absent; Side B fails SILENT (unbounded, neighbour-killing) exactly there** — and
profiling-absent is the current empirical norm (1/132). Side B's compensating virtue is that its
entire stale column is empty. The owner's torn-ness is well-founded: it is "predictable-but-needs-a-
derived-number" vs. "nothing-to-derive-but-unbounded-without-data."

---

## Negative-test proposal (for whichever design is adopted)

Bind the guard to the fact it claims (Proxy-Binding axis): a cap is only real if a violator **dies**
AND its neighbours **survive**. Three-part bracket, tied to the `oom-blast-radius` /
`memory.oom.group` sibling (primitive already present, `cgroup.py:503`):

1. **Violation caught (the guard is not inert).** Plant a node whose command deterministically
   allocates *above* its scaled/operative cap (e.g. a `stress`-style `mmap`+touch of `cap + 512 MiB`
   at the exact `inner_jobs` the plan assigned it). Assert the runner reports it **OOM-KILLED** with
   `oom_kill >= 1` from `memory.events` (`scheduler.py:472`, `model.py:182`) — proving the enforced
   cap really governs the running process. Verify the enforced number by reading the *live* child
   cgroup's `memory.max` and confirming it equals the width-scaled value the plan chose (not the bare
   file number) — i.e. check the running thing, not the config.

2. **N legitimate neighbours clean (the guard is not permissive / blast-radius contained).** Run
   the violator concurrently with **N = 4** sibling nodes each sized comfortably under its own cap
   (independent nodes, no dep edge, co-schedulable per `schedulable_peak_mem_bytes`). Assert all 4
   complete `ok=True` with `oom_kill = 0` — proving the OOM was contained to the violator's cgroup
   and did **not** spill to the outer box and kill a neighbour. (This is the case Side B fails:
   repeat this test with the cap removed and the neighbour-kill should reproduce, which is itself the
   evidence for keeping an inner cap.)

3. **Plant cleans up.** After the run, assert the violator's child cgroup is torn down
   (`cgroup.kill` first, per `scheduler.rs:21`) and no orphaned memory/cgroup remains, so the test is
   repeatable and does not poison the next run.

Positive companion (so the guard is not one that "refuses everything"): a node sized **just under**
its scaled cap at the same width must complete `ok=True` — proving the cap is not so tight it kills
legitimate work. Both legs required: negative proves it kills the violator, positive proves it
spares the compliant.

For the **Hybrid** specifically, add two policy-boundary negatives: (i) with profiling absent,
confirm the enforced cap equals the *floor* (not zero, not unbounded); (ii) with a deliberately
stale-low floor + a profile present, confirm the *model* value is enforced (floor did not pin the cap
below the learned need).

---

## Open questions

1. **Anchor `j` choice (Side A / Hybrid floor).** At what `j` do we state the anchor? The debug
   curve knees by ~j8 and is flat to j316 — stating at/above the knee makes the scaler nearly a
   no-op (good) but requires knowing each node's knee. Is a per-node knee cheap to learn, or do we
   assume a global "state at j=8"?
2. **Does the model interpolate/extrapolate, or only use measured widths?** Today `_cpa_admissible`
   (`estimates.py:887-922`) explicitly refuses to interpolate wall between measured widths. A memory
   model that only trusts measured widths will have **no value** at an unseen width (e.g. the box is
   j=48 but the store only has j=8, j=32) — does it fall back to the floor, or to the nearest
   measured width, or extrapolate along the (saturating!) curve? Linear extrapolation past the knee
   is exactly the over-provisioning trap.
3. **Wiring gap 7.** Adopting anything width-aware requires routing the width-scaled value into the
   enforcement write at `scheduler.py:361` (today it writes the unscaled `step_mem_cap_bytes`). Is
   that a clean change, and does it need the CPA-allocated per-step `alloc_inner_jobs`
   (`estimates.py:711`) to be threaded into the scheduler?
4. **`SpeedupLevel` memory axis.** Adding `peak_bytes` to `SpeedupLevel` (`estimates.py:519`) and a
   width-aware memory summary touches the cross-language differential-parity contract
   (`estimates.py:23-27`, integer-rank percentile). Any new memory-curve arithmetic must be
   byte-identical py↔rs — extra implementation cost on both sides.
5. **How much profiling is enough?** Side B and the Hybrid's operative-value both need coverage far
   above tonight's 1/132. Is there a plan to backfill per-node `{j, peak_bytes}` across the target
   machines, and how many samples before the store is trusted (today `DEFAULT_MIN_SAMPLES = 1`,
   `estimates.py:81`)?
6. **Outer-ceiling correctness (Hybrid).** The ceiling only protects neighbours while each step
   keeps its own capped child cgroup. Is there any code path (degraded-enforcement warning,
   `ambient.py:25`; failed `memory.max` write running uncapped, `protocols.py:27`) where a step
   silently inherits the outer cgroup and the ceiling degrades to Side B's unbounded case?

---

*No verdict. The decision reduces to: accept a derived-and-maintained number that fails safe
everywhere (Side A), or remove the number and accept unbounded failure wherever profiling is absent
which is today's norm (Side B) — or wire all three inputs so each does its own job (Hybrid), at the
cost of building the width-aware memory model the code currently stops one step short of.*
