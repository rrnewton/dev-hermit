#!/usr/bin/env python3
"""Send a message to the Orc Hermit coordinator TUI.

Delivery is acknowledged, not assumed. ``tmux send-keys`` and ``paste-buffer``
exit 0 for any live pane whether or not the application consumed the keystrokes,
so their exit status is a proxy for delivery, not delivery. This script instead
observes the composer transition it caused:

    empty  --(inject)-->  non-empty  --(Enter)-->  empty

and, when the submitted text can be found again above the composer, the `[user]`
transcript echo that only appears once Orc has accepted the message. Exit status
is 0 only when that transition was observed; every other outcome logs
``status="failed"`` and exits nonzero.

Composer recognition is structural (the region between the two horizontal rules
at the bottom of the pane) rather than textual. Every text probe tried before
has been invalidated by an Orc build: the border title flipped twice, and the
post-1.0 TUI dropped both the ``orc (hermit)`` header and the
``Type / for commands`` placeholder that readiness used to key on, which
silently failed 12 consecutive relays over six hours.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO
from zoneinfo import ZoneInfo


UID = os.getuid()
DEFAULT_RUNTIME_DIR = Path(f"/run/user/{UID}")
DEFAULT_SOCKET = Path(f"/run/user/{UID}/orc-tmux/tmux-{UID}/default")
DEFAULT_ORC_COMMAND = Path.home() / "orc-bin/orc"
DEFAULT_ORC_DB = "hermit"
DEFAULT_LOG_FILE = Path.home() / ".local/state/orc-hermit-msg.log"
TMUX_TIMEOUT_SECONDS = 5
# `orc tmux ls` can be slow under heavy fleet load (many concurrent agents).
# A tight timeout was the dominant dropped-tick cause, so allow more time and
# retry a few times before giving up; final failure falls back to a direct
# tmux-socket scan in main() rather than dropping the tick.
ORC_TIMEOUT_SECONDS = 30
ORC_LS_ATTEMPTS = 3
ORC_LS_RETRY_SLEEP = 2.0
# The composer briefly shows non-empty text while the coordinator is typing or
# a prior message is still rendering; retry instead of dropping the tick.
COMPOSER_ATTEMPTS = 6
COMPOSER_RETRY_SLEEP = 2.0
# The TUI repaints asynchronously: `tmux paste-buffer` returns as soon as the
# bytes are in the pane, and under fleet load the composer took several seconds
# to render them. Capturing once immediately after injecting therefore reads the
# PRE-paste screen and reports a false non-delivery, so both post-write checks
# poll instead of sampling once.
INJECT_ATTEMPTS = 20
INJECT_RETRY_SLEEP = 0.5
DRAIN_ATTEMPTS = 20
DRAIN_RETRY_SLEEP = 0.5
# How much scrollback to search for the submitted message echoed as a `[user]`
# transcript block. A long message can scroll its own opening off the visible
# screen, so the echo check must look above it.
TRANSCRIPT_SCROLLBACK_LINES = 200
# Longest slice of the message used to recognise it on screen. It must be long
# enough to tell THIS relay from the previous one: the hourly reminder sends a
# byte-identical file every tick, and the only thing distinguishing two of them
# is the Eastern-time stamp prefix_eastern_time() embeds ~50 characters in. A
# shorter probe would match the last hour's copy still on screen and report a
# corroborated delivery for a message that never arrived.
PROBE_LENGTH = 120
HTML_COMMENT_OPEN = "<!--"
HTML_COMMENT_CLOSE = "-->"
EASTERN_TIME_ZONE = ZoneInfo("America/New_York")
ACTIVE_SESSION_RE = re.compile(r"^(?P<name>[^:]+):\s+\d+\s+windows?\b")
ACTIVE_SESSION_HEADER_RE = re.compile(
    r"^Active sessions on this socket \((?P<count>\d+)\):$"
)
ACTIVE_SESSION_ENTRY_RE = re.compile(r"^  (?P<name>orc-\S+)\s*$")
# The composer is the region delimited by two horizontal rules at the bottom of
# the Orc TUI. This structural shape is the ONLY part of the composer that has
# survived every Orc build so far, which is why readiness keys on it. Text
# probes did not survive: `orc (hermit)` and the `Type / for commands`
# placeholder both vanished in the post-1.0 TUI (which shows a `Session:`/`DB:`
# footer and a bare prompt instead), and the border title had already flipped
# between builds before that. See the module docstring.
COMPOSER_RULE_RE = re.compile(r"^-{20,}$")
# U+203A is the post-1.0 empty-input prompt; '>' covers a plain-ASCII build.
COMPOSER_PROMPT_CHARS = "›>"
# `[idle]` / `[streaming]` / ... — the post-1.0 readiness marker, rendered on the
# key-hint line just above the composer. Recorded as evidence, never used as the
# delivery test: a state label is a proxy for acceptance, the composer
# transition is the acceptance itself.
COMPOSER_STATE_RE = re.compile(
    r"^\[(?P<state>[a-z][a-z0-9 _-]*)\]\s+Enter:\s*submit\b"
)
COMPOSER_STATE_SEARCH_LINES = 3
# Submitting while a turn is streaming does not drop the message: Orc parks it
# and renders `[queued] N pending` above the composer. Recorded so a reader can
# tell "the coordinator has read this" from "the coordinator will read this".
COMPOSER_QUEUE_RE = re.compile(r"^\[queued\]\s+(?P<pending>\d+)\s+pending\b")


class OrcMessageError(RuntimeError):
    """An expected failure while locating or writing to the Orc TUI."""


@dataclass(frozen=True)
class CoordinatorPane:
    session: str
    window: str
    pane_id: str


@dataclass(frozen=True)
class Composer:
    """The Orc input box as rendered on screen, parsed structurally.

    ``text`` is the draft currently in the box with the prompt glyph removed;
    ``state`` is the readiness marker (``idle``, ``streaming``, ...) when the
    build renders one, else None.
    """

    text: str
    state: str | None

    @property
    def is_empty(self) -> bool:
        return not self.text


@dataclass(frozen=True)
class DeliveryAck:
    """What was actually observed about a submitted message.

    ``echo`` distinguishes the two strengths of evidence: a drained composer
    proves Orc consumed the draft, while finding the message again above the
    composer proves Orc took ownership of it — either rendered as a `[user]`
    transcript turn or parked in the `[queued]` list. ``pending`` is the queue
    depth when Orc reported one, so "delivered" never silently means "will be
    read at some point".

    ``echo`` is POSITIVE-ONLY evidence and is never allowed to gate the exit
    status. Messages observed arriving on the live pane were logged with
    ``echo=False`` because the render had not caught up inside the capture
    window, so treating its absence as non-delivery would invent failures.
    Absence of echo means "not corroborated", not "not delivered".
    """

    composer_state: str | None
    echo: bool
    pending: int | None

    @property
    def evidence(self) -> str:
        return "composer-drained+echo" if self.echo else "composer-drained"


@dataclass(frozen=True)
class LivePane:
    """One live pane on the tmux socket, before any coordinator filtering."""

    session: str
    window: str
    pane_id: str
    command: str
    orc_db: str | None

    @property
    def is_orc(self) -> bool:
        return self.command == "orc"

    def render(self) -> str:
        return f"{self.session}:{self.window} ({self.pane_id}, {self.command})"


def fail(message: str) -> NoReturn:
    print(f"orc-hermit-msg: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run_tmux(
    socket: Path,
    *args: str,
    input_text: str | None = None,
) -> str:
    command = ["tmux", "-S", str(socket), *args]
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=TMUX_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise OrcMessageError("tmux is not installed or is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise OrcMessageError(
            f"tmux timed out after {TMUX_TIMEOUT_SECONDS}s: {' '.join(args)}"
        ) from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown tmux error"
        raise OrcMessageError(f"tmux {' '.join(args)} failed: {detail}")
    return result.stdout


def resolve_socket(socket: Path, *, allow_fallback: bool = True) -> Path:
    """Return a live tmux socket, tolerating a relocated orc-tmux dir.

    The default path is deterministic from the UID, but if orc restarts into a
    differently-named socket the hourly tick must still find it, so a missing
    DEFAULT socket falls back to the most-recently-modified one under the
    orc-tmux runtime dir.

    An EXPLICIT ``--socket`` never falls back. Searching for a substitute is
    only ever right for "wherever orc happens to be listening"; applied to a
    socket the caller named, it silently retargets the message to a different
    server. That is not hypothetical: a test aimed at a deliberately
    nonexistent socket path was rewritten to the live coordinator and delivered
    a self-test message to the owner. A named destination that is absent is a
    refusal, not an invitation to pick another one.
    """
    if socket.exists():
        return socket
    if not allow_fallback:
        raise OrcMessageError(
            f"tmux socket does not exist: {socket}. Refusing to look for another "
            "socket because --socket named this one explicitly; a message must go "
            "to the server the caller asked for or to none at all. Omit --socket "
            "to use the default and allow discovery of a relocated orc socket"
        )
    orc_tmux_dir = DEFAULT_RUNTIME_DIR / "orc-tmux"
    candidates = [
        path
        for path in orc_tmux_dir.glob(f"tmux-{UID}/*")
        if path.is_socket()
    ]
    if not candidates:
        candidates = [
            path for path in orc_tmux_dir.rglob("*") if path.is_socket()
        ]
    if not candidates:
        raise OrcMessageError(f"tmux socket does not exist: {socket}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def parse_active_orc_sessions(output: str) -> list[str]:
    legacy_sessions = [
        match.group("name")
        for line in output.splitlines()
        if (match := ACTIVE_SESSION_RE.match(line)) is not None
    ]
    if legacy_sessions:
        return legacy_sessions

    lines = output.splitlines()
    for index, line in enumerate(lines):
        header = ACTIVE_SESSION_HEADER_RE.match(line)
        if header is None:
            continue
        sessions = [
            match.group("name")
            for candidate in lines[index + 1 :]
            if (match := ACTIVE_SESSION_ENTRY_RE.match(candidate)) is not None
        ]
        expected_count = int(header.group("count"))
        if len(sessions) != expected_count:
            raise OrcMessageError(
                "orc tmux ls session count mismatch: "
                f"reported {expected_count}, parsed {len(sessions)}"
            )
        return sessions
    return []


def list_active_orc_sessions(orc_command: Path) -> list[str]:
    command = [str(orc_command), "tmux", "ls"]
    environment = os.environ.copy()
    environment["XDG_RUNTIME_DIR"] = str(DEFAULT_RUNTIME_DIR)
    last_error: OrcMessageError | None = None
    for attempt in range(ORC_LS_ATTEMPTS):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                env=environment,
                text=True,
                timeout=ORC_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            # A missing executable will not become present on retry.
            raise OrcMessageError(
                f"orc executable does not exist: {orc_command}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            last_error = OrcMessageError(
                f"orc tmux ls timed out after {ORC_TIMEOUT_SECONDS}s"
            )
            last_error.__cause__ = exc
        else:
            if result.returncode != 0:
                detail = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "unknown orc error"
                )
                last_error = OrcMessageError(f"orc tmux ls failed: {detail}")
            else:
                sessions = parse_active_orc_sessions(result.stdout)
                if not sessions:
                    last_error = OrcMessageError(
                        "orc tmux ls reported no active sessions"
                    )
                elif len(sessions) != len(set(sessions)):
                    raise OrcMessageError(
                        "orc tmux ls reported duplicate session names"
                    )
                else:
                    return sessions
        if attempt + 1 < ORC_LS_ATTEMPTS:
            time.sleep(ORC_LS_RETRY_SLEEP)
    assert last_error is not None
    raise last_error


def scan_orc_sessions_via_tmux(socket: Path) -> list[str]:
    """Locate active orc sessions directly on the tmux socket.

    Fallback for when the `orc` CLI is too slow to answer under fleet load: any
    session with a live pane whose current command is `orc` is an active orc
    session. This keeps the hourly tick flowing even if `orc tmux ls` times out.
    """
    panes = run_tmux(
        socket,
        "list-panes",
        "-a",
        "-F",
        "#{session_name}\t#{pane_dead}\t#{pane_current_command}",
    )
    sessions: list[str] = []
    for line in panes.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3:
            continue
        session, pane_dead, current_command = fields
        if pane_dead == "0" and Path(current_command).name == "orc":
            if session not in sessions:
                sessions.append(session)
    if not sessions:
        raise OrcMessageError(
            "no live orc coordinator pane found on the tmux socket"
        )
    return sessions


def orc_db_from_start_command(command: str) -> str | None:
    if len(command) >= 2 and command[0] == command[-1] == '"':
        command = command[1:-1]
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    for index, token in enumerate(tokens):
        if token == "--db" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--db="):
            return token.removeprefix("--db=")
    return None


def parse_live_panes(panes: str, active_sessions: list[str]) -> list[LivePane]:
    """Every live pane in an active orc session, with no coordinator filtering.

    Kept separate from coordinator selection so a failure can report what was
    actually on the socket. A count reported without the panes it was computed
    from cannot distinguish "no coordinator exists" from "a hint excluded it".
    """
    active = set(active_sessions)
    live: list[LivePane] = []
    for line in panes.splitlines():
        fields = line.split("\t", 5)
        if len(fields) != 6:
            continue
        session, window, pane_id, pane_dead, current_command, start_command = fields
        if session not in active or pane_dead != "0":
            continue
        live.append(
            LivePane(
                session=session,
                window=window,
                pane_id=pane_id,
                command=Path(current_command).name,
                orc_db=orc_db_from_start_command(start_command),
            )
        )
    return live


def _render_panes(panes: list[LivePane]) -> str:
    return ", ".join(pane.render() for pane in panes) or "none"


def _hint_mismatch_detail(
    orc_panes: list[LivePane],
    live_panes: list[LivePane],
    session_hint: str | None,
    window_hint: str | None,
) -> str:
    """Explain why the hints excluded every coordinator.

    The common mistake is aiming this coordinator-only relay at an *agent*
    pane's window (`--window hermit-perf`). The window filter and the
    "current command is orc" test then intersect to nothing, which used to
    surface as the badly misleading "found 0 (coordinators: none)".
    """
    detail = (
        f"session/window hints excluded every coordinator "
        f"(session_hint={session_hint!r}, window_hint={window_hint!r}); "
        f"live coordinators on this socket: {_render_panes(orc_panes)}"
    )
    if window_hint is None:
        return detail
    named = [
        pane
        for pane in live_panes
        if pane.window == window_hint
        and (session_hint is None or pane.session == session_hint)
    ]
    if not named:
        return f"{detail}; no live pane has window {window_hint!r}"
    if any(pane.is_orc for pane in named):
        return detail
    return (
        f"{detail}; window {window_hint!r} is {_render_panes(named)}, an agent "
        "pane rather than the coordinator. This script only messages the Orc "
        "coordinator TUI; drop --window (or pass the coordinator's window) and "
        "ask the coordinator to relay, or use a TaskGraph note for agent-to-"
        "agent handoff"
    )


def find_coordinator_pane(
    socket: Path,
    active_sessions: list[str],
    orc_db: str,
    *,
    session_hint: str | None = None,
    window_hint: str | None = None,
) -> CoordinatorPane:
    if session_hint is not None and session_hint not in active_sessions:
        raise OrcMessageError(
            f"requested session {session_hint!r} is not present in orc tmux ls"
        )

    panes = run_tmux(
        socket,
        "list-panes",
        "-a",
        "-F",
        "#{session_name}\t#{window_name}\t#{pane_id}\t#{pane_dead}\t"
        "#{pane_current_command}\t#{pane_start_command}",
    )
    live_panes = parse_live_panes(panes, active_sessions)
    orc_panes = [pane for pane in live_panes if pane.is_orc]
    hinted = [
        pane
        for pane in orc_panes
        if (session_hint is None or pane.session == session_hint)
        and (window_hint is None or pane.window == window_hint)
    ]

    matching = [pane for pane in hinted if pane.orc_db == orc_db]
    if not matching:
        expected_session = f"orc-{orc_db}"
        matching = [
            pane
            for pane in hinted
            if pane.orc_db is None and pane.session == expected_session
        ]

    if len(matching) == 1:
        selected = matching[0]
        return CoordinatorPane(
            session=selected.session,
            window=selected.window,
            pane_id=selected.pane_id,
        )

    # Report the count together with the condition that produced it. Each
    # branch below is a materially different operator action, so they must not
    # collapse into one message.
    if not orc_panes:
        raise OrcMessageError(
            f"no live Orc coordinator pane on {socket}: no pane in "
            f"{active_sessions} is running `orc`. Live panes: "
            f"{_render_panes(live_panes)}. The coordinator is probably "
            "restarting; retry shortly"
        )
    if not hinted:
        raise OrcMessageError(
            _hint_mismatch_detail(orc_panes, live_panes, session_hint, window_hint)
        )
    if not matching:
        raise OrcMessageError(
            f"no live Orc coordinator is running database {orc_db!r}; "
            f"candidates: "
            + ", ".join(
                f"{pane.render()} db={pane.orc_db or 'unknown'}" for pane in hinted
            )
        )
    raise OrcMessageError(
        f"expected exactly one live Orc coordinator for database {orc_db!r}; "
        f"found {len(matching)}: {_render_panes(matching)}. Disambiguate with "
        "--session/--window"
    )


def squash(text: str) -> str:
    """Drop all whitespace so a probe survives the pane's own line wrapping.

    tmux wraps at the pane width with no continuation marker and can split
    mid-word, so neither a raw substring test nor whitespace-collapsing is
    reliable. Removing whitespace entirely from both sides matches either way.
    """
    return re.sub(r"\s+", "", text)


def message_probe(message: str) -> str:
    """A short distinctive slice of the message, used to recognise it on screen."""
    return squash(message)[:PROBE_LENGTH]


def parse_composer(screen: str) -> Composer | None:
    """Parse the input box out of a captured pane, or None if it is not there.

    The composer is the region between the last two horizontal rules; the Orc
    footer (``Session:``/``Agents:``/``DB:``) sits below the closing rule and
    contains no rules of its own. Returning None means "this pane does not look
    like an Orc composer at all", which is a different operator action from
    "the composer is busy", so the two must not collapse into one result.
    """
    lines = [line.rstrip() for line in screen.splitlines()]
    rules = [
        index for index, line in enumerate(lines) if COMPOSER_RULE_RE.match(line.strip())
    ]
    if len(rules) < 2:
        return None
    open_index, close_index = rules[-2], rules[-1]

    body: list[str] = []
    for line in lines[open_index + 1 : close_index]:
        body.append(line.lstrip().lstrip(COMPOSER_PROMPT_CHARS).strip())

    state: str | None = None
    for line in reversed(lines[max(0, open_index - COMPOSER_STATE_SEARCH_LINES) : open_index]):
        match = COMPOSER_STATE_RE.match(line.strip())
        if match is not None:
            state = match.group("state")
            break

    return Composer(text="\n".join(body).strip(), state=state)


def parse_pending(screen: str) -> int | None:
    """Depth of Orc's pending-message queue, when it renders one."""
    for line in reversed(screen.splitlines()):
        match = COMPOSER_QUEUE_RE.match(line.strip())
        if match is not None:
            return int(match.group("pending"))
    return None


