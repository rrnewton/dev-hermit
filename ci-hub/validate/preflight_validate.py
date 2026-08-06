#!/usr/bin/env python3
"""Fail-closed admission for a receipt-producing Hermit validation.

A qualifying validation head must satisfy two independent ancestry authorities:

1. it contains every fixed producer/merge-gate floor in
   ``rebase-base-floors.json``; and
2. it contains the freshly fetched tip of ``origin/main``.

The first predicate proves that the checked-out producer can emit a qualifying
receipt.  The second proves that the receipt would describe a state that can
actually land without another rebase.  A green for a head based on yesterday's
main is not portable landing evidence, even when it clears every fixed floor.

The moving-base predicate is exactly::

    git merge-base --is-ancestor <fresh origin/main tip> <head>

The script refreshes ``origin/main`` itself immediately before evaluating that
predicate.  An unresolvable fetch/head/base is an ERROR and fails closed at all
callers; it is never treated as permission to validate.

Exit codes:
  0  OK       -- both ancestry authorities pass
  2  REFUSED  -- head is stale against a fixed floor or fresh origin/main
  3  ERROR    -- the authority could not be resolved

Historical differential debugging after a rebased head fails is not a
qualifying validation.  Run that focused comparison outside ``validate-run``;
do not use an old head to mint or reuse a landing receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

import preflight_anchor


DEFAULT_REPO = preflight_anchor.DEFAULT_REPO
DEFAULT_CHECKOUT = preflight_anchor.DEFAULT_CHECKOUT
DEFAULT_ANCHORS = preflight_anchor.DEFAULT_ANCHORS
DEFAULT_BASE_BRANCH = "main"
NETWORK_TIMEOUT = preflight_anchor.NETWORK_TIMEOUT
LOCAL_TIMEOUT = preflight_anchor.LOCAL_TIMEOUT

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_ERROR = 3
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class AdmissionError(Exception):
    """A required identity or ancestry authority could not be resolved."""


def _run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )


def _short(sha: str) -> str:
    return sha[:12] if sha else "?"


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout).strip() or f"exit {result.returncode}"


def _validate_sha(value: str, *, field: str) -> str:
    value = value.strip()
    if not SHA_RE.fullmatch(value):
        raise AdmissionError(f"{field} is not an exact lowercase 40-hex commit: {value!r}")
    return value


def resolve_current_base(checkout: str, branch: str) -> str:
    """Refresh and return the exact current ``origin/<branch>`` commit."""
    if not BRANCH_RE.fullmatch(branch) or ".." in branch or "@{" in branch:
        raise AdmissionError(f"unsafe base branch name {branch!r}")

    remote_ref = f"refs/remotes/origin/{branch}"
    refspec = f"refs/heads/{branch}:{remote_ref}"
    fetched = _run(
        [
            "with-proxy",
            "git",
            "-C",
            checkout,
            "fetch",
            "--quiet",
            "origin",
            refspec,
        ],
        timeout=NETWORK_TIMEOUT,
    )
    if fetched.returncode != 0:
        raise AdmissionError(
            f"cannot refresh origin/{branch} in {checkout}: {_detail(fetched)}"
        )

    resolved = _run(
        ["git", "-C", checkout, "rev-parse", "--verify", f"{remote_ref}^{{commit}}"],
        timeout=LOCAL_TIMEOUT,
    )
    if resolved.returncode != 0:
        raise AdmissionError(
            f"cannot resolve refreshed origin/{branch} in {checkout}: {_detail(resolved)}"
        )
    return _validate_sha(resolved.stdout, field=f"origin/{branch} tip")


def admission(
    head: str,
    *,
    checkout: str,
    repo: str,
    anchors_path: str,
    base_branch: str,
) -> dict:
    """Return one record carrying both fixed-floor and moving-base verdicts."""
    head = _validate_sha(head, field="validation head")
    try:
        floors = preflight_anchor.preflight(
            head, checkout=checkout, repo=repo, anchors_path=anchors_path
        )
    except preflight_anchor.PreflightError as error:
        raise AdmissionError(str(error)) from error

    base_sha = resolve_current_base(checkout, base_branch)
    try:
        contains_base, checked_via = preflight_anchor.head_contains(
            checkout, repo, base_sha, head
        )
    except preflight_anchor.PreflightError as error:
        raise AdmissionError(str(error)) from error

    moving_base = {
        "remote": "origin",
        "branch": base_branch,
        "sha": base_sha,
        "contained": contains_base,
        "checked_via": checked_via,
    }
    return {
        "schema_version": 1,
        "repo": repo,
        "head": head,
        "fixed_floors": floors,
        "moving_base": moving_base,
        "ok": floors["ok"] and contains_base,
    }


def render(result: dict) -> str:
    lines: list[str] = []
    floors = result["fixed_floors"]
    if not floors["ok"]:
        lines.append(preflight_anchor.render(floors))

    base = result["moving_base"]
    if not base["contained"]:
        lines.append(
            f"REFUSE: head {_short(result['head'])} does not contain freshly fetched "
            f"origin/{base['branch']} {_short(base['sha'])}; validating this SHA "
            "would certify a state that cannot land without another rebase. "
            f"Rebase onto origin/{base['branch']} at {base['sha']} before validating. "
            "Historical differential debugging is non-qualifying and must not mint "
            "landing evidence."
        )

    if lines:
        return "\n".join(lines)
    return (
        f"OK: head {_short(result['head'])} contains freshly fetched "
        f"origin/{base['branch']} {_short(base['sha'])} and all "
        f"{floors['n_anchors']} fixed validation floors."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--head", help="exact lowercase 40-hex validation head")
    target.add_argument("--pr", help="resolve this PR's exact head through GitHub")
    parser.add_argument("--repo-checkout", default=DEFAULT_CHECKOUT)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--anchors", default=DEFAULT_ANCHORS)
    parser.add_argument("--base-branch", default=DEFAULT_BASE_BRANCH)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.pr:
            head, _branch = preflight_anchor.resolve_pr_head(args.repo, args.pr)
        else:
            head = args.head
        result = admission(
            head,
            checkout=os.path.abspath(args.repo_checkout),
            repo=args.repo,
            anchors_path=args.anchors,
            base_branch=args.base_branch,
        )
    except (AdmissionError, preflight_anchor.PreflightError) as error:
        if args.json:
            print(json.dumps({"error": str(error)}, indent=2))
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render(result))
    return EXIT_OK if result["ok"] else EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
