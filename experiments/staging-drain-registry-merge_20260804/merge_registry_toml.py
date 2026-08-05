#!/usr/bin/env python3
"""Keyed 3-way merge for the [[test]] append-manifests (TOML).

WHY: git's builtin `union` driver interleaves both sides of a modify/modify hunk
line-by-line, producing DUPLICATE KEYS inside one table -> git-clean but invalid
TOML (`Cannot overwrite a value`). See note union-driver-corrupts-toml-manifests.
The fix is the SAME shape as the faithful JSON merger (merge_registry_json.py):
match entries by KEY (`id`), resolve structurally per-entry, never by line
position.

DESIGN (dependency-free: only tomllib, which is read-only): operate on TEXT
BLOCKS, never reserialize. Each file = a preamble (before the first `[[test]]`)
plus a list of entry blocks; a block runs from one `[[test]]` line through its
trailing dotted `[test.modes.*]` sub-tables, up to the next `[[test]]`. Key =
the entry's `id`. Emitting the ORIGINAL block text preserves every comment,
prose string, and formatting detail, and — because we never interleave lines
within a table — cannot reproduce the union corruption. The assembled result is
re-parsed with tomllib as a built-in L1 self-check.

3-way per key:
  in ours & theirs, equal            -> keep (identical)
  in ours & theirs, differ, one==base-> take the side that changed
  in ours & theirs, differ, both!=base or both-added-different -> CONFLICT
  ours only, added (not in base)     -> keep ours
  ours only, was in base (theirs del)-> drop if ours==base else CONFLICT
  theirs only, added                 -> take theirs (appended)
  theirs only, was in base (ours del)-> drop if theirs==base else CONFLICT

Exit 0 = clean merge (result written / printed). Exit 1 = CONFLICT(S) listed;
nothing is silently guessed.

Usage:
  merge_registry_toml.py --base B --ours O --theirs T [--output OUT]
  merge_registry_toml.py --git-merge %O %A %B      # git merge-driver: writes %A
  merge_registry_toml.py --self-test
"""
import sys
import re
import argparse

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

ENTRY_RE = re.compile(r"^\[\[test\]\]\s*$", re.M)


def split_blocks(text):
    """Return (preamble, [(id, block_text, parsed_dict), ...])."""
    starts = [m.start() for m in ENTRY_RE.finditer(text)]
    if not starts:
        return text, []
    preamble = text[: starts[0]]
    bounds = starts + [len(text)]
    blocks = []
    for i in range(len(starts)):
        block = text[bounds[i] : bounds[i + 1]]
        parsed = tomllib.loads(block)
        entry = parsed["test"][0]
        key = entry["id"]
        blocks.append((key, block, entry))
    return preamble, blocks


def _index(blocks):
    order = [k for k, _, _ in blocks]
    text = {k: t for k, t, _ in blocks}
    val = {k: v for k, _, v in blocks}
    return order, text, val


