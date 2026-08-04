#!/usr/bin/env python3
"""STAGE 2 — RENDER (pure formatting, NO LLM).

Reads the JSON summary cache written by stage 1 (summarize.py) and emits markdown:

    daily/.summary_data/<YYYY-MM-DD>.json      -> daily/<YYYY-MM-DD>-dev-hermit-daily.md
    daily/.summary_data/weekly/<YYYY-Www>.json -> daily/<YYYY-Www>-dev-hermit-weekly.md

Because this stage never calls a model, FORMAT changes are free: edit the render
rules here and re-run `./render.py` — no re-summarizing, no tokens spent.

Density rules (match the owner's shape):
  * timestamp line `**[HH:MM EDT · chan]**` is IMMEDIATELY followed by the
    verbatim prompt — no blank line after the timestamp;
  * a `----` horizontal rule appears ONLY when the gap to the previous message is
    > GAP_MIN minutes (a blank line precedes it so it renders as a rule, none
    follows it); consecutive/close messages get no separator at all;
  * AI responses are bucketed: omit -> nothing; one_sentence/paragraph ->
    `> AI response: ...` block-quote; full -> a fenced verbatim block.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_transcript import eastern_dt, scrub_internal_fqdns_tree  # noqa: E402

HERE = Path(__file__).resolve().parent
DAILY = HERE / "daily"
SUMMARY_DIR = DAILY / ".summary_data"
WEEKLY_DIR = SUMMARY_DIR / "weekly"

GAP_MIN = 15  # minutes; a larger gap between messages inserts a `----` rule


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _ts(first_ms: int, channel: str) -> str:
    edt = eastern_dt(first_ms)
    return f"**[{edt.strftime('%H:%M')} {edt.strftime('%Z')} · {channel}]**"


def _fence(text: str) -> tuple[str, str]:
    """Pick a fence longer than any backtick run inside `text` (avoids breakage)."""
    longest = 0
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return ticks + "markdown", ticks


def _blockquote(label: str, body: str) -> list[str]:
    """Render `> label body` continuing multi-line bodies as block-quote lines."""
    body = body.strip()
    lines = body.split("\n") if body else [""]
    out = [f"> {label}{lines[0]}".rstrip()]
    for ln in lines[1:]:
        out.append(f"> {ln}".rstrip())
    return out


def _render_ai(rec: dict) -> list[str]:
    bucket = rec.get("bucket", "omit")
    if bucket == "omit":
        return []
    if bucket == "one_sentence":
        return _blockquote("AI response: ", rec.get("summary", "").strip())
    if bucket == "paragraph":
        return _blockquote("AI response: ", rec.get("summary", "").strip())
    if bucket == "full":
        body = rec.get("ai_verbatim", "").rstrip()
        if not body:
            return []
        open_f, close_f = _fence(body)
        return [open_f, "AI response (full msg):", "", body, close_f]
    return []


# --------------------------------------------------------------------------- #
# Daily                                                                       #
# --------------------------------------------------------------------------- #
def render_day(doc: dict) -> str:
    doc = scrub_internal_fqdns_tree(doc)
    date = doc["date"]
    title = f"{date} {doc['weekday']} Daily dev-hermit dev team transcript"
    meta = doc.get("meta", {})
    out = [title, "=" * len(title), "", f"> {meta.get('day_summary', '').strip()}", ""]

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in doc.get("turns", []):
        groups[r["thread_key"]].append(r)
    for rs in groups.values():
        rs.sort(key=lambda r: r["first_ms"])

    ordered = sorted(groups, key=lambda k: (k != "main", groups[k][0]["first_ms"]))
    titles = meta.get("titles", {})
    thread_n = 0
    for key in ordered:
        rs = groups[key]
        topic = titles.get(key, "general")
        if key == "main":
            header = f"Main Chat: {topic}"
        else:
            thread_n += 1
            header = f"Thread {thread_n}: {topic}"
        out += [header, "-" * len(header), ""]

        prev_ms = None
        for r in rs:
            gap = prev_ms is not None and (r["first_ms"] - prev_ms) > GAP_MIN * 60_000
            if gap:
                out.append("")      # blank so `----` renders as a rule, not a heading
                out.append("----")
            out.append(_ts(r["first_ms"], r["channel"]))
            out.append(r["prompt"].strip())        # VERBATIM, immediately after ts
            out += _render_ai(r)
            prev_ms = r["first_ms"]
        out.append("")

    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Weekly                                                                      #
# --------------------------------------------------------------------------- #
def render_week(doc: dict) -> str:
    doc = scrub_internal_fqdns_tree(doc)
    week = doc["week"]
    title = f"{week} Weekly dev-hermit dev team transcript"
    out = [title, "=" * len(title), "", f"> {doc.get('overview', '').strip()}", ""]
    out += ["Daily summaries", "-" * len("Daily summaries"), ""]
    for d in doc.get("days", []):
        topics = ", ".join(t for t in d.get("titles", {}).values() if t)
        out.append(f"**{d['date']} {d['weekday']}** — {d.get('day_summary', '').strip()}")
        if topics:
            out.append(f"> topics: {topics}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Driver                                                                      #
# --------------------------------------------------------------------------- #
def run(days=None) -> tuple[int, int]:
    DAILY.mkdir(parents=True, exist_ok=True)
    day_files = sorted(SUMMARY_DIR.glob("*.json"))
    nd = nw = 0
    for p in day_files:
        date = p.stem
        if days and date not in days:
            continue
        doc = json.loads(p.read_text())
        (DAILY / f"{date}-dev-hermit-daily.md").write_text(render_day(doc))
        nd += 1
    for p in sorted(WEEKLY_DIR.glob("*.json")) if WEEKLY_DIR.exists() else []:
        doc = json.loads(p.read_text())
        (DAILY / f"{doc['week']}-dev-hermit-weekly.md").write_text(render_week(doc))
        nw += 1
    print(f"[render] wrote {nd} daily + {nw} weekly markdown files", file=sys.stderr)
    return nd, nw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="*", help="only render these YYYY-MM-DD days")
    args = ap.parse_args()
    if not SUMMARY_DIR.exists() or not any(SUMMARY_DIR.glob("*.json")):
        sys.exit(f"no summary cache in {SUMMARY_DIR} — run ./summarize.py first")
    run(days=args.days)


if __name__ == "__main__":
    main()
