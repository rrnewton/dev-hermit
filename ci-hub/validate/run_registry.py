#!/usr/bin/env python3
"""Durable handles for ci-hub-owned validation services and observer panes."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping


def record_path(root: Path, unit: str) -> Path:
    return root / "ignored" / "validate" / "runs" / f"{unit.removesuffix('.service')}.json"


def read_record(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read validation handle {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError(f"validation handle {path} has an unsupported schema")
    return value


@contextmanager
def exclusive_record(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_unlocked(path: Path, value: Mapping[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(dict(value), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_record(path: Path, value: Mapping[str, Any]) -> None:
    with exclusive_record(path):
        _write_unlocked(path, value)


def update_record(path: Path, **fields: Any) -> dict[str, Any]:
    # The caller and the observer finish at nearly the same time. Serialize the
    # read-modify-write so a final exit update cannot erase cgroup evidence (or
    # vice versa) while retaining atomic replacement for readers.
    with exclusive_record(path):
        value = read_record(path)
        value.update(fields)
        _write_unlocked(path, value)
    return value
