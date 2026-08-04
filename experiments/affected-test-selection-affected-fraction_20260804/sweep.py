#!/usr/bin/env python3
"""Affected-test-selection measurement sweep.

For each of the last N commits on the current branch of the hermit repo,
compute the commit's changed-file footprint (diff against its first parent)
and feed it to ci/select-tests.rs to get the selection decision + selected
node/shard/cell counts. Report the distribution of decisions and the
genuinely-affected fraction.

Read-only: only `git diff --name-only` and select-tests.rs (which reads the
ci/*.json config from the working tree). No checkout is mutated.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path("/home/newton/work/dev-hermit/hermit")
SELECT = REPO / "ci" / "select-tests.rs"


def git(args):
    return subprocess.run(
        ["git", "-C", str(REPO)] + args,
        capture_output=True, text=True, check=True
    ).stdout


def changed_files(sha):
    # diff against first parent; merge commits use -m 1 semantics via ^1
    out = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--name-only", f"{sha}^1", sha],
        capture_output=True, text=True
    )
    if out.returncode != 0:
        # root commit or no parent; treat as full (all files)
        return None
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def select(files):
    p = subprocess.run(
        [str(SELECT), "--files", "-", "--format", "json"],
        input="\n".join(files) + "\n",
        capture_output=True, text=True, cwd=str(REPO)
    )
    if p.returncode != 0:
        raise RuntimeError(f"select-tests failed: {p.stderr}")
    return json.loads(p.stdout)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    shas = git(["log", "--first-parent", "-n", str(n), "--format=%H"]).split()
    # Universe sizes from a forced-full run.
    full = select(["Cargo.lock"])
    TOT_NODES = full["node_count"]
    TOT_SHARDS = len(full["shards"])
    TOT_CELLS = full["cell_count"]

    rows = []
    for sha in shas:
        files = changed_files(sha)
        if files is None:
            rows.append((sha, "full", TOT_NODES, TOT_SHARDS, TOT_CELLS, 0, ["root/no-parent"]))
            continue
        if not files:
            # empty diff (e.g. merge with no net change) -> treat as skip-ish full-safe
            rows.append((sha, "empty", 0, 0, 0, 0, ["empty-diff"]))
            continue
        r = select(files)
        rows.append((
            sha, r["decision"], r["node_count"], len(r["shards"]),
            r["cell_count"], len(files), r.get("reasons", [])
        ))

    # Aggregate.
    from collections import Counter
    dec = Counter(r[1] for r in rows)
    total = len(rows)
    subj = git(["log", "--first-parent", "-n", str(n), "--format=%ci"]).splitlines()
    window = f"{subj[-1]} .. {subj[0]}" if subj else "?"

    print(f"# Affected-test-selection sweep")
    print(f"N commits (first-parent): {total}")
    print(f"date window: {window}")
    print(f"universe: nodes={TOT_NODES} shards={TOT_SHARDS} cells={TOT_CELLS}")
    print()
    print("## Decision distribution")
    for d in ("skip", "selective", "full", "empty"):
        c = dec.get(d, 0)
        print(f"  {d:10s} {c:4d}  {100*c/total:5.1f}%")
    print()

    # "Genuinely affected fraction": for runs that actually execute something
    # (selective + full), how much of the suite runs? skip=0.
    reducible = [r for r in rows if r[1] in ("skip", "selective")]
    print("## Reducible commits (skip or selective) — where selection helps")
    print(f"  reducible: {len(reducible)}/{total} = {100*len(reducible)/total:.1f}%")
    sel = [r for r in rows if r[1] == "selective"]
    if sel:
        def frac(r, tot_idx, tot):
            return 100 * r[tot_idx] / tot if tot else 0
        node_fracs = sorted(frac(r, 2, TOT_NODES) for r in sel)
        shard_fracs = sorted(frac(r, 3, TOT_SHARDS) for r in sel)
        cell_fracs = sorted(frac(r, 4, TOT_CELLS) for r in sel)
        def med(x):
            return x[len(x)//2]
        print(f"  selective node%  : min={node_fracs[0]:.0f} med={med(node_fracs):.0f} max={node_fracs[-1]:.0f}")
        print(f"  selective shard% : min={shard_fracs[0]:.0f} med={med(shard_fracs):.0f} max={shard_fracs[-1]:.0f}")
        print(f"  selective cell%  : min={cell_fracs[0]:.0f} med={med(cell_fracs):.0f} max={cell_fracs[-1]:.0f}")
    print()

    # Node-work saved across the whole window (sum of selected nodes / sum if all-full).
    executed_nodes = sum(r[2] for r in rows if r[1] != "empty")
    would_be = sum(TOT_NODES for r in rows if r[1] != "empty")
    print("## Aggregate node-work over the window")
    print(f"  selected nodes summed : {executed_nodes}")
    print(f"  all-full would be     : {would_be}")
    print(f"  node-work saved       : {100*(would_be-executed_nodes)/would_be:.1f}%")
    print()

    # Save full CSV.
    out = REPO_PARENT = Path("/home/newton/work/dev-hermit/scratch/affsel/rows.csv")
    with open(out, "w") as f:
        f.write("sha,decision,nodes,shards,cells,nfiles,top_reason\n")
        for r in rows:
            reason = (r[6][-1] if r[6] else "").replace(",", ";")
            f.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]},{reason}\n")
    print(f"rows -> {out}")

    # Return rows for direction verification.
    return rows, TOT_NODES, TOT_SHARDS, TOT_CELLS


if __name__ == "__main__":
    main()
