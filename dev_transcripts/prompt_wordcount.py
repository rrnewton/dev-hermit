#!/usr/bin/env python3
"""Prompt word-count report (PURE CODE — no LLM).

Extracts ONLY the owner's verbatim prompts (role='user', block_type='text') from
the durable content_blocks store and reports word counts daily / weekly /
all-time. Wakeups, notifications, and all assistant text are excluded.

Usage:
  ./prompt_wordcount.py                 # human table over the resolved session
  ./prompt_wordcount.py --json          # machine-readable JSON
  ./prompt_wordcount.py --session <id>  # force a session id
  ./prompt_wordcount.py --db <path>     # force a session.db path
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_transcript import (  # noqa: E402
    iso_week,
    load_blocks,
    resolve_session,
    word_count,
)


def build_report(db_path: Path, session_id: str) -> dict:
    blocks = load_blocks(db_path, session_id)
    prompts = [b for b in blocks if b.is_owner_prompt]

    daily: dict[str, dict] = defaultdict(lambda: {"prompts": 0, "words": 0})
    weekly: dict[str, dict] = defaultdict(lambda: {"prompts": 0, "words": 0})
    by_channel: dict[str, dict] = defaultdict(lambda: {"prompts": 0, "words": 0})
    total_prompts = 0
    total_words = 0

    for b in prompts:
        w = word_count(b.content)
        total_prompts += 1
        total_words += w
        daily[b.date]["prompts"] += 1
        daily[b.date]["words"] += w
        wk = iso_week(b.date)
        weekly[wk]["prompts"] += 1
        weekly[wk]["words"] += w
        ch = b.channel()
        by_channel[ch]["prompts"] += 1
        by_channel[ch]["words"] += w

    return {
        "session_id": session_id,
        "total": {"prompts": total_prompts, "words": total_words},
        "daily": {k: daily[k] for k in sorted(daily)},
        "weekly": {k: weekly[k] for k in sorted(weekly)},
        "by_channel": {k: by_channel[k] for k in sorted(by_channel)},
    }


def print_table(rep: dict) -> None:
    print(f"# Owner-prompt word counts — session {rep['session_id']}")
    print()
    print("## Daily")
    print(f"{'date':<12} {'prompts':>8} {'words':>9}  {'avg words/prompt':>16}")
    for d, v in rep["daily"].items():
        avg = v["words"] / v["prompts"] if v["prompts"] else 0
        print(f"{d:<12} {v['prompts']:>8} {v['words']:>9}  {avg:>16.1f}")
    print()
    print("## Weekly (ISO)")
    print(f"{'week':<12} {'prompts':>8} {'words':>9}")
    for w, v in rep["weekly"].items():
        print(f"{w:<12} {v['prompts']:>8} {v['words']:>9}")
    print()
    print("## By channel")
    print(f"{'channel':<12} {'prompts':>8} {'words':>9}")
    for c, v in rep["by_channel"].items():
        print(f"{c:<12} {v['prompts']:>8} {v['words']:>9}")
    print()
    t = rep["total"]
    avg = t["words"] / t["prompts"] if t["prompts"] else 0
    print("## All-time")
    print(f"prompts={t['prompts']}  words={t['words']}  avg_words_per_prompt={avg:.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", help="explicit orc session id")
    ap.add_argument("--db", help="explicit path to a session.db")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    ref = resolve_session(session_id=args.session, db_path=args.db)
    rep = build_report(ref.db_path, ref.session_id)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print_table(rep)


if __name__ == "__main__":
    main()
