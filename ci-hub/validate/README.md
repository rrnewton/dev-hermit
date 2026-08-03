# `validate/` — local `validate.sh` run visibility

This object turns the records that `hermit/validate.sh` writes into readable,
machine-wide views. It owns the linkage from local run records to retained
profiles; it does **not** own the generic DAG runner or the profile format
(those belong to the pinned `safe-ci-dag-runner`).

Two consumers live here:

| Command | Reads | Answers |
| --- | --- | --- |
| `validate/aggregate.py` | Every `validate.sh` run on this machine (parent JSONL ledger + raw per-run logs + `safe-ci-dag-runner` profiling CSVs). | "What has every worktree/agent/slot validated, and where is the profiling?" |
| `validate/worktrees.py` | This hub's two report artifacts under `ignored/`. | "Which worktrees are registered with this hub, how fresh is each, and how did their recent runs go?" |

Both are read-only. `aggregate.py` is also reachable through the front door as
`ci-hub local-history`; `worktrees.py` is invoked directly for now (a front-door
subcommand may wrap it later).

## Hub-report artifacts

`hermit/validate.sh` walks up from its checkout root (max ~3 levels) looking for
this hub. When it finds one, it reports every run here via `report_run_to_hub`;
when it does not, it proceeds silently and **never fails the run for a missing
hub**. Reporting is best-effort and never blocks or fails validation. Both
artifacts land under `ci-hub/ignored/`, gitignored by the repo-wide
`**/ignored/` rule.

### `ignored/validate-runs.jsonl`

Append-only JSONL, one record per run, byte-identical to the line the same run
writes to the parent validate-run ledger (`build_validation_record`). Runs are
events and are never deduplicated. Records are `schema_version` >= 3; the fields
that matter to `worktrees.py` are:

| Field | Meaning |
| --- | --- |
| `finished_at` | UTC ISO-8601 completion time. |
| `slot` | Worktree slot (e.g. `ci`, `kvm`, `slot03`). |
| `profile` | `quick` / `portable-only` / `full` / `super`. |
| `selection_mode` | Actual selection outcome: `full`, `smart`, `selective`, `skip`, `only`. Records the outcome, not the intent. |
| `result` / `exit_code` | `pass`/`fail` and the process exit code. |
| `checks` / `failures` | Gate counts. |
| `real_seconds` / `user_seconds` / `sys_seconds` | Wall and CPU time. |
| `commit` / `commit_anchored` / `tree_dirty` | Validated commit and its anchoring state. |

`selection_mode` never says `full` unless the complete suite actually ran, so a
`full` here is the signal a `locally-validated` stamp depends on.

### `ignored/worktree-registry.json`

A JSON object keyed by absolute worktree path, exactly one entry per worktree,
upserted idempotently on every run (so re-running never creates duplicate
entries). It requires `jq`; without it the run line is still appended and only
the registry upsert is skipped. Each entry:

```json
{
  "/abs/path/to/worktrees/ci/hermit": {
    "path": "/abs/path/to/worktrees/ci/hermit",
    "repo": "hermit",
    "branch": "codex/validate-smart-selection",
    "slot": "ci",
    "first_seen": "2026-08-01T09:00:00Z",
    "last_seen": "2026-08-03T17:00:00Z",
    "last_seen_epoch": 1785000000,
    "last_commit": "3370d9c8c1caa85012ae10199293543c8871a5b9",
    "last_result": "pass",
    "last_profile": "full",
    "last_selection_mode": "smart"
  }
}
```

`first_seen` is preserved across runs; everything prefixed `last_` is refreshed.

## Consuming the reports

```bash
# Registered-worktree table (newest last; worktrees unseen > 24h flagged STALE).
./ci-hub/validate/worktrees.py

# Add the 10 most-recent hub-reported runs.
./ci-hub/validate/worktrees.py --runs 10

# Tighten the staleness threshold to 6 hours.
./ci-hub/validate/worktrees.py --stale-hours 6

# One machine-readable report (worktrees + runs + counts).
./ci-hub/validate/worktrees.py --json

# Point at a non-default artifact directory (also honors $CI_HUB_IGNORED_DIR).
./ci-hub/validate/worktrees.py --data-dir /path/to/ci-hub/ignored
```

The reader tolerates a missing or partial artifact — a `jq`-less producer, a
truncated JSONL line, or no report file at all — and prints only what is
present, so it is always safe to run.

## Why a consumer matters

Without a reader, `validate-runs.jsonl` and `worktree-registry.json` are
write-only: they accumulate but nothing surfaces them, so the capability exists
and nobody can use it. `worktrees.py` closes that loop, turning the artifacts
into a live map of which worktrees exist, how fresh each is, and how their most
recent validate runs went.
