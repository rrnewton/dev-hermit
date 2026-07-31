#!/usr/bin/env python3
"""STAGE 1 — SUMMARIZE (token-spending, cheap-LLM).

Reads the durable content_blocks store, groups turns, and classifies each AI
reply by SUBSTANCE into one of four buckets, writing machine-readable JSON to

    daily/.summary_data/<YYYY-MM-DD>.json      (per-day: meta + per-turn records)
    daily/.summary_data/weekly/<YYYY-Www>.json (per-week: overview + day list)

This stage is the ONLY one that spends tokens. It is idempotent per turn: a turn
already present in the day's JSON is not re-summarized unless --force is given.
Rendering markdown is a separate, pure, no-LLM stage (see render.py) — so future
FORMAT tweaks never require re-summarizing.

Buckets (see BUCKET_PROMPT):
  omit         -> dropped at render time (no durable substance)
  one_sentence -> a one-line "> AI response: ..." recap
  paragraph    -> a short "> " block-quote recap of the substance
  full         -> the reply is a substantive mini-doc, kept VERBATIM by render

Verbatim USER prompts are captured by pure code here (never via the LLM) and are
never dropped.
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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_transcript import (  # noqa: E402
    EASTERN,
    Turn,
    group_turns,
    iso_week,
    load_blocks,
    resolve_session,
    weekday_abbr,
)

HERE = Path(__file__).resolve().parent
DAILY = HERE / "daily"
SUMMARY_DIR = DAILY / ".summary_data"
WEEKLY_DIR = SUMMARY_DIR / "weekly"

import os

AI_CLASSIFY_CAP = 2800     # chars of the AI reply shown to the classifier
AI_VERBATIM_CAP = 24000    # max chars stored verbatim for a 'full' turn
PROMPT_SNIPPET_CAP = 320   # chars of the owner prompt given to the model as context
# Tunable via env (smaller batch / more time / less concurrency helps the heavy
# design-discussion days that otherwise hit the model timeout).
BATCH = int(os.environ.get("SUMMARIZE_BATCH", "16"))         # turns per call
MAX_WORKERS = int(os.environ.get("SUMMARIZE_WORKERS", "8"))  # concurrent calls
MODEL_TIMEOUT = int(os.environ.get("SUMMARIZE_TIMEOUT", "240"))  # seconds/call

_io_lock = threading.Lock()

VALID_BUCKETS = {"omit", "one_sentence", "paragraph", "full"}


# --------------------------------------------------------------------------- #
# Cheap-model plumbing                                                         #
# --------------------------------------------------------------------------- #
def call_model(prompt: str, model: str, agent: str, timeout: int = MODEL_TIMEOUT) -> str:
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


# --------------------------------------------------------------------------- #
# Prompts                                                                      #
# --------------------------------------------------------------------------- #
BUCKET_PROMPT = (
    "You are curating the archival transcript of a deterministic-execution "
    "('Hermit'/'Reverie') dev team's COORDINATOR chat. For EACH item you get the "
    "owner's prompt (context only) and the AI coordinator's reply (`ai`). Judge "
    "the REPLY purely on SUBSTANCE — the real engineering content: design/"
    "architecture decisions; concrete results (how many tests pass, benchmark "
    "numbers, which workstreams actually moved, PRs/SHAs landed); specific "
    "problems / bugs / root-causes found; and milestones reached. Do NOT reward "
    "coordination chatter or scaffolding: spawning or restarting agents, 'I'll "
    "dispatch / look into it', which tools or shell commands were run, status "
    "pings, or bare acknowledgements — that is NOISE, not substance.\n\n"
    "Classify each reply into EXACTLY one bucket. Return ONLY a JSON array of "
    '{"id":<id>,"bucket":<bucket>,"summary":<string>}:\n'
    '- "omit": no durable substance — bare acks, restart/tmux chatter, pure '
    'tool/code activity, "will do". summary MUST be "".\n'
    '- "one_sentence": exactly one substantive fact/decision/result. summary = '
    "ONE plain sentence, no preamble, no markdown; keep concrete facts (PR#, SHA, "
    "pass ratios, agent/workstream names).\n"
    '- "paragraph": several substantive points worth a short recap. summary = ONE '
    "tight paragraph of the substance (decisions/results/problems) on a SINGLE "
    "line with NO newlines and NO tool narration.\n"
    '- "full": the reply is itself a substantive mini-document worth keeping '
    "VERBATIM — a design/architecture writeup, benchmark/results table, problem "
    "analysis, or milestone/verdict report (headers like '## ... Assessment', "
    "'## M9 ACHIEVED', 'Verdict:', 'CI Overhaul'). summary MUST be \"\" (code "
    "keeps the verbatim text).\n\n"
    "Bias toward 'omit' when there is no durable substance; reserve 'full' for "
    "genuinely document-like substantive replies. Never invent facts.\n\n"
)

DAY_META_PROMPT = (
    "Below are conversation streams from a Hermit/Reverie deterministic-execution "
    "dev team's coordinator chat on {date}. For EACH stream key, produce a short "
    "title = 2-5 comma-separated topic keywords / workstream slugs (e.g. 'demo5 "
    "HPET wedge, KVM verify corpus'). Also write ONE paragraph (<=90 words) "
    "summarizing the substantive work of the whole day (what moved, results, "
    "problems, milestones — not coordination). Return ONLY JSON: "
    '{{"day_summary":..,"titles":{{<key>:<title>,...}}}}.\n\n'
)

WEEK_PROMPT = (
    "Below are the daily summaries for one week of a Hermit/Reverie "
    "deterministic-execution dev team ({week}). Write ONE paragraph (<=120 words) "
    "synthesizing the week's substantive arc: the main workstreams that moved, "
    "concrete results/milestones, and unresolved problems. No coordination "
    'chatter. Return ONLY JSON: {{"overview":<paragraph>}}.\n\n'
)


# --------------------------------------------------------------------------- #
# Classification                                                              #
# --------------------------------------------------------------------------- #
def turn_key(t: Turn) -> str:
    return t.owner_prompts[0].message_id if t.owner_prompts else f"turn-{t.turn_index}"


def _classify_batch(chunk: list[Turn], model: str, agent: str) -> dict[str, dict]:
    items = []
    for t in chunk:
        items.append({
            "id": turn_key(t),
            "prompt": t.prompt_text()[:PROMPT_SNIPPET_CAP],
            "ai": t.ai_text()[:AI_CLASSIFY_CAP],
        })
    prompt = BUCKET_PROMPT + json.dumps(items, ensure_ascii=False)
    try:
        arr = parse_json_lenient(call_model(prompt, model, agent))
        got = {}
        for d in arr:
            if "id" not in d:
                continue
            b = str(d.get("bucket", "")).strip()
            if b not in VALID_BUCKETS:
                b = "one_sentence"
            got[d["id"]] = {"bucket": b, "summary": str(d.get("summary", "")).strip()}
    except Exception as e:  # noqa: BLE001 — never abort a whole run on one batch
        print(f"  [classify] batch failed: {e}; defaulting to one_sentence",
              file=sys.stderr)
        got = {}
    # Fill any misses conservatively.
    for t in chunk:
        k = turn_key(t)
        if k not in got:
            snippet = " ".join(t.ai_text().split())[:200]
            got[k] = {"bucket": "one_sentence" if snippet else "omit",
                      "summary": snippet}
    return got


def _record_for(t: Turn, cls: dict) -> dict:
    bucket = cls["bucket"]
    ai = t.ai_text()
    if not ai.strip() and bucket != "omit":
        bucket = "omit"  # nothing to keep
    rec = {
        "turn_key": turn_key(t),
        "turn_index": t.turn_index,
        "first_ms": t.first_ms,
        "thread_key": t.thread_key,
        "channel": t.channel,
        "prompt": t.prompt_text(),          # VERBATIM owner prompt (pure code)
        "bucket": bucket,
        "summary": cls["summary"] if bucket in ("one_sentence", "paragraph") else "",
        "ai_verbatim": ai[:AI_VERBATIM_CAP] if bucket == "full" else "",
    }
    return rec


# --------------------------------------------------------------------------- #
# Per-day / per-week summarization                                            #
# --------------------------------------------------------------------------- #
def _read_day(date: str) -> dict:
    p = SUMMARY_DIR / f"{date}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _write_day(date: str, doc: dict) -> None:
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    (SUMMARY_DIR / f"{date}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))


def summarize_day(date: str, turns: list[Turn], model: str, agent: str,
                  use_model: bool, force: bool) -> dict:
    existing = {} if force else _read_day(date)
    existing_turns = {r["turn_key"]: r for r in existing.get("turns", [])}

    # Which turns still need classification?
    pending = [t for t in turns if force or turn_key(t) not in existing_turns]
    # Auto-omit empty-AI turns without spending tokens.
    auto = [t for t in pending if not t.ai_text().strip()]
    to_model = [t for t in pending if t.ai_text().strip()]

    classified: dict[str, dict] = {}
    for t in auto:
        classified[turn_key(t)] = {"bucket": "omit", "summary": ""}

    if use_model and to_model:
        chunks = [to_model[i:i + BATCH] for i in range(0, len(to_model), BATCH)]
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(_classify_batch, c, model, agent): c for c in chunks}
            for fut in concurrent.futures.as_completed(futs):
                classified.update(fut.result())
                done += len(futs[fut])
                print(f"    {date}: classified {done}/{len(to_model)} new turns",
                      file=sys.stderr)
    elif to_model:  # --no-model: cheap heuristic
        for t in to_model:
            snippet = " ".join(t.ai_text().split())
            classified[turn_key(t)] = {
                "bucket": "one_sentence" if snippet else "omit",
                "summary": snippet[:200],
            }

    # Assemble the full ordered turn list (reuse existing where kept).
    records = []
    for t in turns:
        k = turn_key(t)
        if k in classified:
            records.append(_record_for(t, classified[k]))
        else:
            records.append(existing_turns[k])
    records.sort(key=lambda r: r["first_ms"])

    # Day meta (summary + section titles) — recompute if new work or forced.
    changed = bool(classified)
    meta = existing.get("meta")
    if meta is None or changed or force:
        meta = _day_meta(date, records, model, agent, use_model)

    doc = {
        "date": date,
        "weekday": weekday_abbr(date),
        "iso_week": iso_week(date),
        "generated_at": datetime.now(EASTERN).isoformat(),
        "meta": meta,
        "turns": records,
    }
    _write_day(date, doc)
    return doc


def _day_meta(date: str, records: list[dict], model: str, agent: str,
              use_model: bool) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[r["thread_key"]].append(r)
    if not use_model:
        return {"day_summary": f"Activity across {len(groups)} stream(s), "
                               f"{len(records)} owner turns.",
                "titles": {k: "general coordination" for k in groups}}
    digest = {}
    for key, rs in groups.items():
        lines = []
        for r in rs[:40]:
            lines.append("U: " + r["prompt"][:110].replace("\n", " "))
            s = r.get("summary") or (r.get("ai_verbatim", "")[:110])
            if s:
                lines.append("A: " + s[:130].replace("\n", " "))
        digest[key] = "\n".join(lines)
    prompt = DAY_META_PROMPT.format(date=date) + json.dumps(digest, ensure_ascii=False)[:12000]
    try:
        obj = parse_json_lenient(call_model(prompt, model, agent))
        ds = str(obj.get("day_summary", "")).strip()
        titles = {k: str(obj.get("titles", {}).get(k, "general")).strip() for k in groups}
        if not ds:
            raise ValueError("empty day_summary")
        return {"day_summary": ds, "titles": titles}
    except Exception as e:  # noqa: BLE001
        print(f"  [day_meta {date}] failed: {e}; fallback", file=sys.stderr)
        return {"day_summary": f"Activity across {len(groups)} stream(s), "
                               f"{len(records)} owner turns.",
                "titles": {k: "general coordination" for k in groups}}


def summarize_week(week: str, day_docs: list[dict], model: str, agent: str,
                   use_model: bool, force: bool) -> dict:
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    p = WEEKLY_DIR / f"{week}.json"
    days = [{"date": d["date"], "weekday": d["weekday"],
             "day_summary": d.get("meta", {}).get("day_summary", ""),
             "titles": d.get("meta", {}).get("titles", {})}
            for d in sorted(day_docs, key=lambda d: d["date"])]
    overview = ""
    if not force and p.exists():
        try:
            overview = json.loads(p.read_text()).get("overview", "")
        except json.JSONDecodeError:
            overview = ""
    if use_model and (force or not overview):
        digest = "\n".join(f"{d['date']} {d['weekday']}: {d['day_summary']}" for d in days)
        prompt = WEEK_PROMPT.format(week=week) + digest[:9000]
        try:
            overview = str(parse_json_lenient(call_model(prompt, model, agent))
                           .get("overview", "")).strip()
        except Exception as e:  # noqa: BLE001
            print(f"  [week {week}] failed: {e}; fallback", file=sys.stderr)
    if not overview:
        overview = f"{len(days)} active day(s): " + "; ".join(
            f"{d['date']} {d['weekday']}" for d in days)
    doc = {"week": week, "generated_at": datetime.now(EASTERN).isoformat(),
           "overview": overview, "days": days}
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    return doc


# --------------------------------------------------------------------------- #
# Driver                                                                      #
# --------------------------------------------------------------------------- #
def run(session=None, db=None, model="sonnet", agent="claude", days=None,
        use_model=True, force=False) -> list[str]:
    ref = resolve_session(session_id=session, db_path=db)
    print(f"[summarize] session {ref.session_id} ({ref.name}) db={ref.db_path}",
          file=sys.stderr)
    blocks = load_blocks(ref.db_path, ref.session_id)
    print(f"[summarize] loaded {len(blocks)} content blocks", file=sys.stderr)

    turns = group_turns(blocks, owner_only=True)
    by_day: dict[str, list[Turn]] = defaultdict(list)
    for t in turns:
        by_day[t.date].append(t)

    target_days = sorted(days) if days else sorted(by_day)
    day_docs = []
    for date in target_days:
        dts = by_day.get(date, [])
        if not dts:
            print(f"[summarize] {date}: no owner turns", file=sys.stderr)
            continue
        print(f"[summarize] {date}: {len(dts)} owner turns", file=sys.stderr)
        day_docs.append(summarize_day(date, dts, model, agent, use_model, force))

    # Weekly rollups. Aggregate the FULL week from the on-disk cache (not just the
    # days rebuilt this run) so a --days subset never truncates a weekly summary.
    touched_weeks = {d["iso_week"] for d in day_docs}
    all_days = _all_day_docs()
    for week in sorted(touched_weeks):
        wk_days = [d for d in all_days if d.get("iso_week") == week]
        summarize_week(week, wk_days, model, agent, use_model, force)
        print(f"[summarize] wrote week {week} ({len(wk_days)} days)", file=sys.stderr)

    return [d["date"] for d in day_docs]


def _all_day_docs() -> list[dict]:
    docs = []
    for p in sorted(SUMMARY_DIR.glob("*.json")):
        try:
            docs.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return docs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session")
    ap.add_argument("--db")
    ap.add_argument("--model", default="sonnet", help="cheap model (default sonnet)")
    ap.add_argument("--agent", default="claude", choices=["claude", "codex"])
    ap.add_argument("--days", nargs="*", help="only these YYYY-MM-DD days")
    ap.add_argument("--no-model", action="store_true",
                    help="heuristic buckets only (no LLM tokens)")
    ap.add_argument("--force", action="store_true",
                    help="re-summarize even turns already cached")
    args = ap.parse_args()
    run(session=args.session, db=args.db, model=args.model, agent=args.agent,
        days=args.days, use_model=not args.no_model, force=args.force)
    print("[summarize] done", file=sys.stderr)


if __name__ == "__main__":
    main()
