# Affected-test-selection: how much of the suite is genuinely affected?

**Question.** #1500 / #1529 wired `ci/select-tests.rs` (footprint → skip / selective / full)
into `validate.sh` and `ci-portable.yml`. Width is exhausted (DAG ceiling 4.24x, j-sweep flat
at j>=5, `strict_compat` alone ~47% of the critical path), so *not running tests a commit cannot
affect* is one of the two remaining CI levers. But a selector only pays off if real commits are
genuinely narrow. **Across a real recent window of commits, what fraction of the suite does
selection actually skip — and does the selector correctly narrow for narrow commits while falling
back to FULL for shared-code commits?**

This is the MEASURE half of `wire-affected-test-selection-and-measure`. The WIRE half already
landed (#1529, `e4d5d5b8`/`be7e26cd` ancestral on `rrnewton/hermit:main`). Note the hosted-CI
path is currently unreachable (#1575 removed `ci-portable.yml`'s `pull_request` trigger, so every
hosted run takes the FULL path); the live, measurable path is LOCAL `validate.sh --selective` and
the selector's decisions themselves, which is what this experiment measures directly.

## Method

For each of the last **N=200 first-parent commits** on `hermit` (window
**2026-08-01 → 2026-08-04**, ~200 commits in 3 days = high bot-fleet churn), compute the commit's
changed-file footprint (`git diff --name-only <sha>^1 <sha>`) and feed it to
`ci/select-tests.rs --files - --format json`. First-parent = one landed unit per merge/commit,
which is the unit CI evaluates. Selection rules are the CURRENT `ci/{test-footprints,dag/portable,
portable-shards,expected-e2e-plan}.json` at hermit `8f656b4d` (the window is only 3 days, so the
map is contemporaneous). Read-only: nothing is checked out or mutated.

Universe (denominators, from a forced-full run): **47 DAG nodes, 11 test shards, 70 e2e cells.**

Reproduce: `python3 sweep.py 200` (needs `rust-script`; run under `with-proxy`). Per-commit rows
in `results.csv`; labeled examples via `python3 examples.py`.

## Results

### Decision distribution (N=200)

| decision  | count | share |
|-----------|-------|-------|
| skip      |  24   | 12.0% |
| selective |  63   | 31.5% |
| full      | 113   | 56.5% |

**Reducible (skip+selective) = 87/200 = 43.5%** — cross-checks an independent prior (the sibling
wallclock task measured ~42/100 reducible over a different 100-run sample).

### How much work selection actually removes over the window

| unit          | executed | all-full | **saved** |
|---------------|----------|----------|-----------|
| DAG nodes     |   7,624  |   9,400  | **18.9%** |
| test shards   |   1,724  |   2,200  | **21.6%** |
| e2e cells     |  12,013  |  14,000  | **14.2%** |

e2e cells dominate runner-minutes, so **~14% is the most decision-relevant figure**: over this
window, selection removes roughly one-seventh of the expensive CI work. Node/shard counts are
proxies; the ~14–22% band is the honest envelope.

### Selective is often not very narrow

Of the 63 selective commits, only **3 run <40% of nodes**; 30 run 40–70%, 30 run >70%. The
package-level reverse-dependency closure is why: a `detcore-dbi` change pulls Hermit's other
backend test nodes because `detcore-dbi` is a Cargo dependency of `hermit` (documented in the
tool's own self-test, `select-tests.rs:1047`). Backend-parity commits are the genuinely narrow
win (9/47 nodes, 1 shard, 8 dbi-only cells).

### Why commits go FULL (n=113)

| reason                       | count | share of full |
|------------------------------|-------|---------------|
| `force_full[ci/**]`          |  59   | 52.2%         |
| `force_full[validate.sh]`    |  19   | 16.8%         |
| unmapped → conservative full |  17   | 15.0%         |
| `force_full[.github/**]`     |  12   | 10.6%         |
| `force_full[Cargo.lock]`     |   4   |  3.5%         |
| `force_full[scripts/**]`     |   2   |  1.8%         |

**~80% of FULL decisions are CI/build/workflow-machinery churn** (`ci/**`, `validate.sh`,
`.github/**`) — changes that *correctly* force full because they can alter how anything is tested.
This window was unusually CI-tooling-heavy (the fleet was rebuilding its own CI), which depresses
the reducible fraction. In an ordinary product-development window (backend parity, syscall work),
the reducible fraction would be higher; **the 43.5% / 14–22% figures are a lower bound colored by
window composition, not a fixed property of the selector.**

## Both directions verified on real commits

A selector that always narrows is as broken as a detector that always passes. Real history
excludes both defects (56.5% full ⇒ not always-narrowing; 43.5% reducible ⇒ not always-full):

- **narrow → skip** — `0f891e43` "Reconcile the workspace package map", `6dd9e8fe` "ci(demo-hot-path):
  install bpftool…" → 0 nodes (all files CI-irrelevant).
- **narrow → selective** — `9e6b4ea3` "backend-parity: pair fixture by singleton fallback" →
  9/47 nodes, 1/11 shards, 8/70 cells, e2e backend-scoped to `dbi`.
- **shared-code → full** — `525627be` (Reverie pin bump) → `validate.sh` force_full;
  `6e24a159` (digest fold) → `ci/test-footprints.json` force_full; `db9f3bb2` (Cargo floor) →
  manifest `Cargo.toml` force_full.
- **fail-safe fires** — `b7960a15` "Remove obsolete detcore-liteinst manifest" → unmapped path
  `detcore-liteinst/Cargo.toml` → **conservative full** (unknown ⇒ full, never a silent narrow).

## Why a selective green is safe (ledger binding)

Selection is only safe if a smaller run records that it ran a subset, or it becomes a fake-green
generator (a smaller run reporting the same green). `validate.sh` binds this: `--selective` sets
`VALIDATION_PROFILE="selective"` (`validate.sh:294`), which is written into the ledger record
(`:954`); ETA/history matching is profile-scoped (`:564`); and a `select-tests.rs` failure falls
back to the FULL lane (`:3760`). So a selective green carries `profile=selective` and cannot be
paired with full coverage — the "carry the condition with the value" cure, resting on the 3-field
ledger predicate (executed / filtered / profile) landed at parent `8c53eb5`.

## Interpretation & the one actionable lever

Selection is real but bounded: **~14–22% of CI work in this window**, concentrated on backend-parity
and docs/CI-cosmetic commits. It is not the 70–86% the best-case spot-examples imply; that gap is
the point. The remaining width lever (splitting the serial `strict_compat` tail) is orthogonal and
likely larger for the FULL-path majority.

The one footprint-map improvement (separate from wiring) is the 17 unmapped→full commits:
`.claude/skills/**` (6 commits — should map to `ci_irrelevant` ⇒ skip) and `common/**` crates
(`digest`, `edit-distance`, `test-allocator` — genuine map gaps ⇒ could be selective). `Makefile`
and per-crate `Cargo.toml` conservative-full is defensible (build-affecting). Closing these would
raise the reducible fraction a few points; it is not required for correctness (fail-safe already
handles them).
