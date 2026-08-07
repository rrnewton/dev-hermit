#!/usr/bin/env python3
"""Check or repair byte-prefix history files against an exact Git revision.

Normal mode proves that every selected path begins with the exact bytes stored
at ``BASE:path``.  ``--repair`` is deliberately narrower than a merge driver:
the candidate must contain every base line, byte-for-byte and in order.  It
then moves only the intervening additive chunks after the complete base.  A
replacement, deletion, or reorder is refused rather than guessed.

JSONL additions are individual records and are appended once by exact bytes.
For other text histories, contiguous insertion chunks are retained once.  This
keeps the frozen base as a byte prefix while preserving branch-only records.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def git_show(repo: Path, revision: str, path: Path) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{revision}:{path.as_posix()}"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise ValueError(f"cannot read {revision}:{path}: {detail}")
    return result.stdout


def additive_chunks(base: bytes, candidate: bytes) -> list[bytes]:
    """Return candidate-only chunks when ``base`` is an ordered line subsequence."""
    base_lines = base.splitlines(keepends=True)
    candidate_lines = candidate.splitlines(keepends=True)
    base_index = 0
    pending: list[bytes] = []
    chunks: list[bytes] = []
    for line in candidate_lines:
        if base_index < len(base_lines) and line == base_lines[base_index]:
            if pending:
                chunks.append(b"".join(pending))
                pending = []
            base_index += 1
        else:
            pending.append(line)
    if pending:
        chunks.append(b"".join(pending))
    if base_index != len(base_lines):
        raise ValueError(
            "candidate is not additive: base bytes were replaced, deleted, or reordered"
        )
    return chunks


def repaired_bytes(path: Path, base: bytes, candidate: bytes) -> bytes:
    chunks = additive_chunks(base, candidate)
    additions: list[bytes] = []
    seen: set[bytes] = set()
    if path.suffix == ".jsonl":
        base_records = set(base.splitlines(keepends=True))
        for chunk in chunks:
            for record in chunk.splitlines(keepends=True):
                if not record.strip():
                    continue
                try:
                    parsed = json.loads(record)
                except json.JSONDecodeError as error:
                    raise ValueError(f"branch-only JSONL record is invalid: {error}") from error
                if not isinstance(parsed, dict):
                    raise ValueError("branch-only JSONL record is not an object")
                if record not in base_records and record not in seen:
                    additions.append(record)
                    seen.add(record)
    else:
        for chunk in chunks:
            if chunk and chunk not in seen:
                additions.append(chunk)
                seen.add(chunk)
    return base + b"".join(additions)


def check_suffix_uniqueness(path: Path, base: bytes, candidate: bytes) -> None:
    if path.suffix != ".jsonl":
        return
    suffix = candidate[len(base) :]
    base_records = set(base.splitlines(keepends=True))
    seen: set[bytes] = set()
    for record in suffix.splitlines(keepends=True):
        if not record.strip():
            continue
        try:
            parsed = json.loads(record)
        except json.JSONDecodeError as error:
            raise ValueError(f"appended JSONL record is invalid: {error}") from error
        if not isinstance(parsed, dict):
            raise ValueError("appended JSONL record is not an object")
        if record in base_records or record in seen:
            raise ValueError("appended JSONL record is not unique")
        seen.add(record)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="exact base Git revision")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("paths", nargs="+", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if FULL_SHA.fullmatch(args.base) is None:
        print("FAIL --base must be an immutable lowercase 40-hex commit", file=sys.stderr)
        return 2
    failures: list[str] = []
    for path in args.paths:
        target = args.repo / path
        try:
            base = git_show(args.repo, args.base, path)
            candidate = target.read_bytes()
            if args.repair:
                candidate = repaired_bytes(path, base, candidate)
                target.write_bytes(candidate)
            if not candidate.startswith(base):
                raise ValueError("candidate does not preserve the base as an exact byte prefix")
            check_suffix_uniqueness(path, base, candidate)
            print(
                f"PASS {path}: base_bytes={len(base)} appended_bytes={len(candidate) - len(base)}"
            )
        except (OSError, ValueError) as error:
            failures.append(f"{path}: {error}")
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