def capture_pane(socket: Path, pane_id: str, *, scrollback: int = 0) -> str:
    args = ["capture-pane", "-p"]
    if scrollback:
        args += ["-S", f"-{scrollback}"]
    return run_tmux(socket, *args, "-t", pane_id)


def _pane_tail(screen: str, lines: int = 12) -> str:
    return " / ".join(line.strip() for line in screen.splitlines()[-lines:] if line.strip())


def wait_for_ready_composer(socket: Path, pane_id: str) -> Composer:
    """Block until the composer is present and empty.

    Emptiness is the load-bearing precondition: it is what makes it safe to
    type, because injecting into an occupied box would corrupt somebody else's
    draft and submit the concatenation. Readiness deliberately does NOT require
    the `[idle]` marker — requiring an idle coordinator is what silently dropped
    17 relays in one day on a busy fleet, and the composer accepts input while a
    turn is streaming. The observed state travels with the result instead.
    """
    screen = ""
    composer: Composer | None = None
    for attempt in range(COMPOSER_ATTEMPTS):
        screen = capture_pane(socket, pane_id)
        composer = parse_composer(screen)
        if composer is not None and composer.is_empty:
            return composer
        if attempt + 1 < COMPOSER_ATTEMPTS:
            time.sleep(COMPOSER_RETRY_SLEEP)

    if composer is None:
        raise OrcMessageError(
            f"pane {pane_id} does not render an Orc composer: found no input box "
            f"between horizontal rules after {COMPOSER_ATTEMPTS} attempts. This is "
            "not a busy coordinator — the pane layout is unrecognised, so nothing "
            f"was typed. Pane tail: {_pane_tail(screen)}"
        )
    raise OrcMessageError(
        f"Orc composer on {pane_id} still holds an unsent draft after "
        f"{COMPOSER_ATTEMPTS} attempts (state={composer.state or 'unknown'}); "
        "refusing to type into it and concatenate with somebody else's message. "
        f"Draft: {composer.text[:200]!r}"
    )


