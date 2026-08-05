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
    refill_attributions,
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


def _red_row_with_classes(
    commit: str, finished_at: str, log_file: str | None, *classes: dict
) -> dict:
    """A red row as the PRODUCER (validate.sh) now writes it: the per-node
    failed_substep_classes are inlined INTO the row, so attribution no longer
    depends on log_file surviving."""
    row = _red_row(commit, finished_at, log_file)
    row["failed_substep_classes"] = list(classes)
    return row


def _cls(node: str, first_error_line: str, fault_class: str) -> dict:
    return {
        "node": node,
        "group": node.split(".", 1)[0],
        "sub_step_class": "build" if node.startswith("build") else "test",
        "fault_class": fault_class,
        "infra_signature": "link-error" if fault_class == "infrastructure" else None,
        "first_error_line": first_error_line,
        "known_flaky": fault_class == "code",
    }


def test_row_inlined_classes_survive_log_eviction(tmp_path):
    """The producer-leg durability contract: a row carrying
    failed_substep_classes is attributable even when its log_file NEVER EXISTED
    (already evicted). This is the eviction test at the consumer boundary."""
    out = tmp_path / "attr.jsonl"
    gone = str(tmp_path / "already-evicted.log")
    assert not Path(gone).exists()
    row = _red_row_with_classes(
        "ec0ec0ec",
        "2026-08-04T22:00:00Z",
        gone,
        _cls(
            "build.runtime_release",
            "scheduler_impl.h:1325: undefined reference to "
            "`dynamorio::drmemtrace::op_infile[abi:cxx11]'",
            "infrastructure",
        ),
    )
    records, (appended, skipped) = _persist([row], out)
    assert records[0]["classes_source"] == "row"
    assert records[0]["log_status"] == "missing"  # log is gone...
    assert (appended, skipped) == (1, 0)  # ...but attribution still persisted
    [rec] = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert rec["node"] == "build.runtime_release"
    assert rec["fault_class"] == "infrastructure"
    assert rec["source"] == "row"
    assert "undefined reference to" in rec["first_error_line"]


def test_row_inlined_classes_preferred_over_surviving_log(tmp_path):
    """When both are present the ROW wins — the producer's inlined verdict is the
    fact; the log is only a fallback. Proves the reader does not silently re-derive
    from (and disagree with) a stale/other log."""
    # The log names a CODE fault; the row inlines an INFRA fault. Row must win.
    log = _log(tmp_path, "both.log", "build.runtime_release", "error: boom")
    out = tmp_path / "attr.jsonl"
    row = _red_row_with_classes(
        "b077b077",
        "2026-08-04T22:05:00Z",
        str(log),
        _cls("build.runtime_release", "row-verbatim: undefined reference to X", "infrastructure"),
    )
    record = attribute_row(row)
    assert record["classes_source"] == "row"
    assert record["log_status"] == "present"
    assert record["classes"][0]["first_error_line"] == "row-verbatim: undefined reference to X"
    persist_attributions([record], out)
    [rec] = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert rec["first_error_line"] == "row-verbatim: undefined reference to X"
    assert rec["source"] == "row"


def test_empty_row_classes_falls_back_to_log(tmp_path):
    """An empty failed_substep_classes (e.g. outer-gate fail, no substep) is not
    row-attribution — the reader falls back to the log rather than recording
    nothing when a log still survives."""
    log = _log(tmp_path, "fb.log", "e2e.metadata", "some noise")
    out = tmp_path / "attr.jsonl"
    row = _red_row("fa11fa11", "2026-08-04T22:10:00Z", str(log))
    row["failed_substep_classes"] = []  # present but empty
    record = attribute_row(row)
    assert record["classes_source"] == "log"
    appended, _ = persist_attributions([record], out)
    assert appended == 1


def _null_record(commit: str, finished_at: str, node: str, log_file: str) -> str:
    """A record as an OLDER extractor would have persisted it: no first_error_line."""
    return json.dumps(
        {
            "commit": commit,
            "finished_at": finished_at,
            "log_file": log_file,
            "node": node,
            "sub_step_class": None,
            "fault_class": None,
            "infra_signature": None,
            "first_error_line": None,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def test_refill_backfills_null_line_when_the_log_survives(tmp_path):
    log = _log(tmp_path, "r.log", "e2e.metadata", "some noise")
    out = tmp_path / "attr.jsonl"
    out.write_text(
        _null_record("f00d", "2026-08-04T21:00:00Z", "e2e.metadata", str(log)) + "\n",
        encoding="utf-8",
    )
    refilled, still_null, evicted = refill_attributions(out)
    assert (refilled, still_null, evicted) == (1, 0, 0)
    [rec] = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    # The harness terminal verdict the hardened extractor now surfaces.
    assert rec["first_error_line"] == "✗ FAIL   e2e.metadata (exit 101)"
    assert rec["node"] == "e2e.metadata"


def test_refill_leaves_evicted_log_records_untouched(tmp_path):
    out = tmp_path / "attr.jsonl"
    original = _null_record(
        "dead", "2026-08-04T21:00:00Z", "e2e.metadata", str(tmp_path / "gone.log")
    )
    out.write_text(original + "\n", encoding="utf-8")
    refilled, still_null, evicted = refill_attributions(out)
    assert (refilled, still_null, evicted) == (0, 0, 1)
    # Never fabricated; the record is byte-identical.
    assert out.read_text() == original + "\n"


def test_refill_does_not_touch_records_that_already_have_a_line(tmp_path):
    log = _log(tmp_path, "s.log", "build.runtime_release", "error: boom")
    out = tmp_path / "attr.jsonl"
    _persist([_red_row("cafe", "2026-08-04T20:00:00Z", str(log))], out)
    before = out.read_text()
    refilled, still_null, evicted = refill_attributions(out)
    assert (refilled, still_null, evicted) == (0, 0, 0)
    assert out.read_text() == before  # byte-identical, no rewrite churn


def test_refill_is_idempotent(tmp_path):
    log = _log(tmp_path, "t.log", "e2e.metadata", "noise")
    out = tmp_path / "attr.jsonl"
    out.write_text(
        _null_record("beef", "2026-08-04T21:00:00Z", "e2e.metadata", str(log)) + "\n",
        encoding="utf-8",
    )
    assert refill_attributions(out)[0] == 1
    assert refill_attributions(out) == (0, 0, 0)  # second pass finds nothing null


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
