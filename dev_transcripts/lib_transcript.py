#!/usr/bin/env python3
"""Shared helpers for the cultivated-transcript mechanism.

DATA SOURCE (see README.md and the STEP-0 finding): the durable, complete,
compaction-independent conversation store is the per-session SQLite database

    ~/.orc/sessions/<session-id>/session.db   table: content_blocks

which is APPEND-ONLY (SQLite triggers RAISE on UPDATE/DELETE) and therefore
retains every turn back to session start, unaffected by in-memory context
compaction. The rotating ~/.orc/logs/*.log files are internal telemetry only
(~100 min retained per session) and are NOT used here.

`content_blocks` columns used:
  message_id, session_id, block_index, created_at_ms, turn_index,
  role            -> 'user' | 'assistant' | 'notification'
  block_type      -> 'text' | 'reasoning' | 'tool_execution'
                     | 'code_execution' | 'wakeup' | 'image'
  content, token_count, model, user_source (JSON tag of the input channel)

Owner VERBATIM prompts  == role='user' AND block_type='text'.
  `user_source` JSON identifies the channel:
    {"Submitted":{"source":{"Web":{"view":...}}}}  -> Web UI
    {"Submitted":{"source":"Tui"}}                  -> TUI
    {"GChat":{"thread_name":..,"space_name":..,"is_owner":true,..}} -> Google Chat
System `user|wakeup` and `notification|text` blocks are NOT owner prompts and are
excluded from user word counts.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Eastern-time rendering (the owner works US/Eastern; transcripts show EDT/EST) #
# --------------------------------------------------------------------------- #
# All displayed clock times are rendered in America/New_York. For the July data
# this yields EDT (UTC-4); zoneinfo keeps it correct year-round (EST in winter).
try:
    from zoneinfo import ZoneInfo

    EASTERN = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - zoneinfo always present on py3.9+
    EASTERN = timezone(timedelta(hours=-4), "EDT")


def eastern_dt(created_at_ms: int) -> datetime:
    """UTC epoch-ms -> aware datetime in America/New_York (EDT/EST)."""
    return datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).astimezone(EASTERN)


def weekday_abbr(date_str: str) -> str:
    """'2026-07-20' -> 'Mon'."""
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%a")

ORC_HOME = Path(os.environ.get("ORC_HOME", Path.home() / ".orc"))
INDEX_DB = ORC_HOME / "index.db"
SESSIONS_DIR = ORC_HOME / "sessions"

# The dev-hermit coordinator session lives in a cwd containing this marker.
DEFAULT_CWD_MARKER = "dev-hermit"

# Internal hostnames can enter transcripts through verbatim prompts and tool
# output. Preserve the short host name while removing the datacenter/domain
# suffix before generated artifacts are written.
_INTERNAL_FQDN_RE = re.compile(
    r"\b(?:(dev(?:big|vm)[0-9]+)(?:\.[A-Za-z0-9-]+)*"
    r"|([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)"
    r"(?:\.[A-Za-z0-9-]+)+)\.facebook\.com\b",
    re.IGNORECASE,
)


def scrub_internal_fqdns(text: str) -> str:
    """Replace Meta-internal FQDNs with their short host names."""
    return _INTERNAL_FQDN_RE.sub(
        lambda match: match.group(1) or match.group(2), text
    )


def scrub_internal_fqdns_tree(value):
    """Recursively scrub every string in a JSON-compatible value."""
    if isinstance(value, str):
        return scrub_internal_fqdns(value)
    if isinstance(value, list):
        return [scrub_internal_fqdns_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub_internal_fqdns_tree(item) for key, item in value.items()}
    return value


# --------------------------------------------------------------------------- #
# Session resolution                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class SessionRef:
    session_id: str
    name: str
    cwd: str
    created_at: str
    updated_at: str
    message_count: int
    db_path: Path


def _content_block_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            (n,) = con.execute("SELECT COUNT(*) FROM content_blocks").fetchone()
            return int(n)
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def resolve_session(
    session_id: str | None = None,
    db_path: str | None = None,
    cwd_marker: str = DEFAULT_CWD_MARKER,
) -> SessionRef:
    """Resolve the dev-hermit session WITHOUT hardcoding its UUID.

    Priority: explicit --db > explicit --session > index.db lookup by cwd marker
    (choosing the session whose session.db has the most content_blocks) >
    filesystem scan fallback.
    """
    if db_path:
        p = Path(db_path).expanduser()
        sid = p.parent.name
        return _session_ref_from_db(sid, p)

    if session_id:
        p = SESSIONS_DIR / session_id / "session.db"
        return _session_ref_from_db(session_id, p)

    # index.db lookup by cwd marker, best (most content) first.
    candidates: list[SessionRef] = []
    if INDEX_DB.exists():
        con = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT id, name, COALESCE(cwd,''), created_at, updated_at, "
                "       message_count "
                "FROM sessions WHERE cwd LIKE ? ORDER BY message_count DESC",
                (f"%{cwd_marker}%",),
            ).fetchall()
        finally:
            con.close()
        for sid, name, cwd, created, updated, mc in rows:
            p = SESSIONS_DIR / sid / "session.db"
            if _content_block_count(p) > 0:
                candidates.append(
                    SessionRef(sid, name, cwd, created, updated, int(mc or 0), p)
                )

    if not candidates:
        # Filesystem fallback: pick the session.db with the most content_blocks.
        best: tuple[int, Path] | None = None
        for p in SESSIONS_DIR.glob("*/session.db"):
            n = _content_block_count(p)
            if n and (best is None or n > best[0]):
                best = (n, p)
        if best is None:
            raise SystemExit(
                "No session.db with a content_blocks table found under "
                f"{SESSIONS_DIR}. Pass --session or --db explicitly."
            )
        return _session_ref_from_db(best[1].parent.name, best[1])

    # Prefer the candidate with the most content blocks (index message_count can
    # lag), tie-break on message_count.
    candidates.sort(key=lambda c: (_content_block_count(c.db_path), c.message_count), reverse=True)
    return candidates[0]


def _session_ref_from_db(sid: str, p: Path) -> SessionRef:
    if not p.exists():
        raise SystemExit(f"session.db not found: {p}")
    name = created = updated = ""
    mc = 0
    cwd = ""
    if INDEX_DB.exists():
        con = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT name, COALESCE(cwd,''), created_at, updated_at, message_count "
                "FROM sessions WHERE id=?",
                (sid,),
            ).fetchone()
            if row:
                name, cwd, created, updated, mc = row[0], row[1], row[2], row[3], int(row[4] or 0)
        finally:
            con.close()
    return SessionRef(sid, name, cwd, created, updated, mc, p)


# --------------------------------------------------------------------------- #
# Content-block model                                                         #
# --------------------------------------------------------------------------- #
@dataclass
class Block:
    id: str
    message_id: str
    block_index: int
    created_at_ms: int
    turn_index: int
    role: str
    block_type: str
    content: str
    token_count: int
    model: str
    user_source: str

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.created_at_ms / 1000, tz=timezone.utc)

    @property
    def date(self) -> str:
        return self.dt.strftime("%Y-%m-%d")

    @property
    def is_owner_prompt(self) -> bool:
        return self.role == "user" and self.block_type == "text"

    # -- channel classification of an owner prompt ------------------------- #
    def channel(self) -> str:
        src = self.user_source or ""
        if '"GChat"' in src:
            return "GChat"
        if '"Tui"' in src:
            return "Tui"
        if '"Web"' in src:
            return "Web"
        return "other"

    def gchat_meta(self) -> dict | None:
        if '"GChat"' not in (self.user_source or ""):
            return None
        try:
            return json.loads(self.user_source)["GChat"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def thread_key(self) -> str:
        """Group key for the transcript: the gchat thread, else 'main'."""
        gm = self.gchat_meta()
        if gm:
            # Home-space / top-level messages are the "Main Chat"; sub-threads
            # get their own section.
            if gm.get("is_home_space") or gm.get("thread_name") in (None, ""):
                return "main"
            return "thread:" + str(gm.get("thread_name"))
        return "main"


def load_blocks(db_path: Path, session_id: str | None = None) -> list[Block]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        q = (
            "SELECT id, message_id, block_index, created_at_ms, turn_index, role, "
            "block_type, COALESCE(content,'') content, COALESCE(token_count,0) token_count, "
            "COALESCE(model,'') model, COALESCE(user_source,'') user_source "
            "FROM content_blocks "
        )
        params: tuple = ()
        if session_id:
            q += "WHERE session_id=? "
            params = (session_id,)
        q += "ORDER BY created_at_ms, turn_index, block_index"
        rows = con.execute(q, params).fetchall()
    finally:
        con.close()
    return [
        Block(
            id=r["id"],
            message_id=r["message_id"],
            block_index=r["block_index"],
            created_at_ms=r["created_at_ms"],
            turn_index=r["turn_index"],
            role=r["role"],
            block_type=r["block_type"],
            content=r["content"],
            token_count=r["token_count"],
            model=r["model"],
            user_source=r["user_source"],
        )
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Word counting                                                               #
# --------------------------------------------------------------------------- #
_WORD_RE = re.compile(r"\S+")


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def iso_week(date_str: str) -> str:
    """Return an ISO-year-week label like '2026-W30' for a YYYY-MM-DD string."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# --------------------------------------------------------------------------- #
