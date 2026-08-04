#!/usr/bin/env python3
"""MAIN-REACHABLE validation-receipt COUNTS + per-node COVERAGE finalizer.

Produces the schema-5 `coverage{}` obligation (plus the aggregate
`executed_tests` / `filtered_tests` diagnostics) for a validate run, reading only
two things that already exist on `main`:

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

SCHEMA_VERSION = 5
MANIFESTS = ("ci/dag/portable.json", "ci/dag/privileged.json")


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
        "schema_version": SCHEMA_VERSION,
        "executed_tests": executed_tests,
        "filtered_tests": filtered_tests,
        "coverage": {
            "planned_test_nodes": len(planned),
            "executed_test_nodes": executed_test_nodes,
            "zero_executed_nodes": zero_executed,
            "absent_nodes": absent,
        },
    }


def upgrade_ledger(ledger_path: str, sha: str, fields: dict) -> int:
    """Upgrade the in-place ledger row(s) for `sha` with the schema-5 fields.

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--log", required=True, help="full safe-ci-dag-runner log ([node]-prefixed)")
    ap.add_argument("--sha", required=True, help="the exact 40-hex Hermit commit validated")
    ap.add_argument("--hermit-checkout", required=True,
                    help="hermit checkout to read ci/dag/*.json at --sha via git show")
    ap.add_argument("--ledger", help="ledger JSONL to upgrade the row for --sha in place")
    ap.add_argument("--emit-only", action="store_true",
                    help="print the schema-5 fields to stdout; do NOT touch a ledger")
    args = ap.parse_args(argv)

    try:
        with open(args.log, errors="replace") as fh:
            log_text = fh.read()
    except OSError as exc:
        print(f"finalize_receipt: cannot read log {args.log!r}: {exc}", file=sys.stderr)
        return 2

    planned = planned_test_nodes(args.hermit_checkout, args.sha)
    fields = build_coverage(log_text, planned)

    if args.emit_only or not args.ledger:
        print(json.dumps({"commit": args.sha, **fields}, indent=2))
        return 0

    if not os.path.isfile(args.ledger):
        print(f"finalize_receipt: ledger not found: {args.ledger}", file=sys.stderr)
        return 2
    upgraded = upgrade_ledger(args.ledger, args.sha, fields)
    if upgraded == 0:
        print(f"finalize_receipt: no ledger row for sha {args.sha} in {args.ledger}",
              file=sys.stderr)
        return 2
    print(f"finalize_receipt: upgraded {upgraded} row(s) for {args.sha} to schema {SCHEMA_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
