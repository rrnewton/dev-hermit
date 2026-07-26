#!/usr/bin/env python3
"""Report operational health for open Hermit and Reverie pull requests."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Sequence

DEFAULT_REPOS = ("rrnewton/hermit", "rrnewton/reverie")
DEFAULT_WARN_THRESHOLD = 10
# Landing is never gated on human review. Every open PR is free to land once CI
# is green; the only distinction this report draws is whether the post-facto
# (post-landing) review label has already been applied.
POST_FACTO_REVIEW_LABEL = "post-facto-review"

RED_CONCLUSIONS = frozenset(
    (
        "FAILURE",
        "TIMED_OUT",
        "CANCELLED",
        "ERROR",
        "ACTION_REQUIRED",
        "STARTUP_FAILURE",
        "STALE",
    )
)
PENDING_STATES = frozenset(
    ("PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED")
)


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    title: str
    url: str
    is_draft: bool
    labels: frozenset[str]
    ci_status: str

    @property
    def has_post_facto_review(self) -> bool:
        return POST_FACTO_REVIEW_LABEL in self.labels


def classify_ci_rollup(checks: object) -> str:
    """Classify a GitHub statusCheckRollup as green, red, pending, or none."""
    if not isinstance(checks, list) or not checks:
        return "none"

    saw_check = False
    saw_pending = False
    for check in checks:
        if not isinstance(check, dict):
            continue
        saw_check = True
        conclusion = str(check.get("conclusion") or check.get("state") or "").upper()
        status = str(check.get("status") or "").upper()

        if conclusion in RED_CONCLUSIONS:
            return "red"
        if (
            conclusion in PENDING_STATES
            or not conclusion
            or (status and status != "COMPLETED")
        ):
            saw_pending = True

    if not saw_check:
        return "none"
    return "pending" if saw_pending else "green"


def parse_pull_request(repo: str, raw: object) -> PullRequest:
    if not isinstance(raw, dict):
        raise ValueError(f"{repo}: expected PR object, got {type(raw).__name__}")

    labels_raw = raw.get("labels")
    labels = frozenset(
        str(label.get("name"))
        for label in labels_raw
        if isinstance(label, dict) and label.get("name")
    ) if isinstance(labels_raw, list) else frozenset()

    try:
        number = int(raw["number"])
        title = str(raw["title"])
        url = str(raw["url"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{repo}: malformed PR payload: {raw!r}") from error

    return PullRequest(
        repo=repo,
        number=number,
        title=" ".join(title.split()),
        url=url,
        is_draft=raw.get("isDraft") is True,
        labels=labels,
        ci_status=classify_ci_rollup(raw.get("statusCheckRollup")),
    )


def fetch_open_prs(repo: str) -> list[PullRequest]:
    command = [
        "with-proxy",
        "gh",
        "pr",
        "list",
        "-R",
        repo,
        "--state",
        "open",
        "--limit",
        "200",
        "--json",
        "number,title,url,isDraft,labels,statusCheckRollup",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise RuntimeError(
            "with-proxy was not found; GitHub queries must use the proxy wrapper"
        ) from error

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"{repo}: gh pr list failed: {detail}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{repo}: gh pr list returned invalid JSON") from error
    if not isinstance(payload, list):
        raise RuntimeError(f"{repo}: gh pr list returned a non-list payload")

    return [parse_pull_request(repo, raw) for raw in payload]


def _format_pr(pr: PullRequest) -> str:
    draft = "yes" if pr.is_draft else "no"
    return (
        f"  {pr.repo}#{pr.number:<4} ci={pr.ci_status:<7} draft={draft:<3} "
        f"{pr.title}\n"
        f"    {pr.url}"
    )


def render_report(prs: Sequence[PullRequest], warn_threshold: int) -> str:
    # Every open PR is free to land once CI is green. The ONLY distinction is
    # whether the post-facto (post-landing) review label is already applied;
    # nothing here is blocked on human review.
    labeled = sorted(
        (pr for pr in prs if pr.has_post_facto_review),
        key=lambda pr: (pr.repo, -pr.number),
    )
    unlabeled = sorted(
        (pr for pr in prs if not pr.has_post_facto_review),
        key=lambda pr: (pr.repo, -pr.number),
    )
    ci_failing = sum(pr.ci_status == "red" for pr in prs)

    lines = [
        "Open PR health: rrnewton/hermit + rrnewton/reverie",
        "",
        "  All open PRs are FREE TO LAND once CI is green. 'post-facto-review' is",
        "  a POST-LANDING tag, never a pre-landing gate. The two groups below only",
        "  differ by whether that label has been applied yet.",
        "",
        f"Free to land (post-facto-review label applied) ({len(labeled)})",
    ]
    lines.extend(_format_pr(pr) for pr in labeled)
    if not labeled:
        lines.append("  (none)")

    lines.extend(
        ("", f"Free to land (no post-facto-review label yet) ({len(unlabeled)})")
    )
    for pr in unlabeled:
        lines.append(_format_pr(pr))
        lines.append(
            "    ACTION: add the post-facto-review label, then merge when CI is green"
        )
    if not unlabeled:
        lines.append("  (none)")

    lines.extend(
        (
            "",
            "Summary",
            f"  total open (all free to land):  {len(prs)}",
            f"  with post-facto-review label:   {len(labeled)}",
            f"  need post-facto-review label:   {len(unlabeled)}",
            f"  CI-failing:                     {ci_failing}",
        )
    )

    if len(prs) > warn_threshold:
        lines.extend(
            (
                "",
                "WARNING: "
                f"{len(prs)} free-to-land PRs exceeds the "
                f"{warn_threshold} PR threshold; prioritize CI repair and landing.",
            )
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        dest="repos",
        action="append",
        help="GitHub OWNER/REPO to query; repeat to override the two defaults",
    )
    parser.add_argument(
        "--warn-threshold",
        type=int,
        default=DEFAULT_WARN_THRESHOLD,
        help=f"warn above this free-to-land count (default: {DEFAULT_WARN_THRESHOLD})",
    )
    args = parser.parse_args(argv)
    if args.warn_threshold < 0:
        parser.error("--warn-threshold must be non-negative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repos = tuple(args.repos) if args.repos else DEFAULT_REPOS

    try:
        prs = [pr for repo in repos for pr in fetch_open_prs(repo)]
    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    print(render_report(prs, args.warn_threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
