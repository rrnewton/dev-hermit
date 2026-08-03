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

# Immediately after an admin/speculative land, arm both exact-SHA verifiers.
./ci-hub/ci-hub arm-land <40-hex-landed-sha> --land-mode admin

# Inspect or recover polling for obligations that are still open.
./ci-hub/ci-hub obligations
./ci-hub/ci-hub watch-obligations --once

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
| `remediation/` | Mandatory post-land dual verification, exact-SHA local execution, watcher, and remediation recommendation. | CI-history ingestion or automatic source-code reverts. |
| `validate/` | Legacy/local `validate.sh` ledger aggregation and linkage to retained profiles. | The generic DAG runner/profile format. |
| `runners/` | Self-hosted runner image, lifecycle, and host status tooling. | Generic CI scheduling. |
| `landing/` | Shared-file **landing mutex** (`land-lock`) that serializes PR landings touching the shared manifest registries. | The land sequence itself (re-union/push/stamp/merge). |

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
- every unresolved speculative-land obligation makes `health` nonzero. A
  verification failure remains unresolved until an explicit fix-forward or
  revert is recorded with `resolve-obligation`.

The outer ORC workflow calls `bin/health-tick` every five minutes. The pinned
tick engine reads `health/tick-hub.yaml`; dev-hermit probes live beside it. A
detached per-obligation watcher records terminal state continuously, while the
dedicated ORC workflow `hermit-dev-speculative-land-remediation-v1` polls every
15 seconds and sends a deduplicated coordinator wakeup on failure. The
five-minute tick is the recovery path if either fast watcher is lost.

## Speculative-land obligation contract

`arm-land` is mandatory immediately after an admin/speculative land. It first
appends an OPEN event to `ignored/ci-hub/obligations.jsonl`, then concurrently:

1. clones Hermit into `ignored/ci-hub/obligations/<id>/hermit`, checks out the
   exact landed commit detached, and runs full `./validate.sh --no-label-pr`;
2. confirms `CI (GitHub-managed portable)` exists for the same SHA, dispatching
   it only when GitHub `main` still equals that SHA; and
3. launches a detached watcher whose durable log lives beside the local
   validation log.

The event log is append-only and locked. Every transition retains timestamps,
verification scope (`total` here), GitHub run IDs, local log path, and failure
recommendation for the history and green-time consumers. The local verifier is
wrapped by `bin/tool-cost`; its history-derived estimate and atomic actual
wall/CPU JSON are copied into the obligation record. Stable cross-store joins
are `landed_sha → gha-runs.csv:head_sha`, `landed_sha → local-runs.csv:git_sha`,
and `github.run_ids → gha-runs.csv:run_id`.

The first failing verifier immediately changes the obligation to
`remediation_required`: revert is recommended when the bad land is still the
main tip; fix-forward is recommended after main has advanced. The fast ORC
workflow raises that recommendation within one 15-second poll. The mechanism
never performs a blind automatic revert. After the chosen repair lands, close
the obligation with:

```bash
./ci-hub/ci-hub resolve-obligation <id> --kind fix-forward --ref <repair-sha>
# or: --kind revert --ref <revert-sha>
```

## Why this exists

The [2026-08-03 skills audit](../ai_docs/transient/2026-08-03-skills-audit.md#where-are-the-ci-health-skills)
found CI knowledge fragmented across the coordinator `hermit-ci` charter,
Hermit's debugging workflow, five dated state skills, standalone scripts, and
an ORC-only poll registration. Skills now point here for live commands and
ownership. Skills describe when/how an agent should act; this hub owns the
actual code, current query paths, state schema, and operator entrypoints.
