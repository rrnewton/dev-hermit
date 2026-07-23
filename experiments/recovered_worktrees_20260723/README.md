# Recovered stale worktrees (2026-07-23)

These mail patches preserve commits from clean top-level Hermit worktrees that
violated the parent workspace layout. Their original remote branches had been
deleted, and the commits were not patch-equivalent to `origin/main` at cleanup
time. The full checkouts remain as ignored, machine-local archives under
`experiments/`; these text patches are the durable recovery mechanism.

| Archived checkout | Branch | Tip | Patch |
| --- | --- | --- | --- |
| `experiments/hermit-wave7-safe_20260723` | `impl-fix-proc-minimal-v2-slot12` | `f115e49b7f38c93c7449ea2c4d730d712f40d98d` | `hermit-wave7-safe.patch` |
| `experiments/hermit-verify-keeplogs_20260723` | `impl-verify-keep-logs` | `660a803a1c5e2c41a9352205a8d20d142d633190` | `hermit-verify-keeplogs.patch` |
| `experiments/landing-nonreview_20260723` | `land-pr-131` | `974e7544a5e0c52012c7ff12b96de99d1bedf38f` | `landing-nonreview.patch` |

The first and third patches contain two commits; the second contains one.
Review and apply a patch from a clean Hermit feature worktree with, for example:

```bash
git am /path/to/hermit-verify-keeplogs.patch
```

The `experiments/reverie-pr18-fix_20260723` checkout is also retained locally,
but has no mail patch because its tip,
`128e4b4e4a33c168f9be2a4d7c395af20eea21f2`, is already reachable from
Reverie `origin/main`.