def inject_message(socket: Path, pane_id: str, message: str) -> None:
    """Type the message into the Orc composer WITHOUT submitting it.

    The Orc TUI composer does not enable terminal bracketed-paste mode, so
    ``tmux paste-buffer -p`` is silently dropped and never reaches the input box
    — the send appears to succeed while nothing arrives. Deliver by injecting
    the text as typed input instead: paste each line UNBRACKETED via a tmux
    buffer (safe for arbitrary content, including lines that start with '-',
    which ``send-keys -l`` misparses as flags), and separate lines with Ctrl+J,
    which the composer inserts as a newline (Enter submits).
    """
    token = secrets.token_hex(4)
    for index, line in enumerate(message.split("\n")):
        if index > 0:
            # Ctrl+J inserts a newline in the composer without submitting.
            run_tmux(socket, "send-keys", "-t", pane_id, "C-j")
        if not line:
            continue
        buffer_name = f"orc-hermit-msg-{os.getpid()}-{token}-{index}"
        run_tmux(socket, "load-buffer", "-b", buffer_name, "-", input_text=line)
        try:
            # No -p: an unbracketed paste is injected as if typed. -d removes
            # the buffer afterwards.
            run_tmux(
                socket,
                "paste-buffer",
                "-d",
                "-b",
                buffer_name,
                "-t",
                pane_id,
            )
        except OrcMessageError:
            with contextlib.suppress(OrcMessageError):
                run_tmux(socket, "delete-buffer", "-b", buffer_name)
            raise


