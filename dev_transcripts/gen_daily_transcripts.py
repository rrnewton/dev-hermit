#!/usr/bin/env python3
"""Cultivated daily-transcript generator (HYBRID code + cheap-model).

PURE CODE  : session resolution, verbatim owner-prompt extraction, turn grouping,
             thread/Main-Chat organization, word/token stats, JSON emission.
CHEAP MODEL: abridged AI-response summaries, topic-keyword section titles, and the
             one-paragraph day summary — driven via `claude -p --model <cheap>`
             (default sonnet) behind `with-proxy`, BATCHED and cached.

Outputs (into the gitignored ./daily/ dir):
  daily/YYYY-MM-DD-dev-hermit-daily.md   one cultivated transcript per day
  daily/session-stats.json               cumulative machine-readable stats
  daily/.abridge-cache.json              per-turn summary cache (gitignored)

Usage:
  ./gen_daily_transcripts.py                     # full history, all days
  ./gen_daily_transcripts.py --days 2026-07-30 2026-07-31
  ./gen_daily_transcripts.py --no-model          # skeleton only (fast, no LLM)
  ./gen_daily_transcripts.py --model gpt-5.6-luna --agent codex
  ./gen_daily_transcripts.py --stats-only        # regenerate only session-stats.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_transcript import (  # noqa: E402
    Turn,
    group_turns,
    iso_week,
    load_blocks,
    resolve_session,
    word_count,
)

HERE = Path(__file__).resolve().parent
DAILY = HERE / "daily"
CACHE = DAILY / ".abridge-cache.json"

AI_INPUT_CAP = 1800        # chars of assistant text fed to the model per turn
PROMPT_SNIPPET_CAP = 240   # chars of the owner prompt fed to the model for context
BATCH = 18                 # turns per abridgement model call
MAX_WORKERS = 8            # concurrent cheap-model calls (subprocess releases GIL)

_cache_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Cheap-model plumbing                                                         #
# --------------------------------------------------------------------------- #
def call_model(prompt: str, model: str, agent: str, timeout: int = 240) -> str:
    if agent == "codex":
        cmd = ["with-proxy", "codex", "exec", "--model", model, prompt]
    else:
        cmd = ["with-proxy", "claude", "-p", "--model", model, prompt]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"model call failed ({r.returncode}): {r.stderr[:300]}")
    return r.stdout.strip()


def parse_json_lenient(text: str):
    """Extract the first balanced JSON array/object from possibly-fenced text."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except json.JSONDecodeError:
                continue
    return json.loads(t)  # raise if truly unparseable


def naive_summary(ai_text: str) -> str:
    """Fallback abridgement: first ~2 sentences, cleaned."""
    txt = " ".join(ai_text.split())
    if not txt:
        return "(no textual AI response — tool/code activity only)"
    parts = re.split(r"(?<=[.!?])\s+", txt)
    return " ".join(parts[:2])[:300]


# --------------------------------------------------------------------------- #
# Abridgement (batched + cached)                                              #
# --------------------------------------------------------------------------- #
def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    DAILY.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=0))


def turn_key(t: Turn) -> str:
    return t.owner_prompts[0].message_id if t.owner_prompts else f"turn-{t.turn_index}"


def abridge_turns(turns: list[Turn], cache: dict, model: str, agent: str,
                  use_model: bool) -> dict[str, str]:
    """Return {turn_key: abridged AI-response summary}. Batched + cached."""
    out: dict[str, str] = {}
    pending: list[Turn] = []
    for t in turns:
        k = turn_key(t)
        if k in cache:
            out[k] = cache[k]
        else:
            pending.append(t)

    if not use_model:
        for t in pending:
            out[turn_key(t)] = naive_summary(t.ai_text())
        return out

    chunks = [pending[i : i + BATCH] for i in range(0, len(pending), BATCH)]
    done = 0

    def run_batch(chunk: list[Turn]) -> dict[str, str]:
        items = []
        for t in chunk:
            ai = t.ai_text()
            if not ai and t.code_blocks:
                ai = f"(tool/code activity: {len(t.code_blocks)} execution block(s))"
            items.append({
                "id": turn_key(t),
                "prompt": t.prompt_text()[:PROMPT_SNIPPET_CAP],
                "ai": ai[:AI_INPUT_CAP],
            })
        prompt = (
            "You are abridging a dev-team coordinator's replies for an archival "
            "transcript. For each item you are given the user's prompt (context) and "
            "the AI's full reply (`ai`). Return ONLY a JSON array of "
            "{\"id\":..,\"summary\":..} where `summary` is a faithful 1-3 sentence "
            "abridgement of the AI reply (what it decided/did/dispatched), no preamble, "
            "no markdown. Keep concrete facts (PR numbers, agent names, SHAs, pass "
            "ratios). If the reply is only tool/code activity, say so briefly.\n\n"
            + json.dumps(items, ensure_ascii=False)
        )
        try:
            arr = parse_json_lenient(call_model(prompt, model, agent))
            got = {d["id"]: str(d.get("summary", "")).strip() for d in arr if "id" in d}
        except Exception as e:  # noqa: BLE001 — fall back per-batch, never abort
            print(f"  [abridge batch] model/parse failed: {e}; naive fallback",
                  file=sys.stderr)
            got = {}
        res = {}
        for t in chunk:
            k = turn_key(t)
            res[k] = got.get(k) or naive_summary(t.ai_text())
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(run_batch, c): c for c in chunks}
        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            out.update(res)
            with _cache_lock:
                cache.update(res)
                save_cache(cache)
            done += len(futs[fut])
            print(f"  abridged {done}/{len(pending)} new turns", file=sys.stderr)
    return out


