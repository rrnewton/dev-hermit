# strict_compat's 8 "artificial deps" — the cargo-build-lock refinement

Task: `prune-artificial-deps-on-validate-critical-path` (the LOCAL half; the
GitHub half is `hermit-ghdag`/#1569). Date 2026-08-03. Verified at hermit
`e8a0d8d3`, method = trace + one empirical probe, not recollection.

## What was already established (prior audit, hermit-parspeed)

- `test.strict_compat.cmd` = `./validate.sh --portable-strict-compat-only …`,
  which **self-builds** its own `cargo build --release -p hermit --features
  third-party-backends` (validate.sh:4074-4076). Its 8 deps
  (`lint.clippy, doc.doctests, doc.rustdoc, test.regular_crates,
  build.flaky_harnesses, test.hermit_unit, test.detcore_unit,
  test.rr_suite_contract`) **produce nothing it consumes** ⇒ data-independent.
- Calibrated makespan sim (per-node warm durations from the owner's real
  a034f39c PASS log, cap1 sim 454s vs real 455s): **pruning yields ≈0 wall at
  the shipped `hermit_guest:1` cap**; 93s/29% only appears once the cap is
  raised to 2. The binding constraint is the resource cap, not the graph.

## New this run — two verified facts that change the recommended fix

### 1. All 47 nodes share ONE `target/` (no per-node isolation)

Static check of `ci/dag/portable.json`: **zero** steps set `CARGO_TARGET_DIR`
or `--target-dir`. Every cargo invocation (build.workspace, clippy, doctests,
rustdoc, nextest, the unit tests, and strict_compat's own release build) writes
to the same `hermit/target/`. The "shared target dir" premise is real.

### 2. cargo's own build-directory lock already serializes concurrent cargo

Empirical probe (throwaway crate in gitignored `scratch/`, negligible CPU;
reproduction below): 3 concurrent `cargo build` against one target dir emitted
**2× "Blocking waiting for file lock on build directory" + 7× "…on package
cache"**. Cargo does not corrupt or truly overlap concurrent builds against a
shared target — the later processes **block on cargo's own lock**.

Reproduction:
```bash
mkdir -p /tmp/lp/src && cd /tmp/lp
printf '[package]\nname="lp"\nversion="0.0.0"\nedition="2021"\n[dependencies]\nsyn={version="2",features=["full"]}\nquote="1"\n' > Cargo.toml
printf 'fn main(){}\n' > src/main.rs
with-proxy cargo fetch -q
cargo clean -q
( cargo build 2>b1 & cargo build 2>b2 & cargo build 2>b3 & wait )
grep -h "Blocking waiting for file lock" b1 b2 b3   # do NOT pass -q (suppresses it)
```

## Consequence: the recommended fix changes

The "8 edges are an implicit mutex on the shared target dir; cut them naively and
you reintroduce the concurrent-cargo/DBI-cmake flake" framing is **half-right**:

- **Cutting the edges is SAFE from cargo corruption.** cargo's build-dir lock
  (fact 2) still holds the same-dir write invariant, and `hermit_guest:1` still
  serializes strict_compat against the two `hermit_guest` test nodes. Neither
  invariant depends on the DAG edges.
- **But cutting the edges alone buys ≈0 wall clock** (fact 2 re-serializes the
  builds cargo-side; and at cap=1 the guest-RUN phase is capped anyway — matches
  the calibrated sim). So the naive prune **fails the acceptance criterion
  "wall clock dropped."**

An ordering constraint encoded as a data dependency is invisible in the graph —
but here the ordering is *doubly* enforced (cargo lock + resource cap), so the
edges are largely **redundant**, not load-bearing. Deleting them is a clarity
win, not a speed win.

To actually get wall-clock speedup from these builds overlapping you must defeat
cargo's serializing lock, i.e. give each build node its **own** `CARGO_TARGET_DIR`
— which is the "separate target dirs" the owner named. That is a real fix but it
is **coupled**, not standalone:

1. **Separate target dirs** so builds overlap instead of queuing on cargo's lock.
   Cost/hazard: 3-6× disk + N× dependency rebuilds unless a shared registry /
   sccache is used; and per-node dirs must be **clean**, not reflink-seeded
   (reflink-seeded target/ poisons the DBI/DynamoRIO cmake cache — see memory
   `reflink-seed-cmake-cache-cross-worktree-pollution`).
2. **Raise `hermit_guest` 1→N** (F2) — the actual warm-1.58x mover; owner-gated
   because cap=1 is partly deliberate PMU/timing-flake protection, invisible to
   CI, big-box-only.
3. **Shard `test.strict_compat`** (F3) — a 175s-of-600s monolith that sets the
   ~180s warm floor no parallelism beats until sharded.

## Acceptance gate is unmet by measurement, not by analysis

Acceptance = "shorter wall clock AND green under concurrent load." A valid A/B
needs a quiet box; the host was at **81.7% executing CPU** (ci-hub load-probe,
NOT SUITABLE per the 50% policy). Running it now would be contaminated and would
starve the fleet. The measured A/B (cap1 vs cap2 × edges-intact vs pruned ×
shared-dir vs per-node-dir, warm, ≥5 reps, zero new timeouts/PMU-skid) is the
remaining gate.

## Bottom line

- Data-independence: CONFIRMED. Shared target dir: CONFIRMED. cargo self-lock:
  CONFIRMED empirically.
- The naive prune is **safe but inert** at the shipped config — refuted as a
  standalone wall-clock lever for two independently sufficient reasons
  (hermit_guest:1 cap; cargo build-dir lock).
- The real win is the **coupled** change (per-node target dirs + cap raise +
  shard), and it cannot be validated to the owner's "green under load" bar until
  a quiet box is available. Keep the DAG edits in the shared graph
  (`ci/dag/portable.json`) so #1569's GitHub convergence inherits them.