def clear_composer(socket: Path, pane_id: str) -> bool:
    """Best-effort: wipe a draft we injected but are not going to submit.

    Only ever called on a composer this process found EMPTY beforehand, so
    whatever is in it now is ours to remove. Without this a refused relay wedges
    every later one, because the next run correctly refuses to type into an
    occupied box.

    Ctrl+U (kill-line), verified against the live coordinator, not Escape: the
    key hints bind Esc to "cancel", which aborts the coordinator's in-flight
    turn — a far worse side effect than a leftover draft.
    """
    with contextlib.suppress(OrcMessageError):
        run_tmux(socket, "send-keys", "-t", pane_id, "C-u")
        time.sleep(INJECT_RETRY_SLEEP)
        composer = parse_composer(capture_pane(socket, pane_id))
        return composer is not None and composer.is_empty
    return False


def verify_injected(socket: Path, pane_id: str, probe: str) -> None:
    """Confirm the keystrokes reached the input box, before pressing Enter.

    This is the check that turns a blind rc=0 into a real result: every tmux
    call above exits 0 for a pane that ignored the input entirely, so without it
    a total non-delivery is indistinguishable from a successful one.

    Either signal is sufficient, because each independently shows the box took
    the text: the composer is no longer empty, or the message is visible on
    screen. Both are needed as alternatives because a draft long enough to
    scroll the box can hide the message's opening, while a draft containing a
    horizontal rule of its own can confuse the structural parse.
    """
    screen = ""
    for attempt in range(INJECT_ATTEMPTS):
        screen = capture_pane(socket, pane_id)
        composer = parse_composer(screen)
        if (composer is not None and not composer.is_empty) or probe in squash(screen):
            return
        if attempt + 1 < INJECT_ATTEMPTS:
            time.sleep(INJECT_RETRY_SLEEP)

    cleared = clear_composer(socket, pane_id)
    raise OrcMessageError(
        f"the message did not reach the Orc composer on {pane_id} within "
        f"{INJECT_ATTEMPTS * INJECT_RETRY_SLEEP:.0f}s: the input box is still empty "
        "after typing it, so tmux accepted the keystrokes but the TUI did not. NOT "
        "submitting — nothing was delivered and no Enter was sent. "
        + (
            "Composer cleared for the next relay."
            if cleared
            else "WARNING: could not clear the composer; a partial draft may remain "
            "and will block the next relay until removed."
        )
        + f" Pane tail: {_pane_tail(screen)}"
    )


