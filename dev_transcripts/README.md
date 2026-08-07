# dev_transcripts — cultivated dev-team transcripts

A mechanism that generates **cultivated daily/weekly transcripts**, **prompt
word-counts**, and **session statistics** from the dev-hermit coordinator's
durable conversation store — so the owner no longer has to keep records by hand.

Tasks: `cultivated-transcript-generator`, `transcript-generator-refinements`,
`transcript-generator-v2-pipeline`.

## TL;DR

```bash
cd dev_transcripts
./prompt_wordcount.py                 # owner-prompt word counts (daily/weekly/all-time)

# v2 two-stage pipeline:
./gen_daily_transcripts.py            # STAGE 1 (summarize, incremental) + STAGE 2 (render) + stats
./gen_daily_transcripts.py --render-only   # STAGE 2 ONLY: re-render markdown from cache, ZERO LLM
./summarize.py                        # STAGE 1 alone (refresh JSON summary cache)
./render.py                           # STAGE 2 alone (JSON cache -> markdown)
./gen_daily_transcripts.py --force    # re-summarize every turn (e.g. after a prompt change)
```

Outputs land in the **gitignored** `daily/` subdir (including the
`daily/.summary_data/` JSON cache). The scripts themselves are version-controlled.

## STEP-0 finding: what the durable source actually is

`~/.orc/logs/*.log` are **not** the history — they are rotating **internal
telemetry** capped at ~100 files ≈ ~100 **minutes** per session. The **durable,
complete, compaction-independent** source is the per-session SQLite database:

```
~/.orc/sessions/<session-id>/session.db      table: content_blocks
```

`content_blocks` is **append-only** (SQLite triggers `RAISE(ABORT, …)` on
`UPDATE`/`DELETE`), so it is unaffected by in-memory context compaction, and it
holds every turn back to session start (2026-07-20). The session is resolved from
`~/.orc/index.db` by matching `cwd` against `dev-hermit` and choosing the session
whose `session.db` has the most blocks — **the UUID is not hardcoded** (override
with `--session` / `--db`). **Owner verbatim prompts** = `role='user' AND
block_type='text'`; wakeups and notifications are excluded. `user_source` gives
the channel (`Web`/`Tui`/`GChat`; GChat threads become **Thread N** sections).

## Architecture: v2 two-stage pipeline

The mechanism is split into two independent stages so that **format changes never
require spending tokens on re-summarization**:

| Stage | Script | LLM? | Reads | Writes |
|---|---|---|---|---|
| **1 SUMMARIZE** | `summarize.py` | yes (cheap `claude -p --model sonnet`, batched + parallel) | `content_blocks` | `daily/.summary_data/*.json` |
| **2 RENDER** | `render.py` | **no — pure code** | `daily/.summary_data/*.json` | `daily/*.md` |

`gen_daily_transcripts.py` orchestrates both stages and refreshes the pure-code
`session-stats.json`. Because rendering is a separate no-LLM stage, tweaking the
markdown format is free: edit `render.py`, run `./render.py`, done — no tokens.

Stage 1 is **idempotent per turn**: a turn already in the day's JSON is not
re-summarized unless `--force` is given (verified: re-running a stable past day is
a ~0.5s no-op with zero model calls; only genuinely new turns hit the model).

### Substance bucketing (stage 1)

Each AI reply is classified by **substance** — design/architecture decisions,
concrete results (tests passing, benchmarks, PRs/SHAs landed), problems/bugs,
milestones — while **coordination chatter and tool-call narration are treated as
noise**. Four buckets drive rendering:

