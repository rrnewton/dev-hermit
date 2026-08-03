# ci-hub history store

The **single** local accumulator of commit/CI knowledge. Do not build a parallel
one — two independently-maintained stores drift (exactly how the hand-maintained
test-footprint map rotted). Everything joins by the documented file-contract
columns below; never import another store module's internals.

## Producers (`ingest.py`)

`ci-hub/history/ingest.py` — incremental and idempotent. Re-running never
duplicates a row and resumes from the last cursor. All GitHub access goes through
`with-proxy gh` (override with `CI_HUB_GH`).

```bash
ci-hub/history/ingest.py                     # incremental, both repos
ci-hub/history/ingest.py --full              # full backfill (recursive windows)
ci-hub/history/ingest.py --since 2026-07-01  # bound the window
ci-hub/history/ingest.py --repo rrnewton/hermit --no-profiles --no-local
```

Dispatched by the front controller: `ci-hub refresh-history [--full] [args…]`
(`ci-hub.rs` `RefreshHistory` seam runs this file when present).

Two ingesters run per invocation:

**(A) GitHub Actions runs → `ignored/ci-hub/gha-runs.csv`.**
Keyed by `(repo, run_id, run_attempt)`; UPSERT by newest `updated_at`, so an
in-progress row is promoted to its terminal conclusion on re-run with **zero
duplicate rows**. Timing is split because the hosted pool is queue-starved and a
single wall figure lies:

- `queue_s = run_started_at − created_at`  (time spent queued)
- `run_s   = updated_at   − run_started_at` (time spent running; blank until terminal)

Incrementality: the cursor `ignored/ci-hub/gha-cursor.json` records
`{last_created_at, max_run_id}` per repo. Every mode fetches through one
**recursive time-window fetcher**: a `created=start..end` window that saturates
(1000 rows — GitHub's hard per-query cap) is bisected in time until it fits, so a
busy drain day (>1000 runs) is never silently truncated. Non-terminal rows older
than the window are additionally re-fetched by id so queued/in_progress runs get
promoted once they finish.

**(B) Per-node CI profiling artifacts → `ignored/ci-hub/gha-profiles/<repo>/…`.**
Downloads GitHub artifacts named `ci-perf-*` (producer: hermit PR #1548, the
portable DAG runner) and unzips their `step_profiles_*.csv` onto local disk.
Idempotent via `gha-profiles/downloaded.json`. Until #1548 lands there are zero
such artifacts and this is a graceful no-op. Also refreshes the local
validate-run history via `ci-hub/validate/aggregate.py --write-global`.

## Consumer (`query.py`)

`ci-hub/history/query.py` — read-only, no GitHub access. Dispatched by
`ci-hub history …`.

```bash
# Per-DAG-node CPU-second budgets for cpu_timeout derivation.
ci-hub/history/query.py node-cpu-budgets --repo rrnewton/hermit --format csv
# → node, n_samples, max_cpu_s, p95_cpu_s, p50_cpu_s, max_wall_s,
#   suggested_cpu_timeout = round(max_cpu_s * 1.5), thin
# n_samples < 5 → suggested null + thin=true (never derive a timeout from noise).
# CPU seconds = step_profiles user_s + sys_s; node id = the `step` column
# ({group}.{job}). Reads local .safe-ci-dag-runner/profiles/ AND downloaded
# ci-perf step_profiles together.

# % of main-branch wall-clock time green (owner headline metric; DERIVED, never
# estimated).
ci-hub/history/query.py green-time --repo rrnewton/hermit
# state timeline over authoritative main runs; fraction of time conclusion==success.

# gha-runs.csv summary with the queue/run split.
ci-hub/history/query.py runs --repo rrnewton/hermit --branch main
```

`green-time` counts only `conclusion == success` as green; `cancelled`,
`failure`, etc. are non-green. The store preserves every conclusion and
timestamp, so an alternative definition (e.g. carry-forward across `cancelled`
supersedes) can be computed later without re-ingesting. Authoritative workflow
defaults: hermit `CI (GitHub-managed portable)`, reverie `Rust`; override with
`--workflow` (repeatable).

## Join keys (file contract — join ONLY via these)

| concept            | gha-runs.csv | local (`validate-run-global.jsonl`) | obligations.jsonl |
|--------------------|--------------|-------------------------------------|-------------------|
| commit             | `head_sha`   | `commit`                            | `landed_sha`      |
| GHA run id         | `run_id`     | —                                   | `github.run_ids`  |
| repo               | `repo`       | (same string)                       | `repo`            |

All commit values are the same 40-hex SHA; `repo` is the same `OWNER/REPO` string
everywhere. `obligations.py` (owner: speculative-land) and the `submodule-bumps`
stream stay separate stores and join by these columns only.

## Live consumers

1. **cpu_timeout derivation** (`ci-timeout-audit-cpu-time-manifest-node`,
   `enable-cgroups-and-cpu-timeouts-across-dag-nodes`) consumes
   `node-cpu-budgets`. Nodes with ≥5 samples get a suggested timeout; the rest
   stay UNSET (thin) rather than guessed. Real per-node CPU rows arrive once
   #1548 lands and profiling artifacts are ingested.
2. **Owner's "% time green on main"** consumes `green-time`, derived from
   `gha-runs.csv`.

## Tests

`python3 -m unittest test_history` (in `tests/`). Offline: synthetic CSVs cover
the queue/run split, UPSERT idempotency, node-budget aggregation + thin flag, and
the green-time interval computation.
