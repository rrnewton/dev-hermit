# dev_transcripts — cultivated dev-team transcripts

A mechanism that generates **cultivated daily transcripts**, **prompt
word-counts**, and **session statistics** from the dev-hermit coordinator's
durable conversation store — so the owner no longer has to keep records by hand.

Task: `cultivated-transcript-generator`.

## TL;DR

```bash
cd dev_transcripts
./prompt_wordcount.py                 # owner-prompt word counts (daily/weekly/all-time)
./gen_daily_transcripts.py            # build daily/*.md + daily/session-stats.json (uses cheap model)
./gen_daily_transcripts.py --no-model # fast skeleton, verbatim prompts only, no LLM
```

Outputs land in the **gitignored** `daily/` subdir. The scripts themselves are
version-controlled.

## STEP-0 finding: what the durable source actually is

The task hypothesized that `~/.orc/logs/*.log` hold the full history. **They do
not.** Those are rotating **internal telemetry** (orc engine/JS trace lines),
capped at ~100 files ≈ ~100 **minutes** retained per session. Conversation text
only leaks into them incidentally (inside effect args), and inbound owner gchat
events carry metadata but no message body. The per-session `events.db` is also
short-lived (only the recent window).

The **durable, complete, compaction-independent** source is the per-session
SQLite database:

```
~/.orc/sessions/<session-id>/session.db      table: content_blocks
```

- `content_blocks` is **append-only** — SQLite triggers `RAISE(ABORT, …)` on any
  `UPDATE`/`DELETE`, so it is unaffected by in-memory context compaction.
- For the dev-hermit coordinator session it holds **52k+ blocks spanning the full
  ~11 days back to session start (2026-07-20)**.
- The session is resolved from `~/.orc/index.db` by matching `cwd` against
  `dev-hermit` and choosing the session whose `session.db` has the most blocks —
  **the UUID is not hardcoded** (override with `--session` / `--db`).

Relevant columns: `role` (`user`/`assistant`/`notification`), `block_type`
(`text`/`reasoning`/`code_execution`/`wakeup`/…), `turn_index` (pairs an owner
prompt with the assistant blocks that answered it), `created_at_ms`, `content`,
`token_count`, `model`, and `user_source` (a JSON tag of the input channel).

**Owner verbatim prompts** = `role='user' AND block_type='text'`. System
`user|wakeup` and `notification|text` blocks are **not** owner prompts and are
excluded from user word counts. `user_source` identifies the channel — `Web`,
`Tui`, or `GChat` (GChat carries `thread_name`/`space_name`, used to split the
transcript into **Main Chat** vs **Thread N** sections).

## Architecture: hybrid code + cheap-model

| Concern | How |
|---|---|
| session resolution, verbatim prompt extraction, turn grouping, thread/Main-Chat split, word/token stats, JSON | **pure code** (`lib_transcript.py`) — deterministic, no LLM |
| abridged AI-response summaries, topic-keyword section titles, one-paragraph day summary | **cheap model** via `claude -p --model sonnet` (or `codex exec`), **batched** (18 turns/call) and **cached** |

The expensive coordinator model is never used. Summaries are cached per turn
(`daily/.abridge-cache.json`) so re-runs are ~free and incremental.

## Files (version-controlled)

- `lib_transcript.py` — shared library: session resolution, `content_blocks`
  reader, `Block`/`Turn` models, channel/thread classification, word counting.
- `prompt_wordcount.py` — **pure-code** owner-prompt extraction + word counts
  (daily / weekly ISO / by-channel / all-time). `--json` for machine output.
- `gen_daily_transcripts.py` — **hybrid** generator: daily `.md` transcripts +
  `session-stats.json`.
- `.gitignore` — ignores `daily/`.

## Generated outputs (in gitignored `daily/`)

- `YYYY-MM-DD-dev-hermit-daily.md` — one cultivated transcript per day:
  - `# YYYY-MM-DD Daily dev-hermit dev team transcript` + `===` underline
  - one-paragraph day summary in a `>` block quote
  - `Main Chat: <topic keywords>` then `Thread N: <topic keywords>` sections
    (each `---` underlined)
  - within each: the owner's **verbatim** prompt, then an `AI response:` label
    above an **abridged** summary in a ` ```markdown ` fence; turns separated by
    `----`
- `session-stats.json` — cumulative machine-readable stats for the whole session:
  block/word/token counts (user vs AI), response counts, per-day breakdown,
  per-channel breakdown, models seen, and taskgraph totals (total / by-status /
  closed, read from `$TG_DB_PATH`).
- `.abridge-cache.json` — per-turn summary cache (regenerate by deleting it).

## Options

```
gen_daily_transcripts.py
  --session <id> / --db <path>   force a session (default: auto-resolve)
  --model <name>                 cheap model (default: sonnet)
  --agent claude|codex           CLI to drive (default: claude)
  --days YYYY-MM-DD ...          only these days
  --no-model                     skeleton only (verbatim prompts, naive summary)
  --stats-only                   only (re)write session-stats.json
```

## Notes / limitations

- `token_count` is populated only for user-input blocks in this schema;
  assistant-block token counts read as 0, so the JSON reports token totals only
  where the store records them (word counts cover all roles).
- The generator is idempotent per day and safe to re-run; only new/uncached
  turns hit the model.
- All network calls (the model) go through `with-proxy`.