def submit_and_acknowledge(
    socket: Path, pane_id: str, probe: str, state: str | None
) -> DeliveryAck:
    """Press Enter once, then confirm Orc consumed the draft.

    Enter is sent exactly once and never retried: a resend after an ambiguous
    outcome is how a relay delivers twice. If the composer does not drain we
    report failure and leave the draft in place rather than guessing.
    """
    run_tmux(socket, "send-keys", "-t", pane_id, "Enter")

    screen = ""
    composer: Composer | None = None
    for attempt in range(DRAIN_ATTEMPTS):
        screen = capture_pane(socket, pane_id)
        composer = parse_composer(screen)
        if composer is not None and composer.is_empty:
            # The composer is empty, so any remaining match is the message
            # rendered back by Orc — as a `[user]` transcript turn if it was
            # taken up immediately, or as a `[queued]` entry if a turn was still
            # streaming. Either way Orc owns it now, which is the thing a
            # cleared input box alone does not prove.
            transcript = capture_pane(
                socket, pane_id, scrollback=TRANSCRIPT_SCROLLBACK_LINES
            )
            return DeliveryAck(
                composer_state=state,
                echo=probe in squash(transcript),
                pending=parse_pending(screen),
            )
        if attempt + 1 < DRAIN_ATTEMPTS:
            time.sleep(DRAIN_RETRY_SLEEP)

    remaining = "composer no longer parses" if composer is None else repr(composer.text[:200])
    raise OrcMessageError(
        f"Enter was sent to {pane_id} but the Orc composer did not drain within "
        f"{DRAIN_ATTEMPTS * DRAIN_RETRY_SLEEP:.0f}s, so the message was NOT "
        f"accepted and is left in the input box ({remaining}). Not resending: a "
        "retry here is how a relay delivers twice. Clear the composer before the "
        f"next relay. Pane tail: {_pane_tail(screen)}"
    )


