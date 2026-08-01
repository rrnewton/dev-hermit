# debug/ episode schema

Three JSON files per episode, all lists except `episode.json`. Stable, small,
diff-friendly, and consumed by `dbg` (and directly by `jq`).

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

### Status conventions

- Hypothesis: `open` → `confirmed` or `killed` (terminal). "Closed" = either terminal.
- Suspect: `open` (default at seed) → `cleared` (ruled out) or `confirmed` (a/the regressor).
- `dbg suspects --open` is the live regressor-hunt worklist.
