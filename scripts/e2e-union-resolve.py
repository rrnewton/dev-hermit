#!/usr/bin/env python3
"""Semantic 3-way delta-union resolver for e2e-manifest registry files.

Used by scripts/e2e-union-rebase.sh to resolve rebase conflicts on shared
append-only registries WITHOUT hand-editing semantics. For each managed file it
computes the rows the PR ADDED relative to the merge base and unions them onto
the current-main side, keyed deterministically:

  tests/e2e/manifests/*.toml            -> [[test]] blocks keyed by `id`
  tests/e2e/manifests/inventory/test-files.json -> files[] keyed by `path`
  tests/backend-parity/matrix.tsv       -> data rows keyed by first column
  ci/expected-e2e-plan.json             -> NOT resolved here (regenerated later)

SAFETY: the union is purely additive. A key present on both sides with DIFFERING
content is a real semantic conflict -> exit 3 (human). Any file type not listed
above -> exit 4 (human). Ordering is deterministic (sort by key) so repeated
runs are byte-stable.

Usage: e2e-union-resolve.py <relpath> <base> <ours> <theirs> <out>
  <base>/<ours>/<theirs> are the three conflict stages (missing => empty).
Exit codes: 0 resolved, 3 non-additive conflict, 4 unmanaged file, 2 usage/parse.
"""
import json
import re
import sys


def read(path):
    if not path or path == "/dev/null":
        return None
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------- TOML blocks
_ID_RE = re.compile(r'^\s*id\s*=\s*"([^"]+)"', re.M)


def split_toml_blocks(text):
    """Return (preamble, {id: block_text}) preserving exact block formatting.

    A block runs from a `[[test]]` header to the next one (or EOF)."""
    if text is None:
        return "", {}
    lines = text.splitlines(keepends=True)
    # find [[test]] header line indices
    heads = [i for i, ln in enumerate(lines) if ln.strip() == "[[test]]"]
    if not heads:
        return text, {}
    preamble = "".join(lines[: heads[0]])
    blocks = {}
    for n, start in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        btext = "".join(lines[start:end])
        m = _ID_RE.search(btext)
        if not m:
            print(f"resolve: [[test]] block without id in {sys.argv[1]}", file=sys.stderr)
            sys.exit(2)
        bid = m.group(1)
        if bid in blocks:
            print(f"resolve: duplicate id {bid!r} within one side", file=sys.stderr)
            sys.exit(2)
        blocks[bid] = btext
    return preamble, blocks


def norm(s):
    return s.strip()


def union_toml(base, ours, theirs):
    pre_b, b = split_toml_blocks(base)
    pre_o, o = split_toml_blocks(ours)
    pre_t, t = split_toml_blocks(theirs)
    preamble = pre_o if pre_o else pre_t
    if pre_o and pre_t and norm(pre_o) != norm(pre_t):
        print("resolve: TOML preamble diverged (non-additive)", file=sys.stderr)
        sys.exit(3)
    result = dict(o)
    added = {k: v for k, v in t.items() if k not in b}  # rows the PR introduced
    for k, v in added.items():
        if k in result:
            if norm(result[k]) != norm(v):
                print(f"resolve: id {k!r} differs between sides (non-additive)", file=sys.stderr)
                sys.exit(3)
        else:
            result[k] = v
    body = "".join(result[k] for k in sorted(result))
    out = preamble
    if out and not out.endswith("\n"):
        out += "\n"
    return out + body


# ---------------------------------------------------------------- JSON by-key
def union_json_by_key(base, ours, theirs, array_field, key_field):
    def load(s):
        return json.loads(s) if s is not None else None

    jb, jo, jt = load(base), load(ours), load(theirs)
    ref = jo if jo is not None else jt
    if ref is None:
        print("resolve: no JSON side present", file=sys.stderr)
        sys.exit(2)

    def index(j):
        out = {}
        if j is None:
            return out
        for row in j.get(array_field, []):
            out[row[key_field]] = row
        return out

    ib, io, it = index(jb), index(jo), index(jt)
    result = dict(io)
    for k, v in it.items():
        if k in ib:
            continue  # already existed at base; not an addition
        if k in result:
            if json.dumps(result[k], sort_keys=True) != json.dumps(v, sort_keys=True):
                print(f"resolve: {key_field}={k!r} differs (non-additive)", file=sys.stderr)
                sys.exit(3)
        else:
            result[k] = v
    merged = dict(ref)
    merged[array_field] = [result[k] for k in sorted(result)]
    return json.dumps(merged, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------- TSV by-key
def union_tsv(base, ours, theirs):
    def rows(s):
        if s is None:
            return None, {}
        lines = [ln for ln in s.splitlines() if ln != ""]
        if not lines:
            return None, {}
        header, data = lines[0], lines[1:]
        d = {}
        for ln in data:
            key = ln.split("\t", 1)[0]
            d[key] = ln
        return header, d

    hb, b = rows(base)
    ho, o = rows(ours)
    ht, t = rows(theirs)
    header = ho or ht
    if ho and ht and ho != ht:
        print("resolve: TSV header diverged (non-additive)", file=sys.stderr)
        sys.exit(3)
    result = dict(o)
    for k, v in t.items():
        if k in b:
            continue
        if k in result:
            if result[k] != v:
                print(f"resolve: matrix row {k!r} differs (non-additive)", file=sys.stderr)
                sys.exit(3)
        else:
            result[k] = v
    return header + "\n" + "".join(rk + "\n" for rk in [result[k] for k in sorted(result)])


def main():
    if len(sys.argv) != 6:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    rel, base_p, ours_p, theirs_p, out_p = sys.argv[1:]
    base, ours, theirs = read(base_p), read(ours_p), read(theirs_p)

    if rel.startswith("tests/e2e/manifests/") and rel.endswith(".toml"):
        out = union_toml(base, ours, theirs)
    elif rel == "tests/e2e/manifests/inventory/test-files.json":
        out = union_json_by_key(base, ours, theirs, "files", "path")
    elif rel == "tests/backend-parity/matrix.tsv":
        out = union_tsv(base, ours, theirs)
    else:
        print(f"resolve: unmanaged file {rel} -> human", file=sys.stderr)
        sys.exit(4)

    with open(out_p, "w") as f:
        f.write(out)


if __name__ == "__main__":
    main()