| Bucket | Rendered as |
|---|---|
| `omit` | *nothing* (dropped — bare acks, restart/tmux chatter, pure tool activity) |
| `one_sentence` | `> AI response: <one line>` |
| `paragraph` | `> AI response: <short substance paragraph>` (block-quote) |
| `full` | the reply kept **verbatim** in a ` ```markdown ` fence (`AI response (full msg):`) |

Verbatim **user prompts** are always captured by pure code (never the LLM) and
are never dropped. Before cache writes and rendering, Meta-internal FQDNs are
reduced to their short host names (for example,
`devbig014` becomes `devbig014`).

> **Note on `full`:** across the ~11-day history the classifier selected `full`
> **zero** times — this is correct for this data, not a bug. The coordinator's own
> chat replies are status/delegation text; the genuinely document-like artifacts
> (`## … Assessment`, `## M9 ACHIEVED`, `Verdict:`, benchmark tables) live in
> `ai_docs/` files, PRs, and sub-agent outputs, which the coordinator *references*
> rather than pastes. The `full` render path is implemented and tested (it also
> auto-lengthens the fence when the verbatim body contains ``` ``` ```).

## Format (denser, owner's shape)

- The H1 title carries the weekday: `YYYY-MM-DD <Wkd> Daily dev-hermit dev team
  transcript` (+ `===`), followed by a `>` day-summary and `Main Chat:` /
  `Thread N:` sections.
- A turn is `**[HH:MM EDT · channel]**` **immediately** followed by the verbatim
  prompt (no blank line), then the bucketed AI response.
- A `----` horizontal rule appears **only** when the gap to the previous message
  exceeds 15 min (blank line before it so it renders as a rule; none after).
  Close/consecutive turns get no separator.
- All clock times are Eastern (`America/New_York`, EDT/EST via `%Z`).

## Files (version-controlled)

- `lib_transcript.py` — shared library: session resolution, `content_blocks`
  reader, `Block`/`Turn` models, channel/thread classification, Eastern-time
  helpers, word counting.
- `summarize.py` — **STAGE 1** (LLM): substance-bucketed JSON summary cache.
- `render.py` — **STAGE 2** (pure): JSON cache → daily + weekly markdown.
- `gen_daily_transcripts.py` — orchestrator (stage 1 + stage 2 + session-stats).
- `prompt_wordcount.py` — pure-code owner-prompt word counts.
- `.gitignore` — ignores `daily/` (which contains `.summary_data/` too).

## Generated outputs (in gitignored `daily/`)

- `YYYY-MM-DD-dev-hermit-daily.md` — one cultivated transcript per day (format
  above).
- `YYYY-Www-dev-hermit-weekly.md` — per-ISO-week rollup: a synthesized week
  overview + each day's summary and topics.
- `.summary_data/<date>.json` — per-day cache: `meta` (day summary + section
  titles) + per-turn records (verbatim prompt, bucket, summary / verbatim).
- `.summary_data/weekly/<week>.json` — per-week cache (overview + day list).
- `session-stats.json` — cumulative machine-readable stats (blocks/words/tokens,
  responses, per-day, per-channel, models, taskgraph totals).

## Options (`gen_daily_transcripts.py`)

```
  --session <id> / --db <path>   force a session (default: auto-resolve)
  --model <name>                 cheap model (default sonnet)
  --agent claude|codex           CLI to drive stage 1 (default claude)
  --days YYYY-MM-DD ...          only these days
  --force                        re-summarize every turn (spend tokens)
  --render-only                  STAGE 2 only — re-render from cache, no LLM
  --summarize-only               STAGE 1 only — refresh the cache, skip render
  --no-model                     heuristic buckets only (no tokens)
  --stats-only                   only refresh session-stats.json
```

Stage 1 tunables (env, for the heavy design-discussion days that can hit the
model timeout): `SUMMARIZE_BATCH` (turns/call, default 16), `SUMMARIZE_WORKERS`
(default 8), `SUMMARIZE_TIMEOUT` (seconds/call, default 240). A batch that fails
or times out falls back to a heuristic summary for its turns and never aborts the
run; re-run those days with smaller batch / longer timeout to upgrade them.

## Notes / limitations

- `token_count` is populated only for user-input blocks in this schema;
  assistant token counts read 0 (word counts cover all roles).
- Today's file is a living snapshot — the session is active, so re-running picks
  up new turns for the current day incrementally.
- All network calls (the model) go through `with-proxy`.
