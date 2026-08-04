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

THREE THINGS THIS TOOL GETS RIGHT (each a Proxy-Binding predicate):

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

CONSUMER: the lander QUERIES for eligible heads (`eligible`) rather than reading
posted notes -- a query has no mailbox to miss, closing the producer-posted-to-
the-wrong-task gap. See memory: ci-hub-ledger-cannot-record-soft-vs-hard-green
(the validate ledger CANNOT record soft-vs-hard green; this store carries the
provenance the ledger cannot), rebase-base-floors-queryable-gate-floors-py,
merge-gate-v2-floor-invalidates-pre-floor-greens.

RECORD SCHEMA (ignored/rebase-records.jsonl, append-only, latest-per-Z wins):
  { schema_version, recorded_utc, source_rev: X, base: Y, result: Z,
    conflicts: [] | [files], resolver, risk_judgement:
      retained-soft-green | needs-full-validate | n/a,
    rationale, soft_green: null | soft-green(zero-conflict) |
      soft-green(resolver-judged),
    base_clears_floor, base_unmet_floors: [...], landable, landable_reason }

SUBCOMMANDS
  record    Record a completed rebase outcome and derive its soft-green level.
            The mechanical zero-conflict path and the resolver path both funnel
            here; this is where refusal-on-absent-judgement lives.
  rebase    OWN the mechanical rebase in a checkout: fetch, resolve the base
            (--onto <sha> | --onto newest-green), attempt `git rebase`. Zero
            conflicts -> complete, record soft-green(zero-conflict) mechanically,
            optionally push. Conflicts -> abort cleanly, report the conflicted
            files, and REFUSE to soft-green (the resolver resolves, then calls
            `record --risk-judgement ...`).
  eligible  Consumer query for the lander: list (or test) heads that are eligible
            to land NOW -- soft-green AND the base clears every live floor.

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
import json
import os
import subprocess
import sys

# Reuse the floor half of the contract verbatim: one enumeration, one verifier.
_VALIDATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "validate")
sys.path.insert(0, _VALIDATE_DIR)
import gate_floors  # noqa: E402  (path injected above)

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_ERROR = 3

LOCAL_TIMEOUT = 30.0
NETWORK_TIMEOUT = 120.0

DEFAULT_CHECKOUT = gate_floors.DEFAULT_CHECKOUT
DEFAULT_REGISTRY = gate_floors.DEFAULT_REGISTRY
# Beside the validate ledger (ignored/validate-run-ledger.jsonl) at parent root,
# so a lander finds both provenance stores in one place.
_PARENT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_STORE = os.path.join(_PARENT_ROOT, "ignored", "rebase-records.jsonl")

SCHEMA_VERSION = 1

# The resolving agent's judgement is a CLOSED set: an absent or unrecognised value
# is a REFUSAL, never a silent default to green.
RISK_RETAIN = "retained-soft-green"
RISK_VALIDATE = "needs-full-validate"
RISK_NA = "n/a"
VALID_JUDGEMENTS = {RISK_RETAIN, RISK_VALIDATE}

SOFT_ZERO_CONFLICT = "soft-green(zero-conflict)"
SOFT_RESOLVER_JUDGED = "soft-green(resolver-judged)"


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


