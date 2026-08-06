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

# The shared qualifying-receipt predicate lives at the ci-hub root (beside
# check_outcome.py), one directory above this validation/ package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qualifying_receipt  # noqa: E402


RECEIPT_REPO = "rrnewton/dev-hermit"
RECEIPT_BRANCH = "validation-receipts"
LABEL = "locally-validated"
# The count-schema boundary is NOT redefined here -- it lives once in
# qualifying-receipt.json (`counts_schema`) and is read via qualifying_receipt.


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"publish-receipt: {message}")


def qualifying_row(rows: list[dict[str, Any]], sha: str) -> dict[str, Any]:
    # The green predicate is the ONE shared qualifying-receipt predicate
    # (`ci-hub/validate/qualifying-receipt.json`) that every consumer reads --
    # restating the clauses inline is the drift this delegation removes (task
    # `one-shared-qualifying-receipt-predicate-five-consumers-bypass-the-registry`).
    # Publishing additionally requires the receipt to be preservable, so it keeps
    # the started_at + log_file checks the predicate deliberately does not carry.
    pred = qualifying_receipt.active()
    matches: list[dict[str, Any]] = []
    for row in rows:
        if not qualifying_receipt.row_qualifies(row, sha, pred):
            continue
        if not row.get("started_at") or not row.get("log_file") or not row.get("host"):
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


def producer_registry_path() -> Path:
    override = os.environ.get("PRODUCER_DEFINITION_REGISTRY")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "validate" / "producer-definition.json"


def registered_producer() -> dict[str, str]:
    """The registered current producer definition (file -> git blob)."""
    path = producer_registry_path()
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read producer-definition registry {path}: {error}")
    registered = value.get("registered")
    if not isinstance(registered, dict) or not registered:
        fail(f"malformed producer-definition registry (no non-empty .registered): {path}")
    return registered


def producer_definition(row: dict[str, Any], sha: str) -> dict[str, Any]:
    """Identify the check definition that produced this row.

    Derived from the VALIDATED COMMIT (`git rev-parse <sha>:<path>`), not
    self-reported by the producer: the blobs are a property of the commit that
    was validated, so a producer cannot claim a definition it did not run
    without also changing the commit under test.  Failure to resolve is fatal --
    a receipt that cannot name its producer must not be minted at all, because a
    producer-less receipt is exactly what the consumer now refuses.
    """
    checkout = row.get("cwd")
    if not checkout or not Path(checkout).is_dir():
        fail(
            "ledger row has no usable `cwd`, so the producing check definition "
            f"cannot be resolved for {sha}"
        )
    definition: dict[str, str] = {}
    for relative in sorted(registered_producer()):
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", f"{sha}:{relative}"],
            text=True,
            capture_output=True,
        )
        blob = result.stdout.strip()
        if result.returncode != 0 or len(blob) != 40:
            fail(
                f"cannot resolve producer blob for {relative} at {sha} in {checkout}: "
                f"{result.stderr.strip() or 'no output'}"
            )
        definition[relative] = blob
    return {"resolved_from": str(checkout), "definition": definition}


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
        # Producer binding: WHICH check definition produced this receipt, at
        # which head. The consumer requires this to equal the registered
        # current definition, so a receipt minted by an older/foreign
        # validate.sh cannot authorize a landing (task bind_receipt_to_producer).
        "producer": producer_definition(row, sha),
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