# Turn grouping                                                               #
# --------------------------------------------------------------------------- #
@dataclass
class Turn:
    turn_index: int
    date: str
    owner_prompts: list[Block] = field(default_factory=list)
    assistant_text: list[Block] = field(default_factory=list)
    assistant_reasoning: list[Block] = field(default_factory=list)
    code_blocks: list[Block] = field(default_factory=list)
    notifications: list[Block] = field(default_factory=list)
    first_ms: int = 0

    @property
    def thread_key(self) -> str:
        return self.owner_prompts[0].thread_key() if self.owner_prompts else "main"

    @property
    def channel(self) -> str:
        return self.owner_prompts[0].channel() if self.owner_prompts else "other"

    def prompt_text(self) -> str:
        return "\n\n".join(b.content.strip() for b in self.owner_prompts if b.content.strip())

    def ai_text(self) -> str:
        return "\n\n".join(b.content.strip() for b in self.assistant_text if b.content.strip())


def group_turns(blocks: list[Block], owner_only: bool = True) -> list[Turn]:
    """Group blocks into turns keyed by turn_index. When owner_only, keep only
    turns that contain at least one genuine owner prompt (role=user text)."""
    turns: dict[int, Turn] = {}
    for b in blocks:
        t = turns.get(b.turn_index)
        if t is None:
            t = Turn(turn_index=b.turn_index, date=b.date, first_ms=b.created_at_ms)
            turns[b.turn_index] = t
        t.first_ms = min(t.first_ms, b.created_at_ms)
        if b.is_owner_prompt:
            t.owner_prompts.append(b)
            t.date = b.date  # anchor the turn's date to the owner prompt
        elif b.role == "assistant" and b.block_type == "text":
            t.assistant_text.append(b)
        elif b.role == "assistant" and b.block_type == "reasoning":
            t.assistant_reasoning.append(b)
        elif b.block_type in ("code_execution", "tool_execution"):
            t.code_blocks.append(b)
        elif b.role == "notification":
            t.notifications.append(b)
    ordered = sorted(turns.values(), key=lambda t: t.first_ms)
    if owner_only:
        ordered = [t for t in ordered if t.owner_prompts]
    return ordered
