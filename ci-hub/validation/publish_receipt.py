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
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


RECEIPT_REPO = "rrnewton/dev-hermit"
RECEIPT_BRANCH = "validation-receipts"
LABEL = "locally-validated"
CI_HUB_BIN = Path(__file__).resolve().parents[1] / "ci-hub"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"publish-receipt: {message}")


def verifier_report(ledger: Path, sha: str, hermit_repo: Path) -> dict[str, Any]:
    """Dereference the one semantic verifier; never reimplement its clauses."""
    proc = subprocess.run(
        [
            str(CI_HUB_BIN),
            "validate-status",
            "--repo",
            "rrnewton/hermit",
            "--sha",
            sha,
            "--ledger",
            str(ledger),
            "--hermit-repo",
            str(hermit_repo),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fail(
            "canonical verifier returned no JSON: "
            + ((proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}")
        )
    if proc.returncode != 0:
        fail(
            f"canonical verifier refused {sha}: "
            f"{report.get('verdict', 'UNVERIFIABLE')}"
        )
    return report


def qualifying_row(report: dict[str, Any], sha: str) -> dict[str, Any]:
    """Extract the exact row already admitted by `ci-hub validate-status`.

    Only preservation-envelope fields remain local to this publisher. Receipt
    semantics, including the fresh Reverie binding, belong solely to Rust.
    """
    if (
        report.get("sha") != sha
        or report.get("verdict") != "VALIDATED"
        or not isinstance(report.get("qualifying_count"), int)
        or report["qualifying_count"] < 1
        or not isinstance(report.get("newest_qualifying_record"), dict)
    ):
        fail(f"canonical verifier did not admit exact head {sha}")
    row = report["newest_qualifying_record"]
    if not row.get("started_at") or not row.get("log_file") or not row.get("host"):
        fail("canonical row lacks started_at/log_file/host needed for preservation")
    return row


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
    # Host-in-identity (Req2): the receipt identity carries the producing host,
    # so a receipt cannot be read blind to where it was produced and its host
    # field cannot be swapped without breaking this tamper-evident run_id.
    run_id = f"{sha}@{row['started_at']}@{row['host']}"
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
    environment = os.environ.copy()
    if not environment.get("GH_CONFIG_DIR"):
        candidate = Path.home() / ".config" / "gh"
        if (candidate / "hosts.yml").is_file():
            environment["GH_CONFIG_DIR"] = str(candidate)
    result = subprocess.run(
        gh_command() + args, text=True, capture_output=True, env=environment
    )
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
    parser.add_argument(
        "--hermit-repo",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "hermit",
    )
    parser.add_argument("--receipt-repo", default=RECEIPT_REPO)
    parser.add_argument("--receipt-branch", default=RECEIPT_BRANCH)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if len(args.sha) != 40 or any(ch not in "0123456789abcdef" for ch in args.sha):
        fail("--sha must be exactly 40 lowercase hex characters")
    row = qualifying_row(verifier_report(args.ledger, args.sha, args.hermit_repo), args.sha)
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
