# validate.sh TRUE-dependency audit + critical path + migration order

Task: `validate-sh-should-be-dag-runner-orchestrated` (P0). This is the
non-A/B half the owner authorized: TRUE-dependency inventory, critical path /
theoretical max speedup, and incremental migration order. The measured cap-raise
A/B is deferred (cap-blocked slot; owner signals when free).

Companion to `measured_owner_run_addendum.md`. All model numbers here come from
`true_deps.py` in this directory (reads the SHIPPED DAG
`hermit/ci/dag/portable.json` for deps + resource tags, overlays MEASURED warm
durations from `measured_portable_nodes.tsv`, owner green run `a034f39c`).

## 0. Framing corrections (both premises were stale)

- **"36 serial gates" is stale.** validate.sh `--full` is DAG-orchestrated:
  `ci/run-dag.sh` → `safe-ci-dag-runner run`. Reality = **5 validate.sh
  top-level gates**, dominated by the **47-node portable DAG** (+ a 7-node
  privileged DAG, ~26s, effectively serial, not the bottleneck). There is no
  36-node serial script to convert; the orchestrator already exists.
- **"1.58x = -j 2" is only half right.** `-j 2` is the outer scheduler width,
  but it is NOT the binding limit. The binding limit is the DAG's
  `resource_caps: {hermit_guest: 1}`, which serializes the ~16 latency-bound
  determinism tests onto a single lane. Proof (model, warm):

  | | outer-j=2 | outer-j=4 | outer-j=16 |
  |---|---|---|---|
  | **hermit_guest cap=1** | 461s | 449s | 449s |
  | **hermit_guest cap=4** | 409s | 298s | 229s |

  At cap=1, raising `-j` from 2→16 buys **12s**. The lever is the resource cap,
  not `-j`. `-j` only starts mattering once the cap is raised.

## 1. Method & model validation

List-scheduler simulation (deps + `resource_caps` + outer `-j`), longest-job-
first, same model the addendum validated. Two duration regimes:
- **WARM** = measured per-node seconds (owner run `a034f39c`), total work 677s.
- **COLD** = `hint.est_duration_s` from the DAG as a cold-cache proxy, total 5360s.

Model validation: shipped config (cap=1, j16) → **449s warm**, vs the measured
wall **455s**. Within 6s → the model tracks reality at the shipped operating
point.

## 2. TRUE-dependency inventory

Every declared edge falls in one of three classes:

- **DATA** — the successor consumes an artifact the predecessor produces. Real;
  keep.
- **SERIALIZATION** — no artifact flow, but concurrent execution is unsafe on a
  shared resource (concurrent `cargo`/CMake on one `target/`, PMU contention).
  Pragmatic; keep unless the resource is proven safe to share.
- **ORDERING** — pure "run X before Y" preference, no artifact and no resource
  conflict. Removable with zero risk once verified.

Two ORDERING edges dominate the graph and are the only defensible prunes:

### 2a. `build.workspace ← e2e.metadata` — ORDERING (HIGH confidence)
`e2e.metadata` validates the e2e inventory JSON; it produces nothing the Rust
compiler consumes. It is the DAG's near-universal root (build.workspace,
build.manifest_guests, and all 11 manifest buckets chain through it), so it
gates the whole graph behind a 7s (warm) / but ordering-only step. Cold, this
matters more: it delays the 360s workspace compile. **Prune → build.workspace
becomes a true root.** (Manifest buckets legitimately read the inventory, so
keep `e2e.metadata` on the manifest edges — prune it only off build.workspace.)

### 2b. `test.strict_compat ← {8 gates}` — ORDERING (HIGH), sole DATA dep = build.workspace
`test.strict_compat` (175s warm / 600s cold — the tail node) declares 8 deps:
`lint.clippy, doc.doctests, doc.rustdoc, test.regular_crates,
build.flaky_harnesses, test.hermit_unit, test.detcore_unit,
test.rr_suite_contract`. It runs `./validate.sh --portable-strict-compat-only`,
which needs the **built hermit binary** and nothing else. The 8 are a "gate the
big blocking matrix behind the cheap checks" ordering choice. **Prune to
`[build.workspace]`.**
- Confidence: HIGH that the 8 are ordering, not data. MEDIUM that strict_compat
  needs *nothing* beyond the workspace build — **verify** the compat run reuses
  the prebuilt binary rather than rebuilding (if it rebuilds, its true dep is
  still just the source tree, not the 8 gates).

### 2c. Kept as SERIALIZATION (not pruned)
- `build.dbi_release ← build.workspace`, `build.sabre_release ←
  build.dbi_release`, `build.liteinst_runtime_release ← build.dbi_release`:
  chained release builds. No artifact flow proven, but concurrent cargo/CMake on
  shared `target/` is a known hazard (cf. reflink cmake-cache pollution). Keep
  until proven isolable.
- `lint.clippy/doc.*/test.regular_crates ← build.workspace`: recompile-adjacent;
  keep to avoid concurrent-cargo contention. (Not on any critical path anyway.)

Full 47-node inventory with per-node warm/cold/resource/deps: run
`python3 true_deps.py` (table emitted by the inventory helper) — kept
reproducible rather than pasted to avoid drift.