def day_titles(date: str, groups: dict[str, list[Turn]], summaries: dict[str, str],
               model: str, agent: str, use_model: bool) -> tuple[str, dict[str, str]]:
    """Return (day_summary_paragraph, {group_key: topic-title})."""
    # Compact per-group digest from the (already abridged) turn summaries.
    digest = {}
    for key, turns in groups.items():
        lines = []
        for t in turns[:40]:
            lines.append("U: " + t.prompt_text()[:120].replace("\n", " "))
            lines.append("A: " + summaries.get(turn_key(t), "")[:120])
        digest[key] = "\n".join(lines)

    if not use_model:
        titles = {k: "General coordination" for k in groups}
        return (f"Activity across {len(groups)} conversation stream(s) with "
                f"{sum(len(v) for v in groups.values())} owner turns.", titles)

    prompt = (
        f"Date {date}. Below are conversation streams from a Hermit/Reverie "
        "deterministic-execution dev team's coordinator chat. For EACH stream key, "
        "produce a short title = 2-5 comma-separated topic keywords / workstream "
        "slugs (e.g. 'demo5 HPET wedge, KVM verify corpus'). Also write ONE "
        "paragraph (<=90 words) summarizing the whole day. Return ONLY JSON: "
        "{\"day_summary\":..,\"titles\":{<key>:<title>,...}}.\n\n"
        + json.dumps(digest, ensure_ascii=False)[:12000]
    )
    try:
        obj = parse_json_lenient(call_model(prompt, model, agent))
        ds = str(obj.get("day_summary", "")).strip()
        titles = {k: str(obj.get("titles", {}).get(k, "General")).strip() for k in groups}
        if not ds:
            raise ValueError("empty day_summary")
        return ds, titles
    except Exception as e:  # noqa: BLE001
        print(f"  [day_titles {date}] failed: {e}; using fallback", file=sys.stderr)
        return (f"Activity across {len(groups)} conversation stream(s) with "
                f"{sum(len(v) for v in groups.values())} owner turns.",
                {k: "General coordination" for k in groups})


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #
def render_day(date: str, groups: dict[str, list[Turn]], summaries: dict[str, str],
               day_summary: str, titles: dict[str, str]) -> str:
    title = f"{date} Daily dev-hermit dev team transcript"
    out = [title, "=" * len(title), "", f"> {day_summary}", ""]

    # Main Chat first, then threads in chronological order of first message.
    ordered_keys = sorted(
        groups, key=lambda k: (k != "main", groups[k][0].first_ms)
    )
    thread_n = 0
    for key in ordered_keys:
        turns = groups[key]
        topic = titles.get(key, "General")
        if key == "main":
            header = f"Main Chat: {topic}"
        else:
            thread_n += 1
            header = f"Thread {thread_n}: {topic}"
        out.append(header)
        out.append("-" * len(header))
        out.append("")
        for idx, t in enumerate(turns):
            ts = datetime.fromtimestamp(t.first_ms / 1000, tz=timezone.utc).strftime("%H:%M")
            ch = t.channel
            out.append(f"**[{ts} UTC · {ch}]**")
            out.append("")
            out.append(t.prompt_text())          # VERBATIM owner prompt
            out.append("")
            out.append("AI response:")
            out.append("")
            out.append("```markdown")
            out.append(summaries.get(turn_key(t), "").strip() or "(no summary)")
            out.append("```")
            out.append("")
            if idx != len(turns) - 1:
                out.append("----")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Session-stats JSON                                                          #
# --------------------------------------------------------------------------- #
def build_session_stats(blocks: list[Block], ref, tg_stats: dict) -> dict:  # noqa: F821
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
    """Taskgraph totals read straight from the tg SQLite DB. Never fatal.

    Uses $TG_DB_PATH (the session's tg database, e.g. ~/.tg/hermit.db)."""
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
    ap.add_argument("--no-model", action="store_true", help="skeleton only (no LLM)")
    ap.add_argument("--stats-only", action="store_true")
    args = ap.parse_args()

    ref = resolve_session(session_id=args.session, db_path=args.db)
    print(f"session {ref.session_id} ({ref.name}) db={ref.db_path}", file=sys.stderr)
    blocks = load_blocks(ref.db_path, ref.session_id)
    print(f"loaded {len(blocks)} content blocks", file=sys.stderr)
    DAILY.mkdir(parents=True, exist_ok=True)

    # session-stats.json (always)
    stats = build_session_stats(blocks, ref, taskgraph_stats())
    (DAILY / "session-stats.json").write_text(json.dumps(stats, indent=2))
    print(f"wrote {DAILY / 'session-stats.json'}", file=sys.stderr)
    if args.stats_only:
        return

    turns = group_turns(blocks, owner_only=True)
    by_day: dict[str, list[Turn]] = defaultdict(list)
    for t in turns:
        by_day[t.date].append(t)

    target_days = sorted(args.days) if args.days else sorted(by_day)
    use_model = not args.no_model
    cache = load_cache()

    for date in target_days:
        day_turns = by_day.get(date, [])
        if not day_turns:
            print(f"{date}: no owner turns, skipping", file=sys.stderr)
            continue
        print(f"{date}: {len(day_turns)} owner turns", file=sys.stderr)
        summaries = abridge_turns(day_turns, cache, args.model, args.agent, use_model)
        groups: dict[str, list[Turn]] = defaultdict(list)
        for t in day_turns:
            groups[t.thread_key].append(t)
        day_summary, titles = day_titles(date, groups, summaries,
                                         args.model, args.agent, use_model)
        md = render_day(date, groups, summaries, day_summary, titles)
        out_path = DAILY / f"{date}-dev-hermit-daily.md"
        out_path.write_text(md)
        print(f"  wrote {out_path}", file=sys.stderr)

    print("done", file=sys.stderr)


if __name__ == "__main__":
    main()
