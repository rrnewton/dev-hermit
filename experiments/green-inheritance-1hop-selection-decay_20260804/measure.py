#!/usr/bin/env python3
"""Reproduce the 1-hop green-inheritance selection-decay measurement.

Run from the hermit checkout root (the submodule), e.g.:
    cd hermit && python3 ../experiments/green-inheritance-1hop-selection-decay_20260804/measure.py

Emits three CSVs next to this script:
  results.csv            -- cumulative decay: diff(anchor..tip@d) selection vs distance (THE 1-hop curve)
  results.per-commit.csv -- per-commit tip-vs-parent selection (the #1529 small-diff contrast)
  results.mutation.csv   -- oracle-based selection-coverage for representative mutated files

The selector is queried exactly as a caller would:
    git diff --name-only A..B | ./ci/select-tests.rs --files - --format json
No checkout/build is needed for the selection-level curves.
"""
import subprocess, json, os, re, csv, sys

ANCHOR = "e8a0d8d3be3b53985dc898bb8e5cbb696a6a719f"  # a counted-full green, ancestor of TIP
TIP    = "b4e94ce4455daef251e10af2174c6998c1ae1a4d"  # main first-parent tip at measurement time
OUT    = os.path.dirname(os.path.abspath(__file__))
SELECT = "./ci/select-tests.rs"
FORCE_FULL_PREFIX = ("ci/", "validate.sh", ".github/", ".claude/", ".llms/",
                     "Cargo.toml", "Cargo.lock", "rust-toolchain")

def sh(args):
    return subprocess.run(args, capture_output=True, text=True).stdout

def select(files):
    if not files:
        return {"decision": "skip", "node_count": 0, "cell_count": 0, "shard_count": 0, "reasons": []}
    p = subprocess.run([SELECT, "--files", "-", "--format", "json"],
                       input="\n".join(files) + "\n", capture_output=True, text=True)
    out = "\n".join(l for l in p.stdout.splitlines() if not l.startswith("#"))
    return json.loads(out)

def diff_files(a, b):
    return [x for x in sh(["git", "diff", "--name-only", f"{a}..{b}"]).split() if x]

def manifest_consumers(path):
    """Oracle: which e2e.manifest_<bucket> node references this fixture/data file."""
    hits = set()
    md = "tests/e2e/manifests"
    base = os.path.basename(path)
    for fn in os.listdir(md):
        if not fn.endswith(".toml"):
            continue
        txt = open(os.path.join(md, fn)).read()
        if base in txt or path in txt:
            m = re.search(r'bucket\s*=\s*"([^"]+)"', txt)
            if m:
                hits.add("e2e.manifest_" + m.group(1).replace("-", "_"))
    return hits

def main():
    commits = sh(["git", "rev-list", "--reverse", "--first-parent", f"{ANCHOR}..{TIP}"]).split()
    N = len(commits)

    # 1. Cumulative decay (THE 1-hop curve): diff(anchor..commit@d)
    with open(os.path.join(OUT, "results.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["distance_commits", "tip_sha", "changed_files",
                    "decision", "nodes_selected", "nodes_total",
                    "cells_selected", "cells_total", "first_force_full_reason"])
        for d in range(1, N + 1):
            c = commits[d - 1]
            files = diff_files(ANCHOR, c)
            r = select(files)
            reason = ";".join(r.get("reasons", []))[:80]
            w.writerow([d, c[:12], len(files), r["decision"],
                        r.get("node_count", ""), 47, r.get("cell_count", ""), 70, reason])

    # 2. Per-commit contrast: diff(parent..commit)
    with open(os.path.join(OUT, "results.per-commit.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "sha", "changed_files", "decision", "nodes_selected", "cells_selected", "reason_or_firstfile"])
        for i, c in enumerate(commits, 1):
            files = diff_files(f"{c}~1", c)
            r = select(files)
            reason = (";".join(r.get("reasons", [])) or (files[0] if files else ""))[:60]
            w.writerow([i, c[:12], len(files), r["decision"],
                        r.get("node_count", ""), r.get("cell_count", ""), reason])

    # 3. Mutation coverage: selected nodes vs independent oracle (manifest consumers)
    CASES = [
        ("detcore/src/scheduler/mod.rs", "core detcore source (Cargo reverse-dep closure)"),
        ("detcore-dbi/src/lib.rs",       "dbi backend source (Cargo, narrow)"),
        ("detcore-sabre/src/lib.rs",     "sabre backend source (Cargo, narrow)"),
        ("hermit-cli/src/bin/hermit/run.rs", "hermit-cli source (Cargo)"),
        ("tests/backend-parity/fixtures/set_tid_address.c", "backend-parity fixture (hand-map)"),
        ("tests/e2e/manifests/inventory/test-files.json", "e2e inventory data (hand-map)"),
    ]
    with open(os.path.join(OUT, "results.mutation.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "class", "decision", "nodes_selected", "oracle_consumers", "oracle_subset_of_selected"])
        for f, desc in CASES:
            r = select([f])
            nodes = set(r.get("nodes", []))
            oracle = manifest_consumers(f) if f.endswith((".c", ".json")) else set()
            verdict = "n/a(Cargo-closure)" if not oracle else ("OK" if oracle <= nodes else f"UNDER-SELECT:{sorted(oracle-nodes)}")
            w.writerow([f, desc, r["decision"], r.get("node_count", ""),
                        "|".join(sorted(oracle)) or "-", verdict])

    print(f"wrote results.csv ({N} rows), results.per-commit.csv, results.mutation.csv to {OUT}")

if __name__ == "__main__":
    main()
