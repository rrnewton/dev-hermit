# debug/ episode schema

Three JSON snapshot files per episode, all lists except `episode.json`. Stable,
small, diff-friendly, and consumed by `dbg` (and directly by `jq`). Episodes may
also carry an append-only JSONL history journal.

## episode.json (object)

| field | type | meaning |
|---|---|---|
| `slug` | string | episode id = directory name |
| `title` | string | one-line title |
| `status` | `open`\|`closed` | is the investigation still active |
| `created` | date | YYYY-MM-DD |
| `owner` | string | lead + contributing agents |
| `question` | string | the central question(s) being answered |
| `anchors` | object | `{good:{sha,note}, bad:{sha,note}}` — the regression endpoints |
| `bisect_interval` | string | `GOOD..BAD` git range used to seed suspects |
| `migrated_from` | string | provenance (prior ledger/experiments path) |
| `root_cause_summary` | string | current best answer (updated as it firms up) |

## hypotheses.json (list of objects)

| field | type | meaning |
|---|---|---|
| `id` | string | `H1`, `H2`, … |
| `statement` | string | the claim |
| `reasoning` | string | detailed argument / source chain |
| `predicted_evidence` | string | what would confirm/refute it (write BEFORE testing) |
| `status` | `open`\|`confirmed`\|`killed` | `confirmed`+`killed` = "closed" |
| `verdict` | string | the reasoning behind the current status |
| `evidence_ids` | [string] | evidence records supporting the verdict |
| `owner` | string | who is/was driving it |

## evidence.json (list of objects)

| field | type | meaning |
|---|---|---|
| `id` | string | `E01`, … |
| `desc` | string | what was observed/measured |
| `hypotheses` | [string] | which hypotheses it bears on |
| `artifact` | string | path under `experiments/`/`scratch/` (or a URL) |
| `source` | string | which agent/run produced it |

## suspects.json (list of objects)

A *suspect* is a candidate cause — normally a commit in the bisect interval that
touched the relevant subsystem, but can be any candidate cause.

| field | type | meaning |
|---|---|---|
| `id` | string | `S01`, … |
| `sha` | string | commit (12-hex) or `""` for a non-commit cause |
| `subject` | string | commit subject / cause description |
| `subsystem` | string | e.g. `time/guest-clock`, `scheduler/admission` |
| `files` | [string] | core files it touched (why it's in scope) |
| `interval` | string | the `GOOD..BAD` range it was seeded from |
| `priority` | `high`\|`medium`\|`low` | relevance to the failing subsystem |
| `status` | `open`\|`cleared`\|`confirmed` | `open` = not yet ruled out |
| `reasoning` | string | why a suspect; cross-refs to hypotheses; clear/confirm notes |

## history/events.jsonl (append-only event objects)

Each line is one complete JSON object. This is an immutable source journal: append
new observations and corrections, but never rewrite or remove an earlier event.
Snapshots and the curated notebook are projections of the journal's current state.
`dbg history` reads and filters it; no CLI command mutates it.

| field | type | meaning |
|---|---|---|
| `schema_version` | string | event envelope version, currently `demo5-history/v1` |
| `seq` | integer | strictly increasing episode-local sequence |
| `event_id` | string | unique stable ID, e.g. `D5-000001` |
| `observed_at` | timestamp | when the source observation happened |
| `recorded_at` | timestamp | when the journal event was appended |
| `source` | object | agent, task, source-note time, and stable note ID/hash |
| `kind` | string | `finding`, `hypothesis_proposed`, `hypothesis_status`, `evidence`, `suspect_status`, `artifact`, or `correction` |
| `hypothesis_refs` | list | referenced `{id, slug}` pairs |
| `suspect_refs` | list | referenced suspect IDs/slugs |
| `summary` | string | concise statement of the event |
| `details` | object | structured event-specific facts |
| `artifacts` | list | evidence paths or URLs |
| `supersedes` | list | prior event IDs corrected by this event |
| `tags` | list | searchable classification labels |

## NOTEBOOK.md (curated prose) + .notebook-state.json

`NOTEBOOK.md` is agent-authored prose (not a schema-bound record): a synopsis of
the whole episode in three sections — **EXPLORED**, **INVALIDATED** (with
adversarially-evaluable resurrection claims), **FRONTIER** (open hypotheses +
suspects + the decisive next experiment).

`.notebook-state.json` is CLI-managed: `{note, state}` where `state` maps each
record type to `{id: {status, hash}}` plus an episode hash, captured at the last
`dbg notebook-sync`. `dbg changed` diffs current state against it to report
added / removed / status-changed / content-changed records — the worklist for the
next synthesize-and-revise pass. Do not hand-edit it.

## VCS_MISSING.md, per-episode .gitignore, ignored/ (hygiene, per dir)

- **`VCS_MISSING.md`** (required in every `experiments/<foo>/` and `debug/<bar>/`):
  a table of what is NOT checked in — path, what it is, regeneratable? (and how),
  and whether tracked code **reads** it (→ would fail on a fresh clone). Scaffolded
  by `dbg new-episode`; keep current with `dbg vcs-check`.
- **`.gitignore`** (per-episode): starter scaffolded by `dbg new-episode`; tune what
  large/scratch artifacts live-but-ignored locally.
- **`ignored/`**: the gitignored home for raw logs / boot artifacts / scratch —
  write them here from the start (ignored/-first default). See
  `.githooks/hygiene-policy.md`.

## Status conventions

- Hypothesis: `open` → `confirmed` or `killed` (terminal). "Closed" = either terminal.
- Suspect: `open` (default at seed) → `cleared` (ruled out) or `confirmed` (a/the regressor).
- `dbg suspects --open` is the live regressor-hunt worklist.
