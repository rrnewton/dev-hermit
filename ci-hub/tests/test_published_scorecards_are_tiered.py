#!/usr/bin/env python3
"""Every PUBLISHED scorecard must carry the comparison-tier columns.

WHY. An untiered row is an unqualified green: a verdict with no record of what
comparison produced it. This is what made the tier-column contradiction look
real — one agent read `scorecard.csv` (tiered) and another read
`fullcorpus-scorecard.csv` (untiered), and BOTH were reporting honestly. The
disagreement was in the data, not in either reader.

The migration script exists and works, but its `csv_path` argument is
`nargs="?"` with `default=.../scorecard.csv`, so it migrates exactly ONE file per
invocation and defaults to the one that was already done. Nothing enumerated the
rest. That is the hardcoded-default-over-a-growing-set shape, and a guard is the
only thing that stops it recurring the next time a scorecard is added.

SCOPE, AND WHY IT IS NOT "EVERY *scorecard*.csv IN THE REPO". Deriving the list
from the repo returns 22 CSVs, but 17 live under `experiments/` and are FROZEN
CAPTURES — a durable experiment is evidence of what was measured on a date, and
rewriting its schema would falsify the record. One more,
`compat-envelope/pre-tightening-baseline-*/scorecard.csv`, is a deliberately
retained pre-tightening BASELINE; migrating it would destroy the baseline it
exists to be. So the scope is the live published set under `compat-envelope/`,
excluding retained baselines — derived by pattern, never hand-listed.
"""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The columns the migration adds; see compat-envelope/migrate-scorecard-schema.py
TIER_COLUMNS = ("bitwise_parity", "compared_log_messages", "tier", "comparison_tier")
KNOWN_COMPARISON_TIERS = {
    "full-stdout-info-stack-heap",
    "stdout-info-stack-heap-spot-check",
    "legacy-unqualified",
    "unqualified-no-comparison",
    "unqualified-stdout-only",
    "unqualified-self-verify-only",
    "unqualified-tool-count-only",
}

# A retained baseline is frozen ON PURPOSE. Matched by directory shape, not by a
# hand-maintained filename list, so a future baseline is covered automatically.
_FROZEN_DIR_MARKERS = ("baseline",)


def _published_scorecards() -> list[str]:
    """Live published scorecards, DERIVED from the repo.

    Uses git rather than a filesystem walk so an untracked scratch CSV cannot
    fail the build, and so the answer is about what the repo PUBLISHES.
    """
    out = subprocess.run(
        ["git", "ls-files", "compat-envelope"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout
    found = []
    for line in out.splitlines():
        p = line.strip()
        if not p.endswith(".csv") or "scorecard" not in Path(p).name:
            continue
        parts = Path(p).parts
        if any(m in seg.lower() for seg in parts[:-1] for m in _FROZEN_DIR_MARKERS):
            continue  # retained baseline: frozen on purpose
        found.append(p)
    return sorted(found)


def _header(path: str) -> list[str]:
    with (REPO_ROOT / path).open(newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh), [])


def test_every_published_scorecard_carries_the_tier_columns() -> None:
    untiered = []
    for path in _published_scorecards():
        header = _header(path)
        missing = [c for c in TIER_COLUMNS if c not in header]
        if missing:
            untiered.append(f"{path} (missing {', '.join(missing)})")
    assert not untiered, (
        "UNTIERED PUBLISHED SCORECARD(S) — an untiered row is an unqualified "
        "green, a verdict with no record of what comparison produced it:\n  "
        + "\n  ".join(untiered)
        + "\n\nMigrate each with:\n"
        "  python3 compat-envelope/migrate-scorecard-schema.py <path> --apply\n"
        "It adds the tier columns without inventing evidence: rows whose tier "
        "was never recorded are labelled legacy-unqualified rather than green."
    )


def test_every_published_row_states_a_known_comparison_tier() -> None:
    violations = []
    for path in _published_scorecards():
        with (REPO_ROOT / path).open(newline="", encoding="utf-8") as fh:
            for line, row in enumerate(csv.DictReader(fh), start=2):
                value = (row.get("comparison_tier") or "").strip()
                if value not in KNOWN_COMPARISON_TIERS:
                    violations.append(f"{path}:{line}: {value or '<blank>'}")
    assert not violations, (
        "REFUSED comparison-tier rows (blank/unknown values are never defaulted):\n  "
        + "\n  ".join(violations[:20])
    )


def test_the_derivation_is_not_inert() -> None:
    """Positive control. A broken `git ls-files` would make the assertion above
    pass vacuously, which is the same unfalsifiability the tier column exists to
    remove."""
    found = _published_scorecards()
    assert len(found) >= 3, f"expected the published set, derived only {found}"
    names = {Path(p).name for p in found}
    for anchor in ("scorecard.csv", "fullcorpus-scorecard.csv"):
        assert anchor in names, f"derivation missed a known published scorecard: {anchor}"


def test_retained_baselines_are_excluded_and_stay_frozen() -> None:
    """The exclusion must be real, and it must be narrow.

    If the baseline filter ever stopped matching, the guard would demand the
    migration of a frozen record — turning a correctness check into a request to
    falsify evidence.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "compat-envelope"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    baselines = [
        p for p in tracked
        if p.endswith(".csv") and "scorecard" in Path(p).name
        and any(m in seg.lower() for seg in Path(p).parts[:-1] for m in _FROZEN_DIR_MARKERS)
    ]
    assert baselines, (
        "the frozen-baseline exclusion matched nothing; either the baseline was "
        "removed (then drop this test) or the marker drifted (then the guard is "
        "about to demand migrating a frozen record)"
    )
    for p in baselines:
        assert p not in _published_scorecards(), f"frozen baseline leaked into scope: {p}"
