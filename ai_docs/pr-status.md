# Pull Request Status Tracker

Snapshot: 2026-07-21 23:25 UTC, captured with the GitHub CLI. This file is a
handoff snapshot, not an automated source of truth. Refresh live state before
review, merge, or release decisions.

## Refresh commands

```bash
export HTTPS_PROXY=http://fwdproxy:8080

gh pr list -R rrnewton/hermit --state open --limit 100 \
  --json number,title,isDraft,mergeStateStatus,statusCheckRollup,headRefName,url
gh pr list -R rrnewton/reverie --state open --limit 100 \
  --json number,title,isDraft,mergeStateStatus,statusCheckRollup,headRefName,url
gh pr list -R rrnewton/dev-hermit --state open --limit 100 \
  --json number,title,isDraft,mergeStateStatus,statusCheckRollup,headRefName,url
```

For a merge candidate, also run `gh pr checks`, inspect the complete diff, and
read review comments. "Hosted green" is not "CI green" when the self-hosted
job is failed or queued.

## rrnewton/hermit

| PR | Draft | Hosted CI | Self-hosted CI | Disposition at snapshot |
| --- | --- | --- | --- | --- |
| [#30 Add portable PMU RCB skid benchmark](https://github.com/rrnewton/hermit/pull/30) | Yes | Running | Queued | New benchmark PR; wait for both lanes and review |
| [#29 Add permanent concurrency chaos stress suite](https://github.com/rrnewton/hermit/pull/29) | Yes | Passed | Queued | Mergeable content locally validated; not CI-green while runner is offline |
| [#27 Support CLONE_VFORK scheduling](https://github.com/rrnewton/hermit/pull/27) | Yes | Passed | Queued | Explicit human-review hold |
| [#25 Add deterministic QEMU syscall handling](https://github.com/rrnewton/hermit/pull/25) | Yes | No reported checks | No reported checks | Explicit human-review hold; includes `ppoll`/vectored I/O/futex changes |
| [#8 Complete portable setup for expanded PMU CI](https://github.com/rrnewton/hermit/pull/8) | No | Passed | Failed | Blocked by mount `EPERM` in all six `hermit_modes` tests |
| [#7 Add schedule search E2E tests to CI](https://github.com/rrnewton/hermit/pull/7) | No | Passed | Failed | Blocked by AMD SpecLockMap PMU warning, Reverie stack panic, and timeout |

Do not merge #7 or #8 as green. Their failures expose infrastructure/backend
requirements that the PRs are intended to test. #25 and #27 require the
requested user review even if CI later turns green.

## rrnewton/reverie

| PR | Draft | Hosted CI | Self-hosted CI | Disposition at snapshot |
| --- | --- | --- | --- | --- |
| [#3 Reduce AMD EPYC 9D85 PMU skid margin](https://github.com/rrnewton/reverie/pull/3) | Yes | Passed | Queued | Benchmark/optimization review; wait for hardware evidence |
| [#1 Restore and stabilize experimental SaBRe backend](https://github.com/rrnewton/reverie/pull/1) | Yes | Failed | Queued | Explicit human-review hold; backend remains experimental |

## rrnewton/dev-hermit

| PR | Draft | CI | Disposition at snapshot |
| --- | --- | --- | --- |
| [#1 Add Hermit development coordinator plugin](https://github.com/rrnewton/dev-hermit/pull/1) | Yes | No checks configured | Merge state clean; requires parent-policy review |

## Recently merged security changes

- Hermit [#28](https://github.com/rrnewton/hermit/pull/28), merge commit
  `5b9a2d31411695140d628664dd67fa67799dd08d`, restricts the self-hosted PR job
  to PRs authored by `rrnewton` while preserving push runs.
- Reverie [#2](https://github.com/rrnewton/reverie/pull/2), merge commit
  `f51c639a4fc65bc7417a838729f26746d241896a`, applies the author/actor gate and
  preserves the `REVERIE_SELF_HOSTED` variable.

Both hosted PR jobs passed before merge. Their self-hosted checks and the
post-merge push jobs were queued because both registered runners were offline.

## Merge policy

1. Preserve explicit review holds regardless of CI state.
2. Require every configured required lane to complete successfully; classify a
   queued offline runner as an infrastructure blocker, not a pass.
3. Read self-hosted logs for capability, CPU, PMU, and namespace failures
   before attributing a failure to a patch.
4. Record the merged SHA, exact checks, runner identity, and known skips.
5. Re-read this tracker against live GitHub state; do not merge from the
   snapshot alone.
