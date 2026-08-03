# dev-hermit CI hub

`ci-hub/` is the single versioned home for dev-hermit CI operations and CI
knowledge. Treat each subdirectory as an object with one responsibility and a
stable public entrypoint; do not add new CI scripts under `scripts/`, `ops/`, or
an experiment directory.

## Public entrypoints

Run `./ci-hub/ci-hub help` for the command list. The core workflows are:

```bash
# Pull fresh open-PR CI state from GitHub and classify it.
./ci-hub/ci-hub fresh

# Summarize current-main plus open-PR health.
./ci-hub/ci-hub health

# Incrementally refresh the local GitHub/local-run history store.
./ci-hub/ci-hub refresh-history

# Query the local commit/CI history store.
./ci-hub/ci-hub history

# Inspect local validate-run history, retained profiles, or runner health.
./ci-hub/ci-hub local-history --since 2026-08-03
./ci-hub/ci-hub runner-health --all
```

Networked commands use `with-proxy` internally.

## Object map and ownership

| Object | Owns | Does not own |
| --- | --- | --- |
| `bin/` | Stable wrappers and pinned shared-tool materialization. | CI classification, scheduling, or history logic. |
| `health/` | Dev-hermit-specific current-main, PR, primary, and agent health adapters plus tick configuration. | Generic cadence or PR-CI classification engines. |
| `history/` | Incremental/idempotent GitHub Actions and local commit/CI knowledge store, ingestion, and queries. | Current-live status presentation. |
| `validate/` | Legacy/local `validate.sh` ledger aggregation and linkage to retained profiles. | The generic DAG runner/profile format. |
| `runners/` | Self-hosted runner image, lifecycle, and host status tooling. | Generic CI scheduling. |

Runtime data is untracked under `ignored/ci-hub/`; versioned code and schemas
live here. Reproducible experiment producers and their frozen outputs remain
with their experiment, but new reusable CI-history queries belong in
`history/`.

The `history` and `refresh-history` front-door commands fall back to the local
validate-run aggregator until the unified store is present. Once
`history/query.py` and `history/ingest.py` exist, dispatch switches to them
automatically without changing callers.

## Shared agent-utils boundary

The `agent-utils` gitlink is a hard dependency, not a source to copy. The
single `bin/agent-tool` adapter materializes the exact pinned commit and runs:

- `pr-landing-planner`: open-PR CI collection/classification. `health/pr_status.py`
  only combines its JSON for the Hermit and Reverie forks; the retired parent
  classifier is not duplicated here.
- `tick-hub`: cadence, gates, and stable `HEALTH`/`ACTION`/`NOTE`/`ERROR`
  emission. Dev-hermit owns only `health/tick-hub.yaml` and project probes.
- `safe-ci-dag-runner`: validation DAG execution and retained profile
  summaries. `validate/aggregate.py` links local run records to that store; it
  does not reimplement the runner or profile schema.

Before adding code, audit the pinned `agent-utils` APIs. If the capability is
generic, add it there and link/use it here.

## Health meanings

`health` is deliberately fail-loud:

- current-main `red` returns 1; missing/unqueryable data returns 2;
- open-PR health is unhealthy for a real regression or systemic runner outage;
- stale/evaluate-once/flaky reds retain their agent-utils classification and
  are not silently relabeled as product failures;
- pending current-main work is displayed as pending and must not be claimed
  green.

The outer ORC workflow calls `bin/health-tick` every five minutes. The pinned
tick engine reads `health/tick-hub.yaml`; dev-hermit probes live beside it.

## Why this exists

The [2026-08-03 skills audit](../ai_docs/transient/2026-08-03-skills-audit.md#where-are-the-ci-health-skills)
found CI knowledge fragmented across the coordinator `hermit-ci` charter,
Hermit's debugging workflow, five dated state skills, standalone scripts, and
an ORC-only poll registration. Skills now point here for live commands and
ownership. Skills describe when/how an agent should act; this hub owns the
actual code, current query paths, state schema, and operator entrypoints.