# --------------------------------------------------------------------------- #
# Pure verdict derivation -- the heart of the mechanism, git-free and testable. #
# --------------------------------------------------------------------------- #
def derive_verdict(conflicts: list[str], risk_judgement: str | None,
                   rationale: str | None, base_clears_floor: bool,
                   base_unmet: list[dict]) -> dict:
    """Map (conflicts, judgement, base-floor-status) -> soft-green + landability.

    RAISES Refused when conflicts exist without a valid judgement + rationale --
    an absent judgement must NEVER read as approval. Returns the derived fields
    otherwise; landability is soft-green AND the base clears every floor.
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

    landable = soft_green is not None and base_clears_floor
    if soft_green is None:
        reason = "resolver-flagged-needs-full-validate: verify the tip before landing"
    elif not base_clears_floor:
        unmet = ", ".join(_short(f["sha"]) for f in base_unmet) or "?"
        reason = (f"unlandable-base-below-floor: base predates floor(s) {unmet}; "
                  "a clean rebase onto a sub-floor base still yields an unlandable "
                  "Z -- rebase onto a base at/after every floor")
    else:
        reason = f"landable via {soft_green}; base clears all floors"
    return {
        "risk_judgement": judgement,
        "soft_green": soft_green,
        "base_clears_floor": base_clears_floor,
        "base_unmet_floors": [
            {"sha": f["sha"], "kind": f["kind"], "field": f["field"]}
            for f in base_unmet],
        "landable": landable,
        "landable_reason": reason,
    }


# --------------------------------------------------------------------------- #
# Store I/O                                                                     #
# --------------------------------------------------------------------------- #
def append_record(store: str, record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(store), exist_ok=True)
        with open(store, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
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
# record                                                                        #
# --------------------------------------------------------------------------- #
def build_record(source: str, base: str, result: str, conflicts: list[str],
                 resolver: str | None, verdict: dict, now_utc: str) -> dict:
    rec = {
        "schema_version": SCHEMA_VERSION,
        "recorded_utc": now_utc,
        "source_rev": source,
        "base": base,
        "result": result,
        "conflicts": conflicts,
        "resolver": resolver or "",
        "rationale": "",
    }
    rec.update(verdict)
    return rec


def do_record(args) -> int:
    for label, rev in (("--source", args.source), ("--base", args.base),
                       ("--result", args.result)):
        if not _is_hexish(rev):
            raise RebaseError(f"{label} {rev!r} is not a 7-40 hex commit sha")
    conflicts = parse_conflicts(args.conflicts)
    # base-floor status is the load-bearing "carry Y" half; refresh unless offline.
    if not args.no_fetch:
        try:
            fetch(args.repo_checkout)
        except RebaseError:
            pass  # offline is not fatal for a purely-local ancestry check
    fstatus = base_floor_status(args.registry, args.repo_checkout, args.base)
    verdict = derive_verdict(conflicts, args.risk_judgement, args.rationale,
                             fstatus["ok"], fstatus["unmet"])
    now = utc_now()
    rec = build_record(args.source, args.base, args.result, conflicts,
                       args.resolver, verdict, now)
    rec["rationale"] = (args.rationale or "").strip()
    append_record(args.store, rec)
    emit_record(rec, args.json, action="RECORDED")
    return EXIT_OK


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
        }, utc_now())
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
    verdict = derive_verdict([], None, None, fstatus["ok"], fstatus["unmet"])
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
    rec = build_record(source, base, result, [], args.resolver, verdict, utc_now())
    append_record(args.store, rec)
    # Leave the tree on a clean detached base (stateless, like union-rebase.sh).
    _git(checkout, ["checkout", "-q", "--detach", base])
    emit_record(rec, args.json, action="RECORDED")
    return EXIT_OK


def conflicted_files(checkout: str) -> list[str]:
    cp = _git(checkout, ["diff", "--name-only", "--diff-filter=U"])
    return [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# eligible (consumer query for the lander)                                     #
# --------------------------------------------------------------------------- #
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

    rows = []
    for z, rec in latest.items():
        soft = rec.get("soft_green")
        base_ok = rec.get("base_clears_floor", False)
        unmet = rec.get("base_unmet_floors", [])
        if floors is not None and rec.get("base"):
            live = gate_floors.clears_all(floors, args.repo_checkout, rec["base"])
            base_ok = live["ok"]
            unmet = [{"sha": f["sha"], "kind": f["kind"], "field": f["field"]}
                     for f in live["unmet"]]
        landable = soft is not None and base_ok
        row = {**rec, "base_clears_floor": base_ok, "base_unmet_floors": unmet,
               "landable": landable}
        rows.append(row)

    if args.result:
        match = next((r for r in rows if r["result"] == args.result), None)
        if match is None:
            out = {"result": args.result, "eligible": False,
                   "reason": "no rebase record for this result sha"}
            _print_query(out, args.json)
            raise Refused(f"no rebase record for result {_short(args.result)}")
        out = {"result": match["result"], "source_rev": match.get("source_rev"),
               "base": match.get("base"), "eligible": match["landable"],
               "soft_green": match.get("soft_green"),
               "reason": match.get("landable_reason")}
        _print_query(out, args.json)
        return EXIT_OK if match["landable"] else EXIT_REFUSED

    eligible = [r for r in rows if r["landable"]]
    if args.source:
        eligible = [r for r in eligible if r.get("source_rev") == args.source]
    if args.json:
        print(json.dumps({"schema_version": SCHEMA_VERSION,
                          "eligible": eligible,
                          "total_records": len(latest)}, indent=2))
    else:
        if not eligible:
            print("ELIGIBLE-HEADS: none "
                  f"({len(latest)} recorded result head(s), none landable)")
        for r in eligible:
            print(f"ELIGIBLE {_short(r['result'])} "
                  f"src={_short(r.get('source_rev',''))} "
                  f"base={_short(r.get('base',''))} {r.get('soft_green')} "
                  f"resolver={r.get('resolver') or '-'}")
    return EXIT_OK


def _print_query(out: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(out, indent=2))
    else:
        verdict = "ELIGIBLE" if out.get("eligible") else "NOT-ELIGIBLE"
        print(f"{verdict} {_short(out.get('result',''))} -- {out.get('reason')}")


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
    print(f"{action}: {_short(rec['source_rev'])} rebased on "
          f"{_short(rec['base'])} -> {_short(rec['result']) or '(no result)'} "
          f"conflicts={conf} soft-green={sg} {land}")
    print(f"  risk-judgement={rec.get('risk_judgement') or '(absent)'} "
          f"base-clears-floor={rec.get('base_clears_floor')} "
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
        p.add_argument("--store", default=DEFAULT_STORE)
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
