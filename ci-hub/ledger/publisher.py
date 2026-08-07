#!/usr/bin/env python3
"""Publish ledger events to the shared Git-backed history.

Three properties, in the order they matter:

**A local event is never lost.** Every event is written to a local spool
*before* any Git operation, and the spool entry is dropped only after the
commit carrying it is confirmed to be an ancestor of the freshly-fetched remote
branch. Push reporting success is not that confirmation — a push can succeed
against a ref that is then replaced — so publication is verified by ancestry,
not by an exit code.

**A concurrent publisher never costs us a row.** Two machines write different
shards and merge trivially. Two producers on one machine serialise through
`flock` in `ledger.append_events`. The remaining case is the interesting one: a
non-fast-forward push, meaning someone appended to the same shard between our
read and our push. That is resolved by an APPEND-AWARE merge — re-read the
remote shard, keep every remote line in place, and re-append only the events
that are not already there. Never `git checkout --theirs`, never a rewrite: the
file is append-only and a whole-file replacement is precisely the data loss
this module exists to prevent.

**No row is ever rewritten.** `verify_append_only` runs against the remote
content before each push. If our proposed content is not a line-for-line
extension of what is published, the publish is refused rather than forced.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Iterable, Sequence

from ledger import (  # noqa: F401 - re-exported for callers
    MalformedEvent,
    append_events,
    parse_line,
    read_shard,
    shard_path,
    union_events,
    validate_event,
    verify_append_only,
)

DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"
#: Bounded so a genuinely wedged remote surfaces as a failure with the spool
#: intact, instead of spinning forever and looking healthy.
DEFAULT_ATTEMPTS = 5


class PublishRefused(Exception):
    """The publish would have rewritten published bytes, or the events are invalid."""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


# --------------------------------------------------------------------------- spool


def spool_file(spool_dir: str | os.PathLike[str], team: str, host: str, when: str) -> Path:
    return Path(spool_dir) / f"{team}__{host}__{when[:7]}.jsonl"


def spool(spool_dir: str | os.PathLike[str], team: str, host: str, events: Sequence[dict]) -> Path:
    """Durably record events locally before any network or Git work.

    This is the ordering that makes loss impossible: if everything after this
    call fails, the events are still on disk and the next publish picks them
    up. Uses the same locked append as the ledger itself, so two producers
    spooling at once cannot tear a line.
    """
    if not events:
        raise PublishRefused("refusing to spool an empty batch")
    when = str(events[0].get("emitted_at", ""))
    target = spool_file(spool_dir, team, host, when)
    append_events(target, events)
    return target


def spooled(spool_dir: str | os.PathLike[str]) -> dict[tuple[str, str, str], list[dict]]:
    """Every pending event, keyed by (team, host, month)."""
    pending: dict[tuple[str, str, str], list[dict]] = {}
    root = Path(spool_dir)
    if not root.is_dir():
        return pending
    for path in sorted(root.glob("*.jsonl")):
        team, host, month = path.stem.split("__", 2)
        pending[(team, host, month)] = read_shard(path)
    return pending


# --------------------------------------------------------------------------- merge


def merge_shard(remote_text: str, events: Sequence[dict]) -> str:
    """Append-aware merge: published lines stay put, new events go after them.

    Returns the full shard text. An event already present remotely is not
    re-appended, which is what makes a retry idempotent — the same batch pushed
    twice yields one copy, not two.
    """
    remote_lines = [ln for ln in remote_text.splitlines() if ln.strip()]
    if remote_text and not remote_text.endswith("\n"):
        raise MalformedEvent("remote shard's final line is truncated; refusing to append")
    have = {ln for ln in remote_lines}
    have_ids = set()
    for line in remote_lines:
        have_ids.add(parse_line(line).get("event_id"))
    additions = []
    for event in events:
        line = json.dumps(event, sort_keys=True)
        if line in have or event.get("event_id") in have_ids:
            continue
        have.add(line)
        have_ids.add(event.get("event_id"))
        additions.append(line)
    body = remote_lines + additions
    return "".join(line + "\n" for line in body)


# --------------------------------------------------------------------------- publish


def publish(
    repo: str | os.PathLike[str],
    spool_dir: str | os.PathLike[str],
    *,
    remote: str = DEFAULT_REMOTE,
    branch: str = DEFAULT_BRANCH,
    attempts: int = DEFAULT_ATTEMPTS,
    fetch: bool = True,
) -> dict:
    """Publish everything spooled; drop spool entries only once ancestry proves it.

    Returns evidence, not a boolean: the commit written, how many attempts the
    push needed, and the ancestry check that authorised draining the spool.
    """
    repo = Path(repo)
    pending = spooled(spool_dir)
    if not pending:
        return {"published": 0, "commit": None, "attempts": 0, "shards": []}

    # Validate before touching Git. A refused batch stays spooled and visible
    # rather than being written into shared history and linted afterwards.
    for (team, host, month), events in pending.items():
        rel = f"ledger/{team}/{host}/{month}.jsonl"
        for event in events:
            codes = validate_event(event, rel)
            if codes:
                raise PublishRefused(f"{rel}: event {event.get('event_id')!r} violates {codes}")

    last_error = ""
    for attempt in range(1, attempts + 1):
        if fetch:
            _git(repo, "fetch", remote, branch, check=False)
        _git(repo, "checkout", "--detach", f"{remote}/{branch}", check=False)

        written: list[str] = []
        for (team, host, month), events in sorted(pending.items()):
            rel = f"ledger/{team}/{host}/{month}.jsonl"
            target = repo / rel
            # The PUBLISHED content, read from the remote ref -- never from the
            # working tree. A leftover file from a failed earlier attempt is our
            # own output, not published state; reading it made publish()
            # conclude "all events already published" and DRAIN THE SPOOL while
            # nothing had landed. Found the hard way migrating the 654-row
            # legacy ledger: attempt 1 wrote the file and then failed at commit,
            # attempt 2 saw its own leftover and reported 0 published.
            shown = _git(repo, "show", f"{remote}/{branch}:{rel}", check=False)
            remote_text = shown.stdout if shown.returncode == 0 else ""
            merged = merge_shard(remote_text, events)
            violations = verify_append_only(remote_text, merged)
            if violations:
                raise PublishRefused(f"{rel}: merge would violate append-only {violations}")
            if merged == remote_text:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(merged)
            written.append(rel)

        if not written:
            _drain(spool_dir, pending)
            return {"published": 0, "commit": None, "attempts": attempt,
                    "shards": [], "note": "all events already published"}

        _git(repo, "add", "--", *written)
        _git(repo, "-c", "user.name=ledger-publisher",
             "-c", "user.email=ledger-publisher@invalid",
             "commit", "-q", "-m",
             f"ledger: append {sum(len(v) for v in pending.values())} event(s)", "--", *written)
        commit = _git(repo, "rev-parse", "HEAD").stdout.strip()

        pushed = _git(repo, "push", remote, f"HEAD:refs/heads/{branch}", check=False)
        if pushed.returncode == 0:
            # Push exit status is not publication. Confirm the commit is an
            # ancestor of the freshly-fetched branch before dropping anything.
            if fetch:
                _git(repo, "fetch", remote, branch, check=False)
            ancestry = _git(repo, "merge-base", "--is-ancestor", commit,
                            f"{remote}/{branch}", check=False)
            if ancestry.returncode == 0:
                _drain(spool_dir, pending)
                return {"published": sum(len(v) for v in pending.values()),
                        "commit": commit, "attempts": attempt, "shards": written,
                        "ancestry": f"{commit} is an ancestor of {remote}/{branch}"}
            last_error = "push reported success but the commit is not an ancestor of the branch"
            continue

        # Non-fast-forward: someone appended to a shard we are also appending
        # to. Loop; the next pass re-reads the remote shard and re-merges, so
        # their lines are preserved and ours are added once.
        last_error = pushed.stderr.strip().splitlines()[-1] if pushed.stderr.strip() else "push failed"

    raise PublishRefused(
        f"gave up after {attempts} attempts; spool retained. last error: {last_error}"
    )


def _drain(spool_dir: str | os.PathLike[str], pending: dict) -> None:
    """Remove spool files only after publication is proven."""
    for (team, host, month) in pending:
        spool_file(spool_dir, team, host, f"{month}-01").unlink(missing_ok=True)
