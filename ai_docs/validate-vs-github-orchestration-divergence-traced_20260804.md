# Traced call-path divergence: local validate vs GitHub portable (2026-08-04)

Author: hermit-226 (validate-speedup workstream). Method: **call-path tracing**,
not string-grep. The original task premise ("validate.sh never calls
safe-ci-dag-runner") was a grep artifact and is REFUTED; the real divergence is
recorded here so the convergence work (owner Phase 1, PR #1569) rests on verified
ground.

Verified against `~/work/dev-hermit/hermit` on `origin/main` fetched
2026-08-04, hermit tip `b824a348` (primary checkout) / `origin/main` post-#1574.

## The three orchestration paths (traced)

| Path | Trigger | Orchestrator | node commands from |
| --- | --- | --- | --- |
| **Local** `validate.sh` (full) | `./validate.sh`, `make validate` | **safe-ci-dag-runner** via `ci/run-dag.sh <lane>` | `ci/dag/<lane>.json` |
| **GitHub portable — AUTHORITATIVE** `ci-portable.yml` job `regular` | push→main, merge_group, workflow_dispatch (NO pull_request) | **GitHub Actions matrix** (`needs:` graph + `ci/portable-shards.json`); `ci/run-node.sh` runs nodes | `ci/dag/portable.json` (`.cmd` only) |
| **GitHub `ci-dag.yml`** ("CI (DAG runner, manual)") | `workflow_dispatch` ONLY — not required, not on push/PR | **safe-ci-dag-runner** via `ci/run-dag.sh` | `ci/dag/<lane>.json` |
| **GitHub privileged** `ci-privileged.yml`, `validation-levels.yml` | self-hosted | **safe-ci-dag-runner** via `ci/run-dag.sh privileged` | `ci/dag/privileged.json` |

Counts (grep of `.github/workflows/`, corroborating the trace):
`ci-portable.yml` = `run-node.sh`×6, `run-dag.sh`×0. `ci-dag.yml` = run-dag×2.
`ci-privileged.yml` / `validation-levels.yml` = run-dag×1 each.

## What each shim actually does (traced, not assumed)

- `ci/run-dag.sh <lane> [args...]` → `exec safe-ci-dag-runner run --dag
  ci/dag/<lane>.json <args>`. The **runner** honours the DAG's `deps`, resource
  caps (`hermit_guest=1`, `manifest_guest=4`), scheduler width (`-j`), and
  per-node perf. This is a single-machine parallel scheduler.
- `ci/run-node.sh <lane> <group.job>[,...]` → for each key, `jq`-extract that
  node's `.cmd` from `ci/dag/<lane>.json`, then `bash -o pipefail -c "$cmd"`,
  serially, stop at first failure, **dependencies NOT run** (they arrive as
  prebuilt artifacts from an upstream job). No runner. No deps. No resource caps.

## The precise divergence (this is the owner's Phase-1 target)

Both paths share the node **command strings** in `ci/dag/portable.json` (single
source of truth for `.cmd`). They do **not** share the **graph execution**:

- Local: one runner executes the whole graph, deriving order + parallelism from
  `deps` in portable.json.
- Authoritative GitHub: the graph structure is **re-encoded twice more** — the
  YAML `needs:` DAG between jobs, and `ci/portable-shards.json` (which node runs
  in which shard). `ci/check-shard-coverage.sh` fail-closes if the shard map
  doesn't cover every portable node, so the three encodings are kept in
  correspondence — but that is a *correspondence between representations*, not
  *one graph run by one runner*.

**Why GitHub doesn't just run `run-dag.sh`:** per `ci-portable.yml`'s own header,
running the whole 46-node DAG on ONE `ubuntu-latest` via the runner was ~32 min
serial (single machine). GitHub's parallelism comes from **many VMs** (matrix
jobs), which a single-machine runner cannot provide. So the fan-out + shard map
is deliberate, and convergence is non-trivial: it needs either the runner driving
a multi-VM fan-out, or the GitHub matrix *derived from* portable.json so the graph
lives in exactly one place.

## Status of the convergence (do NOT re-derive)

- **PR #1569 (DRAFT, hermit-ghdag)** is the convergence fix: it rewrites
  `run-node.sh` to run each node via `<runner> run --only --perf-dir` (so the
  authoritative portable check ALSO goes through safe-ci-dag-runner), plus the
  coupled gitlink flip `84580db→0eb4203` (`run --only` does not exist before
  0eb4203). See `ai_docs/ghdag-handoff-phase1-pr1569_20260803T2228Z.md`.
- **LAND-HOLD (owner):** #1569 must NOT land until the boundary SHA of the
  portable-CI regression (~2 pass/h over 24h collapsing to ~1 pass/h in the last
  ~8h) is pinned — landing a large portable rewrite pre-boundary destroys
  attribution. This is the current bottleneck gating convergence.

## Width side (validate-484s task) — landed + measured

- **PR #1574 LANDED** (merged 2026-08-04T00:50Z). `origin/main:validate.sh:462-464`
  `CI_DAG_JOBS_DEFAULT = host_cpus/8`, floor 2, cap 16 → 16 on the 316-CPU box;
  ubuntu-latest → 2 (OOM-safe). Both sites (`:3682`, `:3796`) read
  `${CI_DAG_JOBS:-$CI_DAG_JOBS_DEFAULT}` — single source of truth, in effect on
  main (verified).
- **MEASURED (predecessor, warm tree, devbig014 316-core, all 47 nodes,
  THIRD_PARTY_BUILD_JOBS=32):** -j2 (owner) 484s @ CPU/wall 2.6x; -j16 426s @
  2.35x. Raising width 8× buys **~12% warm wall**. Cold/build-heavy: 2.6x→~8–21x
  CPU/wall. **On a warm tree width is nearly a no-op**; the gate is
  critical-path-bound (the `test.strict_compat` JOIN barrier ran ~90–110s alone +
  `e2e.manifest_language_runtimes` ~106s), not throughput-bound.
- **NOT MEASURED / NOT CLAIMED:** "validate is Nx faster." Contraindicated warm.
  Real levers = shorten/parallelize `test.strict_compat` (prune its ~8 artificial
  deps) + `e2e.manifest_language_runtimes`, and smart selection (already wired,
  `ci/select-tests.rs`) — not brute width.