## 3. Critical path & theoretical max speedup

Dep-only critical path (longest chain, ignoring resource caps):

| | AS-DECLARED | TRUE-DEPS (2a+2b pruned) |
|---|---|---|
| **warm** | 191s (e2e.metadata→build.workspace→doc.doctests→strict_compat) | **180s** (build.workspace→strict_compat) |
| **cold** | 1265s (…→lint.clippy→strict_compat) | **960s** (build.workspace→strict_compat) |

Theoretical max speedup at infinite workers + no resource cap + TRUE deps:
**3.76x warm** (677/180), **5.58x cold** (5360/960). These are ceilings the
resource cap prevents realizing — see §4.

## 4. The two binding constraints (neither is the dependency graph)

**Removing the spurious edges changes the makespan by ~0 at the shipped cap=1.**
The graph is not what is slow. Two things are:

**(i) `hermit_guest: 1` resource cap.** The 16 hermit_guest-tagged nodes sum to
**437s warm**. Under cap=1 they run strictly serially, so wall ≈ 437s regardless
of dependency edges or `-j`. This IS the ~449s wall. This is the "1.58x" the
owner saw — a resource cap, deliberately set for PMU-safety (concurrent latency-
bound determinism tests on one box risk timeslice/PMU-skid nondeterminism; cf.
load-dependent-timeslice-skid).

**(ii) `test.strict_compat` monolith.** A single 175s warm / 600s cold node.
Even with an infinite cap and pruned deps, the warm floor is **180s**
(build.workspace 5 + strict_compat 175). Nothing parallelizes a single node.

Modeled makespan (warm; gain = as-declared − true-deps):

| hermit_guest cap | as-declared | TRUE-deps | dep-prune gain |
|---|---|---|---|
| 1 (shipped) | 449s | 442s | 7s (cap binds) |
| 2 | 317s | **224s** | 93s (29%) |
| 4 | 229s | **180s** | 49s (21%) |
| 8 | 191s | 180s | 11s (floor) |

Cold shows the same shape, larger: cap=4 as-declared 1385s → true-deps 1050s
(335s / 24%). Reconciliation with the addendum: the addendum's cap-2≈245 /
cap-4≈203 sit between these columns (it used a 13-node hermit set at 430s; this
model uses all 16 tagged nodes at 437s and separates the as-declared vs
true-deps graphs). Same conclusion, finer decomposition.

**Realizable warm ceiling ≈ 2.5x** (449 → 180), gated entirely by the
strict_compat monolith. To go past 2.5x you must shard that node.

## 5. Incremental migration order

Ordered by (payoff × independence). Each step names what it unlocks and whether
it needs the cap-blocked A/B.

**F1 — Prune the two ORDERING edges (§2a, §2b). NO A/B needed.**
- Cost: a DAG edit + one verification (strict_compat reuses the prebuilt binary).
- Direct payoff at shipped cap=1: ~7s warm (negligible) / ~305s cold critical
  path (1265→960). Cold CI runners benefit immediately.
- **Real value: it is the ENABLER for F2.** At cap=2 the entire dep-prune gain
  is 93s (317→224); that gain does not exist until F1 lands. Do F1 first,
  precisely because it is free and unblocks F2's realizable speedup.

**F2 — Raise `hermit_guest` cap 1→2→4. NEEDS the measured A/B.**
- The primary lever. With F1 landed: cap=2 → 224s (2.0x), cap=4 → 180s (2.5x).
- Gated on PMU-safety: does running 2–4 latency-bound determinism tests
  concurrently on the shared box induce timeslice/PMU-skid nondeterminism? That
  is exactly the cap-blocked A/B (raise cap on a big box, diff determinism
  output under load). Raise incrementally (1→2 first) and re-confirm green.
- NOTE: raise the **resource cap**, not just outer `-j`. Outer `-j` alone at
  cap=1 buys 12s (§0 table). The knob that matters is `resource_caps.hermit_guest`
  in the DAG (plus enough outer `-j` to feed it — j≥cap).

**F3 — Shard `test.strict_compat`. Product work; do AFTER F2.**
- Only worthwhile once the cap is raised: at cap=1 a shard can't overlap
  anything; the monolith is the floor only at cap≥3. After F2, strict_compat's
  175s/600s is THE floor (180s warm).
- Split the compat matrix into K independently-schedulable DAG nodes (each an
  e2e-inventory entry). Warm floor then drops from 180s toward the next-largest
  hermit_guest node (command_strict_verify 60s, hermit_integration 58s),
  pushing the ceiling from ~2.5x toward ~7x.

## 6. What still requires the deferred A/B

Everything in F2's realized numbers is model-projected. The measured A/B must
confirm: (a) determinism output is byte-identical at cap≥2 under load, and
(b) actual wall matches the ~224s/180s projection. F1 and the inventory need no
A/B and can proceed now. F3 is product work whose payoff only lands post-F2.

## 7. Reproduction
```
cd experiments/validate_parallelism_audit_20260803
python3 true_deps.py           # critical path + makespan table, warm & cold
```
Inputs: `../../hermit/ci/dag/portable.json` (shipped DAG),
`measured_portable_nodes.tsv` (measured warm durations, owner run a034f39c).
