#!/usr/bin/env python3
"""ci-hub's OWN rebase wrapper: record `X rebased on main Y -> Z` and DERIVE soft-green.

OWNER, 2026-08-04: "ci-hub should have its own REBASE WRAPPER that RECORDS that
REVISION X WAS REBASED ON MAIN Y TO YIELD Z, with or without merge conflicts.
ZERO CONFLICTS SHOULD MARK Z AS SOFT GREEN MECHANICALLY (LAND!). MINOR CONFLICTS
become the JUDGMENT OF THE AGENT fixing them -- part of their report MUST BE
whether they judge the resolution LOW RISK ENOUGH TO KEEP THE SOFT GREEN."

OWNER CORRECTION, same thread: "even the safe no-conflict case has SOME RISK.
THIS IS PROBABILISTIC. IT IS NOT LITERALLY SOUND IN A MATHEMATICAL SENSE."

So the framing this tool encodes: ZERO CONFLICTS IS A HIGH-CONFIDENCE PRIOR, NOT
A PROOF. Git conflict detection is TEXTUAL and line-based; semantic dependency is
not. X adds a caller while Y changes the callee's contract == no conflict, still
broken. X adds a test while Y changes a shared fixture. X uses a symbol Y removed
elsewhere. Trait impls / type inference / feature-flag / macro expansion are all
cross-file and invisible to a line-based merge. So a clean textual rebase is a
LOW BASE-RATE-OF-BREAKAGE bet, and post-facto validation of the tip covers the
residual risk the rebase does not eliminate: land on the prior, verify the tip,
fix forward fast. The two halves are one system.

FOUR THINGS THIS TOOL GETS RIGHT (each a Proxy-Binding predicate):

  1. SOFT-GREEN IS A CONFIDENCE LEVEL, NOT A BOOLEAN. `soft-green(zero-conflict)`
     and `soft-green(resolver-judged)` are DIFFERENT BETS. A consumer deciding
     whether to land immediately or wait for a tip validate must be able to tell
     them apart, so the level is carried WITH the value, never flattened to a bare
     "green" flag.

  2. A CONFLICTED REBASE WITHOUT A RISK JUDGEMENT IS REFUSED, never defaulted to
     green. An ABSENT judgement must never read as approval. The resolving agent's
     judgement (`retained-soft-green` | `needs-full-validate`) is a REQUIRED FIELD
     plus a non-empty rationale; omit it and `record` exits REFUSED and writes no
     eligible record.

  3. CARRY Y, THE BASE. A clean rebase onto a base BELOW A GATE FLOOR yields an
     UNLANDABLE Z even with zero conflicts (23 heads hit exactly that on one base
     today). Landability is therefore soft-green AND base-clears-every-floor;
     the base's floor status is carried in the record and RE-CHECKED live at query
     time, so a floor added after recording demotes a stale record.

  4. CARRY HARD GREEN AT X, NOT JUST A RECEIPT AT Z. X must have passed one of
     the interchangeable exact-SHA authorities (counted local full validation or
     hosted portable+privileged). A conflict-free rebase inherits that evidence
     as a probabilistic soft green at Z. Requiring another full receipt at Z
     before landing destroys the inheritance and recreates the queue-stalling
     protocol this wrapper exists to avoid. A receipt/hosted pass at Z is still
     recorded when present and upgrades Z to exact hard green; it is not required
     for the inherited-soft path. Every speculative landing arms exact-Z
     post-facto validation before the merge, so the residual risk is bounded by
     durable remediation rather than shifted into a multi-day pre-land queue.

CONSUMER: the lander QUERIES for eligible heads (`eligible`) rather than reading
posted notes -- a query has no mailbox to miss, closing the producer-posted-to-
the-wrong-task gap (12 verified heads were invisible that day because a producer
posted to its own task). See memory: ci-hub-ledger-cannot-record-soft-vs-hard-green
(the validate ledger CANNOT record soft-vs-hard green; this store carries the
provenance the ledger cannot), rebase-base-floors-queryable-gate-floors-py,
merge-gate-v2-floor-invalidates-pre-floor-greens.

RECORD SCHEMA (ignored/rebase-records.jsonl, append-only, latest-per-Z wins):
  { schema_version, recorded_utc, source_rev: X, base: Y, result: Z,
    conflicts: [] | [files], resolver, risk_judgement:
      retained-soft-green | needs-full-validate | n/a,
    rationale, soft_green: null | soft-green(zero-conflict) |
      soft-green(resolver-judged),
    base_clears_floor, base_unmet_floors: [...],
    source_hard_green: null | { sha: X, verdict: HARD_GREEN, sources: ... },
    result_hard_green: null | { sha: Z, verdict: HARD_GREEN, sources: ... },
    receipt_at_Z: null | {...}, # legacy compatibility/cache only
    eligibility_kind: hard-green(exact-result) | soft-green(inherited-source),
    landable, landable_reason }

SUBCOMMANDS
  record    Record a completed rebase outcome and derive its soft-green level.
            Resolver-only: a non-empty conflict list, closed judgement, rationale,
            and exact hard-green evidence are required. Caller-asserted clean
            rebases are refused; only `rebase` observes and mints that fact.
  rebase    OWN the mechanical rebase in a checkout: fetch, resolve the base
            (--onto <sha> | --onto newest-green), attempt `git rebase`. Zero
            conflicts -> complete, record soft-green(zero-conflict) mechanically,
            optionally push, carrying X's hard-green evidence into the record. Conflicts
            -> abort cleanly, report the conflicted files, and REFUSE to soft-green
            (the resolver resolves, then calls `record --risk-judgement ...`).
  eligible  Consumer query for the lander: list (or test) heads that are eligible
            to land NOW -- either exact hard green at Z, or source hard green at X
            plus a retained soft-green derivation, and a base clearing every floor.

EXIT CODES (shared with the sibling floor tools)
  0  OK       recorded / listed / the queried head IS eligible
  2  REFUSED  conflicted rebase with no risk judgement / queried head NOT eligible
  3  ERROR    git, registry, or store failure

Usage:
  rebase_wrapper.py record   --source X --base Y --result Z
                             --conflicts none|<file,file,...>
                             [--resolver A --risk-judgement R --rationale "..."]
                             [--repo-checkout P] [--registry P] [--store P]
                             [--no-fetch] [--json]
  rebase_wrapper.py rebase   --source <sha-or-branch> --onto <sha|newest-green>
                             [--resolver A] [--push] [--repo-checkout P]
                             [--registry P] [--store P] [--no-fetch] [--json]
  rebase_wrapper.py eligible [--result Z | --source X] [--recheck-floor/--no-...]
                             [--repo-checkout P] [--registry P] [--store P] [--json]
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys

# Reuse the floor half of the contract verbatim: one enumeration, one verifier.
_CIHUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VALIDATE_DIR = os.path.join(_CIHUB_DIR, "validate")
sys.path.insert(0, _VALIDATE_DIR)
import gate_floors  # noqa: E402  (path injected above)

# A2 durable provenance reuses the receipts-branch plumbing verbatim (one
# publisher, one branch-head creator, one gh wrapper) so rebase provenance and
# validate receipts live on the SAME shared branch with identical semantics.
_VALIDATION_DIR = os.path.join(_CIHUB_DIR, "validation")
sys.path.insert(0, _VALIDATION_DIR)
import publish_receipt  # noqa: E402  (path injected above)

_LANDING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _LANDING_DIR)
import hard_green as hard_green_authority  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_ERROR = 3

LOCAL_TIMEOUT = 30.0
NETWORK_TIMEOUT = 120.0

DEFAULT_CHECKOUT = gate_floors.DEFAULT_CHECKOUT
DEFAULT_REGISTRY = gate_floors.DEFAULT_REGISTRY


def parent_root() -> str:
    """The canonical dev-hermit parent root, resolved the SAME way every ci-hub
    tool resolves it (matches validate/aggregate.py:parent_root): the
    DEV_HERMIT_PARENT env when set, else three levels up from THIS file.

    Anchoring to the ENV (not `__file__`) is the load-bearing half of Fix A. A
    scratch/worktree-slot COPY of this wrapper has a DIFFERENT `__file__`, so a
    `__file__`-derived root resolves a DIFFERENT parent -> a DIFFERENT store: the
    producer writes a store the lander never reads. That is the same
    producer-wrote-own / consumer-read-other mailbox gap `eligible` was built to
    kill, merely relocated onto a filesystem path (dbi's reopened finding, and
    empirically live: ci-hub/landing/ and scratch/.../ci-hub/landing/ each
    resolved their own root). With DEV_HERMIT_PARENT set once on a host, EVERY
    copy converges on ONE store.
    """
    env = os.environ.get("DEV_HERMIT_PARENT")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def default_store() -> str:
    """Canonical rebase-records store path -- beside the validate ledger
    (ignored/validate-run-ledger.jsonl) at the parent root, so a lander finds
    both provenance stores in one place.

    CI_HUB_REBASE_STORE overrides outright (a fleet can pin ONE shared path);
    otherwise the path is anchored to the canonical `parent_root()`, NEVER to
    `__file__`, so every copy of the wrapper on a host writes and reads the same
    store. This closes per-checkout divergence ON ONE HOST. Cross-HOST sharing is
    the A2 follow-up (publish the non-derivable soft-green provenance to the
    shared validation-receipts branch and treat this JSONL as a pure cache);
    until then, `eligible`'s reconciliation against the LIVE open-PR population
    already surfaces a cross-host gap as UNACCOUNTED (a loud row) rather than
    silent absence -- invisible != nothing-pending.
    """
    env = os.environ.get("CI_HUB_REBASE_STORE")
    if env:
        return os.path.abspath(env)
    return os.path.join(parent_root(), "ignored", "rebase-records.jsonl")


_PARENT_ROOT = parent_root()
DEFAULT_STORE = default_store()

SCHEMA_VERSION = 2

# The resolving agent's judgement is a CLOSED set: an absent or unrecognised value
# is a REFUSAL, never a silent default to green.
RISK_RETAIN = "retained-soft-green"
RISK_VALIDATE = "needs-full-validate"
RISK_NA = "n/a"
VALID_JUDGEMENTS = {RISK_RETAIN, RISK_VALIDATE}

SOFT_ZERO_CONFLICT = "soft-green(zero-conflict)"
SOFT_RESOLVER_JUDGED = "soft-green(resolver-judged)"

# Legacy receipt-at-Z helpers retain a TRI-STATE for schema-1 records. New
# schema-2 records use the combined hard-green authority below.
# whole mechanism exists to kill invisible failures, so the query must NEVER
# conflate "authority answered: no receipt" (ABSENT, a real pending head) with
# "authority could not be reached" (UNKNOWN). Fail-closing an UNKNOWN to
# ABSENT/None makes a genuinely-eligible head VANISH silently -- the exact
# invisible-failure class reproduced inside the tool built to eliminate it.
RECEIPT_VALIDATED = "validated"   # authority dereferenced a clean full receipt.
RECEIPT_ABSENT = "absent"         # authority answered: no qualifying receipt yet.
RECEIPT_UNKNOWN = "unknown"       # authority unreachable/unparseable -> VISIBLE.

# The ONE receipt authority: `ci-hub validate-status` dereferences the validate
# ledger and answers "does exactly this SHA carry a clean full-validation receipt?"
# We never re-derive that verdict here (one verifier per authority) -- we call it.
_CI_HUB = os.path.join(_PARENT_ROOT, "ci-hub", "ci-hub")

# Reconciliation population: the lander lands HERMIT PRs, so the open-PR set is
# rrnewton/hermit by default (matches `ci-hub validate-status --repo`'s default).
# The population authority is GitHub PR state (shared/remote), NOT the local store.
DEFAULT_PR_REPO = "rrnewton/hermit"


class RebaseError(Exception):
    """Git / store / registry failure (an ERROR, exit 3 -- not a REFUSE)."""


class Refused(Exception):
    """A required judgement is absent or a queried head is not eligible (exit 2)."""


def _run(cmd: list[str], *, cwd: str | None = None,
         timeout: float = LOCAL_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def _short(sha: str) -> str:
    return sha[:12] if sha else "?"


def _is_hexish(rev: str) -> bool:
    r = (rev or "").strip().lower()
    return 7 <= len(r) <= 40 and all(c in "0123456789abcdef" for c in r)


def _is_full_sha(rev: str) -> bool:
    r = (rev or "").strip().lower()
    return len(r) == 40 and all(c in "0123456789abcdef" for c in r)


# --------------------------------------------------------------------------- #
# Pure verdict derivation -- the heart of the mechanism, git-free and testable. #
# --------------------------------------------------------------------------- #
def landable_reason(soft_green: str | None, base_clears_floor: bool,
                    base_unmet: list[dict], source_hard_green_present: bool,
                    result_hard_green_present: bool, result: str = "",
                    hard_green_state: str | None = None) -> str:
    """Explain the exact-hard OR inherited-soft landing decision."""
    if not base_clears_floor:
        unmet = ", ".join(_short(f["sha"]) for f in base_unmet) or "?"
        return (f"unlandable-base-below-floor: base predates floor(s) {unmet}; "
                "a clean rebase onto a sub-floor base still yields an unlandable "
                "Z -- rebase onto a base at/after every floor")
    if hard_green_state in {"HARD_RED", "DISAGREEMENT"}:
        return "hard-green-refused: an exact-SHA authority reports red/disagreement"
    if result_hard_green_present:
        return "landable via hard-green(exact-result); base clears all floors"
    if soft_green is not None and source_hard_green_present:
        return (f"landable via {soft_green} inherited from exact source hard green; "
                "post-land exact-result validation is mandatory")
    if soft_green is None:
        return ("resolver-flagged-needs-full-validate: no exact-result hard green; "
                "verify the resolved tip before landing")
    return ("no-hard-green-at-source: soft-green derivation has no exact-SHA hard "
            "green to inherit; validate X or Z before landing")


def derive_verdict(conflicts: list[str], risk_judgement: str | None,
                   rationale: str | None, base_clears_floor: bool,
                   base_unmet: list[dict], source_hard_green_present: bool = True,
                   result_hard_green_present: bool = False,
                   result: str = "") -> dict:
    """Map conflict judgement + exact evidence to hard/soft landability.

    RAISES Refused when conflicts exist without a valid judgement + rationale --
    an absent judgement must NEVER read as approval. Returns the derived fields
    Exact hard green at Z is sufficient. Otherwise Z may inherit a soft-green
    prior only when X was hard green and the conflict judgement retains it.
    """
    if conflicts:
        rj = (risk_judgement or "").strip()
        if rj not in VALID_JUDGEMENTS:
            raise Refused(
                f"conflicted rebase ({len(conflicts)} file(s): "
                f"{', '.join(conflicts)}) requires --risk-judgement in "
                f"{{{RISK_RETAIN}|{RISK_VALIDATE}}}; got {risk_judgement!r}. "
                "An absent judgement is NOT approval -- the resolving agent must "
                "state whether the resolution is low-risk enough to KEEP the "
                "soft green. Refusing to record an eligible head.")
        if not (rationale or "").strip():
            raise Refused(
                f"--risk-judgement {rj} requires a non-empty --rationale "
                "justifying the judgement; refusing to record a bare verdict.")
        if rj == RISK_RETAIN:
            soft_green = SOFT_RESOLVER_JUDGED
        else:  # RISK_VALIDATE
            soft_green = None  # resolver demands a full tip validate; not green.
        judgement = rj
    else:
        # Zero conflicts -> mechanical high-confidence prior. Any passed judgement
        # is irrelevant here; the confidence LEVEL records how it was earned.
        soft_green = SOFT_ZERO_CONFLICT
        judgement = RISK_NA

    inherited_soft = soft_green is not None and source_hard_green_present
    landable = base_clears_floor and (result_hard_green_present or inherited_soft)
    eligibility_kind = None
    if landable:
        eligibility_kind = ("hard-green(exact-result)" if result_hard_green_present
                            else "soft-green(inherited-source)")
    return {
        "risk_judgement": judgement,
        "soft_green": soft_green,
        "base_clears_floor": base_clears_floor,
        "base_unmet_floors": [
            {"sha": f["sha"], "kind": f["kind"], "field": f["field"]}
            for f in base_unmet],
        "landable": landable,
        "eligibility_kind": eligibility_kind,
        "landable_reason": landable_reason(soft_green, base_clears_floor,
                                           base_unmet, source_hard_green_present,
                                           result_hard_green_present, result),
    }


# --------------------------------------------------------------------------- #
# receipt at Z -- the ONE receipt authority, dereferenced (never re-derived)   #
# --------------------------------------------------------------------------- #
def receipt_identity(report: dict | None, result: str) -> dict | None:
    """Pure map from a `ci-hub validate-status --json` report to a receipt identity
    bound to Z, or None. A receipt has no opaque id; its IDENTITY is what it
    verified (Proxy-Binding: carry the condition with the value) -- the exact SHA
    plus the qualifying record's profile/selection/timestamp/host/slot. Anything
    short of a VALIDATED verdict with a qualifying record is None (no receipt)."""
    if not report or report.get("verdict") != "VALIDATED":
        return None
    nq = report.get("newest_qualifying")
    if not nq or (report.get("qualifying_count") or 0) < 1:
        return None
    return {
        "sha": result,
        "verdict": "VALIDATED",
        "qualifying_count": report.get("qualifying_count"),
        "profile": nq.get("profile"),
        "selection_mode": nq.get("selection_mode"),
        "result": nq.get("result"),
        "finished_at": nq.get("finished_at"),
        "slot": nq.get("slot"),
        "host": nq.get("host"),
    }


def receipt_status(result: str) -> dict:
    """Dereference the ONE receipt authority at the PUSHED head Z and return a
    TRI-STATE: {status: validated|absent|unknown, identity: {...}|None, detail}.

    The load-bearing distinction (this is the reopened finding): an authority
    FAILURE is UNKNOWN, never ABSENT. `validate-status --json` on a full sha
    ALWAYS emits a parseable JSON verdict (VALIDATED / NOT-VALIDATED / ...) and
    exits nonzero for not-validated, so:
      - JSON parses  -> the authority ANSWERED: VALIDATED (with a qualifying
                        record) => `validated`; anything else => `absent`.
      - could not run / stdout does not parse -> the authority could NOT be
                        reached => `unknown` (VISIBLE; never read as absent, never
                        landable). This is what stops an eligible head vanishing.
    """
    if not result or not _is_hexish(result):
        return {"status": RECEIPT_UNKNOWN, "identity": None,
                "detail": f"result {result!r} is not a 7-40 hex sha"}
    try:
        cp = _run([_CI_HUB, "validate-status", "--sha", result, "--json"],
                  timeout=NETWORK_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as err:
        return {"status": RECEIPT_UNKNOWN, "identity": None,
                "detail": f"validate-status could not be invoked: {err}"}
    try:
        report = json.loads(cp.stdout)
    except ValueError:
        return {"status": RECEIPT_UNKNOWN, "identity": None,
                "detail": ("validate-status emitted no parseable JSON "
                           f"(rc={cp.returncode}); authority NOT reached")}
    ident = receipt_identity(report, result)
    if ident is not None:
        return {"status": RECEIPT_VALIDATED, "identity": ident, "detail": ""}
    return {"status": RECEIPT_ABSENT, "identity": None,
            "detail": (f"authority answered verdict={report.get('verdict')!r}, "
                       "qualifying_count="
                       f"{report.get('qualifying_count')}: no receipt at Z yet")}


def receipt_at(result: str) -> dict | None:
    """Record-time snapshot convenience: the receipt IDENTITY at Z, or None.
    Delegates to `receipt_status`; None here folds absent+unknown together, which
    is acceptable ONLY for the frozen snapshot (null is re-checked live by
    `eligible`, which uses the full tri-state and never lets UNKNOWN vanish)."""
    return receipt_status(result)["identity"]


# --------------------------------------------------------------------------- #
# Combined exact-SHA hard-green authority (local full OR hosted full)           #
# --------------------------------------------------------------------------- #
def hard_green_status(revision: str) -> dict:
    """Dereference the shared exact-SHA authority without re-deriving it here."""
    if not revision or not _is_full_sha(revision):
        return {
            "schema_version": 1,
            "sha": revision,
            "verdict": "NO_RESULT",
            "exit_code": hard_green_authority.EXIT_NO_RESULT,
            "reason": "revision is not a commit SHA",
            "passing_authorities": [],
            "sources": {},
        }
    return hard_green_authority.status(revision, repo=DEFAULT_PR_REPO)


def hard_green_at(revision: str) -> dict | None:
    """Return a self-describing hard-green evidence record, or None."""
    report = hard_green_status(revision)
    return report if _is_hard_green(report, revision) else None


def _is_hard_green(evidence: dict | None, expected_sha: str | None = None) -> bool:
    return bool(evidence and evidence.get("verdict") == "HARD_GREEN"
                and (expected_sha is None or evidence.get("sha") == expected_sha))


def _legacy_result_hard_green(rec: dict, result: str) -> dict | None:
    """Schema-1 snapshots are caches, never authority.

    They are deliberately not upgraded without a live verifier: a planted JSONL
    row must not become an authorization merely because it is well-shaped.
    """
    return None


# --------------------------------------------------------------------------- #
# Store I/O                                                                     #
# --------------------------------------------------------------------------- #
def append_record(store: str, record: dict) -> None:
    """Append one record under an exclusive advisory lock. The land-lock covers
    the merge, not this JSONL; concurrent producers (rebase front + resolvers)
    append here, so serialise writes to keep a partial line from interleaving."""
    try:
        os.makedirs(os.path.dirname(store), exist_ok=True)
        with open(store, "a", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass  # advisory lock unavailable (some network FS); O_APPEND still
                      # gives atomic small writes on POSIX -- best-effort either way.
            try:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
                fh.flush()
            finally:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except OSError as err:
        raise RebaseError(f"cannot append to rebase store {store}: {err}") from err


def load_records(store: str) -> list[dict]:
    """Read every record, newest-appended last. Malformed lines are skipped."""
    if not os.path.exists(store):
        return []
    out = []
    try:
        with open(store, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError as err:
        raise RebaseError(f"cannot read rebase store {store}: {err}") from err
    return out


def latest_by_result(records: list[dict]) -> dict[str, dict]:
    """Latest record wins per result SHA Z (append-only; last line is newest).

    Disambiguation by latest mirrors the merge-gate keying: a re-recorded Z (e.g.
    a resolver upgrading needs-full-validate -> retained after a tip validate)
    supersedes the earlier attempt.
    """
    latest: dict[str, dict] = {}
    for rec in records:
        z = rec.get("result")
        if z:
            latest[z] = rec
    return latest


# --------------------------------------------------------------------------- #
# Git helpers (only the `rebase` producer path touches a working tree)         #
# --------------------------------------------------------------------------- #
def _git(checkout: str, args: list[str], *,
         timeout: float = LOCAL_TIMEOUT) -> subprocess.CompletedProcess:
    return _run(["git", "-C", checkout, *args], timeout=timeout)


def resolve_rev(checkout: str, rev: str) -> str:
    cp = _git(checkout, ["rev-parse", "--verify", "-q", f"{rev}^{{commit}}"])
    if cp.returncode != 0:
        raise RebaseError(f"cannot resolve revision {rev!r} in {checkout}")
    return cp.stdout.strip()


def fetch(checkout: str) -> None:
    cmd = ["git", "-C", checkout, "fetch", "--quiet", "origin"]
    if gate_floors._on_path("with-proxy"):
        cmd = ["with-proxy", *cmd]
    cp = _run(cmd, timeout=NETWORK_TIMEOUT)
    if cp.returncode != 0:
        raise RebaseError(f"git fetch origin failed: {(cp.stderr or cp.stdout).strip()}")


def newest_green_sha(checkout: str, branch: str, no_fetch: bool) -> str:
    """Resolve --onto newest-green via the ledger query (trust the ledger)."""
    ci_hub = os.path.join(_PARENT_ROOT, "ci-hub", "ci-hub")
    cmd = [ci_hub, "newest-green", "--branch", branch, "--json"]
    if no_fetch:
        cmd.append("--no-fetch")
    cp = _run(cmd, timeout=NETWORK_TIMEOUT)
    if cp.returncode != 0:
        raise RebaseError(
            "ci-hub newest-green found no qualifying green base (rc "
            f"{cp.returncode}): {(cp.stdout or cp.stderr).strip()}")
    try:
        rep = json.loads(cp.stdout)
        sha = rep["green"]["sha"]
    except (ValueError, KeyError) as err:
        raise RebaseError(f"cannot parse ci-hub newest-green --json: {err}") from err
    if not sha:
        raise RebaseError("ci-hub newest-green returned an empty green sha")
    return sha


# --------------------------------------------------------------------------- #
# base-floor status                                                            #
# --------------------------------------------------------------------------- #
def base_floor_status(registry: str, checkout: str, base: str) -> dict:
    """Does the base Y clear every rebase-base floor? (delegates to gate_floors)."""
    floors = gate_floors.load_floors(registry)
    return gate_floors.clears_all(floors, checkout, base)


# --------------------------------------------------------------------------- #
# open-PR population -- the reconciliation authority (dereferenced, not a file) #
# --------------------------------------------------------------------------- #
def open_pushed_prs(pr_repo: str) -> list[dict]:
    """Dereference the LIVE set of open pushed PRs for `pr_repo` via `gh`. This is
    the population authority for reconciliation: a query that lists only store
    records cannot tell "quiet because landed" from "quiet because never written".
    Reconciling the store against THIS set is what makes invisible != nothing-
    pending. A gh/parse failure RAISES (visible ERROR) -- it must never degrade
    silently into a store-only answer that looks complete."""
    cmd = ["gh", "pr", "list", "--repo", pr_repo, "--state", "open",
           "--limit", "500", "--json", "number,headRefOid,headRefName,url,state"]
    if gate_floors._on_path("with-proxy"):
        cmd = ["with-proxy", *cmd]
    try:
        cp = _run(cmd, timeout=NETWORK_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as err:
        raise RebaseError(
            f"gh pr list --repo {pr_repo} could not be invoked for "
            f"reconciliation: {err} (pass --no-reconcile for store-only, which "
            "CANNOT assert invisible != nothing-pending)") from err
    if cp.returncode != 0:
        raise RebaseError(
            f"gh pr list --repo {pr_repo} failed (rc {cp.returncode}): "
            f"{(cp.stderr or cp.stdout).strip()}")
    try:
        prs = json.loads(cp.stdout)
    except ValueError as err:
        raise RebaseError(f"cannot parse gh pr list JSON: {err}") from err
    return [p for p in prs if p.get("headRefOid")]


# --------------------------------------------------------------------------- #
# A2: durable, cross-host soft-green provenance on the shared receipts branch   #
# --------------------------------------------------------------------------- #
# The store's irreducibly non-derivable data are HOW the soft-green was earned
# and WHICH exact-source hard-green record it inherited. A machine-local JSONL
# cannot carry them across hosts
# (dbi's finding: two hosts each write their own store, each lander sees the
# other's landable heads as UNTRACKED forever). Publishing just this datum,
# content-addressed and keyed by Z, to the SAME validation-receipts branch the
# validate receipts already use makes the JSONL a pure cache: any host
# dereferences the durable provenance. Content-addressing (path carries the
# body's digest) makes a judgement UPGRADE (needs-full-validate -> retained after
# a tip validate) a NEW immutable file under the same Z/ prefix -- never a
# rewrite, matching the ledger's latest-per-Z semantics without mutable state.
RECEIPT_REPO = publish_receipt.RECEIPT_REPO
RECEIPT_BRANCH = publish_receipt.RECEIPT_BRANCH


def provenance_body(rec: dict) -> bytes:
    """Canonicalise the non-derivable soft-green provenance for a record.

    Carries ONLY what another host cannot re-derive (Proxy-Binding: the value
    records its own conditions). Floor status and the receipt at Z are omitted on
    purpose -- they are live-authority reads, and freezing them here would let a
    stale copy assert landability the live checks must own."""
    prov = {
        "schema_version": SCHEMA_VERSION,
        "source_rev": rec.get("source_rev"),
        "base": rec.get("base"),
        "result": rec.get("result"),
        "conflicts": rec.get("conflicts", []),
        "soft_green": rec.get("soft_green"),
        "risk_judgement": rec.get("risk_judgement"),
        "rationale": rec.get("rationale", ""),
        "resolver": rec.get("resolver", ""),
        "recorded_utc": rec.get("recorded_utc"),
        "source_hard_green": rec.get("source_hard_green"),
        "result_hard_green": rec.get("result_hard_green"),
    }
    return json.dumps(prov, sort_keys=True, separators=(",", ":")).encode()


def provenance_path(z: str, digest: str) -> str:
    return f"rebase-provenance/{z}/{digest}.json"


def durable_hard_green(evidence: dict | None, expected_sha: str) -> bool:
    """Whether a frozen hard-green pointer is independently dereferenceable.

    Machine-local ledger rows are intentionally not publishable as cross-host
    authority. Hosted evidence carries both exact run and job IDs for both lanes;
    immutable local receipts may carry their commit/path/digest tuple.
    """
    if not _is_hard_green(evidence, expected_sha):
        return False
    sources = evidence.get("sources") or {}
    local = sources.get("local") or {}
    immutable = local.get("immutable_receipt") or {}
    if all(immutable.get(key) for key in ("commit", "path", "sha256")):
        return True
    github = sources.get("github") or {}
    lanes = github.get("lanes") or []
    return (len(lanes) == 2
            and all(lane.get("state") == "passed"
                    and lane.get("sha") == expected_sha
                    and lane.get("run_id") and lane.get("job_id")
                    for lane in lanes))


def publish_provenance(rec: dict, *, repo: str = RECEIPT_REPO,
                       branch: str = RECEIPT_BRANCH) -> dict:
    """Publish one record's soft-green provenance to the shared receipts branch.
    Content-addressed + immutable (publish_receipt.publish refuses a same-path
    different-body write). Returns {path, digest, commit}. Only a soft-green
    result with a real Z is publishable (a null soft-green carries no durable
    claim; a conflict-abort record has no Z)."""
    z = rec.get("result")
    if not z or not _is_hexish(z):
        raise RebaseError(f"cannot publish provenance: result {z!r} is not a sha")
    if rec.get("soft_green") is None:
        raise RebaseError(
            "cannot publish provenance for a null soft-green (no durable claim to "
            "make); resolver said needs-full-validate or the rebase was aborted")
    if not durable_hard_green(rec.get("source_hard_green"), rec.get("source_rev", "")):
        raise RebaseError(
            "cannot publish provenance: source hard green is only machine-local or "
            "malformed. Publish an immutable local receipt pointer or carry exact "
            "hosted run+job IDs for both lanes.")
    import hashlib
    body = provenance_body(rec)
    digest = hashlib.sha256(body).hexdigest()
    path = provenance_path(z, digest)
    commit = publish_receipt.publish(repo, branch, path, body)
    return {"path": path, "digest": digest, "commit": commit}


def fetch_provenance(z: str, *, repo: str = RECEIPT_REPO,
                     branch: str = RECEIPT_BRANCH) -> dict | None:
    """Dereference durable provenance for Z from the shared branch, or None.

    Reads every content-addressed file under rebase-provenance/{Z}/ and returns
    the latest by recorded_utc (mirrors the JSONL latest-per-Z, so a published
    judgement upgrade wins). A missing dir / unreachable branch returns None (the
    caller renders that as its own visible state, never as a silent landable)."""
    if not z or not _is_hexish(z):
        return None
    listing = publish_receipt.gh(
        ["api", f"repos/{repo}/contents/rebase-provenance/{z}?ref={branch}"],
        check=False)
    if listing.returncode != 0:
        return None
    try:
        entries = json.loads(listing.stdout)
    except ValueError:
        return None
    if not isinstance(entries, list):
        return None
    provs: list[dict] = []
    for ent in entries:
        if not isinstance(ent, dict) or ent.get("type") != "file":
            continue
        blob = publish_receipt.gh(
            ["api", f"repos/{repo}/contents/{ent.get('path')}?ref={branch}"],
            check=False)
        if blob.returncode != 0:
            continue
        try:
            import base64
            content = base64.b64decode(json.loads(blob.stdout)["content"])
            provs.append(json.loads(content))
        except (ValueError, KeyError):
            continue
    if not provs:
        return None
    return max(provs, key=lambda p: p.get("recorded_utc") or "")


def record_from_provenance(prov: dict) -> dict:
    """Adapt a durable provenance blob into a record shape `_classify_row` accepts.
    Carries ONLY the non-derivable derivation plus source/base/result; the base
    floor and both exact-SHA authorities are re-derived LIVE. Durability never
    grants landability; it supplies only the derivation a fresh checkout cannot
    reconstruct."""
    return {
        "source_rev": prov.get("source_rev"),
        "base": prov.get("base"),
        "result": prov.get("result"),
        "conflicts": prov.get("conflicts", []),
        "resolver": prov.get("resolver", ""),
        "rationale": prov.get("rationale", ""),
        "risk_judgement": prov.get("risk_judgement"),
        "soft_green": prov.get("soft_green"),
        "recorded_utc": prov.get("recorded_utc"),
        "source_hard_green": (prov.get("source_hard_green")
                              if durable_hard_green(prov.get("source_hard_green"),
                                                    prov.get("source_rev", ""))
                              else None),
        "result_hard_green": prov.get("result_hard_green"),
        "receipt_at_Z": None,          # schema-1 compatibility only
        "base_clears_floor": False,    # re-derived live (recheck_floor default on)
        "base_unmet_floors": [],
        "landable": False,
        "provenance_source": "durable-shared-branch",
    }


# --------------------------------------------------------------------------- #
# record                                                                        #
# --------------------------------------------------------------------------- #
def build_record(source: str, base: str, result: str, conflicts: list[str],
                 resolver: str | None, verdict: dict, now_utc: str,
                 source_hard_green: dict | None = None,
                 result_hard_green: dict | None = None,
                 receipt: dict | None = None) -> dict:
    rec = {
        "schema_version": SCHEMA_VERSION,
        "recorded_utc": now_utc,
        "source_rev": source,
        "base": base,
        "result": result,
        "conflicts": conflicts,
        "resolver": resolver or "",
        "rationale": "",
        "source_hard_green": source_hard_green,
        "result_hard_green": result_hard_green,
        # Retained for schema-1 readers. New readers use result_hard_green,
        # which can represent either the local or hosted exact-SHA authority.
        "receipt_at_Z": receipt,
    }
    rec.update(verdict)
    return rec


def do_record(args) -> int:
    for label, rev in (("--source", args.source), ("--base", args.base),
                       ("--result", args.result)):
        if not _is_full_sha(rev):
            raise RebaseError(f"{label} {rev!r} is not a full 40-hex commit sha")
    conflicts = parse_conflicts(args.conflicts)
    if not conflicts:
        raise Refused(
            "`record --conflicts none` cannot attest a clean rebase: only the "
            "wrapper-owned `rebase` operation observes that fact. Use `rebase` for "
            "zero-conflict inheritance; use `record` only after resolving the "
            "non-empty conflict list with an explicit risk judgement.")
    # base-floor status is the load-bearing "carry Y" half; refresh unless offline.
    if not args.no_fetch:
        try:
            fetch(args.repo_checkout)
        except RebaseError:
            pass  # offline is not fatal for a purely-local ancestry check
    fstatus = base_floor_status(args.registry, args.repo_checkout, args.base)
    # Bind exact hard-green evidence at both ends. X is what a soft Z inherits;
    # exact hard green at Z independently authorizes the resolved result.
    source_status = ({"verdict": "NO_RESULT"} if args.no_hard_green_check
                     else hard_green_status(args.source))
    result_status = ({"verdict": "NO_RESULT"} if args.no_hard_green_check
                     else hard_green_status(args.result))
    source_hard = source_status if _is_hard_green(source_status, args.source) else None
    result_hard = result_status if _is_hard_green(result_status, args.result) else None
    verdict = derive_verdict(conflicts, args.risk_judgement, args.rationale,
                             fstatus["ok"], fstatus["unmet"],
                             source_hard_green_present=_is_hard_green(source_hard),
                             result_hard_green_present=_is_hard_green(result_hard),
                             result=args.result)
    if (source_status.get("verdict") in {"HARD_RED", "DISAGREEMENT"}
            or result_status.get("verdict") in {"HARD_RED", "DISAGREEMENT"}):
        verdict["landable"] = False
        verdict["eligibility_kind"] = None
        verdict["landable_reason"] = (
            "hard-green-refused: an exact-SHA authority reports red/disagreement")
    now = utc_now()
    rec = build_record(args.source, args.base, args.result, conflicts,
                       args.resolver, verdict, now,
                       source_hard_green=source_hard,
                       result_hard_green=result_hard)
    rec["rationale"] = (args.rationale or "").strip()
    rec["source_hard_green_state"] = source_status.get("verdict")
    rec["result_hard_green_state"] = result_status.get("verdict")
    append_record(args.store, rec)
    maybe_publish_provenance(args, rec)
    emit_record(rec, args.json, action="RECORDED")
    return EXIT_OK


def maybe_publish_provenance(args, rec: dict) -> None:
    """Publish durable provenance iff asked AND there is a soft-green claim to make.
    A publish failure is a visible ERROR here (the datum is the whole point of A2)
    -- never swallowed into a store-only success that looks complete."""
    if not getattr(args, "publish_provenance", False):
        return
    if rec.get("soft_green") is None or not rec.get("result"):
        return  # nothing durable to claim (aborted / needs-full-validate)
    info = publish_provenance(rec)
    rec["provenance"] = info
    if not getattr(args, "json", False):
        print(f"  durable provenance published: {RECEIPT_REPO}@"
              f"{_short(info['commit'])}:{info['path']}", file=sys.stderr)


def parse_conflicts(raw: str | None) -> list[str]:
    """`none`/empty -> []; else comma/space-separated file list."""
    if raw is None:
        return []
    r = raw.strip()
    if r == "" or r.lower() == "none":
        return []
    return [p for p in (x.strip() for x in r.replace(",", " ").split()) if p]


def utc_now() -> str:
    # datetime.now(UTC) is disallowed in some sandboxes; use the shell clock.
    cp = _run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"])
    return cp.stdout.strip() if cp.returncode == 0 else "unknown"


# --------------------------------------------------------------------------- #
# rebase (owns the mechanical rebase)                                          #
# --------------------------------------------------------------------------- #
def do_rebase(args) -> int:
    checkout = args.repo_checkout
    if not args.no_fetch:
        fetch(checkout)
    if args.onto == "newest-green":
        base = newest_green_sha(checkout, args.branch, args.no_fetch)
    else:
        base = resolve_rev(checkout, args.onto)
    source = resolve_rev(checkout, args.source)
    source_hard = None if args.no_hard_green_check else hard_green_at(source)
    if not _is_hard_green(source_hard):
        raise Refused(
            f"source {_short(source)} has no exact-SHA hard green to inherit; "
            "run full local validation or hosted portable+privileged first")

    # Work on a detached, PID-unique wip ref so we never clobber a caller's branch.
    wip = f"_rebasewrap_{os.getpid()}"
    _git(checkout, ["branch", "-D", wip])  # best-effort cleanup of a stale wip
    cp = _git(checkout, ["checkout", "-q", "--detach", source])
    if cp.returncode != 0:
        raise RebaseError(f"cannot detach at source {source}: "
                          f"{(cp.stderr or cp.stdout).strip()}")
    cp = _run(["git", "-C", checkout, "-c", "rebase.autoStash=false",
               "rebase", base], timeout=NETWORK_TIMEOUT)
    if cp.returncode == 0:
        result = resolve_rev(checkout, "HEAD")
        conflicts: list[str] = []
    else:
        conflicts = conflicted_files(checkout)
        _git(checkout, ["rebase", "--abort"])
        if not conflicts:
            _git(checkout, ["checkout", "-q", "--detach", base])
            raise RebaseError(
                "rebase stopped without recorded conflicts: "
                f"{(cp.stderr or cp.stdout).strip()}")

    if conflicts:
        # Return the tree to a clean detached base and REFUSE to soft-green.
        _git(checkout, ["checkout", "-q", "--detach", base])
        # Record a non-eligible attempt so history shows it; soft_green stays null.
        fstatus = base_floor_status(args.registry, checkout, base)
        rec = build_record(source, base, "", conflicts, args.resolver, {
            "risk_judgement": "",  # absent: the resolver must supply one
            "soft_green": None,
            "base_clears_floor": fstatus["ok"],
            "base_unmet_floors": [
                {"sha": f["sha"], "kind": f["kind"], "field": f["field"]}
                for f in fstatus["unmet"]],
            "landable": False,
            "landable_reason": (
                "CONFLICTS: resolver must resolve, then run `record --source "
                f"{_short(source)} --base {_short(base)} --result <Z> --conflicts "
                f"'{','.join(conflicts)}' --risk-judgement {RISK_RETAIN}|"
                f"{RISK_VALIDATE} --rationale ...`"),
        }, utc_now(), source_hard_green=source_hard)  # no result yet
        append_record(args.store, rec)
        emit_record(rec, args.json, action="REFUSED")
        msg = (f"CONFLICTS ({len(conflicts)}): {', '.join(conflicts)}. Rebase "
               "aborted; tree clean. Soft-green REFUSED -- resolver must resolve "
               "and record a risk judgement.")
        if not args.json:
            print(msg, file=sys.stderr)
        return EXIT_REFUSED

    # Zero conflicts: mechanical soft-green(zero-conflict).
    fstatus = base_floor_status(args.registry, checkout, base)
    if args.push:
        push_ref = args.push_ref or f"refs/heads/rebasewrap/{_short(source)}-on-{_short(base)}"
        cmd = ["git", "-C", checkout, "push", "--force-with-lease",
               "origin", f"{result}:{push_ref}"]
        if gate_floors._on_path("with-proxy"):
            cmd = ["with-proxy", *cmd]
        pcp = _run(cmd, timeout=NETWORK_TIMEOUT)
        if pcp.returncode != 0:
            _git(checkout, ["checkout", "-q", "--detach", base])
            raise RebaseError(f"push failed: {(pcp.stderr or pcp.stdout).strip()}")
    # A clean rebase inherits X's hard green immediately. If Z already has its
    # own exact hard green, record the stronger basis too; it is an upgrade, not
    # a prerequisite for speculative landing.
    result_status = ({"verdict": "NO_RESULT"} if args.no_hard_green_check
                     else hard_green_status(result))
    result_hard = result_status if _is_hard_green(result_status, result) else None
    verdict = derive_verdict([], None, None, fstatus["ok"], fstatus["unmet"],
                             source_hard_green_present=True,
                             result_hard_green_present=_is_hard_green(result_hard),
                             result=result)
    if result_status.get("verdict") in {"HARD_RED", "DISAGREEMENT"}:
        verdict["landable"] = False
        verdict["eligibility_kind"] = None
        verdict["landable_reason"] = (
            "hard-green-refused: exact result has a red/disagreement answer")
    rec = build_record(source, base, result, [], args.resolver, verdict,
                       utc_now(), source_hard_green=source_hard,
                       result_hard_green=result_hard)
    rec["source_hard_green_state"] = "HARD_GREEN"
    rec["result_hard_green_state"] = result_status.get("verdict")
    append_record(args.store, rec)
    maybe_publish_provenance(args, rec)
    # Leave the tree on a clean detached base (stateless, like union-rebase.sh).
    _git(checkout, ["checkout", "-q", "--detach", base])
    emit_record(rec, args.json, action="RECORDED")
    if not args.json:
        print(rec["landable_reason"], file=sys.stderr)
    return EXIT_OK


def conflicted_files(checkout: str) -> list[str]:
    cp = _git(checkout, ["diff", "--name-only", "--diff-filter=U"])
    return [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# eligible (consumer query for the lander)                                     #
# --------------------------------------------------------------------------- #
def _refresh_hard_green(revision: str, frozen: dict | None,
                        recheck: bool) -> tuple[dict | None, str]:
    """Refresh evidence; a frozen JSON object never authorizes on its own."""
    if not recheck:
        return None, "NO_RESULT(unverified-cache)"
    live = hard_green_status(revision)
    verdict = str(live.get("verdict") or "NO_RESULT")
    if verdict == "HARD_GREEN" and _is_hard_green(live, revision):
        return live, verdict
    if verdict == "HARD_GREEN":
        return None, "ERROR(mismatched-hard-green-sha)"
    if verdict in {"HARD_RED", "DISAGREEMENT"}:
        return None, verdict
    return None, verdict


def _classify_row(z: str, rec: dict, floors, args) -> dict:
    """Compute live exact-hard OR inherited-soft landability."""
    soft = rec.get("soft_green")
    base_ok = rec.get("base_clears_floor", False)
    unmet = rec.get("base_unmet_floors", [])
    if floors is not None and rec.get("base"):
        live = gate_floors.clears_all(floors, args.repo_checkout, rec["base"])
        base_ok = live["ok"]
        unmet = [{"sha": f["sha"], "kind": f["kind"], "field": f["field"]}
                 for f in live["unmet"]]
    source = rec.get("source_rev") or ""
    source_frozen = rec.get("source_hard_green")
    result_frozen = rec.get("result_hard_green") or _legacy_result_hard_green(rec, z)
    source_hard, source_state = _refresh_hard_green(
        source, source_frozen, args.recheck_source_hard_green)
    result_hard, result_state = _refresh_hard_green(
        z, result_frozen, args.recheck_result_hard_green)
    source_present = _is_hard_green(source_hard)
    result_present = _is_hard_green(result_hard)
    inherited = soft is not None and source_present
    authority_conflict = (source_state in {"HARD_RED", "DISAGREEMENT"}
                          or result_state in {"HARD_RED", "DISAGREEMENT"})
    landable = base_ok and not authority_conflict and (result_present or inherited)
    eligibility_kind = None
    if landable:
        eligibility_kind = ("hard-green(exact-result)" if result_present
                            else "soft-green(inherited-source)")
    return {**rec, "base_clears_floor": base_ok, "base_unmet_floors": unmet,
            "source_hard_green": source_hard,
            "source_hard_green_state": source_state,
            "result_hard_green": result_hard,
            "result_hard_green_state": result_state,
            "landable": landable, "eligibility_kind": eligibility_kind,
            "landable_reason": landable_reason(soft, base_ok, unmet,
                                               source_present, result_present, z,
                                               ("DISAGREEMENT" if authority_conflict
                                                else source_state))}


def _bucket_of(row: dict) -> str:
    """Which reconciliation bucket a classified record row belongs to."""
    if row["landable"]:
        return "eligible"
    if (row.get("source_hard_green_state") in {"HARD_RED", "DISAGREEMENT"}
            or row.get("result_hard_green_state") in {"HARD_RED", "DISAGREEMENT"}):
        return "hard-green-conflict"
    if (row.get("source_hard_green_state") in {"NO_RESULT", "ERROR"}
            and row.get("result_hard_green_state") in {"NO_RESULT", "ERROR"}):
        return "hard-green-unknown"
    return "disqualified"


def do_eligible(args) -> int:
    records = load_records(args.store)
    latest = latest_by_result(records)
    # Re-check the base floor live by default: a floor added AFTER recording must
    # demote a stale soft-green. Trust the query, not the frozen verdict.
    floors = None
    if args.recheck_floor:
        try:
            floors = gate_floors.load_floors(args.registry)
        except gate_floors.FloorError as err:
            raise RebaseError(str(err)) from err

    # Single-head targeted check (the lander's `eligible --result Z`): no
    # population reconciliation, no gh -- just this exact head's live verdict.
    if args.result:
        rec = latest.get(args.result)
        if rec is None:
            out = {"result": args.result, "eligible": False,
                   "reason": "no rebase record for this result sha"}
            _print_query(out, args.json)
            raise Refused(f"no rebase record for result {_short(args.result)}")
        match = _classify_row(args.result, rec, floors, args)
        out = {"result": match["result"], "source_rev": match.get("source_rev"),
               "base": match.get("base"), "eligible": match["landable"],
               "soft_green": match.get("soft_green"),
               "eligibility_kind": match.get("eligibility_kind"),
               "source_hard_green_state": match.get("source_hard_green_state"),
               "result_hard_green_state": match.get("result_hard_green_state"),
               "reason": match.get("landable_reason")}
        _print_query(out, args.json)
        return EXIT_OK if match["landable"] else EXIT_REFUSED

    # List mode: RECONCILE the store against the live open-PR population so
    # "invisible" != "nothing pending" (default; --no-reconcile for store-only).
    prs = None if args.no_reconcile else open_pushed_prs(args.pr_repo)

    buckets: dict[str, list] = {"eligible": [], "hard-green-unknown": [],
                                "hard-green-conflict": [], "disqualified": [],
                                "unaccounted": []}
    recorded_not_open: list[str] = []

    if prs is not None:
        # Population = open pushed PRs. Every PR head is accounted for exactly once;
        # a PR with no store record is UNACCOUNTED (pushed by a path that never
        # called the wrapper) -- surfaced, never silently omitted.
        for pr in prs:
            z = pr.get("headRefOid")
            rec = latest.get(z)
            if rec is None and args.durable_provenance:
                # A2 cross-host recovery: this host's local cache has no record,
                # but another host may have published the soft-green provenance to
                # the shared branch. Dereference it and classify as if local --
                # the base floor and exact-SHA hard evidence are STILL re-checked
                # live, so durability supplies only derivation provenance.
                prov = fetch_provenance(z)
                if prov is not None:
                    rec = record_from_provenance(prov)
            if rec is None:
                buckets["unaccounted"].append({
                    "result": z, "number": pr.get("number"),
                    "headRefName": pr.get("headRefName"), "url": pr.get("url"),
                    "reason": ("open pushed PR with NO rebase record -- pushed by a "
                               "path that does not call the wrapper (union-rebase.sh"
                               "/land-pr.sh/manual), or provenance not published to "
                               "the shared branch. UNACCOUNTED: invisible != "
                               "nothing-pending.")})
                continue
            row = _classify_row(z, rec, floors, args)
            row["pr_number"] = pr.get("number")
            row["url"] = pr.get("url")
            buckets[_bucket_of(row)].append(row)
        pr_heads = {pr.get("headRefOid") for pr in prs}
        # Store records whose Z is no open PR's head: superseded / force-pushed
        # away (a re-rebase mints a new Z). Excluded from the population, noted.
        recorded_not_open = [z for z in latest if z not in pr_heads]
    else:
        # Offline / --no-reconcile: store-only, still tri-state so nothing vanishes,
        # but this mode CANNOT assert invisible != nothing-pending.
        for z, rec in latest.items():
            row = _classify_row(z, rec, floors, args)
            buckets[_bucket_of(row)].append(row)

    if args.source:
        for k in buckets:
            if k != "unaccounted":
                buckets[k] = [r for r in buckets[k]
                              if r.get("source_rev") == args.source]

    summary = {k: len(v) for k, v in buckets.items()}
    summary["recorded_not_open"] = len(recorded_not_open)
    if prs is not None:
        summary["open_pushed_prs"] = len(prs)

    if args.json:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "reconciled": prs is not None,
            "pr_repo": args.pr_repo if prs is not None else None,
            "summary": summary,
            "eligible": buckets["eligible"],
            "hard_green_unknown": buckets["hard-green-unknown"],
            "hard_green_conflict": buckets["hard-green-conflict"],
            "unaccounted": buckets["unaccounted"],
            "disqualified": buckets["disqualified"],
            "recorded_not_open": recorded_not_open,
            "total_records": len(latest),
        }, indent=2))
    else:
        _print_reconciliation(buckets, summary, recorded_not_open, prs is not None)
    return EXIT_OK


def _print_reconciliation(buckets, summary, recorded_not_open, reconciled) -> None:
    if reconciled:
        print(f"RECONCILED against {summary.get('open_pushed_prs', 0)} open pushed "
              f"PR(s): eligible={summary['eligible']} "
              f"hard-green-unknown={summary['hard-green-unknown']} "
              f"hard-green-conflict={summary['hard-green-conflict']} "
              f"unaccounted={summary['unaccounted']} "
              f"disqualified={summary['disqualified']}")
    else:
        print("STORE-ONLY (not reconciled -- cannot assert invisible != nothing-"
              f"pending): eligible={summary['eligible']} "
              f"hard-green-unknown={summary['hard-green-unknown']} "
              f"hard-green-conflict={summary['hard-green-conflict']} "
              f"disqualified={summary['disqualified']}")
    for r in buckets["eligible"]:
        print(f"  ELIGIBLE {_short(r['result'])} "
              f"src={_short(r.get('source_rev',''))} "
              f"base={_short(r.get('base',''))} {r.get('soft_green')} "
              f"kind={r.get('eligibility_kind')} pr=#{r.get('pr_number','?')} "
              f"resolver={r.get('resolver') or '-'}")
    for r in buckets["hard-green-conflict"]:
        print(f"  HARD-GREEN-CONFLICT {_short(r['result'])} "
              f"pr=#{r.get('pr_number','?')} -- {r.get('landable_reason','')}")
    for r in buckets["hard-green-unknown"]:
        print(f"  HARD-GREEN-UNKNOWN {_short(r['result'])} "
              f"pr=#{r.get('pr_number','?')} -- no exact source/result pass")
    for r in buckets["unaccounted"]:
        print(f"  UNACCOUNTED pr=#{r.get('number','?')} {_short(r.get('result',''))} "
              f"{r.get('headRefName','')} -- no rebase record")
    if recorded_not_open:
        print(f"  ({len(recorded_not_open)} recorded head(s) match no open PR: "
              "superseded / force-pushed away)")


def _print_query(out: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(out, indent=2))
    else:
        verdict = "ELIGIBLE" if out.get("eligible") else "NOT-ELIGIBLE"
        rstate = out.get("result_hard_green_state")
        tag = f" [result-hard-green={rstate}]" if rstate and not out.get("eligible") else ""
        print(f"{verdict} {_short(out.get('result',''))}{tag} -- {out.get('reason')}")


# --------------------------------------------------------------------------- #
# output                                                                        #
# --------------------------------------------------------------------------- #
def emit_record(rec: dict, as_json: bool, *, action: str) -> None:
    if as_json:
        print(json.dumps({"action": action, "record": rec}, indent=2))
        return
    conf = "none" if not rec["conflicts"] else f"[{', '.join(rec['conflicts'])}]"
    sg = rec.get("soft_green") or "NONE"
    land = "LANDABLE" if rec.get("landable") else "NOT-LANDABLE"
    source_hard = rec.get("source_hard_green")
    result_hard = rec.get("result_hard_green")
    print(f"{action}: {_short(rec['source_rev'])} rebased on "
          f"{_short(rec['base'])} -> {_short(rec['result']) or '(no result)'} "
          f"conflicts={conf} soft-green={sg} {land}")
    print(f"  risk-judgement={rec.get('risk_judgement') or '(absent)'} "
          f"base-clears-floor={rec.get('base_clears_floor')} "
          f"source-hard={source_hard.get('verdict') if source_hard else 'null'} "
          f"result-hard={result_hard.get('verdict') if result_hard else 'null'} "
          f"resolver={rec.get('resolver') or '-'}")
    print(f"  {rec.get('landable_reason')}")


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--repo-checkout", default=DEFAULT_CHECKOUT)
        p.add_argument("--registry", default=DEFAULT_REGISTRY)
        p.add_argument("--store", default=default_store())
        p.add_argument("--json", action="store_true")

    pr = sub.add_parser("record", help="record a completed rebase outcome")
    common(pr)
    pr.add_argument("--source", required=True, help="revision X (pre-rebase sha)")
    pr.add_argument("--base", required=True, help="main base Y rebased onto")
    pr.add_argument("--result", required=True, help="rebased result Z")
    pr.add_argument("--conflicts", default="none",
                    help="'none' or comma/space-separated conflicted files")
    pr.add_argument("--resolver", help="agent that resolved conflicts")
    pr.add_argument("--risk-judgement", dest="risk_judgement",
                    help=f"{RISK_RETAIN}|{RISK_VALIDATE} (required iff conflicts)")
    pr.add_argument("--rationale", help="justification (required iff conflicts)")
    pr.add_argument("--no-hard-green-check", "--no-receipt-check",
                    dest="no_hard_green_check", action="store_true",
                    help="offline/debug only: do not query exact-SHA hard-green "
                         "authorities; the record cannot inherit soft green")
    pr.add_argument("--publish-provenance", dest="publish_provenance",
                    action="store_true",
                    help="ALSO publish the non-derivable soft-green provenance "
                         "(level+judgement+rationale) durably to the shared "
                         f"{RECEIPT_BRANCH} branch so any host can dereference it "
                         "(A2 cross-host close; no-op for a null soft-green)")
    pr.add_argument("--no-fetch", action="store_true")
    pr.set_defaults(func=do_record)

    rb = sub.add_parser("rebase", help="own the mechanical rebase in a checkout")
    common(rb)
    rb.add_argument("--source", required=True, help="revision/branch X to rebase")
    rb.add_argument("--onto", required=True,
                    help="base Y: a sha/ref, or the literal 'newest-green'")
    rb.add_argument("--branch", default="main",
                    help="branch for --onto newest-green (default main)")
    rb.add_argument("--resolver", help="agent running the rebase")
    rb.add_argument("--push", action="store_true",
                    help="push the zero-conflict result to origin")
    rb.add_argument("--push-ref", dest="push_ref",
                    help="destination ref for --push")
    rb.add_argument("--no-hard-green-check", "--no-receipt-check",
                    dest="no_hard_green_check", action="store_true",
                    help="offline/debug only; without exact source hard green the "
                         "rebase is refused")
    rb.add_argument("--publish-provenance", dest="publish_provenance",
                    action="store_true",
                    help="ALSO publish the soft-green provenance durably to the "
                         f"shared {RECEIPT_BRANCH} branch (A2 cross-host close)")
    rb.add_argument("--no-fetch", action="store_true")
    rb.set_defaults(func=do_rebase)

    el = sub.add_parser("eligible", help="query heads eligible to land now")
    common(el)
    el.add_argument("--result", help="test whether this result sha Z is eligible")
    el.add_argument("--source", help="filter eligible heads by source rev X")
    el.add_argument("--recheck-floor", dest="recheck_floor",
                    action="store_true", default=True,
                    help="re-verify the base floor live (default)")
    el.add_argument("--no-recheck-floor", dest="recheck_floor",
                    action="store_false",
                    help="trust the frozen record verdict (offline)")
    el.add_argument("--recheck-source-hard-green", dest="recheck_source_hard_green",
                    action="store_true", default=True,
                    help="refresh source X through the combined authority (default); "
                         "NO_RESULT retains a durable cached pass, red invalidates it")
    el.add_argument("--no-recheck-source-hard-green", dest="recheck_source_hard_green",
                    action="store_false")
    el.add_argument("--recheck-result-hard-green", "--recheck-receipt",
                    dest="recheck_result_hard_green", action="store_true", default=True,
                    help="refresh exact-result hard green at Z (default)")
    el.add_argument("--no-recheck-result-hard-green", "--no-recheck-receipt",
                    dest="recheck_result_hard_green", action="store_false",
                    help="trust the frozen exact-result evidence (offline)")
    el.add_argument("--pr-repo", dest="pr_repo", default=DEFAULT_PR_REPO,
                    help="repo whose open PRs are the reconciliation population "
                         f"(default {DEFAULT_PR_REPO}); the lander lands its PRs")
    el.add_argument("--no-reconcile", dest="no_reconcile", action="store_true",
                    help="store-only listing; skip reconciling against open PRs "
                         "(offline -- CANNOT assert invisible != nothing-pending)")
    el.add_argument("--durable-provenance", dest="durable_provenance",
                    action="store_true",
                    help="on reconcile, recover an UNACCOUNTED open-PR head from "
                         f"the shared {RECEIPT_BRANCH} branch (A2 cross-host): a "
                         "head recorded on ANOTHER host is dereferenced and held "
                         "to the same LIVE floor+receipt gates as a local record")
    el.set_defaults(func=do_eligible)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Refused as err:
        if getattr(args, "json", False):
            print(json.dumps({"verdict": "REFUSED", "reason": str(err)}, indent=2))
        else:
            print(f"REFUSED: {err}", file=sys.stderr)
        return EXIT_REFUSED
    except (RebaseError, gate_floors.FloorError) as err:
        if getattr(args, "json", False):
            print(json.dumps({"verdict": "ERROR", "reason": str(err)}, indent=2))
        else:
            print(f"ERROR: {err}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