def send_message(socket: Path, pane_id: str, message: str) -> DeliveryAck:
    """Deliver the message and return what was observed about its acceptance."""
    composer = wait_for_ready_composer(socket, pane_id)
    probe = message_probe(message)
    inject_message(socket, pane_id, message)
    verify_injected(socket, pane_id, probe)
    return submit_and_acknowledge(socket, pane_id, probe, composer.state)


def validate_message(message: str) -> None:
    if not message or not message.strip():
        raise OrcMessageError("message must not be empty")
    controls = sorted(
        {
            ord(char)
            for char in message
            if (ord(char) < 32 and char not in "\n\t") or ord(char) == 127
        }
    )
    if controls:
        rendered = ", ".join(f"0x{code:02x}" for code in controls)
        raise OrcMessageError(
            f"message contains unsupported control characters ({rendered})"
        )


def strip_html_comments(text: str) -> str:
    """Remove complete HTML comment blocks while preserving other text."""
    output: list[str] = []
    offset = 0
    while offset < len(text):
        opening = text.find(HTML_COMMENT_OPEN, offset)
        closing = text.find(HTML_COMMENT_CLOSE, offset)
        if closing != -1 and (opening == -1 or closing < opening):
            raise OrcMessageError("message file contains an unmatched '-->'")
        if opening == -1:
            output.append(text[offset:])
            break
        output.append(text[offset:opening])
        closing = text.find(HTML_COMMENT_CLOSE, opening + len(HTML_COMMENT_OPEN))
        if closing == -1:
            raise OrcMessageError("message file contains an unterminated '<!--' comment")
        offset = closing + len(HTML_COMMENT_CLOSE)
    return "".join(output)


