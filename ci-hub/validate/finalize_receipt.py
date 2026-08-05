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
zero_executed_nodes == [] && absent_nodes == []`. This finalizer does NOT decide
validated/not; it only WRITES the qualified row (carry the condition with the
value) so the Rust consumer can re-derive the verdict from receipt fields alone.

Usage:
  finalize_receipt.py --log LOG --sha SHA --hermit-checkout DIR --emit-only
  finalize_receipt.py --log LOG --sha SHA --hermit-checkout DIR --ledger LEDGER
"""
from __future__ import annotations

import argparse
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
    absent at that commit (or a checkout without git) is skipped, never fatal, so
    a lane that ships only one manifest still yields its planned set. A TEST node
    is a manifest step with `group == "test"`; its runner tag is `test.<job>`.
    """
    planned: set[str] = set()
    for manifest in MANIFESTS:
        try:
            proc = subprocess.run(
                ["git", "-C", hermit_checkout, "show", f"{sha}:{manifest}"],
                capture_output=True,
                text=True,
            )
        except OSError:
            continue
        if proc.returncode != 0:
            continue
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
        for step in data.get("steps", []):
            if step.get("group") == "test":
                planned.add(f"test.{step.get('job')}")
    return planned


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
        and n["terminal"] is not None
        and (n["banner_count"] == 0 or n["executed"] > 0)
    )

    total_banner = sum(n["banner_count"] for n in nodes.values())
    if total_banner == 0:
        executed_tests: int | None = None
        filtered_tests: int | None = None
    else:
        executed_tests = sum(n["executed"] for n in nodes.values())
        filtered_tests = sum(n["filtered"] for n in nodes.values())

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


def upgrade_ledger(ledger_path: str, sha: str, fields: dict) -> int:
    """Upgrade in-place ledger row(s) for `sha` with schema-6 bound fields.

    Preserves every other field on the row, adds `executed_tests`,
    `filtered_tests`, and `coverage`, and sets `schema_version = 5`. Returns the
    number of rows upgraded; 0 means no row matched `sha` (caller errors out --
    the finalizer NEVER fabricates a row).
    """
    with open(ledger_path, errors="replace") as fh:
        lines = fh.readlines()

    upgraded = 0
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if rec.get("commit") == sha:
            rec.update(fields)
            out.append(json.dumps(rec) + "\n")
            upgraded += 1
        else:
            out.append(line)

    if upgraded:
        with open(ledger_path, "w") as fh:
            fh.writelines(out)
    return upgraded


def ledger_has_commit(ledger_path: str, sha: str) -> bool:
    with open(ledger_path, errors="replace") as ledger_file:
        for line in ledger_file:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("commit") == sha:
                return True
    return False


# --- race-safe scan/append minting (the auto-wired path) --------------------
#
# `upgrade_ledger` (above) does a full-file read-then-`"w"`-rewrite: correct for
# a one-shot single-SHA CLI against a private copy, but it RACES a concurrent
# `validate.sh` (which appends with O_APPEND) -- an append landing between the
# read and the rewrite is silently lost. The consumer-wired minting path below
# NEVER rewrites: it derives coverage where needed, obtains a fresh exact pin
# binding from the Rust authority, and APPENDS a schema-6 clone. `assess()`
# validates a commit if ANY row qualifies, so an appended satisfied row mints the
# count-backed green without touching -- or losing -- any concurrent write.


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
    )


def _coverage_satisfied(rec: dict) -> bool:
    cov = rec.get("coverage") or {}
    return (
        isinstance(rec.get("executed_tests"), int)
        and not isinstance(rec.get("executed_tests"), bool)
        and rec["executed_tests"] > 0
        and cov.get("planned_test_nodes", 0) > 0
        and not cov.get("zero_executed_nodes")
        and not cov.get("absent_nodes")
    )


def _has_satisfied_schema6(rec: dict, binding: dict) -> bool:
    """Idempotency guard bound to the freshly verified dependency identity."""
    if (rec.get("schema_version") or 0) < SCHEMA_VERSION:
        return False
    return _coverage_satisfied(rec) and rec.get("reverie_binding") == binding


def scan_and_finalize(ledger_path: str, hermit_checkout: str,
                      dry_run: bool = False,
                      binding_resolver=resolve_reverie_bindings) -> list[dict]:
    """Mint schema-6 rows for every clean/full/pass row
    whose recorded `log_file` still exists and whose planned set is derivable at
    its sha. APPEND-ONLY (race-safe). Idempotent: a sha already carrying a
    satisfied schema-6 row with the same fresh binding is skipped.

    Returns one result dict per handled sha:
      {sha, satisfied, reason, executed_tests, planned_test_nodes}
    reason in {"minted", "no-log", "no-manifest"}. Only "minted" rows are
    appended; "no-manifest" (planned set empty -> cannot judge from this
    checkout) and "no-log" are reported but NEVER fabricated.
    """
    with open(ledger_path, errors="replace") as fh:
        recs = [json.loads(l) for l in fh if l.strip()]

    candidates: dict[str, list[dict]] = {}
    for rec in recs:
        sha = rec.get("commit")
        if sha and _is_clean_full_pass(rec):
            candidates.setdefault(sha, []).append(rec)
    bindings, binding_problems = binding_resolver(hermit_checkout, list(candidates))
    results: list[dict] = []
    to_append: list[dict] = []

    for sha, sha_rows in candidates.items():
        binding = bindings.get(sha)
        if binding is None:
            results.append({
                "sha": sha,
                "satisfied": False,
                "reason": "reverie-pin",
                "detail": binding_problems.get(sha, "cross-repository authority refused"),
            })
            continue
        if any(_has_satisfied_schema6(row, binding) for row in sha_rows):
            continue

        # Prefer an already counted/covered row; otherwise use the newest
        # candidate whose durable log can derive coverage.
        rec = max(
            sha_rows,
            key=lambda row: (_coverage_satisfied(row), row.get("finished_at") or ""),
        )
        if _coverage_satisfied(rec):
            fields = _schema6_fields(
                {
                    "schema_version": COVERAGE_SCHEMA_VERSION,
                    "executed_tests": rec.get("executed_tests"),
                    "filtered_tests": rec.get("filtered_tests"),
                    "coverage": rec.get("coverage"),
                },
                binding,
            )
        else:
            log = rec.get("log_file")
            if not log or not os.path.isfile(log):
                results.append({"sha": sha, "satisfied": False, "reason": "no-log"})
                continue

            with open(log, errors="replace") as lf:
                log_text = lf.read()
            planned = planned_test_nodes(hermit_checkout, sha)
            if not planned:
                results.append({"sha": sha, "satisfied": False, "reason": "no-manifest"})
                continue
            fields = _schema6_fields(build_coverage(log_text, planned), binding)
        cov = fields["coverage"]
        satisfied = (cov["planned_test_nodes"] > 0
                     and not cov["zero_executed_nodes"]
                     and not cov["absent_nodes"])
        results.append({
            "sha": sha,
            "satisfied": satisfied,
            "reason": "minted",
            "executed_tests": fields["executed_tests"],
            "planned_test_nodes": cov["planned_test_nodes"],
        })
        to_append.append(_clone_upgraded(rec, fields))

    if to_append and not dry_run:
        with open(ledger_path, "a") as fh:
            for row in to_append:
                fh.write(json.dumps(row) + "\n")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--log", help="full safe-ci-dag-runner log ([node]-prefixed)")
    ap.add_argument("--sha", help="the exact 40-hex Hermit commit validated")
    ap.add_argument("--hermit-checkout", required=True,
                    help="hermit checkout to read ci/dag/*.json at --sha via git show")
    ap.add_argument("--ledger", help="ledger JSONL to upgrade the row for --sha in place")
    ap.add_argument("--emit-only", action="store_true",
                    help="print non-authorizing schema-5 coverage fields; do NOT touch a ledger")
    ap.add_argument("--scan", action="store_true",
                    help="APPEND-safe mint: bind every clean/full/pass row in --ledger "
                         "to exact Hermit/Reverie identity, deriving coverage if needed")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --scan: report what would be minted; do NOT write")
    args = ap.parse_args(argv)

    if args.scan:
        if not args.ledger:
            print("finalize_receipt: --scan requires --ledger", file=sys.stderr)
            return 2
        if not os.path.isfile(args.ledger):
            print(f"finalize_receipt: ledger not found: {args.ledger}", file=sys.stderr)
            return 2
        results = scan_and_finalize(args.ledger, args.hermit_checkout, dry_run=args.dry_run)
        minted = [r for r in results if r["reason"] == "minted" and r["satisfied"]]
        unsat = [r for r in results if r["reason"] == "minted" and not r["satisfied"]]
        no_log = [r for r in results if r["reason"] == "no-log"]
        no_man = [r for r in results if r["reason"] == "no-manifest"]
        pin_refused = [r for r in results if r["reason"] == "reverie-pin"]
        verb = "would mint" if args.dry_run else "minted"
        print(f"finalize_receipt: scan {verb} {len(minted)} satisfied schema-{SCHEMA_VERSION} "
              f"row(s); {len(unsat)} unsatisfied-coverage; "
              f"{len(no_log)} no-log; {len(no_man)} no-manifest; "
              f"{len(pin_refused)} reverie-pin-refused "
              f"(candidates={len(results)})")
        for r in minted:
            print(f"  + {r['sha'][:12]} executed={r['executed_tests']} "
                  f"planned={r['planned_test_nodes']}")
        return 0

    if not (args.log and args.sha):
        print("finalize_receipt: --log and --sha are required (or use --scan)",
              file=sys.stderr)
        return 2

    try:
        with open(args.log, errors="replace") as fh:
            log_text = fh.read()
    except OSError as exc:
        print(f"finalize_receipt: cannot read log {args.log!r}: {exc}", file=sys.stderr)
        return 2

    planned = planned_test_nodes(args.hermit_checkout, args.sha)
    fields = build_coverage(log_text, planned)

    if args.emit_only or not args.ledger:
        # Coverage-only diagnostics remain schema 5 and are not authorization.
        print(json.dumps({"commit": args.sha, **fields}, indent=2))
        return 0

    if not os.path.isfile(args.ledger):
        print(f"finalize_receipt: ledger not found: {args.ledger}", file=sys.stderr)
        return 2
    if not ledger_has_commit(args.ledger, args.sha):
        print(f"finalize_receipt: no ledger row for sha {args.sha} in {args.ledger}",
              file=sys.stderr)
        return 2

    bindings, problems = resolve_reverie_bindings(args.hermit_checkout, [args.sha])
    binding = bindings.get(args.sha)
    if binding is None:
        print(
            f"finalize_receipt: Reverie binding refused for {args.sha}: "
            f"{problems.get(args.sha, 'unknown refusal')}",
            file=sys.stderr,
        )
        return 2
    fields = _schema6_fields(fields, binding)

    upgraded = upgrade_ledger(args.ledger, args.sha, fields)
    if upgraded == 0:
        print(f"finalize_receipt: no ledger row for sha {args.sha} in {args.ledger}",
              file=sys.stderr)
        return 2
    print(f"finalize_receipt: upgraded {upgraded} row(s) for {args.sha} to schema {SCHEMA_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
