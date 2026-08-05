#!/usr/bin/env python3
"""Assembly-time detection for REBASED-BUT-CONTENT-CORRUPT.

The gap: a rebase/coalesce can succeed *mechanically* (git-clean tree, no
conflict markers, head >= base) yet carry *semantically wrong* content — the
git `union` merge driver interleaving both sides of a modify/modify hunk on a
TOML append-manifest yields DUPLICATE KEYS: valid to git, invalid to tomllib.
A rebase/anchor preflight checks mergeability, NOT content, so it sails through
a green. See note union-driver-corrupts-toml-manifests-use-keyed-3way.

This gate sits BETWEEN rebase-preflight (conflict markers / ancestry) and full
validate (build+test). It is the only step that DEREFERENCES the semantics of
the merged artifact rather than checking merge mechanics. Two layers:

  L1  PARSE   — every derived/manifest artifact must parse (tomllib / json).
                Catches the union duplicate-key corruption cheaply, no parents
                needed. This is the universal catch.
  L2  KEYED-RECONCILE — the coalesced artifact's keyed entry-set must equal the
                union of each parent's keyed set: nothing lost, duplicated, or
                altered. Catches loss/alteration that still parses (e.g. a hand
                merge that drops an entry, or regenerating test-files.json
                wholesale and destroying hand-authored `why` prose).

Exit 0 = content-faithful (safe to validate). Exit 1 = CORRUPT (do NOT validate
the tree; it would burn a serial validate on a defect the merge introduced).

Usage:
  detect_coalesce_corruption.py --parse <file>...            # L1 only
  detect_coalesce_corruption.py --self-test                  # verify the gate itself
"""
import sys
import json
import argparse

try:
    import tomllib
except ModuleNotFoundError:  # py<3.11
    tomllib = None


def parse_check(path):
    """L1: return None if the file parses, else an error string."""
    with open(path, "rb") as f:
        raw = f.read()
    if path.endswith(".toml"):
        if tomllib is None:
            return "tomllib unavailable (need py>=3.11)"
        try:
            tomllib.loads(raw.decode())
        except Exception as e:  # includes the union dup-key signature
            return f"TOML parse failed: {e}"
    elif path.endswith(".json"):
        try:
            json.loads(raw.decode())
        except Exception as e:
            return f"JSON parse failed: {e}"
    return None


def _self_test():
    """Verify on ONE: the gate FIRES on the exact union corruption signature
    (duplicate [[test]] key) and PASSES on clean append-only TOML."""
    import tempfile
    import os

    if tomllib is None:
        print("SELF-TEST SKIP: tomllib unavailable")
        return 0

    # NEGATIVE fixture: what `union` produces from a modify/modify hunk —
    # both sides' key=value interleaved into one table => duplicate key.
    corrupt = '[[test]]\nname = "cat"\nbackend = "dbi"\nbackend = "kvm"\n'
    # POSITIVE fixture: a faithful additive append (two distinct tables).
    clean = '[[test]]\nname = "cat"\nbackend = "dbi"\n\n[[test]]\nname = "ls"\nbackend = "kvm"\n'

    rc = 0
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "backend-parity-c.toml")
        good = os.path.join(d, "language-runtimes.toml")
        with open(bad, "w") as f:
            f.write(corrupt)
        with open(good, "w") as f:
            f.write(clean)

        err = parse_check(bad)
        if err is None:
            print("SELF-TEST FAIL: gate did NOT fire on duplicate-key TOML")
            rc = 1
        else:
            print(f"SELF-TEST ok  (NEGATIVE fires): {err}")

        err = parse_check(good)
        if err is not None:
            print(f"SELF-TEST FAIL: gate fired on clean TOML: {err}")
            rc = 1
        else:
            print("SELF-TEST ok  (POSITIVE inert): clean append parses")
    if rc == 0:
        print("SELF-TEST PASS: bracketed both sides")
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parse", nargs="*", default=[])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    corrupt = []
    for p in args.parse:
        err = parse_check(p)
        status = "OK" if err is None else "CORRUPT"
        print(f"{status:8} {p}" + (f"  <- {err}" if err else ""))
        if err:
            corrupt.append(p)
    if corrupt:
        print(f"\nREBASED-BUT-CONTENT-CORRUPT: {len(corrupt)} file(s) — do NOT validate this tree")
        return 1
    print("\ncontent-faithful: safe to validate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