def load_message_file(path: Path) -> str:
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OrcMessageError(f"could not read message file {path}: {exc}") from exc
    except UnicodeError as exc:
        raise OrcMessageError(f"message file is not valid UTF-8: {path}") from exc
    return strip_html_comments(contents).strip()


def prefix_eastern_time(message: str, now: datetime.datetime | None = None) -> str:
    current = now or datetime.datetime.now(tz=EASTERN_TIME_ZONE)
    eastern = current.astimezone(EASTERN_TIME_ZONE)
    timestamp = eastern.strftime("%Y-%m-%d %H:%M:%S %Z")
    return (
        "We're working in eastern time and the current date/time is "
        f"`{timestamp}`, use that to orient yourself around day/night and any "
        f"potential future deadlines.  {message}"
    )


def log_delivery(
    path: Path,
    status: str,
    *,
    session: str,
    window: str,
    pane_id: str | None,
    message: str | None,
    message_file: Path | None,
    error: str | None = None,
    ack: DeliveryAck | None = None,
) -> None:
    # A bare "sent" does not say what was verified, and this log is the only
    # durable record of a relay. Every `sent` therefore carries the evidence it
    # rests on and the composer state it was submitted from; every `failed`
    # carries the stage that refused, so a reader can tell "nothing was typed"
    # from "typed but never accepted" without re-deriving it from the message.
    record: dict[str, object] = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "target": f"{session}:{window}",
    }
    if ack is not None:
        record["ack"] = ack.evidence
        record["echo"] = ack.echo
        record["composer_state"] = ack.composer_state or "unknown"
        if ack.pending is not None:
            record["pending"] = ack.pending
    if pane_id is not None:
        record["pane"] = pane_id
    if message_file is not None:
        record["message_file"] = str(message_file)
    if message is not None:
        encoded = message.encode("utf-8")
        record["message_bytes"] = len(encoded)
        record["message_sha256"] = hashlib.sha256(encoded).hexdigest()
    if error is not None:
        record["error"] = error

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log_file:
        fcntl.flock(log_file, fcntl.LOCK_EX)
        log_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        log_file.flush()
        os.fsync(log_file.fileno())


