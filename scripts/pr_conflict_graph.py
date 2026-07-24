#!/usr/bin/env python3
"""Build a conflict graph and landing plan for open GitHub pull requests.

The script fetches PR metadata through ``with-proxy gh``, fetches PR and base
refs through ``with-proxy git``, and uses ``git merge-tree`` to test real
pairwise merge conflicts without changing a worktree. JSON is the default
output; pass ``--human`` for a landing-agent summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "rrnewton/hermit"
DEFAULT_GIT_DIR = str(WORKSPACE_ROOT / "hermit")
GH_FIELDS = ",".join(
    [
        "number",
        "title",
        "author",
        "baseRefName",
        "headRefName",
        "headRefOid",
        "isDraft",
        "mergeable",
        "reviewDecision",
        "createdAt",
        "updatedAt",
        "additions",
        "deletions",
        "changedFiles",
    ]
)


class RawAuthor(TypedDict, total=False):
    login: str


class RawPr(TypedDict):
    number: int
    title: str
    author: RawAuthor
    baseRefName: str
    headRefName: str
    headRefOid: str
    isDraft: bool
    mergeable: str
    reviewDecision: str
    createdAt: str
    updatedAt: str
    additions: int
    deletions: int
    changedFiles: int


@dataclass
class PrInfo:
    number: int
    title: str
    author: str
    head_ref: str
    base_ref: str
    api_head_sha: str
    is_draft: bool
    mergeable: str
    review_decision: str
    created_at: str
    updated_at: str
    additions: int
    deletions: int
    api_changed_files: int
    head_sha: str = ""
    base_sha: str = ""
    files: frozenset[str] = field(default_factory=frozenset)
    base_conflict_paths: tuple[str, ...] = ()

    @property
    def size(self) -> int:
        return self.additions + self.deletions

    @property
    def base_conflicting(self) -> bool:
        return bool(self.base_conflict_paths) or self.mergeable == "CONFLICTING"


@dataclass(frozen=True)
class ConflictEdge:
    a: int
    b: int
    paths: tuple[str, ...]


@dataclass(frozen=True)
class OverlapEdge:
    a: int
    b: int
    paths: tuple[str, ...]


@dataclass(frozen=True)
class OrderingEdge:
    before: int
    after: int
    reason: str


@dataclass(frozen=True)
class RebaseStep:
    pr: int
    after: tuple[int, ...]
    reasons: tuple[str, ...]


@dataclass
class LandingPlan:
    batches: list[list[int]]
    rebase_steps: list[RebaseStep]
    cycle_nodes: list[int]


class CommandError(RuntimeError):
    def __init__(self, cmd: list[str], proc: subprocess.CompletedProcess[str]) -> None:
        detail = proc.stderr.strip() or proc.stdout.strip()
        super().__init__(
            f"command failed ({proc.returncode}): {shlex.join(cmd)}"
            + (f"\n{detail}" if detail else "")
        )


def run_process(
    cmd: list[str],
    *,
    cwd: str | None = None,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode not in allowed_returncodes:
        raise CommandError(cmd, proc)
    return proc


def run(
    cmd: list[str],
    *,
    cwd: str | None = None,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> str:
    return run_process(cmd, cwd=cwd, allowed_returncodes=allowed_returncodes).stdout


def network_command(wrapper: list[str], command: list[str]) -> list[str]:
    return [*wrapper, *command]


def fetch_open_prs(wrapper: list[str], repo: str) -> list[RawPr]:
    output = run(
        network_command(
            wrapper,
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                "500",
                "--json",
                GH_FIELDS,
            ],
        )
    )
    return cast(list[RawPr], json.loads(output)) if output.strip() else []


def select_prs(
    raw_prs: list[RawPr],
    base: str | None,
    only_numbers: set[int] | None,
) -> list[RawPr]:
    selected = list(raw_prs)
    if base is not None:
        included = {int(pr["number"]) for pr in selected if pr["baseRefName"] == base}
        changed = True
        while changed:
            changed = False
            included_heads = {
                pr["headRefName"] for pr in selected if int(pr["number"]) in included
            }
            for pr in selected:
                number = int(pr["number"])
                if number not in included and pr["baseRefName"] in included_heads:
                    included.add(number)
                    changed = True
        selected = [pr for pr in selected if int(pr["number"]) in included]
    if only_numbers is not None:
        selected = [pr for pr in selected if int(pr["number"]) in only_numbers]
    return sorted(selected, key=lambda pr: int(pr["number"]))


def local_base_ref(base: str) -> str:
    digest = hashlib.sha256(base.encode()).hexdigest()[:16]
    return f"refs/pr-conflict-graph/base-{digest}"


def fetch_ref(
    git_dir: str,
    remote: str,
    source: str,
    destination: str,
    wrapper: list[str],
) -> str:
    run(
        network_command(
            wrapper,
            [
                "git",
                "fetch",
                "--quiet",
                "--no-tags",
                remote,
                f"+{source}:{destination}",
            ],
        ),
        cwd=git_dir,
    )
    return run(["git", "rev-parse", destination], cwd=git_dir).strip()


def changed_files(git_dir: str, base_sha: str, head_sha: str) -> frozenset[str]:
    merge_base = run(["git", "merge-base", base_sha, head_sha], cwd=git_dir).strip()
    output = run(
        ["git", "diff", "--name-only", f"{merge_base}...{head_sha}"],
        cwd=git_dir,
    )
    return frozenset(line for line in output.splitlines() if line)


def parse_merge_tree_paths(output: str) -> tuple[str, ...]:
    lines = output.splitlines()
    if len(lines) < 2:
        return ()
    paths: list[str] = []
    for line in lines[1:]:
        candidate = line.strip()
        if not candidate:
            break
        paths.append(candidate)
    return tuple(sorted(set(paths)))


def merge_conflict_paths(git_dir: str, left: str, right: str) -> tuple[str, ...]:
    proc = run_process(
        [
            "git",
            "merge-tree",
            "--write-tree",
            "--name-only",
            "--messages",
            left,
            right,
        ],
        cwd=git_dir,
        allowed_returncodes=(0, 1),
    )
    if proc.returncode == 0:
        return ()
    return parse_merge_tree_paths(proc.stdout)


def is_ancestor(git_dir: str, ancestor: str, descendant: str) -> bool:
    proc = run_process(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=git_dir,
        allowed_returncodes=(0, 1),
    )
    return proc.returncode == 0


def collect_prs(
    *,
    git_dir: str,
    remote: str,
    repo: str,
    base: str | None,
    only_numbers: set[int] | None,
    wrapper: list[str],
    quiet: bool,
) -> list[PrInfo]:
    raw_prs = select_prs(fetch_open_prs(wrapper, repo), base, only_numbers)
    base_shas: dict[str, str] = {}
    nodes: list[PrInfo] = []

    for index, pr in enumerate(raw_prs, start=1):
        number = int(pr["number"])
        base_ref = str(pr["baseRefName"])
        if not quiet:
            print(f"fetching PR #{number} ({index}/{len(raw_prs)})", file=sys.stderr)
        if base_ref not in base_shas:
            base_shas[base_ref] = fetch_ref(
                git_dir,
                remote,
                f"refs/heads/{base_ref}",
                local_base_ref(base_ref),
                wrapper,
            )
        head_sha = fetch_ref(
            git_dir,
            remote,
            f"refs/pull/{number}/head",
            f"refs/pr-conflict-graph/pr-{number}",
            wrapper,
        )
        api_head_sha = str(pr["headRefOid"])
        if head_sha != api_head_sha:
            raise RuntimeError(
                f"PR #{number} changed during collection: API={api_head_sha}, fetched={head_sha}; rerun"
            )
        base_sha = base_shas[base_ref]
        author = pr.get("author") or {}
        node = PrInfo(
            number=number,
            title=str(pr["title"]),
            author=str(author.get("login") or "unknown"),
            head_ref=str(pr["headRefName"]),
            base_ref=base_ref,
            api_head_sha=api_head_sha,
            is_draft=bool(pr["isDraft"]),
            mergeable=str(pr["mergeable"]),
            review_decision=str(pr["reviewDecision"]),
            created_at=str(pr["createdAt"]),
            updated_at=str(pr["updatedAt"]),
            additions=int(pr["additions"]),
            deletions=int(pr["deletions"]),
            api_changed_files=int(pr["changedFiles"]),
            head_sha=head_sha,
            base_sha=base_sha,
            files=changed_files(git_dir, base_sha, head_sha),
        )
        node.base_conflict_paths = merge_conflict_paths(git_dir, base_sha, head_sha)
        nodes.append(node)
    return nodes


def build_ordering_edges(
    nodes: list[PrInfo], git_dir: str | None
) -> list[OrderingEdge]:
    edges: dict[tuple[int, int], OrderingEdge] = {}
    by_head_ref = {node.head_ref: node for node in nodes}
    for node in nodes:
        predecessor = by_head_ref.get(node.base_ref)
        if predecessor is not None and predecessor.number != node.number:
            edge = OrderingEdge(predecessor.number, node.number, "base-ref")
            edges[(edge.before, edge.after)] = edge

    if git_dir is not None:
        for index, left in enumerate(nodes):
            for right in nodes[index + 1 :]:
                if left.head_sha == right.head_sha:
                    continue
                if is_ancestor(git_dir, left.head_sha, right.head_sha):
                    edge = OrderingEdge(left.number, right.number, "ancestry")
                    edges.setdefault((edge.before, edge.after), edge)
                elif is_ancestor(git_dir, right.head_sha, left.head_sha):
                    edge = OrderingEdge(right.number, left.number, "ancestry")
                    edges.setdefault((edge.before, edge.after), edge)
    return sorted(edges.values(), key=lambda edge: (edge.before, edge.after))


def build_pair_edges(
    nodes: list[PrInfo], git_dir: str | None, quiet: bool = True
) -> tuple[list[ConflictEdge], list[OverlapEdge]]:
    conflicts: list[ConflictEdge] = []
    overlaps: list[OverlapEdge] = []
    pair_count = len(nodes) * (len(nodes) - 1) // 2
    pair_index = 0
    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            pair_index += 1
            overlap = tuple(sorted(left.files & right.files))
            if overlap:
                overlaps.append(OverlapEdge(left.number, right.number, overlap))
            if git_dir is not None:
                if not quiet and (pair_index == 1 or pair_index % 100 == 0):
                    print(
                        f"checking merge pair {pair_index}/{pair_count}",
                        file=sys.stderr,
                    )
                conflict_paths = merge_conflict_paths(
                    git_dir, left.head_sha, right.head_sha
                )
                if conflict_paths:
                    conflicts.append(
                        ConflictEdge(left.number, right.number, conflict_paths)
                    )
    return conflicts, overlaps


def has_path(
    adjacency: dict[int, set[int]], start: int, target: int, skip: tuple[int, int]
) -> bool:
    pending = [start]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for child in adjacency.get(current, set()):
            if (current, child) == skip:
                continue
            if child == target:
                return True
            pending.append(child)
    return False


def reduce_ordering_edges(edges: list[OrderingEdge]) -> list[OrderingEdge]:
    adjacency: dict[int, set[int]] = {}
    for edge in edges:
        adjacency.setdefault(edge.before, set()).add(edge.after)
    return [
        edge
        for edge in edges
        if not has_path(adjacency, edge.before, edge.after, (edge.before, edge.after))
    ]


def build_stacks(edges: list[OrderingEdge]) -> list[list[int]]:
    reduced = reduce_ordering_edges(edges)
    children: dict[int, set[int]] = {}
    parents: dict[int, set[int]] = {}
    involved: set[int] = set()
    for edge in reduced:
        children.setdefault(edge.before, set()).add(edge.after)
        parents.setdefault(edge.after, set()).add(edge.before)
        involved.update((edge.before, edge.after))
    roots = sorted(node for node in involved if not parents.get(node))
    stacks: list[list[int]] = []

    def visit(node: int, path: list[int]) -> None:
        next_nodes = sorted(children.get(node, set()))
        if not next_nodes:
            if len(path) > 1:
                stacks.append(path)
            return
        for child in next_nodes:
            if child in path:
                continue
            visit(child, [*path, child])

    for root in roots:
        visit(root, [root])
    return stacks


def plan_landing(
    nodes: list[PrInfo],
    conflict_edges: list[ConflictEdge],
    ordering_edges: list[OrderingEdge],
) -> LandingPlan:
    numbers = {node.number for node in nodes}
    by_number = {node.number: node for node in nodes}
    conflicts: dict[int, set[int]] = {number: set() for number in numbers}
    predecessors: dict[int, set[int]] = {number: set() for number in numbers}
    children: dict[int, set[int]] = {number: set() for number in numbers}
    for edge in conflict_edges:
        if edge.a in numbers and edge.b in numbers:
            conflicts[edge.a].add(edge.b)
            conflicts[edge.b].add(edge.a)
    for edge in ordering_edges:
        if edge.before in numbers and edge.after in numbers:
            predecessors[edge.after].add(edge.before)
            children[edge.before].add(edge.after)

    descendant_cache: dict[int, int] = {}

    def descendant_count(number: int, visiting: set[int] | None = None) -> int:
        if number in descendant_cache:
            return descendant_cache[number]
        visiting = set() if visiting is None else visiting
        if number in visiting:
            return 0
        reachable: set[int] = set()
        pending = list(children[number])
        while pending:
            child = pending.pop()
            if child in reachable or child in visiting:
                continue
            reachable.add(child)
            pending.extend(children[child])
        descendant_cache[number] = len(reachable)
        return len(reachable)

    remaining = set(numbers)
    placed: set[int] = set()
    batches: list[list[int]] = []
    cycle_nodes: list[int] = []
    while remaining:
        ready = [
            number for number in remaining if predecessors[number].issubset(placed)
        ]
        if not ready:
            cycle_nodes = sorted(remaining)
            batches.extend([[number] for number in cycle_nodes])
            break
        ready.sort(
            key=lambda number: (
                -descendant_count(number),
                len(conflicts[number] & remaining),
                by_number[number].size,
                by_number[number].created_at,
                number,
            )
        )
        batch: list[int] = []
        for number in ready:
            if all(peer not in conflicts[number] for peer in batch):
                batch.append(number)
        batches.append(batch)
        remaining.difference_update(batch)
        placed.update(batch)

    batch_of = {
        number: index for index, batch in enumerate(batches) for number in batch
    }
    rebase_steps: list[RebaseStep] = []
    for number in sorted(numbers, key=lambda item: (batch_of.get(item, 0), item)):
        earlier_conflicts = sorted(
            peer
            for peer in conflicts[number]
            if batch_of.get(peer, 0) < batch_of.get(number, 0)
        )
        earlier_dependencies = sorted(
            peer
            for peer in predecessors[number]
            if batch_of.get(peer, 0) < batch_of.get(number, 0)
        )
        after = tuple(sorted(set(earlier_conflicts + earlier_dependencies)))
        reasons: list[str] = []
        if earlier_conflicts:
            reasons.append("pair-conflict")
        if earlier_dependencies:
            reasons.append("stack-dependency")
        if after:
            rebase_steps.append(RebaseStep(number, after, tuple(reasons)))
    return LandingPlan(batches, rebase_steps, cycle_nodes)


def held_reasons(
    nodes: list[PrInfo], ordering_edges: list[OrderingEdge]
) -> dict[int, list[str]]:
    reasons: dict[int, list[str]] = {}
    for node in nodes:
        node_reasons: list[str] = []
        if node.is_draft:
            node_reasons.append("draft")
        if node.base_conflict_paths:
            node_reasons.append("local-base-conflict")
        if node.mergeable == "CONFLICTING":
            node_reasons.append("github-base-conflicting")
        if node_reasons:
            reasons[node.number] = node_reasons

    changed = True
    while changed:
        changed = False
        for edge in ordering_edges:
            if edge.before in reasons and edge.after not in reasons:
                reasons[edge.after] = [f"depends-on-held:#{edge.before}"]
                changed = True
    return reasons


def graph_json(
    repo: str,
    nodes: list[PrInfo],
    conflicts: list[ConflictEdge],
    overlaps: list[OverlapEdge],
    ordering: list[OrderingEdge],
    eventual: LandingPlan,
) -> dict[str, object]:
    holds = held_reasons(nodes, ordering)
    eligible = [node for node in nodes if node.number not in holds]
    eligible_numbers = {node.number for node in eligible}
    eligible_conflicts = [
        edge
        for edge in conflicts
        if edge.a in eligible_numbers and edge.b in eligible_numbers
    ]
    eligible_ordering = [
        edge
        for edge in ordering
        if edge.before in eligible_numbers and edge.after in eligible_numbers
    ]
    ready_plan = plan_landing(eligible, eligible_conflicts, eligible_ordering)
    return {
        "repository": repo,
        "nodes": [
            {
                "pr": node.number,
                "title": node.title,
                "author": node.author,
                "head_ref": node.head_ref,
                "head": node.head_sha,
                "base_ref": node.base_ref,
                "base": node.base_sha,
                "draft": node.is_draft,
                "mergeable": node.mergeable,
                "review_decision": node.review_decision,
                "additions": node.additions,
                "deletions": node.deletions,
                "files_count": len(node.files),
                "base_conflict_paths": list(node.base_conflict_paths),
            }
            for node in nodes
        ],
        "conflict_edges": [
            {"a": edge.a, "b": edge.b, "paths": list(edge.paths)} for edge in conflicts
        ],
        "file_overlap_edges": [
            {"a": edge.a, "b": edge.b, "paths": list(edge.paths)} for edge in overlaps
        ],
        "ordering_edges": [
            {"before": edge.before, "after": edge.after, "reason": edge.reason}
            for edge in ordering
        ],
        "stacks": build_stacks(ordering),
        "suggested_landing_batches": eventual.batches,
        "suggested_rebase_plan": [
            {
                "pr": step.pr,
                "after": list(step.after),
                "reasons": list(step.reasons),
            }
            for step in eventual.rebase_steps
        ],
        "ready_landing_batches": ready_plan.batches,
        "ready_now": ready_plan.batches[0] if ready_plan.batches else [],
        "held_prs": [
            {"pr": number, "reasons": reasons}
            for number, reasons in sorted(holds.items())
        ],
        "ordering_cycles": eventual.cycle_nodes,
        "heuristic": (
            "dependency roots first; then low conflict degree, small diff, age, and PR number; "
            "each batch is pairwise conflict-free"
        ),
    }


def render_human(data: dict[str, object]) -> str:
    nodes = cast(list[dict[str, object]], data["nodes"])
    conflicts = cast(list[dict[str, object]], data["conflict_edges"])
    overlaps = cast(list[dict[str, object]], data["file_overlap_edges"])
    ordering = cast(list[dict[str, object]], data["ordering_edges"])
    stacks = cast(list[list[int]], data["stacks"])
    batches = cast(list[list[int]], data["suggested_landing_batches"])
    ready_now = cast(list[int], data["ready_now"])
    held = cast(list[dict[str, object]], data["held_prs"])
    rebase_plan = cast(list[dict[str, object]], data["suggested_rebase_plan"])
    base_counts = Counter(str(node["base_ref"]) for node in nodes)
    base_summary = ", ".join(
        f"{base}={count}" for base, count in sorted(base_counts.items())
    )
    lines = [
        f"Repository: {data['repository']}",
        (
            f"{len(nodes)} open PR(s), {len(conflicts)} actual pair conflict(s), "
            f"{len(overlaps)} file-overlap risk pair(s), {len(ordering)} ordering edge(s)"
        ),
        f"Base branches: {base_summary or '(none)'}",
        "",
        "Detected stacks:",
    ]
    if stacks:
        lines.extend(
            "  " + " -> ".join(f"#{number}" for number in stack) for stack in stacks
        )
    else:
        lines.append("  (none)")
    lines.extend(["", "Actual pair conflicts:"])
    if conflicts:
        for edge in conflicts:
            paths = cast(list[str], edge["paths"])
            preview = ", ".join(paths[:5])
            more = f" (+{len(paths) - 5} more)" if len(paths) > 5 else ""
            lines.append(f"  #{edge['a']} <-> #{edge['b']}: {preview}{more}")
    else:
        lines.append("  (none)")
    lines.extend(
        [
            "",
            "Suggested eventual landing order (each batch is pairwise conflict-free):",
        ]
    )
    if batches:
        for index, batch in enumerate(batches, start=1):
            lines.append(
                f"  batch {index}: " + ", ".join(f"#{number}" for number in batch)
            )
    else:
        lines.append("  (no open PRs)")
    lines.extend(
        [
            "",
            "Ready now: " + (", ".join(f"#{number}" for number in ready_now) or "none"),
            "",
            "Held PRs:",
        ]
    )
    if held:
        for item in held:
            reasons = ", ".join(cast(list[str], item["reasons"]))
            lines.append(f"  #{item['pr']}: {reasons}")
    else:
        lines.append("  (none)")
    lines.extend(["", "Suggested rebase/retarget sequence:"])
    if rebase_plan:
        for step in rebase_plan:
            after = ", ".join(f"#{number}" for number in cast(list[int], step["after"]))
            reasons = ", ".join(cast(list[str], step["reasons"]))
            lines.append(f"  #{step['pr']} after {after} lands ({reasons})")
    else:
        lines.append("  (none)")
    overlap_only = len(overlaps) - len(
        {(int(edge["a"]), int(edge["b"])) for edge in conflicts}
    )
    lines.extend(
        [
            "",
            f"Overlap-only semantic review risks: {max(overlap_only, 0)} pair(s); see JSON for paths.",
            "The order is a deterministic heuristic, not an authorization to merge.",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--base",
        default=None,
        help="Restrict to PRs targeting this base plus their transitive stacks (default: all open PRs)",
    )
    parser.add_argument(
        "--prs", help="Comma-separated PR numbers to inspect (default: all in scope)"
    )
    parser.add_argument("--git-dir", default=DEFAULT_GIT_DIR)
    parser.add_argument("--remote", default="origin")
    parser.add_argument(
        "--network-wrapper",
        default="with-proxy",
        help="Command prefix for gh and git fetch (default: with-proxy; pass an empty string for none)",
    )
    parser.add_argument("--human", action="store_true")
    parser.add_argument("--json-indent", type=int, default=2)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    wrapper = shlex.split(args.network_wrapper) if args.network_wrapper else []
    only_numbers = None
    if args.prs:
        only_numbers = {
            int(value.strip()) for value in args.prs.split(",") if value.strip()
        }
    nodes = collect_prs(
        git_dir=args.git_dir,
        remote=args.remote,
        repo=args.repo,
        base=args.base,
        only_numbers=only_numbers,
        wrapper=wrapper,
        quiet=args.quiet,
    )
    ordering = build_ordering_edges(nodes, args.git_dir)
    conflicts, overlaps = build_pair_edges(nodes, args.git_dir, args.quiet)
    eventual = plan_landing(nodes, conflicts, ordering)
    data = graph_json(args.repo, nodes, conflicts, overlaps, ordering, eventual)
    if args.human:
        print(render_human(data))
    else:
        print(json.dumps(data, indent=args.json_indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (CommandError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