def merge(base_text, ours_text, theirs_text):
    """Return (merged_text_or_None, conflicts:list[str])."""
    conflicts = []
    b_pre, b_blocks = split_blocks(base_text)
    o_pre, o_blocks = split_blocks(ours_text)
    t_pre, t_blocks = split_blocks(theirs_text)

    _, _, b_val = _index(b_blocks)
    o_order, o_text, o_val = _index(o_blocks)
    t_order, t_text, t_val = _index(t_blocks)

    # Preamble resolution.
    if o_pre == t_pre:
        pre = o_pre
    elif t_pre == b_pre:
        pre = o_pre
    elif o_pre == b_pre:
        pre = t_pre
    else:
        conflicts.append("preamble: both sides changed the header differently")
        pre = o_pre  # provisional; conflict already recorded

    # Decide each key. resolved[key] = text or None(drop). Order = ours then theirs-added.
    resolved = {}
    all_keys = set(o_val) | set(t_val) | set(b_val)
    for k in all_keys:
        in_o, in_t, in_b = k in o_val, k in t_val, k in b_val
        if in_o and in_t:
            if o_val[k] == t_val[k]:
                resolved[k] = o_text[k]
            elif in_b and o_val[k] == b_val[k]:
                resolved[k] = t_text[k]           # only theirs changed
            elif in_b and t_val[k] == b_val[k]:
                resolved[k] = o_text[k]           # only ours changed
            else:
                conflicts.append(f"{k}: both sides changed this entry differently")
                resolved[k] = o_text[k]
        elif in_o and not in_t:
            if in_b:
                if o_val[k] == b_val[k]:
                    resolved[k] = None            # theirs deleted, ours unchanged
                else:
                    conflicts.append(f"{k}: ours modified, theirs deleted")
                    resolved[k] = o_text[k]
            else:
                resolved[k] = o_text[k]           # added by ours
        elif in_t and not in_o:
            if in_b:
                if t_val[k] == b_val[k]:
                    resolved[k] = None            # ours deleted, theirs unchanged
                else:
                    conflicts.append(f"{k}: theirs modified, ours deleted")
                    resolved[k] = t_text[k]
            else:
                resolved[k] = t_text[k]           # added by theirs

    if conflicts:
        return None, conflicts

    ordered = [k for k in o_order if resolved.get(k) is not None]
    ordered += [k for k in t_order if k not in o_val and resolved.get(k) is not None]
    merged = pre + "".join(resolved[k] for k in ordered)

    # L1 self-check: result must parse, and its keyed-set must be exactly the union we computed.
    reparsed = tomllib.loads(merged)
    got = [e["id"] for e in reparsed.get("test", [])]
    if len(got) != len(set(got)):
        return None, ["POST-MERGE duplicate id in result (should be impossible)"]
    if set(got) != set(ordered):
        return None, ["POST-MERGE keyed-set != computed union (L2 violation)"]
    return merged, []


def _read(p):
    with open(p, "r") as f:
        return f.read()


