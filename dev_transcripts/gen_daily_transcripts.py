#!/usr/bin/env python3
"""Cultivated daily-transcript generator — v2 two-stage orchestrator.

The mechanism is split into two independent stages so that FORMAT changes never
require spending tokens on re-summarization:

  STAGE 1  summarize.py  (token-spending): content_blocks -> JSON summary cache
                                           in daily/.summary_data/ (bucketed AI
                                           summaries + verbatim prompts). Idempotent
                                           per turn.
  STAGE 2  render.py     (pure, no LLM):   JSON cache -> markdown (daily + weekly).

This orchestrator runs stage 1 then stage 2, and (always) refreshes the pure-code
session-stats.json. Use --render-only to re-render from the cache with ZERO model
calls (the proof that format tweaks are free), or --summarize-only to just refresh
the cache.

Usage:
  ./gen_daily_transcripts.py                 # summarize (incremental) + render + stats
  ./gen_daily_transcripts.py --force         # re-summarize every turn (new prompt), render
  ./gen_daily_transcripts.py --render-only   # re-render markdown from cache, no LLM
  ./gen_daily_transcripts.py --summarize-only
  ./gen_daily_transcripts.py --no-model      # heuristic buckets (no tokens)
  ./gen_daily_transcripts.py --stats-only    # only refresh session-stats.json
  ./gen_daily_transcripts.py --days 2026-07-30 2026-07-31
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
import summarize  # noqa: E402
from lib_transcript import (  # noqa: E402
    EASTERN,
    Block,
    load_blocks,
    resolve_session,
    word_count,
)

HERE = Path(__file__).resolve().parent
DAILY = HERE / "daily"


# --------------------------------------------------------------------------- #
# Session-stats JSON (pure code)                                              #
# --------------------------------------------------------------------------- #
def build_session_stats(blocks: list[Block], ref, tg_stats: dict) -> dict:
    def wc(bs):
        return sum(word_count(b.content) for b in bs)

    user_prompts = [b for b in blocks if b.is_owner_prompt]
    wakeups = [b for b in blocks if b.role == "user" and b.block_type == "wakeup"]
    notifs = [b for b in blocks if b.role == "notification"]
    a_text = [b for b in blocks if b.role == "assistant" and b.block_type == "text"]
    a_reason = [b for b in blocks if b.role == "assistant" and b.block_type == "reasoning"]
    a_code = [b for b in blocks if b.block_type in ("code_execution", "tool_execution")]

    tok = lambda bs: sum(b.token_count for b in bs)  # noqa: E731
    days = sorted({b.date for b in blocks})
    per_day = defaultdict(lambda: {"owner_prompts": 0, "owner_words": 0,
                                   "ai_text_blocks": 0, "ai_words": 0})
    for b in user_prompts:
        per_day[b.date]["owner_prompts"] += 1
        per_day[b.date]["owner_words"] += word_count(b.content)
    for b in a_text:
        per_day[b.date]["ai_text_blocks"] += 1
        per_day[b.date]["ai_words"] += word_count(b.content)

    models = defaultdict(int)
    for b in blocks:
        if b.model:
            models[b.model] += 1

    return {
        "generated_at": datetime.now(EASTERN).isoformat(),
        "session": {
            "id": ref.session_id, "name": ref.name, "cwd": ref.cwd,
            "created_at": ref.created_at, "updated_at": ref.updated_at,
            "index_message_count": ref.message_count,
        },
        "coverage": {
            "first_block": min(b.date for b in blocks) if blocks else None,
            "last_block": max(b.date for b in blocks) if blocks else None,
            "num_days": len(days), "days": days,
            "source": "content_blocks (append-only, compaction-independent)",
        },
        "blocks": {
            "total": len(blocks),
            "owner_prompts": len(user_prompts),
            "user_wakeups": len(wakeups),
            "notifications": len(notifs),
            "assistant_text": len(a_text),
            "assistant_reasoning": len(a_reason),
            "code_execution": len(a_code),
        },
        "words": {
            "owner_prompts": wc(user_prompts),
            "assistant_text": wc(a_text),
            "assistant_reasoning": wc(a_reason),
            "notifications": wc(notifs),
        },
        "tokens": {
            "owner_prompts": tok(user_prompts),
            "assistant_text": tok(a_text),
            "assistant_reasoning": tok(a_reason),
            "code_execution": tok(a_code),
            "all_blocks": tok(blocks),
        },
        "responses": {
            "assistant_text_blocks": len(a_text),
            "turns_with_owner_prompt": len({b.turn_index for b in user_prompts}),
        },
        "by_channel": _channel_counts(user_prompts),
        "models_seen": dict(sorted(models.items(), key=lambda x: -x[1])),
        "per_day": {k: per_day[k] for k in sorted(per_day)},
        "taskgraph": tg_stats,
    }


def _channel_counts(prompts) -> dict:
    d = defaultdict(lambda: {"prompts": 0, "words": 0})
    for b in prompts:
        d[b.channel()]["prompts"] += 1
        d[b.channel()]["words"] += word_count(b.content)
    return {k: d[k] for k in sorted(d)}


def taskgraph_stats() -> dict:
    """Taskgraph totals read straight from the tg SQLite DB. Never fatal."""
    import os
    import sqlite3

    out: dict = {}
    db = os.environ.get("TG_DB_PATH", str(Path.home() / ".tg" / "hermit.db"))
    out["db"] = db
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            (total,) = con.execute("SELECT COUNT(*) FROM tasks").fetchone()
            rows = con.execute(
                "SELECT status, COUNT(*) FROM tasks GROUP BY status"
            ).fetchall()
        finally:
            con.close()
        by_status = {s: c for s, c in rows}
        out["total_tasks"] = int(total)
        out["by_status"] = by_status
        out["closed"] = by_status.get("CLOSED", 0)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"tg db unavailable: {e}"
    return out


def write_stats(session, db) -> None:
    ref = resolve_session(session_id=session, db_path=db)
    blocks = load_blocks(ref.db_path, ref.session_id)
    stats = build_session_stats(blocks, ref, taskgraph_stats())
    DAILY.mkdir(parents=True, exist_ok=True)
    (DAILY / "session-stats.json").write_text(json.dumps(stats, indent=2))
    print(f"[stats] wrote {DAILY / 'session-stats.json'}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session")
    ap.add_argument("--db")
    ap.add_argument("--model", default="sonnet", help="cheap model (default sonnet)")
    ap.add_argument("--agent", default="claude", choices=["claude", "codex"])
    ap.add_argument("--days", nargs="*", help="only these YYYY-MM-DD days")
    ap.add_argument("--no-model", action="store_true", help="heuristic buckets (no LLM)")
    ap.add_argument("--force", action="store_true",
                    help="re-summarize every turn (spend tokens); default is "
                         "incremental (only new turns)")
    ap.add_argument("--render-only", action="store_true",
                    help="STAGE 2 ONLY: re-render markdown from the cache, no LLM")
    ap.add_argument("--summarize-only", action="store_true",
                    help="STAGE 1 ONLY: refresh the JSON cache, skip rendering")
    ap.add_argument("--stats-only", action="store_true",
                    help="only refresh session-stats.json")
    args = ap.parse_args()

    if args.stats_only:
        write_stats(args.session, args.db)
        return

    if not args.render_only:
        summarize.run(session=args.session, db=args.db, model=args.model,
                      agent=args.agent, days=args.days,
                      use_model=not args.no_model, force=args.force)

    if not args.summarize_only:
        render.run(days=args.days)

    write_stats(args.session, args.db)
    print("[gen] done", file=sys.stderr)


if __name__ == "__main__":
    main()
