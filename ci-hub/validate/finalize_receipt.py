#!/usr/bin/env python3
"""MAIN-REACHABLE validation-receipt coverage + dependency-binding finalizer.

Produces the schema-5 `coverage{}` obligation plus the schema-6 exact Reverie
binding for a validate run. Coverage reads two things that already exist on `main`:

  1. the full safe-ci-dag-runner log (`[<node.tag>] `-prefixed child stdout +
     `[<node.tag>] ✓ PASS/✗ FAIL` terminal lines), and
  2. the PLANNED test-node set, read from the DAG manifests AT THE EXACT COMMIT
     via `git show <sha>:ci/dag/{portable,privileged}.json`.

Reading the manifest at the commit — WITHOUT running that branch's `validate.sh`
— is what makes this main-reachable and version-independent: the planned set is
the branch's own declared intent, but the finalizer that judges it lives on main
and cannot be weakened by an older PR-branch YAML.

Design source: ai_docs/bind-validation-counts-per-node-coverage-design_20260804.md.

The obligation (computed here, ENFORCED by the Rust consumer from the receipt):
  * ran/absent   : every PLANNED `test.*` node must have a terminal PASS/FAIL line;
                   a planned node with none -> `absent_nodes` -> NOT satisfied.
  * zero-executed: a planned node that emitted >=1 libtest banner must have
                   passed-sum > 0; a banner-emitting node summing to 0 ->
                   `zero_executed_nodes` -> NOT satisfied. A node with ZERO
                   banners is EXEMPT (legit shell/e2e/nextest node).
  * `filtered_tests` is a DIAGNOSTIC ONLY -- there is NO `filtered == 0` predicate.

The consumer's `coverage_satisfied` == `planned_test_nodes > 0 &&
executed_test_nodes == planned_test_nodes && zero_executed_nodes == [] &&
absent_nodes == []`. This finalizer does NOT decide
validated/not; it only WRITES the qualified row (carry the condition with the
value) so the Rust consumer can re-derive the verdict from receipt fields alone.

Authoritative exact-SHA usage (append-only; the row chooses its own log):
  finalize_receipt.py --repo rrnewton/hermit --sha SHA --ledger LEDGER \
    --hermit-checkout DIR

Coverage-only diagnostics (never authorizes or writes a ledger):
  finalize_receipt.py --log LOG --sha SHA --hermit-checkout DIR --emit-only
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys

# ONE extractor, not a second regex copy that could drift: per-node counting
# lives solely in the remediation `nonzero_result` module (also imported by
# aggregate.py and protocol.py). Mirror aggregate.py's sys.path setup.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "remediation"))
from nonzero_result import per_node_counts  # noqa: E402

SCHEMA_VERSION = 6
COVERAGE_SCHEMA_VERSION = 5
MANIFESTS = ("ci/dag/portable.json", "ci/dag/privileged.json")
CI_HUB_BIN = os.path.join(os.path.dirname(__file__), "..", "ci-hub")


def planned_test_nodes(hermit_checkout: str, sha: str) -> set[str]:
    """Union of `test.<job>` tags from the DAG manifests AT `sha`.

    Reads each manifest with `git -C <checkout> show <sha>:<manifest>`; a manifest
    Missing, unreadable, or malformed manifests are fatal: silently dropping a
    lane would let a partial run claim complete coverage. A TEST node is a
    manifest step with `group == "test"`; its runner tag is `test.<job>`.
    """
    planned: set[str] = set()
    for manifest in MANIFESTS:
        try:
            proc = subprocess.run(
                ["git", "-C", hermit_checkout, "show", f"{sha}:{manifest}"],
                capture_output=True,
                text=True,
                env=_sanitized_git_environment(),
            )
        except OSError as error:
            raise ValueError(f"cannot read required manifest {manifest}: {error}") from error
        if proc.returncode != 0:
            raise ValueError(
                f"cannot read required manifest {manifest} at {sha}: "
                f"{proc.stderr.strip() or f'exit {proc.returncode}'}"
            )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as error:
            raise ValueError(f"required manifest {manifest} is invalid JSON: {error}") from error
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            raise ValueError(f"required manifest {manifest} has no steps array")
        for step in data["steps"]:
            if not isinstance(step, dict):
                raise ValueError(f"required manifest {manifest} has a non-object step")
            if step.get("group") == "test":
                job = step.get("job")
                if not isinstance(job, str) or not job:
                    raise ValueError(f"required manifest {manifest} has a test step without a job")
                planned.add(f"test.{job}")
    return planned


def _sanitized_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_PARAMETERS", "GIT_CONFIG", "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
    ):
        environment.pop(variable, None)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = "/dev/null"
    environment["GIT_CONFIG_SYSTEM"] = "/dev/null"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    return environment


def build_coverage(log_text: str, planned: set[str]) -> dict:
    """Compute the schema-5 `coverage{}` object + aggregate counts from a log.

    `planned` is the manifest-derived planned test-node set. The two coverage
    lists (`zero_executed_nodes`, `absent_nodes`) are subsets of `planned`: the
    obligation is about the set the profile PLANNED, so a stray unplanned node is
    never counted, and a planned node the log never mentions is `absent`.

    Returns a dict with `schema_version`, `executed_tests`, `filtered_tests`, and
    `coverage`. `executed_tests`/`filtered_tests` are the aggregate sum of
    `passed`/`filtered out` across ALL banners in the log; both are `None`
    (JSON null = UNKNOWN) iff the log carried NO libtest banner at all, distinct
    from `0` (banners present, summing to zero).
    """
    nodes = per_node_counts(log_text)

    absent = sorted(
        tag for tag in planned
        if (n := nodes.get(tag)) is None or n["terminal"] is None
    )
    failed = sorted(
        tag for tag in planned
        if (n := nodes.get(tag)) is not None and n["terminal"] == "fail"
    )
    zero_executed = sorted(
        tag for tag in planned
        if (n := nodes.get(tag)) is not None
        and n["banner_count"] >= 1
        and n["executed"] == 0
    )
    # A planned node is "executed" iff it ran (has a terminal line) and is not
    # inert (either emitted no banner -> exempt, or its banners sum positive).
    executed_test_nodes = sum(
        1 for tag in planned
        if (n := nodes.get(tag)) is not None
        and n["terminal"] == "pass"
        and (n["banner_count"] == 0 or n["executed"] > 0)
    )

    planned_nodes = [nodes[tag] for tag in planned if tag in nodes]
    total_banner = sum(n["banner_count"] for n in planned_nodes)
    if total_banner == 0:
        executed_tests: int | None = None
        filtered_tests: int | None = None
    else:
        executed_tests = sum(n["executed"] for n in planned_nodes)
        filtered_tests = sum(n["filtered"] for n in planned_nodes)

    return {
        # Coverage alone is schema 5. Only a caller that also obtains the exact
        # cross-repository binding below may mint a schema-6 receipt.
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "executed_tests": executed_tests,
        "filtered_tests": filtered_tests,
        "coverage": {
            "planned_test_nodes": len(planned),
            "executed_test_nodes": executed_test_nodes,
            "zero_executed_nodes": zero_executed,
            "absent_nodes": absent,
            "failed_nodes": failed,
        },
    }


def resolve_reverie_bindings(
    hermit_checkout: str, shas: list[str]
) -> tuple[dict[str, dict], dict[str, str]]:
    """Call the one Rust cross-repo authority once for all candidate SHAs."""
    unique = list(dict.fromkeys(shas))
    if not unique:
        return {}, {}
    command = [CI_HUB_BIN, "reverie-pin-status", "--hermit-repo", hermit_checkout, "--json"]
    for sha in unique:
        command += ["--sha", sha]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {}, {sha: f"authority unavailable: {error}" for sha in unique}
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        return {}, {sha: f"authority returned no report: {detail}" for sha in unique}
    bindings: dict[str, dict] = {}
    problems: dict[str, str] = {}
    for result in report.get("results", []):
        sha = result.get("hermit_sha")
        if sha not in unique:
            continue
        if result.get("status") == "CURRENT" and isinstance(result.get("binding"), dict):
            bindings[sha] = result["binding"]
        else:
            problems[sha] = str(result.get("reason") or "cross-repository authority refused")
    for sha in unique:
        if sha not in bindings and sha not in problems:
            problems[sha] = "cross-repository authority omitted the requested SHA"
    return bindings, problems


def _schema6_fields(coverage_fields: dict, binding: dict) -> dict:
    fields = dict(coverage_fields)
    fields["schema_version"] = SCHEMA_VERSION
    fields["reverie_binding"] = binding
    return fields


# --- race-safe append-only authority minting --------------------------------
#
# There is deliberately no in-place upgrade API. Rewriting an append-only ledger
# can erase a validate row appended between read and write, and accepting an
# arbitrary `--log` lets one run's bytes authorize every row for a SHA. The only
# authority path below selects one original row, follows that row's own
# `log_file`, recomputes every coverage field, and appends one bound clone.

FINALIZER_ID = "ci-hub-schema6-finalizer-v1"


def _semantic(value):
    """Canonical receipt semantics shared by minting and verification.

    Rust's typed ledger reader materializes absent optional fields as JSON null
    and absent vector fields as ``[]``.  Those representation-only differences
    must not change the source-row identity when the Rust authority asks this
    finalizer to re-verify a ledger snapshot.  Coverage lists are emitted by the
    finalizer itself, so only producer-side default vectors are omitted here.
    """
    if isinstance(value, dict):
        return {
            key: _semantic(item)
            for key, item in value.items()
            if item is not None
            and not (key in {"gates", "failed_substeps"} and item == [])
        }
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    return value


def _canonical(value: dict) -> bytes:
    return json.dumps(
        _semantic(value), sort_keys=True, separators=(",", ":")
    ).encode()


def _full_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _clone_upgraded(base_row: dict, fields: dict) -> dict:
    """A schema-6 clone of `base_row` with bound coverage/dependency `fields`
    merged in. Every base field (commit, anchoring, cleanliness, profile,
    selection, result, log_file, ...) is preserved so the appended row still
    satisfies `is_clean_full_coverage` on the consumer side."""
    row = dict(base_row)
    row.update(fields)
    return row


def _is_clean_full_pass(rec: dict) -> bool:
    """Candidate pass row; the canonical Rust verifier remains the arbiter."""
    return (
        rec.get("commit_anchored") is True
        and rec.get("tree_dirty") is False
        and rec.get("selection_mode") == "full"
        and rec.get("profile") == "full"
        and rec.get("result") == "pass"
        and rec.get("failures") == 0
        and rec.get("receipt_finalizer") is None
    )


def _coverage_satisfied(rec: dict) -> bool:
    cov = rec.get("coverage") or {}
    return (
        isinstance(rec.get("executed_tests"), int)
        and not isinstance(rec.get("executed_tests"), bool)
        and rec["executed_tests"] > 0
        and isinstance(rec.get("filtered_tests"), int)
        and not isinstance(rec.get("filtered_tests"), bool)
        and cov.get("planned_test_nodes", 0) > 0
        and cov.get("executed_test_nodes") == cov.get("planned_test_nodes")
        and not cov.get("zero_executed_nodes")
        and not cov.get("absent_nodes")
        and not cov.get("failed_nodes")
    )


def _read_rows(ledger_file) -> list[dict]:
    ledger_file.seek(0)
    rows: list[dict] = []
    for line in ledger_file:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _candidate_rows(recs: list[dict], only_shas: set[str] | None) -> dict[str, list[dict]]:
    candidates: dict[str, list[dict]] = {}
    for rec in recs:
        sha = rec.get("commit")
        if (
            _full_sha(sha)
            and (only_shas is None or sha in only_shas)
            and rec.get("repo") in (None, "hermit")
            and _is_clean_full_pass(rec)
        ):
            candidates.setdefault(sha, []).append(rec)
    return candidates


def _newest_source(rows: list[dict]) -> dict:
    return max(
        rows,
        key=lambda row: (
            row.get("finished_at") or "",
            row.get("started_at") or "",
        ),
    )


def scan_and_finalize(ledger_path: str, hermit_checkout: str,
                      dry_run: bool = False,
                      binding_resolver=resolve_reverie_bindings,
                      only_shas: set[str] | None = None) -> list[dict]:
    """Mint rows from each selected original row's own durable log.

    APPEND-ONLY and finalizer-serialized. A preexisting superficial schema-6 row
    never suppresses recomputation: idempotency requires byte-for-byte equality
    with the clone derived from the exact source row, log digest, manifest and
    fresh dependency binding.

    Returns one result dict per handled sha:
      {sha, satisfied, reason, executed_tests, planned_test_nodes}
    reason in {"minted", "no-log", "no-manifest"}. Only "minted" rows are
    appended; "no-manifest" (planned set empty -> cannot judge from this
    checkout) and "no-log" are reported but NEVER fabricated.
    """
    # Snapshot briefly. Expensive network resolution, git-show and log parsing
    # happen unlocked so unrelated appenders never wait on this finalizer.
    with open(ledger_path, "a+", encoding="utf-8", errors="replace") as ledger_file:
        fcntl.flock(ledger_file.fileno(), fcntl.LOCK_SH)
        recs = _read_rows(ledger_file)
        fcntl.flock(ledger_file.fileno(), fcntl.LOCK_UN)
    candidates = _candidate_rows(recs, only_shas)
    bindings, binding_problems = binding_resolver(hermit_checkout, list(candidates))
    results: list[dict] = []
    proposals: list[tuple[str, str, dict, dict]] = []

    for sha, sha_rows in candidates.items():
        # Never substitute an older run's log for the newest original row.
        rec = _newest_source(sha_rows)
        binding = bindings.get(sha)
        if binding is None:
            results.append({
                "sha": sha,
                "satisfied": False,
                "reason": "reverie-pin",
                "detail": binding_problems.get(
                    sha, "cross-repository authority refused"
                ),
            })
            continue
        log = rec.get("log_file")
        if not isinstance(log, str) or not os.path.isabs(log) or not os.path.isfile(log):
            results.append({"sha": sha, "satisfied": False, "reason": "no-log"})
            continue
        try:
            with open(log, "rb") as log_file:
                log_bytes = log_file.read()
        except OSError as error:
            results.append({
                "sha": sha,
                "satisfied": False,
                "reason": "no-log",
                "detail": str(error),
            })
            continue
        try:
            planned = planned_test_nodes(hermit_checkout, sha)
        except ValueError as error:
            results.append({
                "sha": sha,
                "satisfied": False,
                "reason": "no-manifest",
                "detail": str(error),
            })
            continue
        if not planned:
            results.append({"sha": sha, "satisfied": False, "reason": "no-manifest"})
            continue
        fields = _schema6_fields(
            build_coverage(log_bytes.decode(errors="replace"), planned), binding
        )
        source_digest = hashlib.sha256(_canonical(rec)).hexdigest()
        log_digest = hashlib.sha256(log_bytes).hexdigest()
        fields["source_log_sha256"] = log_digest
        fields["receipt_finalizer"] = {
            "id": FINALIZER_ID,
            "source_row_sha256": source_digest,
        }
        minted = _clone_upgraded(rec, fields)
        if not _coverage_satisfied(minted):
            results.append({
                "sha": sha,
                "satisfied": False,
                "reason": "unsatisfied-coverage",
                "executed_tests": fields["executed_tests"],
                "planned_test_nodes": fields["coverage"]["planned_test_nodes"],
            })
            continue
        result = {
            "sha": sha,
            "satisfied": True,
            "reason": "minted",
            "executed_tests": fields["executed_tests"],
            "planned_test_nodes": fields["coverage"]["planned_test_nodes"],
            "source_log_sha256": log_digest,
            "source_row_sha256": source_digest,
        }
        proposals.append((sha, source_digest, minted, result))

    # Serialize only the revalidation + append window. If another run for the
    # same SHA became the newest source while derivation was in flight, refuse
    # this stale proposal; a subsequent exact invocation derives that new row.
    with open(ledger_path, "a+", encoding="utf-8", errors="replace") as ledger_file:
        fcntl.flock(ledger_file.fileno(), fcntl.LOCK_EX)
        current = _read_rows(ledger_file)
        current_candidates = _candidate_rows(current, only_shas)
        appended = False
        for sha, source_digest, minted, result in proposals:
            rows = current_candidates.get(sha, [])
            current_source = _newest_source(rows) if rows else None
            current_digest = (
                hashlib.sha256(_canonical(current_source)).hexdigest()
                if current_source is not None
                else None
            )
            if current_digest != source_digest:
                stale = dict(result)
                stale.update({"satisfied": False, "reason": "source-changed"})
                results.append(stale)
                continue
            if any(existing == minted for existing in current):
                existing = dict(result)
                existing["reason"] = "already-finalized"
                results.append(existing)
                continue
            results.append(result)
            if not dry_run:
                ledger_file.seek(0, os.SEEK_END)
                ledger_file.write(json.dumps(minted, separators=(",", ":")) + "\n")
                current.append(minted)
                appended = True
        if appended:
            ledger_file.flush()
            os.fsync(ledger_file.fileno())
        fcntl.flock(ledger_file.fileno(), fcntl.LOCK_UN)
    return results


def verify_finalized_row_data(
    row: dict,
    snapshot: list,
    log_bytes: bytes,
    hermit_checkout: str,
    sha: str,
) -> tuple[bool, str]:
    """Re-derive one schema-6 row from its source row, log, and exact tree."""
    if not isinstance(row, dict) or not isinstance(snapshot, list):
        return False, "row must be an object and snapshot must be an array"
    if row.get("commit") != sha or row.get("schema_version") != SCHEMA_VERSION:
        return False, "selected row is not schema-6 bound to the exact SHA"
    finalizer = row.get("receipt_finalizer")
    if not isinstance(finalizer, dict) or set(finalizer) != {"id", "source_row_sha256"}:
        return False, "selected row has no exact finalizer provenance"
    if finalizer.get("id") != FINALIZER_ID or not isinstance(
        finalizer.get("source_row_sha256"), str
    ) or len(finalizer["source_row_sha256"]) != 64 or any(
        ch not in "0123456789abcdef" for ch in finalizer["source_row_sha256"]
    ):
        return False, "selected row finalizer provenance is malformed"
    matching_sources = [
        candidate
        for candidate in snapshot
        if isinstance(candidate, dict)
        and hashlib.sha256(_canonical(candidate)).hexdigest()
        == finalizer["source_row_sha256"]
    ]
    if len(matching_sources) != 1:
        return False, "ledger snapshot does not carry the unique finalizer source row"
    source_row = matching_sources[0]
    if (
        source_row.get("commit") != sha
        or source_row.get("repo") not in (None, "hermit")
        or not _is_clean_full_pass(source_row)
    ):
        return False, "finalizer source row is not an original clean full pass"
    try:
        planned = planned_test_nodes(hermit_checkout, sha)
    except ValueError as error:
        return False, str(error)
    if not planned:
        return False, "exact tree declares no planned test nodes"
    binding = row.get("reverie_binding")
    if not isinstance(binding, dict):
        return False, "selected row has no Reverie binding"
    fields = _schema6_fields(
        build_coverage(log_bytes.decode(errors="replace"), planned), binding
    )
    fields["source_log_sha256"] = hashlib.sha256(log_bytes).hexdigest()
    fields["receipt_finalizer"] = {
        "id": FINALIZER_ID,
        "source_row_sha256": finalizer["source_row_sha256"],
    }
    expected = _clone_upgraded(source_row, fields)

    if _semantic(expected) != _semantic(row):
        return False, "selected row differs from recomputed finalizer output"
    if not _coverage_satisfied(expected):
        return False, "recomputed finalizer coverage is not satisfied"
    return True, "verified"


def verify_finalized_row(
    row_path: str,
    snapshot_path: str,
    log_path: str,
    hermit_checkout: str,
    sha: str,
) -> tuple[bool, str]:
    try:
        with open(row_path, encoding="utf-8") as source:
            row = json.load(source)
        with open(snapshot_path, encoding="utf-8") as source:
            snapshot = json.load(source)
        with open(log_path, "rb") as source:
            log_bytes = source.read()
    except (OSError, json.JSONDecodeError) as error:
        return False, f"cannot read verification input: {error}"
    return verify_finalized_row_data(
        row, snapshot, log_bytes, hermit_checkout, sha
    )


def verified_finalized_rows(
    ledger_path: str,
    hermit_checkout: str,
    shas: set[str],
    log_map_path: str | None = None,
) -> list[dict]:
    """Return only rows whose source, log, manifests, and clone recompute.

    ``log_map_path`` is a non-authorizing locator override keyed by the row's
    claimed log SHA-256.  The bytes at every override are still hashed and the
    complete finalized row is still re-derived.  Immutable receipt consumers
    use it after materializing content-addressed durable logs.
    """
    snapshot: list[dict] = []
    try:
        with open(ledger_path, encoding="utf-8", errors="replace") as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    snapshot.append(value)
    except OSError as error:
        raise ValueError(f"cannot read ledger snapshot: {error}") from error
    log_map: dict[str, str] = {}
    if log_map_path:
        try:
            with open(log_map_path, encoding="utf-8") as source:
                candidate_map = json.load(source)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot read finalized log map: {error}") from error
        if not isinstance(candidate_map, dict) or any(
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
            or not isinstance(path, str)
            or not os.path.isabs(path)
            for digest, path in candidate_map.items()
        ):
            raise ValueError("finalized log map must be absolute paths keyed by SHA-256")
        log_map = candidate_map

    verified: list[dict] = []
    for row in snapshot:
        sha = row.get("commit")
        if sha not in shas or not isinstance(row.get("receipt_finalizer"), dict):
            continue
        digest = row.get("source_log_sha256")
        if not isinstance(digest, str):
            continue
        log_path = log_map.get(digest, row.get("log_file"))
        if not isinstance(log_path, str) or not os.path.isabs(log_path):
            continue
        try:
            with open(log_path, "rb") as source:
                log_bytes = source.read()
        except OSError:
            continue
        ok, _detail = verify_finalized_row_data(
            row, snapshot, log_bytes, hermit_checkout, sha
        )
        if ok:
            verified.append(row)
    return verified


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--log", help="full safe-ci-dag-runner log ([node]-prefixed)")
    ap.add_argument("--sha", help="the exact 40-hex Hermit commit validated")
    ap.add_argument("--repo", help="trusted validation target; authoritative mint supports rrnewton/hermit")
    ap.add_argument("--hermit-checkout", required=True,
                    help="hermit checkout to read ci/dag/*.json at --sha via git show")
    ap.add_argument("--ledger", help="append-only ledger JSONL")
    ap.add_argument("--emit-only", action="store_true",
                    help="print non-authorizing schema-5 coverage fields; do NOT touch a ledger")
    ap.add_argument("--scan", action="store_true",
                    help="APPEND-safe mint: bind every clean/full/pass row in --ledger "
                         "to exact Hermit/Reverie identity, deriving coverage if needed")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --scan: report what would be minted; do NOT write")
    ap.add_argument("--verify-finalized-row",
                    help="verify one selected schema-6 row JSON against its source/log/tree")
    ap.add_argument("--ledger-snapshot",
                    help="JSON array carrying the selected row's original source row")
    ap.add_argument("--verify-finalized-ledger", action="store_true",
                    help="emit every finalized row whose source/log/tree recomputes")
    ap.add_argument("--verify-sha", action="append", default=[],
                    help="exact SHA to include in --verify-finalized-ledger; repeatable")
    ap.add_argument("--finalized-log-map",
                    help="optional SHA-256-to-absolute-path JSON locator map")
    args = ap.parse_args(argv)

    if args.log and args.ledger:
        print(
            "finalize_receipt: --log cannot be combined with --ledger; "
            "an authority mint follows the selected row's own log_file",
            file=sys.stderr,
        )
        return 2

    if args.verify_finalized_row:
        if not (
            args.repo == "rrnewton/hermit"
            and _full_sha(args.sha)
            and args.log
            and args.ledger_snapshot
        ):
            print(
                "finalize_receipt: --verify-finalized-row requires --repo "
                "rrnewton/hermit, full --sha, --log, and --ledger-snapshot",
                file=sys.stderr,
            )
            return 2
        ok, detail = verify_finalized_row(
            args.verify_finalized_row,
            args.ledger_snapshot,
            args.log,
            args.hermit_checkout,
            args.sha,
        )
        print(json.dumps({"verified": ok, "detail": detail}, sort_keys=True))
        return 0 if ok else 4

    if args.verify_finalized_ledger:
        shas = set(args.verify_sha)
        if not (
            args.repo == "rrnewton/hermit"
            and args.ledger
            and os.path.isfile(args.ledger)
            and shas
            and all(_full_sha(sha) for sha in shas)
        ):
            print(
                "finalize_receipt: --verify-finalized-ledger requires --repo "
                "rrnewton/hermit, --ledger, and one or more full --verify-sha",
                file=sys.stderr,
            )
            return 2
        try:
            verified = verified_finalized_rows(
                args.ledger,
                args.hermit_checkout,
                shas,
                args.finalized_log_map,
            )
        except ValueError as error:
            print(f"finalize_receipt: {error}", file=sys.stderr)
            return 2
        print(json.dumps(verified, sort_keys=True, separators=(",", ":")))
        return 0

    if args.scan:
        if not args.ledger:
            print("finalize_receipt: --scan requires --ledger", file=sys.stderr)
            return 2
        if not os.path.isfile(args.ledger):
            print(f"finalize_receipt: ledger not found: {args.ledger}", file=sys.stderr)
            return 2
        if args.repo != "rrnewton/hermit":
            print("finalize_receipt: --scan requires --repo rrnewton/hermit", file=sys.stderr)
            return 2
        results = scan_and_finalize(args.ledger, args.hermit_checkout, dry_run=args.dry_run)
        minted = [r for r in results if r["reason"] == "minted" and r["satisfied"]]
        existing = [r for r in results if r["reason"] == "already-finalized"]
        unsat = [r for r in results if r["reason"] == "unsatisfied-coverage"]
        no_log = [r for r in results if r["reason"] == "no-log"]
        no_man = [r for r in results if r["reason"] == "no-manifest"]
        pin_refused = [r for r in results if r["reason"] == "reverie-pin"]
        verb = "would mint" if args.dry_run else "minted"
        print(f"finalize_receipt: scan {verb} {len(minted)} satisfied schema-{SCHEMA_VERSION} "
              f"row(s); {len(existing)} already-finalized; {len(unsat)} unsatisfied-coverage; "
              f"{len(no_log)} no-log; {len(no_man)} no-manifest; "
              f"{len(pin_refused)} reverie-pin-refused "
              f"(candidates={len(results)})")
        for r in minted:
            print(f"  + {r['sha'][:12]} executed={r['executed_tests']} "
                  f"planned={r['planned_test_nodes']}")
        return 0

    if args.emit_only:
        if not (args.log and _full_sha(args.sha)):
            print("finalize_receipt: --emit-only requires --log and full --sha", file=sys.stderr)
            return 2
        try:
            with open(args.log, errors="replace") as fh:
                log_text = fh.read()
        except OSError as exc:
            print(f"finalize_receipt: cannot read log {args.log!r}: {exc}", file=sys.stderr)
            return 2
        try:
            planned = planned_test_nodes(args.hermit_checkout, args.sha)
        except ValueError as error:
            print(f"finalize_receipt: {error}", file=sys.stderr)
            return 4
        fields = build_coverage(log_text, planned)
        # Coverage-only diagnostics remain schema 5 and are not authorization.
        print(json.dumps({"commit": args.sha, **fields}, indent=2))
        return 0

    if args.repo != "rrnewton/hermit" or not _full_sha(args.sha) or not args.ledger:
        print(
            "finalize_receipt: authoritative mode requires --repo rrnewton/hermit, "
            "full --sha, and --ledger",
            file=sys.stderr,
        )
        return 2
    if not os.path.isfile(args.ledger):
        print(f"finalize_receipt: ledger not found: {args.ledger}", file=sys.stderr)
        return 2
    results = scan_and_finalize(
        args.ledger,
        args.hermit_checkout,
        dry_run=args.dry_run,
        only_shas={args.sha},
    )
    result = next((row for row in results if row.get("sha") == args.sha), None)
    if result is None:
        print(f"finalize_receipt: no eligible original row for sha {args.sha}", file=sys.stderr)
        return 4
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("satisfied") else 4


if __name__ == "__main__":
    sys.exit(main())
