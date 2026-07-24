# PR conflict graph and landing planner

`pr_conflict_graph.py` inspects every open pull request in `rrnewton/hermit`,
simulates pairwise merges, detects stacked branches, and recommends a landing
and rebase sequence. It does not modify branches, merge PRs, or change a
worktree.

Run it from anywhere:

```bash
~/work/dev-hermit/scripts/pr_conflict_graph.py --human
~/work/dev-hermit/scripts/pr_conflict_graph.py > /tmp/hermit-pr-graph.json
~/work/dev-hermit/scripts/pr_conflict_graph.py --base main --human
~/work/dev-hermit/scripts/pr_conflict_graph.py --prs 25,42,81,89,90 --human
```

The default scope is all open PRs. `--base main` restricts the graph to PRs
targeting `main` plus PRs transitively stacked on their head branches. GitHub
API calls and Git fetches use `with-proxy` by default. Override the local clone
with `--git-dir` or the remote with `--remote`.

## Output

JSON output contains:

- `conflict_edges`: pairs that `git merge-tree --write-tree` confirms cannot
  currently merge cleanly.
- `file_overlap_edges`: conservative semantic-review risks, including pairs
  that Git can merge automatically.
- `ordering_edges`: explicit base-branch dependencies and commit-ancestry
  dependencies.
- `stacks`: root-to-leaf dependency chains.
- `suggested_landing_batches`: eventual pairwise conflict-free batches in
  dependency order.
- `suggested_rebase_plan`: later PRs that should be rebased or retargeted after
  earlier conflicting or prerequisite PRs land.
- `ready_now`: the first batch after excluding drafts, base-conflicting PRs,
  and PRs depending on held work.
- `held_prs`: why an open PR is not currently landable.

The landing heuristic prioritizes dependency roots, then lower conflict
degree, smaller diffs, older PRs, and finally PR number. This generally opens
the largest conflict-free front while unblocking stacks, but it is not a proof
of global optimality and is not merge authorization. A landing agent must
still verify review and CI state at the current head.

Fetched refs are stored under `refs/pr-conflict-graph/`. Pairwise analysis is
read-only with respect to branches and working trees, although `merge-tree`
may add unreachable temporary tree objects to the local Git object database.

Run the offline unit tests with:

```bash
python3 ~/work/dev-hermit/scripts/test_pr_conflict_graph.py
```