def locked(lock_path: Path) -> TextIO:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w")
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    return lock_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?", help="message to submit")
    parser.add_argument(
        "--message-file",
        type=Path,
        help="read a UTF-8 message, remove HTML comments, and prefix Eastern time",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=None,
        help=f"tmux server socket (default: {DEFAULT_SOCKET}). When given "
        "explicitly it is used verbatim: a missing socket is an error rather "
        "than a cue to search for another server to deliver to",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the coordinator and check the composer is ready, then "
        "stop. Nothing is typed and no Enter is sent, so detection can be "
        "validated without delivering anything",
    )
    parser.add_argument(
        "--orc-command",
        type=Path,
        default=DEFAULT_ORC_COMMAND,
        help=f"orc executable used for session discovery (default: {DEFAULT_ORC_COMMAND})",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help=f"append delivery results as JSON lines (default: {DEFAULT_LOG_FILE})",
    )
    parser.add_argument("--orc-db", default=DEFAULT_ORC_DB, help=argparse.SUPPRESS)
    # Disambiguators for the rare multi-coordinator case only. They select
    # among panes that are already running `orc`; they cannot address an agent
    # pane, and pointing them at one is the documented way to get a confusing
    # zero-match failure. Kept visible so that misuse is discouraged up front
    # rather than only explained after it fails.
    parser.add_argument(
        "--session",
        help="restrict coordinator discovery to this Orc session (an Orc "
        "coordinator session, not an agent pane)",
    )
    parser.add_argument(
        "--window",
        help="restrict coordinator discovery to this window of the Orc "
        "coordinator session. This is NOT how you message another agent: "
        "agent windows run claude/codex, never `orc`, so naming one matches "
        "no coordinator. Use a TaskGraph note for agent-to-agent handoff",
    )
    args = parser.parse_args()
    if args.message_file is not None and args.message is not None:
        parser.error("message and --message-file are mutually exclusive")
    if args.message_file is None and args.message is None and not args.dry_run:
        parser.error("provide a message or use --message-file")
    return args


def main() -> int:
    args = parse_args()
    message: str | None = None
    pane_id: str | None = None
    session = args.session or f"db={args.orc_db}"
    window = args.window or "coordinator"
    submitted = False
    try:
        if args.message_file is not None:
            message = prefix_eastern_time(load_message_file(args.message_file))
        else:
            message = args.message
        if message is not None:
            validate_message(message)
        socket = resolve_socket(
            args.socket if args.socket is not None else DEFAULT_SOCKET,
            allow_fallback=args.socket is None,
        )
        lock_path = socket.parent / f".{args.orc_db}-msg.lock"
        with locked(lock_path):
            try:
                active_sessions = list_active_orc_sessions(args.orc_command)
            except OrcMessageError as discovery_error:
                # Do not drop the tick when the orc CLI is merely slow: locate
                # the coordinator directly on the tmux socket instead.
                print(
                    f"orc-hermit-msg: warning: {discovery_error}; "
                    "falling back to direct tmux scan",
                    file=sys.stderr,
                )
                active_sessions = scan_orc_sessions_via_tmux(socket)
            coordinator = find_coordinator_pane(
                socket,
                active_sessions,
                args.orc_db,
                session_hint=args.session,
                window_hint=args.window,
            )
            session = coordinator.session
            window = coordinator.window
            pane_id = coordinator.pane_id
            if args.dry_run:
                # Detection only. Nothing is typed and no Enter is sent, so a
                # readiness regression can be caught without putting a single
                # character in front of a human.
                composer = wait_for_ready_composer(socket, pane_id)
                log_delivery(
                    args.log_file,
                    "dry-run",
                    session=session,
                    window=window,
                    pane_id=pane_id,
                    message=None,
                    message_file=None,
                )
                print(
                    f"orc-hermit-msg: dry-run OK: {session}:{window} ({pane_id}) "
                    f"composer ready [state={composer.state or 'unknown'}]; "
                    "nothing was typed or sent"
                )
                return 0
            # send_message returns only if the composer was observed to take the
            # message and then drain. Anything else raises, so there is no path
            # that logs "sent" or exits 0 on a message that was not accepted.
            ack = send_message(socket, pane_id, message)
            submitted = True
            log_delivery(
                args.log_file,
                "sent",
                session=session,
                window=window,
                pane_id=pane_id,
                message=message,
                message_file=args.message_file,
                ack=ack,
            )
    except (OrcMessageError, OSError) as exc:
        if not submitted:
            with contextlib.suppress(OSError):
                log_delivery(
                    args.log_file,
                    "failed",
                    session=session,
                    window=window,
                    pane_id=pane_id,
                    message=message,
                    message_file=args.message_file,
                    error=str(exc),
                )
        fail(str(exc))

    print(
        f"orc-hermit-msg: sent to {session}:{window} ({pane_id}) "
        f"[ack={ack.evidence} composer_state={ack.composer_state or 'unknown'}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
