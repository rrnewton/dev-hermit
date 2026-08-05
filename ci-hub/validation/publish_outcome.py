#!/usr/bin/env python3
"""Publish one deny/no-result exact-SHA validation outcome snapshot.

This is mechanical transport. Rust computes and re-verifies the typed verdict;
the append-only content-addressed artifact merely makes every completed outcome
discoverable to immutable consumers, including failures that must dominate an
older pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from publish_receipt import (
    RECEIPT_BRANCH,
    RECEIPT_REPO,
    canonical,
    ledger_snapshot,
    publish,
)


DENY_VERDICTS = (
    "FAILED",
    "NEEDS-RERUN",
    "TRUNCATED",
    "NO-RESULT",
    "NOT-VALIDATED",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--verdict", required=True, choices=DENY_VERDICTS)
    parser.add_argument("--receipt-repo", default=RECEIPT_REPO)
    parser.add_argument("--receipt-branch", default=RECEIPT_BRANCH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = ledger_snapshot(args.ledger, args.repo, args.sha)
    outcome = {
        "schema_version": 1,
        "repository": args.repo,
        "commit": args.sha,
        "verdict": args.verdict,
        "ledger_records": rows,
        "selected_receipt_identity": None,
        "receipt": None,
    }
    body = canonical(outcome)
    digest = hashlib.sha256(body).hexdigest()
    path = f"validation-outcomes/{args.repo}/{args.sha}/{digest}.json"
    commit = None
    if not args.dry_run:
        commit = publish(args.receipt_repo, args.receipt_branch, path, body)
    print(json.dumps({
        "schema_version": 1,
        "action": "would-publish" if args.dry_run else "published",
        "receipt_repository": args.receipt_repo,
        "receipt_branch": args.receipt_branch,
        "outcome_commit": commit,
        "outcome_path": path,
        "outcome_sha256": digest,
        "outcome_body": body.decode(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
