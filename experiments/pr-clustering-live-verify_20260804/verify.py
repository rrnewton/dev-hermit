#!/usr/bin/env python3
"""Live verification of pr_landing_planner conflict-clustering over rrnewton/hermit open PRs.

Stage 1: file-overlap clustering over ALL open PRs (cheap) -> find the parity-mass candidate.
Stage 2: REAL git merge-tree pairwise WITHIN that candidate -> confirm how many real conflict
         components it is, proving merge-tree clustering is TIGHTER than same-file overlap.

Uses the tool's own pure functions (cluster_by_conflict / connected_components / rebases_avoided)
and its real GitHubHost.merge_tree, so this exercises the shipped code, not a re-implementation.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "work/dev-hermit/agent-utils/py"))

from pr_landing_planner.graph import (  # noqa: E402
    build_conflict_edges_file_overlap,
    cluster_by_conflict,
    connected_components,
    rebases_avoided,
)
from pr_landing_planner.githubhost import GitHubHost  # noqa: E402
from pr_landing_planner.model import ConflictEdge, PrNode  # noqa: E402

HERE = Path(__file__).resolve().parent
GIT_DIR = str(Path.home() / "work/dev-hermit/hermit")
LIGHT = HERE / "pr-list-light.json"


def load_nodes() -> list[PrNode]:
    raw = json.loads(LIGHT.read_text())
    nodes = []
    for o in raw:
        files = frozenset(f["path"] for f in o.get("files", []))
        nodes.append(
            PrNode(
                number=o["number"],
                head_ref=o["headRefName"],
                base_ref="main",
                head_sha=o["headRefOid"],
                base_sha="",
                files=files,
            )
        )
    return nodes


def stage1(nodes: list[PrNode]) -> tuple[int, ...]:
    edges = build_conflict_edges_file_overlap(nodes)
    clusters = cluster_by_conflict(nodes, edges)
    sizes = sorted((c.size for c in clusters), reverse=True)
    print(f"[stage1 file-overlap] {len(nodes)} PRs, {len(edges)} same-file edges "
          f"-> {len(clusters)} components; sizes(top10)={sizes[:10]}")
    print(f"[stage1] rebases_avoided(file-overlap)={rebases_avoided(clusters)}")
    biggest = max(clusters, key=lambda c: c.size)
    print(f"[stage1] LARGEST component size={biggest.size}; "
          f"members {min(biggest.members)}..{max(biggest.members)}")
    (HERE / "file-overlap-clusters.json").write_text(json.dumps(
        {"sizes": sizes, "largest": sorted(biggest.members),
         "n_components": len(clusters), "rebases_avoided": rebases_avoided(clusters)},
        indent=2))
    return biggest.members


def fetch_heads(subset: list[PrNode]) -> dict[int, str]:
    refspecs = [f"+pull/{n.number}/head:refs/pr-verify/{n.number}" for n in subset]
    # Batch fetch in chunks to keep the command line sane.
    for i in range(0, len(refspecs), 40):
        chunk = refspecs[i:i + 40]
        subprocess.run(["with-proxy", "git", "-C", GIT_DIR, "fetch", "--quiet",
                        "--no-tags", "origin", *chunk], check=True)
    shas: dict[int, str] = {}
    for n in subset:
        out = subprocess.run(["git", "-C", GIT_DIR, "rev-parse", f"refs/pr-verify/{n.number}"],
                             capture_output=True, text=True, check=True)
        shas[n.number] = out.stdout.strip()
    return shas


def stage2(nodes: list[PrNode], members: tuple[int, ...]) -> None:
    subset = [n for n in nodes if n.number in set(members)]
    print(f"\n[stage2 merge-tree] real conflict probe within candidate of {len(subset)} PRs "
          f"(~{len(subset) * (len(subset) - 1) // 2} pairs)")
    shas = fetch_heads(subset)
    host = GitHubHost(git_dir=GIT_DIR)
    real_edges: list[ConflictEdge] = []
    n = len(subset)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = subset[i], subset[j]
            paths = host.merge_tree(shas[a.number], shas[b.number])
            if paths:
                real_edges.append(ConflictEdge(a.number, b.number, paths))
        if (i + 1) % 10 == 0:
            print(f"  ...{i + 1}/{n} rows done, {len(real_edges)} real edges so far")
    real_clusters = cluster_by_conflict(subset, tuple(real_edges))
    comps = connected_components([s.number for s in subset], tuple(real_edges))
    multi = [c for c in real_clusters if c.size >= 2]
    print(f"[stage2] real conflict edges={len(real_edges)} (vs "
          f"{len(build_conflict_edges_file_overlap(subset))} same-file edges on same subset)")
    print(f"[stage2] real components: {len(comps)} total, {len(multi)} multi-PR; "
          f"sizes={sorted((len(c) for c in comps), reverse=True)[:10]}")
    print(f"[stage2] rebases_avoided(real merge-tree, this subset)={rebases_avoided(real_clusters)}")
    for c in sorted(multi, key=lambda c: -c.size)[:5]:
        print(f"    real stack size={c.size} members(base->tip)={list(c.members)} "
              f"conflict_paths(top5)={list(c.conflict_paths)[:5]}")
    (HERE / "merge-tree-clusters.json").write_text(json.dumps(
        {"subset_size": len(subset),
         "real_edges": [{"a": e.a, "b": e.b, "paths": list(e.paths)} for e in real_edges],
         "real_component_sizes": sorted((len(c) for c in comps), reverse=True),
         "multi_pr_clusters": [{"members": list(c.members),
                                "conflict_paths": list(c.conflict_paths)} for c in multi],
         "rebases_avoided": rebases_avoided(real_clusters)},
        indent=2))


def main() -> int:
    nodes = load_nodes()
    members = stage1(nodes)
    stage2(nodes, members)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
