#!/usr/bin/env python3
"""Known-green anchor provider -- consumes ci-hub history.

A multisect search needs a trustworthy GOOD lower bound. Guessing wastes probes;
worse, an accidentally-broken --good silently caps the search below the real
regression. ci-hub already knows which commits were green, so consume that
instead of asking the agent to guess.

Source of truth, in priority order:
  1. ci-hub/history/query.py   (the unified commit/CI store, once it exists;
     ci-hub's README says dispatch switches to it automatically).
  2. ci-hub/validate/aggregate.py --json   (local validate-run ledger: every
     `hermit validate` on this machine, with SHA + PASS/FAIL). This is what
     exists TODAY and is what `ci-hub history` falls back to.

The provider is fail-SAFE: if it cannot find a green anchor it returns None and
the orchestrator requires an explicit --good. It never fabricates an anchor.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GreenAnchor:
    sha: str
    source: str  # human-readable provenance
    detail: str


def _parent_root(start: Path) -> Path | None:
    p = start.resolve()
    for cand in [p, *p.parents]:
        if (cand / "ci-hub" / "ci-hub").exists():
            return cand
    return None


def _run(argv: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _is_ancestor(repo: Path, sha: str, of: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, of],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def green_anchors_from_aggregate(parent: Path) -> list[GreenAnchor]:
    """Every SHA that PASSED `hermit validate` locally, newest first."""
    agg = parent / "ci-hub" / "validate" / "aggregate.py"
    if not agg.exists():
        return []
    raw = _run(["python3", str(agg), "--json"], parent)
    if not raw:
        return []
    try:
        records = json.loads(raw)
    except json.JSONDecodeError:
        return []
    anchors: list[GreenAnchor] = []
    for rec in records if isinstance(records, list) else []:
        sha = rec.get("git_sha") or rec.get("sha")
        status = str(rec.get("status") or rec.get("result") or "").lower()
        if sha and status in {"pass", "passed", "success", "green", "ok"}:
            anchors.append(
                GreenAnchor(
                    sha=sha,
                    source="ci-hub validate ledger",
                    detail=f"local validate PASS ({rec.get('timestamp', 'unknown time')})",
                )
            )
    return anchors


def green_anchors_from_query(parent: Path) -> list[GreenAnchor]:
    """Unified store, once ci-hub/history/query.py exists (fail-safe empty)."""
    query = parent / "ci-hub" / "history" / "query.py"
    if not query.exists():
        return []
    raw = _run(["python3", str(query), "--green", "--json"], parent)
    if not raw:
        return []
    try:
        records = json.loads(raw)
    except json.JSONDecodeError:
        return []
    anchors = []
    for rec in records if isinstance(records, list) else []:
        sha = rec.get("sha") or rec.get("git_sha")
        if sha:
            anchors.append(
                GreenAnchor(sha=sha, source="ci-hub history store", detail="green on main")
            )
    return anchors


def suggest_good(repo: Path, bad: str, start: Path | None = None) -> GreenAnchor | None:
    """Newest known-green commit that is an ancestor of `bad` -- the tightest
    trustworthy lower bound. Returns None if none is known (caller must then
    require an explicit --good)."""
    parent = _parent_root(start or repo)
    if parent is None:
        return None
    candidates = green_anchors_from_query(parent) or green_anchors_from_aggregate(parent)
    for anchor in candidates:
        # Normalize to a full sha within THIS repo and require ancestry of bad.
        full = _run_git_rev(repo, anchor.sha)
        if full and _is_ancestor(repo, full, bad):
            return GreenAnchor(sha=full, source=anchor.source, detail=anchor.detail)
    return None


def _run_git_rev(repo: Path, ref: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()