def _self_test():
    if tomllib is None:
        print("SELF-TEST SKIP: tomllib unavailable (need py>=3.11)")
        return 0
    pre = 'schema = 2\nbucket = "x"\n\n'
    def e(id_, backend):
        return f'[[test]]\nid = "{id_}"\nbackend = "{backend}"\n\n'
    base = pre + e("a", "ptrace")
    ours = pre + e("a", "ptrace") + e("b", "dbi")          # ours adds b
    theirs = pre + e("a", "ptrace") + e("c", "kvm")        # theirs adds c
    rc = 0

    def fail(msg):
        nonlocal rc
        print("SELF-TEST FAIL: " + msg); rc = 1

    # CASE 1 — two sides adding DIFFERENT keys -> both present, no duplicates.
    merged, conflicts = merge(base, ours, theirs)
    if conflicts or merged is None:
        fail(f"case1 unexpected conflict {conflicts}")
    else:
        ids = [x["id"] for x in tomllib.loads(merged)["test"]]
        if set(ids) == {"a", "b", "c"} and len(ids) == 3:
            print("SELF-TEST ok  CASE1 (disjoint adds): {a,b,c}, valid TOML, no dup")
            # ORDER STABILITY: ours' entries keep their order; theirs-only appended at end.
            if ids == ["a", "b", "c"]:
                print("SELF-TEST ok  ORDER-STABLE: ours order preserved, theirs-add appended")
            else:
                fail(f"order not stable: {ids}")
        else:
            fail(f"case1 ids={ids}")

    # CASE 2 — two sides changing the SAME key to DIFFERENT values -> MUST refuse, never silent.
    o2 = pre + e("a", "dbi")     # ours -> dbi
    t2 = pre + e("a", "kvm")     # theirs -> kvm
    merged2, conflicts2 = merge(base, o2, t2)
    if merged2 is None and any(c.startswith("a:") for c in conflicts2):
        print(f"SELF-TEST ok  CASE2 (same key, diff vals): REFUSED -> {conflicts2}")
    else:
        fail(f"case2 expected refusal, got merged={merged2 is not None}")

    # CASE 3 — IDENTICAL change on both sides -> converge cleanly, NO spurious conflict.
    same = pre + e("a", "sabre")
    merged3, conflicts3 = merge(base, same, same)
    if merged3 is not None and not conflicts3:
        ids3 = [x["id"] for x in tomllib.loads(merged3)["test"]]
        if ids3 == ["a"] and 'backend = "sabre"' in merged3:
            print("SELF-TEST ok  CASE3 (identical change): converged, no spurious conflict")
        else:
            fail(f"case3 wrong content ids={ids3}")
    else:
        fail(f"case3 spurious conflict {conflicts3}")

    # ACCEPTANCE — a fixture whose CORRECT merge is known; assert byte-exact, incl. comments,
    # multi-line sub-tables, and prose strings. This is NOT one of the PRs under repair.
    a_pre = 'schema = 2\nbucket = "acc"\n\n'
    ea = ('[[test]]\nid = "acc/alpha"\n# hand-authored rationale, must survive\n'
          'requires = ["linux", "x86_64"]\n\n[test.modes.verify]\nbackends_enabled = ["ptrace"]\n\n')
    eb = ('[[test]]\nid = "acc/beta"\nprogram = "b.c"\n\n')   # ours appends beta
    ec = ('[[test]]\nid = "acc/gamma"\nprogram = "g.c"\n\n')  # theirs appends gamma
    acc_base = a_pre + ea
    acc_ours = a_pre + ea + eb
    acc_theirs = a_pre + ea + ec
    known_good = a_pre + ea + eb + ec                          # ours order, theirs-add last
    acc_merged, acc_conf = merge(acc_base, acc_ours, acc_theirs)
    if acc_conf or acc_merged is None:
        fail(f"acceptance conflict {acc_conf}")
    elif acc_merged != known_good:
        fail("acceptance != known-good:\n--- got ---\n" + repr(acc_merged) +
             "\n--- want ---\n" + repr(known_good))
    else:
        print("SELF-TEST ok  ACCEPTANCE: byte-exact vs known-good (comments/sub-tables/prose preserved)")

    print("SELF-TEST PASS: 3 cases + order-stability + known-good acceptance"
          if rc == 0 else "SELF-TEST FAILED")
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base"); ap.add_argument("--ours"); ap.add_argument("--theirs")
    ap.add_argument("--output")
    ap.add_argument("--git-merge", nargs=3, metavar=("BASE", "OURS", "THEIRS"),
                    help="git merge-driver mode: writes merged result back to OURS")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if tomllib is None:
        print("ERROR: tomllib unavailable (need py>=3.11)", file=sys.stderr); return 2

    if args.git_merge:
        base_p, ours_p, theirs_p = args.git_merge
        merged, conflicts = merge(_read(base_p), _read(ours_p), _read(theirs_p))
        if conflicts:
            print("CONFLICT (keyed-toml):", file=sys.stderr)
            for c in conflicts:
                print(f"  {c}", file=sys.stderr)
            return 1
        with open(ours_p, "w") as f:
            f.write(merged)
        return 0

    if not (args.base and args.ours and args.theirs):
        ap.error("need --base/--ours/--theirs (or --git-merge / --self-test)")
    merged, conflicts = merge(_read(args.base), _read(args.ours), _read(args.theirs))
    if conflicts:
        print("CONFLICT (keyed-toml):", file=sys.stderr)
        for c in conflicts:
            print(f"  {c}", file=sys.stderr)
        return 1
    if args.output:
        with open(args.output, "w") as f:
            f.write(merged)
        print(f"merged -> {args.output}")
    else:
        sys.stdout.write(merged)
    return 0


if __name__ == "__main__":
    sys.exit(main())
