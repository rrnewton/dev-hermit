#!/usr/bin/env python3
"""Call-site audit for the canonical Rust qualified-row authority."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_legacy_python_entrypoint_is_only_an_exec_shim() -> None:
    source = (ROOT / "ci-hub/validate/qualified_rows.py").read_text()
    assert "os.execv" in source
    assert "def is_qualified" not in source
    assert "def qualified_rows" not in source


def test_front_door_and_pr_status_use_one_bulk_verifier() -> None:
    rust = (ROOT / "ci-hub/ci-hub.rs").read_text()
    planner = (ROOT / "ci-hub/health/pr_status.py").read_text()
    assert "run_qualified_rows(root, qualified_args)" in rust
    assert '"ledger", "qualified-rows"' in planner
    assert 'run_python(root, "ci-hub/validate/qualified_rows.py"' not in rust


def test_authoritative_consumers_do_not_call_python_row_predicate() -> None:
    consumers = [
        ROOT / "ci-hub/validation/publish_receipt.py",
        ROOT / "ci-hub/history/query.py",
        ROOT / "ci-hub/validation/verify_receipt.sh",
    ]
    for path in consumers:
        assert "qualifying_receipt.row_qualifies" not in path.read_text(), path
