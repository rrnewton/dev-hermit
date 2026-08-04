#!/usr/bin/env python3
"""Publish one immutable, remotely readable local-validation receipt.

The local JSONL ledger is the authority.  A GitHub label or comment is only a
cache of a qualifying ledger row and cannot create evidence by itself.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


RECEIPT_REPO = "rrnewton/dev-hermit"
RECEIPT_BRANCH = "validation-receipts"
LABEL = "locally-validated"
COUNTS_SCHEMA = 5


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"publish-receipt: {message}")


def qualifying_row(rows: list[dict[str, Any]], sha: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for row in rows:
        if not (
            row.get("commit") == sha
            and row.get("profile") == "full"
            and row.get("selection_mode") == "full"
            and row.get("commit_anchored") is True
            and row.get("tree_dirty") is False
            and row.get("result") == "pass"
            and row.get("failures") == 0
            and isinstance(row.get("executed_tests"), int)
            and row["executed_tests"] > 0
        ):
            continue
        if (row.get("schema_version") or 0) >= COUNTS_SCHEMA:
            coverage = row.get("coverage")
            if not (
                isinstance(coverage, dict)
                and coverage.get("planned_test_nodes", 0) > 0
                and coverage.get("zero_executed_nodes") == []
                and coverage.get("absent_nodes") == []
            ):
                continue
        if not row.get("started_at") or not row.get("log_file"):
            continue
        matches.append(row)
    if not matches:
        fail(f"no counted clean full PASS ledger row for exact head {sha}")
    return max(matches, key=lambda row: row.get("finished_at") or "")


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                fail(f"{path}:{number}: invalid JSON: {error}")
            if isinstance(value, dict):
                rows.append(value)
    except OSError as error:
        fail(f"cannot read ledger {path}: {error}")
    return rows


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def preserve_log(ledger: Path, sha: str, row: dict[str, Any]) -> Path:
    source = Path(row["log_file"])
    if not source.is_absolute() or not source.is_file():
        fail(f"ledger log is not a readable absolute file: {source}")
    evidence_dir = ledger.parent / "validation-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    started = "".join(ch for ch in row["started_at"] if ch.isalnum())
    destination = evidence_dir / f"{sha}-{started}.log"
    content = source.read_bytes()
    if destination.exists() and destination.read_bytes() != content:
        fail(f"durable log path already contains different content: {destination}")
    if not destination.exists():
        destination.write_bytes(content)
    return destination


def build_receipt(repo: str, sha: str, row: dict[str, Any], durable_log: Path) -> tuple[dict[str, Any], bytes, str]:
    log_digest = hashlib.sha256(durable_log.read_bytes()).hexdigest()
    run_id = f"{sha}@{row['started_at']}"
    receipt = {
        "schema_version": 1,
        "repository": repo,
        "commit": sha,
        "run_id": run_id,
        "source_log_file": row["log_file"],
        "durable_log_file": str(durable_log),
        "log_sha256": log_digest,
        "ledger_record": row,
    }
    body = canonical(receipt)
    return receipt, body, hashlib.sha256(body).hexdigest()


def gh_command() -> list[str]:
    return ["with-proxy", "gh"] if shutil.which("with-proxy") else ["gh"]


def gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(gh_command() + args, text=True, capture_output=True)
    if check and result.returncode:
        fail(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result


def branch_head(repo: str, branch: str) -> str:
    result = gh(["api", f"repos/{repo}/git/ref/heads/{branch}"], check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)["object"]["sha"]
    main = gh(["api", f"repos/{repo}/git/ref/heads/main"])
    main_sha = json.loads(main.stdout)["object"]["sha"]
    created = gh(
        [
            "api",
            "--method",
            "POST",
            f"repos/{repo}/git/refs",
            "-f",
            f"ref=refs/heads/{branch}",
            "-f",
            f"sha={main_sha}",
        ],
        check=False,
    )
    if created.returncode != 0:
        # A concurrent publisher may have created the branch.
        result = gh(["api", f"repos/{repo}/git/ref/heads/{branch}"])
        return json.loads(result.stdout)["object"]["sha"]
    return json.loads(created.stdout)["object"]["sha"]


def publish(repo: str, branch: str, path: str, body: bytes) -> str:
    branch_head(repo, branch)
    endpoint = f"repos/{repo}/contents/{path}"
    existing = gh(["api", f"{endpoint}?ref={branch}"], check=False)
    if existing.returncode == 0:
        value = json.loads(existing.stdout)
        found = base64.b64decode(value["content"].replace("\n", ""))
        if found != body:
            fail(f"immutable receipt path already exists with different content: {path}")
        return branch_head(repo, branch)

    result = gh(
        [
            "api",
            "--method",
            "PUT",
            endpoint,
            "-f",
            f"message=validation receipt: {path.rsplit('/', 1)[-1]}",
            "-f",
            f"content={base64.b64encode(body).decode()}",
            "-f",
            f"branch={branch}",
        ]
    )
    return json.loads(result.stdout)["commit"]["sha"]


def issue_comments(repo: str, pr: int) -> list[dict[str, Any]]:
    result = gh(["api", "--paginate", "--slurp", f"repos/{repo}/issues/{pr}/comments?per_page=100"])
    values: list[dict[str, Any]] = []
    for page in json.loads(result.stdout):
        for value in page:
            if isinstance(value, dict):
                values.append(value)
    return values


def bind_pr(repo: str, pr: int, sha: str, receipt: dict[str, Any], receipt_commit: str,
            receipt_path: str, receipt_sha256: str) -> None:
    marker = (
        "<!-- locally-validated-receipt "
        f"commit={receipt_commit} path={receipt_path} sha256={receipt_sha256} -->"
    )
    if not any(marker in (comment.get("body") or "") for comment in issue_comments(repo, pr)):
        body = "\n".join(
            [
                "[impl agent, ci-hub]",
                "",
                "Local validation receipt published before applying `locally-validated`.",
                "",
                f"- SHA: `{sha}`",
                f"- Run ID: `{receipt['run_id']}`",
                f"- Executed tests: `{receipt['ledger_record']['executed_tests']}`",
                f"- Receipt: `{RECEIPT_REPO}@{receipt_commit}:{receipt_path}`",
                f"- Receipt SHA-256: `{receipt_sha256}`",
                f"- Log SHA-256: `{receipt['log_sha256']}`",
                "",
                marker,
            ]
        )
        gh(["pr", "comment", str(pr), "--repo", repo, "--body", body])
    gh(["pr", "edit", str(pr), "--repo", repo, "--add-label", LABEL])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--receipt-repo", default=RECEIPT_REPO)
    parser.add_argument("--receipt-branch", default=RECEIPT_BRANCH)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if len(args.sha) != 40 or any(ch not in "0123456789abcdef" for ch in args.sha):
        fail("--sha must be exactly 40 lowercase hex characters")
    row = qualifying_row(read_rows(args.ledger), args.sha)
    durable_log = preserve_log(args.ledger, args.sha, row)
    receipt, body, digest = build_receipt(args.repo, args.sha, row, durable_log)
    path = f"validation-receipts/{args.repo}/{args.sha}/{digest}.json"
    if args.dry_run:
        print(json.dumps({"action": "would-publish-and-bind", "path": path,
                          "receipt_sha256": digest, "receipt": receipt}, sort_keys=True))
        return 0
    commit = publish(args.receipt_repo, args.receipt_branch, path, body)
    bind_pr(args.repo, args.pr, args.sha, receipt, commit, path, digest)
    print(json.dumps({"action": "bound", "receipt_commit": commit,
                      "path": path, "receipt_sha256": digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
