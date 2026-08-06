# Dev-hermit main queue-depth-1 verification

**Task:** `queue-depth-1-on-dev-hermit-main-too`

**Mode:** local-only verification; external egress unavailable

**Question:** does dev-hermit main preserve in-flight completion while making
high-commit-rate sampling sparse?

## Result

The requested change is **already present**. Commit
`420bb363fff9d1bb869f44847a46d387c0a00572` changed
`.github/workflows/dev-hermit-ci.yml` from unconditional cancellation to the
same event-aware expression used by Hermit:

```yaml
concurrency:
  group: dev-hermit-tooling-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

The commit is an ancestor of the locally checked-out parent `main`
(`19feac38110af940c42c84216937e18296509254` at verification time), and the
workflow has no uncommitted diff. Reapplying it would create a duplicate
mechanism, so this verification deliberately makes **no workflow edit**.

The load-bearing truth table is:

| Event | Group identity | `cancel-in-progress` | Consequence |
|---|---|---:|---|
| `push` to `main` | stable per main ref | false | running run completes; GitHub retains at most the newest pending run |
| `pull_request` | stable per PR number | true | superseded PR-head run may be cancelled |
| `workflow_dispatch` | ref-based | false | no in-flight cancellation |

Thus commit rate changes which main SHAs are sampled, not whether the running
sample finishes. For three rapid main pushes, the expected sequence is run 1
completes, pending run 2 may be superseded by run 3, and run 3 completes; no
running main job is cancelled. The requested live three-push/one-push
observation is deferred because it requires egress and should not be simulated
with junk local commits.

## Complete dev-hermit workflow inventory

All five workflows under `.github/workflows/` were inspected:

| Workflow | Concurrency behavior on main | Disposition |
|---|---|---|
| `dev-hermit-ci.yml` | stable main group; cancellation false on push | already queue-depth-1; requested fix is present |
| `compat-envelope.yml` | non-PR group uses unique `github.run_id`; `cancel-in-progress: true` cannot collide across main runs | unchanged; does not cancel main, and preserves every path-filtered run |
| `nightly-demo-sweep.yml` | non-PR group uses unique `github.run_id`; `cancel-in-progress: true` cannot collide across main runs | unchanged; deliberate per-run culprit attribution |
| `demo-review-gate.yml` | no concurrency block | unchanged; runs do not cancel |
| `portability.yml` | no concurrency block | unchanged; runs do not cancel |

Only `dev-hermit-ci.yml` had the cancel-and-lose-data shape. Earlier measured
runtime evidence found the two unbounded hosted policy workflows cheap and the
self-hosted workflows intentionally attribution-preserving, so capping them
would discard coverage without addressing a measured cancellation problem.

## Other owned repositories — local scope audit

This is a local checkout snapshot, not a remote-fresh claim:

- **Hermit:** the product main lanes are already protected:
  `ci-portable.yml` and `ci-privileged.yml` use `cancel-in-progress: false`;
  `demo-hot-path.yml` and `merge-gate.yml` cancel only pull-request runs.
  `docs.yml` still cancels an older main documentation deployment so only the
  newest site publishes. `runner-health.yml` and `validation-levels.yml` also
  use unconditional cancellation, but are schedule/manual/merge-group
  workflows rather than main-push product signals. These are distinct
  semantics and were not changed by this parent-repository task.
- **Reverie:** two workflow files; neither has a concurrency block, so neither
  cancels main.
- **agent-utils:** six workflow files; none has a concurrency block, so none
  cancels main.

The Hermit documentation deployment is the only newly observed owned-repo
main-push cancellation outside dev-hermit. It is a latest-state deployment,
not a validation-record producer; whether completion rather than latest-state
publication should dominate there is a separate owner-policy question, not a
reason to expand this task silently.

## Local evidence

```text
git merge-base --is-ancestor 420bb363fff9d1bb869f44847a46d387c0a00572 HEAD
  => 0

git status --short -- .github
  => clean

rg -n -U '^(concurrency:|  cancel-in-progress:)' .github/workflows
  => concurrency blocks only in dev-hermit-ci, compat-envelope,
     and nightly-demo-sweep; classifications are recorded above
```

No fetch, push, GitHub API call, rapid-push acceptance run, or landing action
was performed.
