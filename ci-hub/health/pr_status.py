#!/usr/bin/env python3
"""Summarize open-PR CI health, robustly, on the hosts we actually run ops from.

Two engines:

* ``gh`` (DEFAULT) — a single proxied ``gh pr list --json`` call per repo.
  ``gh`` returns ``mergeable`` and ``statusCheckRollup`` inline, so the whole
  open-PR picture comes back in ONE API round-trip with NO per-PR local
  ``git fetch``. This is the cheap, fast, robust path and it is what actually
  works on 3pai hosts.

* ``planner`` (opt-in, ``--engine planner``) — adapts the pinned
  agent-utils/pr-landing-planner and runs REAL ``git merge-tree`` conflict
  detection over the open set (``--conflict-detector merge-tree``). The thing
  that once made this look expensive was never the analysis: one merge-tree
  base-conflict probe is ~36.5ms local (measured 2026-08-04, 40 probes over
  rrnewton/hermit's 107 open PRs; whole open set vs main ~4s). The real cost
  was a per-PR ``git fetch`` fan-out (21.5s over 25 heads), which agent-utils
  PR #14 collapses to ONE batched fetch (0.85s, ~25x). ``gh`` stays the DEFAULT
  for the every-tick path because it needs no local ``git fetch`` at all and is
  the only path that works under 3pai BpfJailer (where the planner's network
  fetch is intermittently blocked with FILE_OPEN denials or hangs). Reach for
  ``--engine planner`` on a planning run, where real conflict data is worth the
  one fetch.

Why the ``gh`` default and the loudness discipline below matter: this is the
sanctioned ops tool, dispatched every PR-status tick. Its previous per-PR git
fan-out could hang or be silently blocked on 3pai, and *no output plus a zero
exit is indistinguishable from "there are no PRs"* — a monitoring tool that
reports emptiness when it is actually blind will eventually convince someone the
backlog is clear. So: a failed/blocked query is ALWAYS reported as UNAVAILABLE
with an actionable reason and a NON-ZERO exit, never as an empty-but-green
report. The report heading always prints, so stdout is never empty.

Boxing discipline (unchanged intent): per-repo subprocess timeout, an overall
command deadline, bounded retries with backoff on transient network errors, and
partial results plus an explicit statement of what could not be fetched, so the
tool ALWAYS terminates with a report instead of hanging.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci-hub"))

from check_outcome import CheckOutcome, classify_check, select_latest_checks
from validate.flake_class import executed_plausibility

AGENT_TOOL = Path(os.environ.get("CI_HUB_AGENT_TOOL", ROOT / "ci-hub/bin/agent-tool"))
CI_HUB_BIN = Path(os.environ.get("CI_HUB_BIN", ROOT / "ci-hub/ci-hub"))
DEFAULT_REPOS = ("rrnewton/hermit", "rrnewton/reverie")
DEFAULT_WARN_THRESHOLD = 10
MAX_FETCH_ATTEMPTS = 3

# Timeout basis (derived, not a plausible constant):
#   planner engine — the binding cost is the FETCH, not the conflict analysis.
#   With the pre-#14 pinned agent-utils it fetches per PR: measured 2026-08-03
#   on a devserver, the reverie planner completed in 35.17s for 26 open PRs =>
#   ~1.35s/PR of sequential proxied `git fetch` (hermit's 128 open PRs =>
#   ~173s happy-path; combined ~208s), which is what these budgets are sized
#   for. agent-utils PR #14 collapses that fan-out to ONE batched fetch (0.85s
#   over 25 heads, ~25x), after which the whole planning run is dominated by
#   fetch-once + cheap local merge-tree probes (~36.5ms each; open set vs main
#   ~4s). The `merge-tree` conflict flip in planner_command adds only those
#   cheap local probes, not more fetching, so these budgets stay ample.
#   gh engine — a single `gh pr list` API call per repo returns in a few
#   seconds regardless of PR count, so the same budgets are comfortably ample
#   headroom for network variance and leave a stalled call bounded.
DEFAULT_PER_REPO_TIMEOUT = float(os.environ.get("CI_HUB_PR_STATUS_TIMEOUT", "300"))
DEFAULT_OVERALL_DEADLINE = float(os.environ.get("CI_HUB_PR_STATUS_DEADLINE", "480"))
# Seconds to wait for the killed planner child to die before moving on.
_TERMINATE_GRACE = 10.0

# Default network wrapper. Bare `gh`/`git` reach GitHub directly, which fails on
# proxied hosts ("network is unreachable"); `with-proxy` is idempotent (wrapping
# an already-proxied command is a no-op), so we prefix it by default when it is
# on PATH. Override with --net-wrapper "" to disable, or --net-wrapper CMD.
DEFAULT_NET_WRAPPER = "with-proxy"

# Fields pulled in the single `gh pr list` call. mergeable + statusCheckRollup
# are what let us classify every open PR without any per-PR local git fetch.
GH_FIELDS = (
    "number",
    "title",
    "isDraft",
    "mergeable",
    "mergeStateStatus",
    "reviewDecision",
    "baseRefName",
    "headRefName",
    "updatedAt",
    "labels",
    "statusCheckRollup",
    "headRefOid",
)
MECHANISM_GH_FIELDS = ("number", "title", "isDraft", "labels")

# Transient network/GH errors worth a bounded retry.
_RETRYABLE_MARKERS = (
    "stream error",
    "CANCEL",
    "504",
    "502",
    "timeout",
    "timed out",
    "connection reset",
    "temporarily unavailable",
    "changed during collection",
)

# Keep this exact language in sync with Hermit's
# scripts/core-review-protocol-lint.sh. Review activity is durable and numbered;
# approval is an unsuffixed assertion about the current head.
_REVIEW_ROUND_LABEL = re.compile(
    r"^adversarial-review-(?P<reviewer>codex|claude)(?P<round>[1-4])$"
)
_POST_FACTO_LABEL = "post-facto-human-review"
_PASSED_REVIEW_LABELS = {
    "codex": "passed-review-codex",
    "claude": "passed-review-claude",
}
_MECHANISM_TAG_PREFIX = "mechanism:"
HEALTH_VERDICT_RULE = (
    "ready non-draft PR GitHub check rollups only: UNHEALTHY iff any available "
    "repo has real_reds>0 or outage_suspected=yes"
)
HEALTH_VERDICT_EXCLUDES = (
    "local validation receipts",
    "draft PRs",
    "main-branch health",
    "queue depth",
)


class RepoUnavailable(RuntimeError):
    """The status query could not complete within its time budget or was blocked."""


@dataclass(frozen=True)
class ReviewProtocolStatus:
    """Review activity and current-head approval for one post-facto PR."""

    pr: int | None
    title: str
    draft: bool
    codex_rounds: tuple[int, ...]
    claude_rounds: tuple[int, ...]
    review_rounds: str
    current_approvals: str
    complete: bool
    missing: tuple[str, ...]
    invalid_labels: tuple[str, ...]


@dataclass(frozen=True)
class MechanismPr:
    """One open PR carrying a mechanism tag."""

    pr: int | None
    title: str
    draft: bool


@dataclass(frozen=True)
class MechanismOverlap:
    """Two or more open PRs that require joint coordinator inspection."""

    mechanism: str
    prs: tuple[MechanismPr, ...]


@dataclass(frozen=True)
class RepoStatus:
    repo: str
    open: int
    green: int
    red: int
    pending: int
    real_reds: int
    outage_suspected: bool
    prs: tuple[dict[str, object], ...]
    review_protocol: tuple[ReviewProtocolStatus, ...] = ()
    mechanism_overlaps: tuple[MechanismOverlap, ...] = ()
    undetermined_reds: int = 0
    # Split of real_reds by what actually failed at head. A product-red has at
    # least one failing check that is a genuine product test/build; a gate-red
    # fails ONLY landing-gate/review meta-checks (merge-gate*, review-protocol),
    # i.e. the PR merely lacks a valid receipt/review — main is not broken.
    # product_reds + gate_reds == real_reds on the gh engine (0 on the planner,
    # which does not enumerate per-check names).
    product_reds: int = 0
    gate_reds: int = 0
    # Open-PR heads whose EXACT SHA carries a full-green receipt in the LOCAL
    # validate ledger — the second authority the GitHub ``green`` count is blind
    # to. A ledger-green head that GitHub shows red/pending is counted here and
    # kept out of the red buckets; this is what reconciles a GitHub ``green=0``
    # with banked local greens (they measure different authorities).
    green_local: int = 0
    # Open-PR heads whose EXACT SHA carries only a NON-durable failure in the LOCAL
    # validate ledger — a red the ledger-side executed_tests gate already demotes
    # (validate_status::failure_disposition / flake_class.executed_plausibility).
    # ``ledger_no_result`` = the local run executed <= 1 test (a NO-RESULT wearing a
    # red badge); ``ledger_needs_rerun`` = a partial suite below the plausible-full
    # floor. Both are demoted OUT of real_reds: the GitHub product red is not
    # corroborated by any complete local run, so it must not mark the fleet
    # unhealthy or block landing as a genuine break. This is the SYMMETRIC peer of
    # green_local — green_local rescues a GitHub red the ledger proved GREEN;
    # these demote a GitHub product red the ledger proved was a no-result/partial.
    # A ledger row that ran the full suite and genuinely failed (tier "ok") is NOT
    # counted here — it stays a real product red.
    ledger_no_result: int = 0
    ledger_needs_rerun: int = 0
    available: bool = True
    reason: str = ""

    @property
    def unhealthy(self) -> bool:
        # An unavailable repo is UNKNOWN, not unhealthy: we must not synthesize a
        # red from a query we never completed.
        return self.available and (self.real_reds > 0 or self.outage_suspected)


def _unavailable(
    repo: str,
    reason: str,
    mechanism_overlaps: tuple[MechanismOverlap, ...] = (),
) -> RepoStatus:
    return RepoStatus(
        repo=repo,
        open=0,
        green=0,
        red=0,
        pending=0,
        real_reds=0,
        outage_suspected=False,
        prs=(),
        mechanism_overlaps=mechanism_overlaps,
        available=False,
        reason=reason,
    )


def _checkout_for(repo: str) -> Path:
    name = repo.rsplit("/", 1)[-1]
    if name == "dev-hermit":
        return ROOT
    checkout = ROOT / name
    if not checkout.is_dir():
        raise RuntimeError(f"{repo}: local checkout is missing: {checkout}")
    return checkout


def resolve_net_wrapper(spec: str | None) -> list[str]:
    """Turn a --net-wrapper spec into a command prefix.

    ``None`` selects the default (``with-proxy`` if on PATH, else nothing, with a
    warning). An explicit empty string disables wrapping. A non-empty string is
    split shell-style. A named wrapper that is not on PATH is an error rather
    than a silent bare call that would fail with "network is unreachable".
    """
    if spec is None:
        if shutil.which(DEFAULT_NET_WRAPPER):
            return [DEFAULT_NET_WRAPPER]
        print(
            f"pr_status: note: default net wrapper {DEFAULT_NET_WRAPPER!r} not on "
            "PATH; issuing bare gh/git (may fail on proxied hosts). Pass "
            "--net-wrapper to override.",
            file=sys.stderr,
        )
        return []
    spec = spec.strip()
    if not spec:
        return []
    import shlex

    parts = shlex.split(spec)
    if parts and not shutil.which(parts[0]):
        raise RuntimeError(
            f"--net-wrapper {parts[0]!r} is not on PATH; refusing to run a bare "
            "network call that would fail on a proxied host"
        )
    return parts


# --------------------------------------------------------------------------- #
# gh engine (default)
# --------------------------------------------------------------------------- #


def _rollup_ci_state(rollup: object, *, head_sha: str = "") -> str:
    """Reduce a statusCheckRollup list to one of red/pending/green.

    Empty rollup (no checks yet) is treated as pending, not green: a PR with no
    checks has not demonstrated health.
    """
    rollup = select_latest_checks(rollup, head_sha=head_sha)
    if not rollup:
        return "pending"
    any_fail = False
    any_pending = False
    any_ok = False
    for check in rollup:
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "").upper()
        # CheckRun => conclusion; StatusContext => state.
        outcome = str(check.get("conclusion") or check.get("state") or "").upper()
        classified = classify_check(status, outcome)
        if classified is CheckOutcome.FAILED:
            any_fail = True
        elif classified is CheckOutcome.PASSED:
            any_ok = True
        else:
            # A hole in the record blocks admission but never reports a red.
            any_pending = True
    if any_fail:
        return "red"
    if any_pending:
        return "pending"
    if any_ok:
        return "green"
    return "pending"


# Landing-gate / review META-checks. A red on one of these means the PR lacks a
# valid receipt (merge-gate) or a completed review (core-review-protocol) at its
# current head — a landing blocker, NOT a product-test failure. Kept in sync with
# Hermit's `merge-gate-v2` / `core-review-protocol` and Reverie's `merge-gate`
# check names. Matched tolerantly so a version bump (`merge-gate-v3`) or the
# planner's spaced form ("Merge Gate") still classifies as a gate check.
_GATE_META_CHECK_NAMES = frozenset(
    {"merge-gate", "merge-gate-v2", "merge gate", "core-review-protocol"}
)


def _is_gate_meta_check(name: str) -> bool:
    normalized = name.strip().lower()
    if normalized in _GATE_META_CHECK_NAMES:
        return True
    return normalized.startswith(("merge-gate", "merge gate")) or (
        "review-protocol" in normalized
    )


def _failing_check_names(rollup: object, *, head_sha: str = "") -> list[str]:
    """Names of the FAILED checks in the latest-per-context rollup at head.

    Mirrors ``_rollup_ci_state``'s FAILED test so the split cannot disagree with
    the red verdict it refines.
    """
    names: list[str] = []
    for check in select_latest_checks(rollup, head_sha=head_sha):
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "").upper()
        outcome = str(check.get("conclusion") or check.get("state") or "").upper()
        if classify_check(status, outcome) is CheckOutcome.FAILED:
            names.append(str(check.get("name") or check.get("context") or ""))
    return names


def _label_names(entry: dict[str, object]) -> set[str]:
    """Normalize ``gh pr list --json labels`` without accepting bad schemas."""
    labels = entry.get("labels")
    if not isinstance(labels, list):
        return set()
    names: set[str] = set()
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.add(label["name"])
    return names


def _review_protocol_status(entry: dict[str, object]) -> ReviewProtocolStatus | None:
    """Classify one PR using the same label semantics as the merge-gate lint."""
    labels = _label_names(entry)
    if _POST_FACTO_LABEL not in labels:
        return None

    rounds: dict[str, set[int]] = {"codex": set(), "claude": set()}
    invalid_labels: list[str] = []
    for label in labels:
        match = _REVIEW_ROUND_LABEL.fullmatch(label)
        if match:
            rounds[match.group("reviewer")].add(int(match.group("round")))
        elif label.startswith(
            ("adversarial-review-codex", "adversarial-review-claude")
        ):
            invalid_labels.append(label)
        elif label.startswith(
            ("passed-review-codex", "passed-review-claude")
        ) and label not in _PASSED_REVIEW_LABELS.values():
            invalid_labels.append(label)

    has_round = {reviewer: bool(values) for reviewer, values in rounds.items()}
    has_approval = {
        reviewer: expected in labels
        for reviewer, expected in _PASSED_REVIEW_LABELS.items()
    }

    def paired_state(values: dict[str, bool]) -> str:
        present = sum(values.values())
        if present == 2:
            return "complete"
        return "partial" if present == 1 else "missing"

    missing: list[str] = []
    for reviewer in ("codex", "claude"):
        if not has_round[reviewer]:
            missing.append(f"review-round-{reviewer}")
        if not has_approval[reviewer]:
            missing.append(f"current-approval-{reviewer}")

    number = entry.get("number")
    return ReviewProtocolStatus(
        pr=(
            number
            if isinstance(number, int) and not isinstance(number, bool)
            else None
        ),
        title=str(entry.get("title") or ""),
        draft=entry.get("isDraft") is True,
        codex_rounds=tuple(sorted(rounds["codex"])),
        claude_rounds=tuple(sorted(rounds["claude"])),
        review_rounds=paired_state(has_round),
        current_approvals=paired_state(has_approval),
        complete=not missing,
        missing=tuple(missing),
        invalid_labels=tuple(sorted(invalid_labels)),
    )


def _mechanism_overlaps(raw: Sequence[object]) -> tuple[MechanismOverlap, ...]:
    """Group open PRs by exact ``mechanism:<slug>`` GitHub labels.

    The query already contains only open PRs. Drafts stay in this audit because
    a draft and a ready PR can still implement contradictory policies and later
    merge cleanly. This deliberately detects overlap only; intent remains a
    coordinator decision.
    """
    grouped: dict[str, list[MechanismPr]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        number = entry.get("number")
        pr = MechanismPr(
            pr=(
                number
                if isinstance(number, int) and not isinstance(number, bool)
                else None
            ),
            title=str(entry.get("title") or ""),
            draft=entry.get("isDraft") is True,
        )
        for label in _label_names(entry):
            if (
                label.startswith(_MECHANISM_TAG_PREFIX)
                and label != _MECHANISM_TAG_PREFIX
            ):
                grouped.setdefault(label, []).append(pr)

    overlaps: list[MechanismOverlap] = []
    for mechanism, prs in sorted(grouped.items()):
        if len(prs) < 2:
            continue
        ordered = tuple(
            sorted(prs, key=lambda item: (item.pr is None, item.pr or 0))
        )
        overlaps.append(MechanismOverlap(mechanism=mechanism, prs=ordered))
    return tuple(overlaps)


def banked_green_commits(
    repo: str, *, ci_hub_bin: Path = CI_HUB_BIN, timeout: float = 30.0
) -> frozenset[str]:
    """Full-green commit SHAs for ``repo`` from the LOCAL validate ledger.

    This is the second authority the every-tick GitHub view is blind to. GitHub
    ``statusCheckRollup`` is the ONLY thing that feeds ``green``/``gate``; a PR
    head can be red or pending on GitHub (e.g. the blanket self-hosted red) while
    LOCAL ``validate.sh`` proved a complete, nonempty PASS at that exact SHA and
    banked a receipt the merge gate honors. Without this cross-reference the tool
    reports ``green=0`` even when banked greens exist — the local evidence and the
    GitHub display never meet, which reads as "no validated work" when the truth is
    "validated, just not reflected in a GitHub check".

    Binding is by EXACT full commit SHA (an identity link, not a proxy). We do NOT
    re-derive the green predicate here: we dereference the ONE canonical verifier,
    ``ci-hub ledger qualified-rows`` (complete, nonempty PASS rows), and bucket by
    repo. If the ledger/binary is unavailable (e.g. a 3pai host without the parent
    ledger) we return the empty set — pr-status must ALWAYS report, never fail, so
    a missing ledger degrades to the prior GitHub-only view, never to an error.
    """
    name = repo.rsplit("/", 1)[-1]
    if not Path(ci_hub_bin).exists():
        return frozenset()
    try:
        result = subprocess.run(
            [str(ci_hub_bin), "ledger", "qualified-rows"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    if result.returncode != 0:
        return frozenset()
    commits: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("result") != "pass":
            continue
        cwd = str(row.get("cwd") or "")
        row_repo = row.get("repo") or ("reverie" if "/reverie" in cwd else "hermit")
        if row_repo != name:
            continue
        sha = row.get("commit")
        if isinstance(sha, str) and sha:
            commits.add(sha)
    return frozenset(commits)


# Strength order for a per-SHA failure tier: a genuine full-suite failure ("ok",
# i.e. a durable red) dominates a partial run ("needs-rerun"), which dominates a
# ran-nothing no-result. When a head has several fail rows, the STRONGEST wins —
# one complete failing run at a SHA makes it a real red even if other runs there
# ran nothing (e.g. 71bc3856: exec=515 needs-rerun AND exec=765 full failure => ok).
_FAILURE_TIER_STRENGTH = {"no-result": 0, "needs-rerun": 1, "ok": 2}


def banked_failure_tier_commits(
    repo: str, *, ledger_rel: str = "ignored/validate-run-ledger.jsonl"
) -> dict[str, str]:
    """Map each ``repo`` head SHA that FAILED locally to its strongest failure tier.

    The symmetric peer of :func:`banked_green_commits`. That function dereferences
    the ledger's canonical GREEN verifier (``qualified-rows``); there is no
    equivalent binary command for reds, so we read the same machine-local ledger
    directly and apply the ONE shared executed-count predicate,
    :func:`executed_plausibility` (mirrored in the Rust ``failure_disposition``),
    to each fail/timeout row. The returned tier is ``"no-result"`` (the local run
    executed <= 1 test), ``"needs-rerun"`` (a partial suite below the plausible-full
    floor), or ``"ok"`` (a full-suite run that genuinely failed). Binding is by
    EXACT full commit SHA — an identity link, not a proxy.

    Degrades to an empty map when the ledger is absent/unreadable (a 3pai host, a
    fresh clone), exactly like :func:`banked_green_commits`: pr-status must ALWAYS
    report, never fail, so a missing ledger reverts to the prior GitHub-only view.

    LIMITATION (stated by design): the ledger is machine-local — most PR heads have
    NO local row here, so this can only demote a red where a fail/timeout row
    exists at the EXACT head SHA. A head red only on hosted CI keeps its GitHub
    verdict untouched.
    """
    name = repo.rsplit("/", 1)[-1]
    path = ROOT / ledger_rel
    try:
        text = path.read_text()
    except OSError:
        return {}
    tiers: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("result") not in ("fail", "timeout"):
            continue
        cwd = str(row.get("cwd") or "")
        row_repo = row.get("repo") or ("reverie" if "/reverie" in cwd else "hermit")
        if row_repo != name:
            continue
        sha = row.get("commit")
        if not isinstance(sha, str) or not sha:
            continue
        tier = executed_plausibility(row)
        prev = tiers.get(sha)
        if prev is None or _FAILURE_TIER_STRENGTH[tier] > _FAILURE_TIER_STRENGTH[prev]:
            tiers[sha] = tier
    return tiers


def _classify_gh_prs(
    repo: str,
    raw: list,
    banked_green: frozenset[str] = frozenset(),
    banked_failure: dict[str, str] | None = None,
) -> RepoStatus:
    if banked_failure is None:
        banked_failure = {}
    prs: list[dict[str, object]] = []
    review_protocol: list[ReviewProtocolStatus] = []
    mechanism_overlaps = _mechanism_overlaps(raw)
    green = red = pending = real_reds = undetermined_reds = 0
    product_reds = gate_reds = 0
    green_local = 0
    ledger_no_result = ledger_needs_rerun = 0
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        review_status = _review_protocol_status(entry)
        if review_status is not None:
            review_protocol.append(review_status)
        if entry.get("isDraft") is True:
            continue
        number = entry.get("number")
        head_sha = str(entry.get("headRefOid") or "")
        ci = _rollup_ci_state(entry.get("statusCheckRollup"), head_sha=head_sha)
        mergeable = str(entry.get("mergeable") or "").upper()
        merge_state = str(entry.get("mergeStateStatus") or "").upper()
        # Stale-base vs real: a CONFLICTING/DIRTY red is red because its merge
        # ref is unbuildable (rebase fixes it), NOT a genuine product failure.
        # BUT GitHub computes mergeability lazily, so a cold query returns
        # mergeable=UNKNOWN / merge_state=UNKNOWN; classifying those reds as
        # "real" would falsely spike real_reds/outage and flap between runs.
        # Treat unknown-base reds as their own "undetermined" class so the health
        # signal is stable and honest — a warm re-run resolves them.
        base_known = mergeable in ("MERGEABLE", "CONFLICTING") or merge_state not in (
            "",
            "UNKNOWN",
        )
        stale_base = mergeable == "CONFLICTING" or merge_state == "DIRTY"
        # Second authority: does the LOCAL validate ledger hold a full-green
        # receipt at THIS exact head SHA? A full-SHA match is an identity bind,
        # not a proxy. An authoritative local green overrides a stale/absent
        # GitHub check (e.g. the blanket self-hosted red) — the head IS validated,
        # so it is not a real red, and green=0 is a GitHub-display artifact.
        ledger_green = bool(head_sha) and head_sha in banked_green
        # Third authority (symmetric with ledger_green): the strongest LOCAL failure
        # tier at this exact head. "" when no local fail row exists.
        failure_tier = banked_failure.get(head_sha, "") if head_sha else ""
        red_class = ""
        real_red_kind = ""
        if ci == "red" and ledger_green:
            # GitHub says red, but LOCAL validate proved full green at this SHA.
            # The local receipt is authoritative and the merge gate honors it, so
            # this is NOT a real/gate/product red — it is a green whose GitHub
            # check is stale/self-hosted-blanket. Count it as green_local, keep it
            # OUT of every red bucket so real_reds (and thus unhealthy) stay honest.
            red += 1
            red_class = "ledger-green"
            green_local += 1
        elif ci == "red":
            red += 1
            if stale_base:
                red_class = "stale-base"
            elif not base_known:
                red_class = "undetermined"
                undetermined_reds += 1
            else:
                # Refine the real-red: gate-only (lacks receipt/review) vs a
                # genuine product break. An unnamed/empty failing set falls to
                # "product" so a red is never hidden by the split.
                fails = _failing_check_names(
                    entry.get("statusCheckRollup"), head_sha=head_sha
                )
                if fails and all(_is_gate_meta_check(name) for name in fails):
                    # A landing-gate/review meta-check red is a genuine blocker
                    # regardless of the test count (review is missing, receipt is
                    # missing) — a DIFFERENT authority than the validate ledger, so
                    # the executed_tests carve-out does NOT apply. Keep it a real red.
                    red_class = "real-red"
                    real_reds += 1
                    real_red_kind = "gate"
                    gate_reds += 1
                elif failure_tier == "no-result":
                    # A product-looking GitHub red whose EXACT head ran <= 1 test
                    # locally: a NO-RESULT wearing a red badge, not a corroborated
                    # break. Demote OUT of real_reds (merge-gate WAIT, not FAIL).
                    red_class = "ledger-no-result"
                    ledger_no_result += 1
                elif failure_tier == "needs-rerun":
                    # Product red whose only local run was a partial suite below the
                    # plausible-full floor — not durable evidence of a break; re-run
                    # before condemning. Demoted out of real_reds.
                    red_class = "ledger-needs-rerun"
                    ledger_needs_rerun += 1
                else:
                    # No local row, or a full-suite local run that genuinely failed
                    # (tier "ok") — a real product break.
                    red_class = "real-red"
                    real_reds += 1
                    real_red_kind = "product"
                    product_reds += 1
        elif ci == "green":
            green += 1
            if ledger_green:
                green_local += 1
        else:
            pending += 1
            if ledger_green:
                # Never dispatched on GitHub, but LOCAL validate banked a green at
                # this SHA — landable via the merge gate; not a no-result.
                green_local += 1
        prs.append(
            {
                "pr": number,
                "ci": ci,
                "red_class": red_class,
                "real_red_kind": real_red_kind,
                "ledger_green": ledger_green,
                "ledger_failure_tier": failure_tier,
                "mergeable": mergeable or "UNKNOWN",
                "merge_state": merge_state or "UNKNOWN",
                "title": entry.get("title", ""),
            }
        )
    open_count = len(prs)
    # Outage heuristic: a large simultaneous *known* real-red fraction smells
    # like infra, not N independent product breaks. Built only from resolved
    # real reds so a cold mergeability query cannot trigger a false outage.
    outage = open_count >= 4 and real_reds >= max(3, (open_count + 1) // 2)
    return RepoStatus(
        repo=repo,
        open=open_count,
        green=green,
        red=red,
        pending=pending,
        real_reds=real_reds,
        undetermined_reds=undetermined_reds,
        product_reds=product_reds,
        gate_reds=gate_reds,
        green_local=green_local,
        ledger_no_result=ledger_no_result,
        ledger_needs_rerun=ledger_needs_rerun,
        outage_suspected=outage,
        prs=tuple(prs),
        review_protocol=tuple(review_protocol),
        mechanism_overlaps=mechanism_overlaps,
    )


def fetch_repo_status(
    repo: str,
    warn_threshold: int = DEFAULT_WARN_THRESHOLD,
    *,
    timeout: float | None = None,
) -> RepoStatus:
    """Default single-repo query: the robust gh engine, self-proxied.

    Stable entry point for consumers (e.g. operational_health). ``warn_threshold``
    is accepted for signature compatibility but unused by the gh engine. Raises
    :class:`RepoUnavailable` (a ``RuntimeError``) on any blocked/failed query, so
    a caller catching ``RuntimeError`` still handles it and never mistakes a
    failure for zero open PRs.
    """
    return fetch_repo_status_gh(
        repo, net_wrapper=resolve_net_wrapper(None), timeout=timeout
    )


def fetch_repo_status_gh(
    repo: str,
    *,
    net_wrapper: Sequence[str],
    gh_cmd: str = "gh",
    timeout: float | None = None,
) -> RepoStatus:
    """Query one repo's open-PR health via a single proxied ``gh pr list`` call.

    Raises :class:`RepoUnavailable` on timeout, block, transient-exhaustion, or
    an unparseable/malformed response, so the caller records a partial result
    (UNAVAILABLE) instead of silently reporting zero open PRs.
    """
    return _classify_gh_prs(
        repo,
        _fetch_open_prs_gh(
            repo,
            fields=GH_FIELDS,
            net_wrapper=net_wrapper,
            gh_cmd=gh_cmd,
            timeout=timeout,
        ),
        banked_green_commits(repo),
        banked_failure_tier_commits(repo),
    )


def _fetch_open_prs_gh(
    repo: str,
    *,
    fields: Sequence[str],
    net_wrapper: Sequence[str],
    gh_cmd: str = "gh",
    timeout: float | None = None,
) -> list[object]:
    """Fetch open PRs with a caller-selected bounded GraphQL field set."""
    command = [
        *net_wrapper,
        gh_cmd,
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        "500",
        "--json",
        ",".join(fields),
    ]
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        if timeout is not None and timeout <= 0:
            raise RepoUnavailable(
                f"{repo}: time budget exhausted before gh attempt {attempt}"
            )
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            budget = "unbounded" if timeout is None else f"{timeout:.0f}s"
            raise RepoUnavailable(
                f"{repo}: `gh pr list` exceeded {budget} (proxy stall or gh hang)"
            ) from None
        if result.returncode == 0:
            break
        detail = (result.stderr.strip() or result.stdout.strip() or "").strip()
        # BpfJailer FILE_OPEN denial is not retryable by re-running the same call.
        if "security policy" in detail or "bpfjailer" in detail.lower():
            raise RepoUnavailable(
                f"{repo}: gh/git blocked by BpfJailer security policy "
                f"(FILE_OPEN); ensure the call is proxied. detail: {detail[:400]}"
            )
        retryable = any(m.lower() in detail.lower() for m in _RETRYABLE_MARKERS)
        if not retryable or attempt == MAX_FETCH_ATTEMPTS:
            break
        backoff = float(attempt)
        if timeout is not None:
            elapsed = time.monotonic() - started
            timeout = max(0.0, timeout - elapsed)
            backoff = min(backoff, timeout)
        time.sleep(backoff)
    assert result is not None
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RepoUnavailable(f"{repo}: `gh pr list` failed: {detail[:400]}")
    text = result.stdout.strip()
    if not text:
        # gh exited 0 with empty stdout — never treat as "0 open PRs"; that is the
        # blind-but-green failure mode this tool must not produce.
        raise RepoUnavailable(
            f"{repo}: `gh pr list` returned exit 0 but EMPTY output; refusing to "
            "report 0 open PRs from an empty response"
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise RepoUnavailable(f"{repo}: gh returned non-JSON: {text[:200]}") from error
    if not isinstance(raw, list):
        raise RepoUnavailable(f"{repo}: gh returned an unexpected schema (not a list)")
    return raw


def fetch_mechanism_overlaps_gh(
    repo: str,
    *,
    net_wrapper: Sequence[str],
    gh_cmd: str = "gh",
    timeout: float | None = None,
) -> tuple[MechanismOverlap, ...]:
    """Fetch only labels needed to preserve semantic warnings during CI 504s."""
    raw = _fetch_open_prs_gh(
        repo,
        fields=MECHANISM_GH_FIELDS,
        net_wrapper=net_wrapper,
        gh_cmd=gh_cmd,
        timeout=timeout,
    )
    return _mechanism_overlaps(raw)


# --------------------------------------------------------------------------- #
# planner engine (opt-in)
# --------------------------------------------------------------------------- #


def planner_command(repo: str, warn_threshold: int) -> list[str]:
    return [
        str(AGENT_TOOL),
        "pr-landing-planner",
        "status",
        "--repo",
        repo,
        "--base",
        "main",
        "--git-dir",
        str(_checkout_for(repo)),
        "--remote",
        "origin",
        "--net-wrapper",
        "with-proxy",
        "--gh-cmd",
        "gh",
        # Real merge-tree conflict detection, ON for planning runs (this is the
        # opt-in `--engine planner` path, NOT the every-tick `gh` default). It
        # was previously pinned to `file-overlap` on the theory that real
        # conflict analysis is an "expensive fan-out" — a conservative default
        # nobody had measured. Measured 2026-08-04 (denominator: 40 probes over
        # rrnewton/hermit's 107 open PRs): one `git merge-tree` base-conflict
        # probe = 36.5 ms local; the whole open set vs main ~4 s. The real cost
        # was never the analysis — it was a per-PR `git fetch` fan-out (21.5 s
        # over 25 hermit heads), which agent-utils PR #14 collapses to ONE
        # batched fetch (0.85 s, ~25x). So real conflict data over the full open
        # set costs seconds. A stated derivation cannot silently rot back into a
        # guess. `gh` stays the default for the every-tick path (no local fetch;
        # the only path that works under 3pai BpfJailer); planning runs opt in.
        "--conflict-detector",
        "merge-tree",
        "--gate-check",
        "merge-gate" if repo == "rrnewton/hermit" else "Merge Gate",
        "--format",
        "json",
        "--warn-threshold",
        str(warn_threshold),
    ]


def _parse_planner_payload(repo: str, stdout: str) -> RepoStatus:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{repo}: planner returned invalid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), dict):
        raise RuntimeError(f"{repo}: planner returned an unexpected schema")
    summary = payload["summary"]
    prs = payload.get("prs")
    if not isinstance(prs, list):
        raise RuntimeError(f"{repo}: planner result has no PR list")

    def count(key: str) -> int:
        value = summary.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    return RepoStatus(
        repo=repo,
        open=count("open"),
        green=count("green"),
        red=count("red"),
        pending=count("pending"),
        real_reds=count("real_reds"),
        outage_suspected=summary.get("outage_suspected") is True,
        prs=tuple(pr for pr in prs if isinstance(pr, dict)),
    )


def fetch_repo_status_planner(
    repo: str,
    warn_threshold: int = DEFAULT_WARN_THRESHOLD,
    *,
    timeout: float | None = None,
) -> RepoStatus:
    """Query one repo's PR health via the planner, bounded by ``timeout`` seconds.

    Raises :class:`RepoUnavailable` if the (possibly-retried) planner cannot
    complete within the budget, so the caller can record a partial result and
    keep going instead of hanging.
    """
    command = planner_command(repo, warn_threshold)
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        if timeout is not None and timeout <= 0:
            raise RepoUnavailable(
                f"{repo}: time budget exhausted before planner attempt {attempt}"
            )
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run has already SIGKILLed the direct planner child; a
            # single in-flight `git fetch` grandchild (if any) exits on its own.
            budget = "unbounded" if timeout is None else f"{timeout:.0f}s"
            raise RepoUnavailable(
                f"{repo}: planner exceeded {budget} "
                f"(per-PR proxied git fetch fan-out; ~1.35s/PR measured)"
            ) from None
        if result.returncode == 0:
            break
        detail = result.stderr.strip() or result.stdout.strip()
        if "security policy" in detail or "bpfjailer" in detail.lower():
            raise RepoUnavailable(
                f"{repo}: planner git fetch blocked by BpfJailer (FILE_OPEN); "
                f"use --engine gh on 3pai hosts. detail: {detail[:300]}"
            )
        retryable = "504" in detail or "changed during collection" in detail
        if not retryable or attempt == MAX_FETCH_ATTEMPTS:
            break
        # Bounded backoff, but never sleep past the remaining budget.
        backoff = float(attempt)
        if timeout is not None:
            elapsed = time.monotonic() - started
            timeout = max(0.0, timeout - elapsed)
            backoff = min(backoff, timeout)
        time.sleep(backoff)
    assert result is not None
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"{repo}: agent-utils pr-landing-planner failed: {detail}")
    return _parse_planner_payload(repo, result.stdout)


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #


def collect_statuses(
    repos: Sequence[str],
    warn_threshold: int,
    *,
    engine: str,
    net_wrapper: Sequence[str],
    per_repo_timeout: float,
    overall_deadline: float,
) -> list[RepoStatus]:
    """Query every repo, always returning one status each (partial on failure)."""
    deadline = time.monotonic() + overall_deadline
    statuses: list[RepoStatus] = []
    for repo in repos:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            statuses.append(
                _unavailable(
                    repo,
                    f"overall deadline {overall_deadline:.0f}s exhausted "
                    f"before querying {repo}",
                )
            )
            continue
        budget = min(per_repo_timeout, remaining)
        try:
            if engine == "planner":
                statuses.append(
                    fetch_repo_status_planner(repo, warn_threshold, timeout=budget)
                )
            else:
                statuses.append(
                    fetch_repo_status_gh(
                        repo, net_wrapper=net_wrapper, timeout=budget
                    )
                )
        except RepoUnavailable as unavailable:
            reason = str(unavailable)
            mechanism_overlaps: tuple[MechanismOverlap, ...] = ()
            if engine == "gh":
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    try:
                        mechanism_overlaps = fetch_mechanism_overlaps_gh(
                            repo,
                            net_wrapper=net_wrapper,
                            timeout=min(60.0, remaining),
                        )
                    except RepoUnavailable as fallback_error:
                        reason += f"; mechanism-label fallback failed: {fallback_error}"
            statuses.append(_unavailable(repo, reason, mechanism_overlaps))
        except RuntimeError as error:
            # A hard planner/schema error still yields a report line rather than
            # aborting the whole command with nothing printed.
            statuses.append(_unavailable(repo, f"query failed: {error}"))
    return statuses


def _render_mechanism_overlaps(
    lines: list[str], overlaps: Sequence[MechanismOverlap]
) -> None:
    if not overlaps:
        return
    lines.append(
        "    Mechanism overlaps: "
        f"{len(overlaps)} coordinator review required "
        "(shared tag exposes overlap; it does not prove conflicting intent)"
    )
    for overlap in overlaps:
        lines.append(f"      {overlap.mechanism}")
        for pr in overlap.prs:
            draft = " draft=yes" if pr.draft else ""
            lines.append(
                f"        #{pr.pr if pr.pr is not None else '?':<5}"
                f"{draft} {pr.title}"
            )


def health_verdict(statuses: Sequence[RepoStatus]) -> dict[str, object]:
    """Explain the top-level banner from the exact inputs that determine it."""
    unhealthy = any(status.unhealthy for status in statuses)
    unavailable = [status for status in statuses if not status.available]
    state = "unhealthy" if unhealthy else ("degraded" if unavailable else "healthy")
    inputs: list[dict[str, object]] = []
    for status in statuses:
        stale_base_reds = sum(
            pr.get("red_class") == "stale-base" for pr in status.prs
        )
        inputs.append(
            {
                "repo": status.repo,
                "available": status.available,
                "ready_prs": status.open,
                "green": status.green,
                "green_local": status.green_local,
                "real_reds": status.real_reds,
                "product_reds": status.product_reds,
                "gate_reds": status.gate_reds,
                "stale_base_reds": stale_base_reds,
                "undetermined_reds": status.undetermined_reds,
                "no_result": status.pending,
                "outage_suspected": status.outage_suspected,
                "triggers_unhealthy": status.unhealthy,
                "unavailable_reason": status.reason if not status.available else "",
            }
        )
    return {
        "state": state,
        "rule": HEALTH_VERDICT_RULE,
        "inputs": inputs,
        "not_inputs": list(HEALTH_VERDICT_EXCLUDES),
    }


def render_report(statuses: Sequence[RepoStatus], warn_threshold: int, engine: str) -> str:
    total = sum(status.open for status in statuses if status.available)
    unavailable = [status for status in statuses if not status.available]
    verdict = health_verdict(statuses)
    if verdict["state"] == "unhealthy":
        heading = "CI health: UNHEALTHY"
    elif unavailable:
        heading = (
            f"CI health: DEGRADED (partial: {len(unavailable)} of "
            f"{len(statuses)} repos unavailable)"
        )
    else:
        heading = "CI health: HEALTHY"
    source = {
        "gh": (
            "gh pr list --json (single proxied API call per repo; labels-only "
            "fallback after a full-query failure)"
        ),
        "planner": "pinned agent-utils/pr-landing-planner status (per-PR git fetch)",
    }.get(engine, engine)
    lines = [
        heading,
        f"Source: {source}",
        f"Verdict rule: {HEALTH_VERDICT_RULE}",
        "Verdict does not read: " + ", ".join(HEALTH_VERDICT_EXCLUDES),
        "Verdict inputs:",
    ]
    for item in verdict["inputs"]:
        assert isinstance(item, dict)
        lines.append(
            "  {repo}: available={available} ready={ready_prs} green={green} "
            "green_local={green_local} "
            "real_reds={real_reds} (product={product_reds} gate={gate_reds}) "
            "stale_base_reds={stale_base_reds} "
            "undetermined_reds={undetermined_reds} no_result={no_result} "
            "outage={outage_suspected} triggers_unhealthy={triggers_unhealthy}".format(
                **item
            )
        )
    # Make the banner actionable: say WHETHER any product test is actually broken,
    # so a gate/review-only UNHEALTHY is not mistaken for product breakage (and,
    # conversely, a real product red is pointed at directly). Only over available
    # repos — an unavailable repo's product state is unknown, not zero.
    if verdict["state"] == "unhealthy":
        available_statuses = [s for s in statuses if s.available]
        total_product = sum(s.product_reds for s in available_statuses)
        if total_product == 0:
            lines.append(
                "  Actionability: 0 product-test reds on any ready PR head — "
                "UNHEALTHY is driven entirely by landing-gate/review reds "
                "(PRs lacking a valid receipt or completed review at head), not "
                "product breakage. Fix by producing receipts / completing review, "
                "not by debugging tests."
            )
        else:
            hot = ", ".join(
                f"{s.repo}={s.product_reds}"
                for s in available_statuses
                if s.product_reds
            )
            lines.append(
                f"  Actionability: {total_product} product-test red(s) — a genuine "
                f"break at a ready PR head: {hot}. Remaining real_reds are "
                "landing-gate/review only."
            )
    for status in statuses:
        if not status.available:
            lines.append(f"  {status.repo}: UNAVAILABLE — {status.reason}")
            _render_mechanism_overlaps(lines, status.mechanism_overlaps)
            continue
        undet = (
            f" undetermined_reds={status.undetermined_reds}"
            if status.undetermined_reds
            else ""
        )
        # Show the product/gate split only when it fully accounts for real_reds
        # (the gh engine classifies every real-red; the planner does not).
        split = ""
        if status.real_reds and status.product_reds + status.gate_reds == status.real_reds:
            split = f" (product={status.product_reds} gate={status.gate_reds})"
        local = (
            f" green_local={status.green_local}" if status.green_local else ""
        )
        demoted = ""
        if status.ledger_no_result or status.ledger_needs_rerun:
            demoted = (
                f" ledger_no_result={status.ledger_no_result}"
                f" ledger_needs_rerun={status.ledger_needs_rerun}"
            )
        lines.append(
            f"  {status.repo}: open={status.open} green={status.green}{local} "
            f"red={status.red} pending={status.pending} real_reds={status.real_reds}"
            f"{split}{demoted}{undet} outage={'yes' if status.outage_suspected else 'no'}"
        )
        if status.ledger_no_result or status.ledger_needs_rerun:
            lines.append(
                f"    ledger: {status.ledger_no_result} red PR head(s) ran <= 1 test "
                f"locally (NO-RESULT) and {status.ledger_needs_rerun} ran a partial "
                "suite (NEEDS-RERUN) at the exact head — demoted OUT of real_reds; the "
                "GitHub product red is uncorroborated by any complete local run "
                "(executed_tests carve-out, see ledger_failure_tier flag)"
            )
        if status.green_local:
            lines.append(
                f"    ledger: {status.green_local} open head(s) carry an "
                "authoritative LOCAL full-green receipt (landable via merge gate) "
                "though GitHub shows red/pending — GitHub green=0 is a check-state "
                "artifact, not absence of validated work (see ledger_green flag)"
            )
        if status.undetermined_reds:
            lines.append(
                f"    CAUTION: {status.undetermined_reds} red PR(s) have mergeability "
                "still computing (base state UNDETERMINED this query); real-red vs "
                "stale-base unresolved — re-run to classify."
            )
        for pr in status.prs:
            klass = pr.get("red_class") or "-"
            kind = pr.get("real_red_kind")
            if kind:
                klass = f"{klass}:{kind}"
            lines.append(
                f"    #{pr.get('pr', '?'):<5} ci={pr.get('ci', 'unknown'):<7} "
                f"class={klass:<23} {pr.get('title', '')}"
            )
        _render_mechanism_overlaps(lines, status.mechanism_overlaps)
        if status.review_protocol:
            audits = status.review_protocol
            complete = sum(audit.complete for audit in audits)
            dual_review = sum(audit.review_rounds == "complete" for audit in audits)
            partial_review = sum(audit.review_rounds == "partial" for audit in audits)
            no_review = sum(audit.review_rounds == "missing" for audit in audits)
            dual_approval = sum(
                audit.current_approvals == "complete" for audit in audits
            )
            invalid = sum(bool(audit.invalid_labels) for audit in audits)
            lines.append(
                "    Review protocol: "
                f"labeled={len(audits)} complete={complete} "
                f"dual_review={dual_review} partial_review={partial_review} "
                f"no_review_evidence={no_review} "
                f"current_dual_approval={dual_approval} invalid_label_prs={invalid}"
            )
            for audit in audits:
                invalid_detail = (
                    f" invalid={','.join(audit.invalid_labels)}"
                    if audit.invalid_labels
                    else ""
                )
                draft = " draft=yes" if audit.draft else ""
                lines.append(
                    f"      #{audit.pr if audit.pr is not None else '?':<5} "
                    f"review={audit.review_rounds:<8} "
                    f"approval={audit.current_approvals:<8} "
                    f"missing={','.join(audit.missing) or '-'}"
                    f"{invalid_detail}{draft} {audit.title}"
                )
    if unavailable:
        lines.append(
            f"PARTIAL RESULT: {len(unavailable)} of {len(statuses)} repo(s) "
            "could not be queried within the time budget; open-PR totals above "
            "cover only the repos that responded (NOT a claim that they have no "
            "PRs)."
        )
    if total > warn_threshold:
        lines.append(
            f"WARNING: {total} open PRs exceeds the {warn_threshold} PR threshold."
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        dest="repos",
        action="append",
        help="GitHub OWNER/REPO to query; repeat to override defaults",
    )
    parser.add_argument(
        "--engine",
        choices=("gh", "planner"),
        default=os.environ.get("CI_HUB_PR_STATUS_ENGINE", "gh"),
        help=(
            "status backend: 'gh' (default; single proxied gh API call, no "
            "per-PR git fetch) or 'planner' (agent-utils, real merge-tree "
            "conflict detection over the open set; use on planning runs)"
        ),
    )
    parser.add_argument(
        "--net-wrapper",
        dest="net_wrapper",
        default=None,
        help=(
            "command prefix for gh (gh engine); default 'with-proxy' if on PATH "
            "(idempotent). Pass '' to disable."
        ),
    )
    parser.add_argument("--warn-threshold", type=int, default=DEFAULT_WARN_THRESHOLD)
    parser.add_argument(
        "--per-repo-timeout",
        type=float,
        default=DEFAULT_PER_REPO_TIMEOUT,
        help=(
            "seconds to allow one repo's query before recording it "
            f"unavailable (default: {DEFAULT_PER_REPO_TIMEOUT:.0f}; "
            "env CI_HUB_PR_STATUS_TIMEOUT)"
        ),
    )
    parser.add_argument(
        "--overall-deadline",
        type=float,
        default=DEFAULT_OVERALL_DEADLINE,
        help=(
            "total seconds across all repos before remaining repos are marked "
            f"unavailable (default: {DEFAULT_OVERALL_DEADLINE:.0f}; "
            "env CI_HUB_PR_STATUS_DEADLINE)"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.warn_threshold < 0:
        parser.error("--warn-threshold must be non-negative")
    if args.per_repo_timeout <= 0:
        parser.error("--per-repo-timeout must be positive")
    if args.overall_deadline <= 0:
        parser.error("--overall-deadline must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repos = tuple(args.repos) if args.repos else DEFAULT_REPOS
    try:
        net_wrapper = resolve_net_wrapper(args.net_wrapper)
    except RuntimeError as error:
        # Configuration error: fail loudly, never silently issue a bare call.
        print(f"CI health: ERROR — {error}", file=sys.stderr)
        return 3
    statuses = collect_statuses(
        repos,
        args.warn_threshold,
        engine=args.engine,
        net_wrapper=net_wrapper,
        per_repo_timeout=args.per_repo_timeout,
        overall_deadline=args.overall_deadline,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "engine": args.engine,
                    "verdict": health_verdict(statuses),
                    "repos": [asdict(s) for s in statuses],
                },
                sort_keys=True,
            )
        )
    else:
        print(render_report(statuses, args.warn_threshold, args.engine))
    if any(status.unhealthy for status in statuses):
        return 1
    if any(not status.available for status in statuses):
        # Degraded/partial: cannot fully verify PR health, but we terminated
        # with a report. Non-zero mirrors github_main_health's "cannot verify".
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("CI health: ERROR — interrupted", file=sys.stderr)
        raise SystemExit(130)
