#!/usr/bin/env python3
"""Bar for the durable red-attribution capture (attribute_reds --persist).

WHY: the run ledger's ``log_file`` is ephemeral /tmp state; a red row is
attributable only while its log survives. The capture must lift the VERBATIM
first_error_line out of a surviving log into a durable append-only record that
outlives the log — and must NEVER fabricate a line when the log is gone, and must
be idempotent so it can run on every landing without duplicating rows.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attribute_reds import (  # noqa: E402
    attribute_row,
    persist_attributions,
)


def _log(tmp_path: Path, name: str, node: str, *body: str) -> Path:
    lines = [f"[{node}] {b}" for b in body]
    lines.append(f"[{node}] ✗ FAIL   {node} (exit 101)")
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _red_row(commit: str, finished_at: str, log_file: str | None) -> dict:
    return {
        "commit": commit,
        "finished_at": finished_at,
        "result": "fail",
        "exit_code": 1,
        "gates": [{"name": "portable CI DAG manifest", "result": "fail"}],
        "log_file": log_file,
    }


def _persist(rows, out: Path):
    records = [attribute_row(r) for r in rows]
    return records, persist_attributions(records, out)


def test_persist_captures_verbatim_line_and_survives_the_log(tmp_path):
    log = _log(
        tmp_path,
        "u7wd6E.log",
        "build.runtime_release",
        "scheduler_impl.h:1325: undefined reference to "
        "`dynamorio::drmemtrace::op_infile[abi:cxx11]'",
    )
    out = tmp_path / "attr.jsonl"
    _, (appended, skipped) = _persist(
        [_red_row("deadbeef", "2026-08-04T19:31:00Z", str(log))], out
    )
    assert (appended, skipped) == (1, 0)

    # Delete the log: the durable record must still carry the verbatim line.
    log.unlink()
    [rec] = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert rec["node"] == "build.runtime_release"
    assert rec["fault_class"] == "infrastructure"
    assert rec["first_error_line"] == (
        "scheduler_impl.h:1325: undefined reference to "
        "`dynamorio::drmemtrace::op_infile[abi:cxx11]'"
    )
    assert rec["commit"] == "deadbeef"


def test_persist_separates_two_faults_under_one_gate_name(tmp_path):
    dynamorio = _log(
        tmp_path, "a.log", "build.runtime_release",
        "undefined reference to `dynamorio::drmemtrace::op_infile[abi:cxx11]'",
    )
    lockfile = _log(
        tmp_path, "b.log", "build.runtime_release",
        "error: cannot update the lock file "
        "liteinst-runtime-build/Cargo.lock because --locked was passed",
    )
    out = tmp_path / "attr.jsonl"
    _persist(
        [
            _red_row("aaaa1111", "2026-08-04T19:31:00Z", str(dynamorio)),
            _red_row("bbbb2222", "2026-08-04T19:32:00Z", str(lockfile)),
        ],
        out,
    )
    recs = {
        json.loads(l)["commit"]: json.loads(l)
        for l in out.read_text().splitlines()
        if l.strip()
    }
    # Same gate name, same failing node — separated by the verbatim line alone.
    assert "undefined reference to" in recs["aaaa1111"]["first_error_line"]
    assert "--locked was passed" in recs["bbbb2222"]["first_error_line"]
    assert recs["aaaa1111"]["fault_class"] == "infrastructure"
    assert recs["bbbb2222"]["fault_class"] == "code"


def test_persist_is_idempotent_across_reruns(tmp_path):
    log = _log(tmp_path, "c.log", "build.runtime_release", "error: boom")
    out = tmp_path / "attr.jsonl"
    rows = [_red_row("cafe", "2026-08-04T20:00:00Z", str(log))]

    _, first = _persist(rows, out)
    _, second = _persist(rows, out)
    assert first == (1, 0)
    assert second == (0, 1)  # already present -> nothing re-appended
    lines = [l for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 1


def test_persist_never_fabricates_when_log_is_missing(tmp_path):
    out = tmp_path / "attr.jsonl"
    _, (appended, skipped) = _persist(
        [_red_row("dead", "2026-08-04T20:00:00Z", str(tmp_path / "gone.log"))], out
    )
    assert (appended, skipped) == (0, 0)
    assert not out.exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
