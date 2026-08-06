# Queue-depth-1 on main — which workflows cancel, and which must be left alone

**Task:** `main-queue-depth-1-not-cancel-in-progress` (P1, owner directive)
**Date:** 2026-08-06 · **Bound to:** hermit main `b64d893a`, `.github/workflows/` (9 workflows)
**Mode:** local enumeration only. **No file edited** (see *Why nothing was edited*). No egress.

---

## Result: the "only ONE cancels" finding is CONFIRMED, and it is `docs.yml`

The directive said to enumerate first because a prior finding claimed only one workflow cancels.
Independent enumeration of all 9 workflows confirms it — and names it.

| Workflow | `cancel-in-progress` | Triggers on **main push**? | Verdict |
|---|---|---|---|
| **`docs.yml`** | **`true`** (unconditional) | **YES** — `push: branches: [main]` | **the sole main-push canceller** |
| `runner-health.yml` | `true` | no — `schedule` + `workflow_dispatch` | **leave** |
| `validation-levels.yml` | `true` | no — `merge_group` + `schedule` + `workflow_dispatch` | **leave** |
| `ci-dag.yml` | *(no concurrency block)* | no — `workflow_dispatch` | leave |
| `ci-portable-autoretry.yml` | *(no concurrency block)* | no — `workflow_run` | leave |
| `ci-portable.yml` | `false` | yes | already correct |
| `ci-privileged.yml` | `false` | yes | already correct |
| `demo-hot-path.yml` | `${{ event_name == 'pull_request' }}` | yes | already correct |
| `merge-gate.yml` | `${{ event_name == 'pull_request' }}` | — | already correct |

Three workflows carry unconditional `cancel-in-progress: true`, but **only `docs.yml` can ever
cancel a main-push run.** The other two have no main-push trigger at all, so their `true` is
unreachable from the path this directive governs.

**Why the other six must not be touched** — exactly the failure mode the directive warned about:

- `ci-portable`, `ci-privileged`, `demo-hot-path`, `merge-gate` already complete every main run
  (PR #1575, landed `d5fcdbe8`). Capping them would drop per-SHA coverage for no gain.
- `ci-dag` and `ci-portable-autoretry` have **no concurrency block at all** — they are already
  unlimited. Adding a group to either would *introduce* superseding where none exists today, which
  is the opposite of the directive.
- `runner-health` is a twice-hourly poller on a fixed group. Superseding a stale health probe is
  correct behaviour; a queue of stale probes is worse than none, and it produces no per-SHA dataset.
- `validation-levels` fires on `merge_group` (keyed per head_sha) plus cron and dispatch. Its group
  includes `${{ github.event_name }}`, so lanes don't collide, and there is no main-push path.

## The one-line patch

`hermit/.github/workflows/docs.yml:15-17`, currently:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

Change `true` → `false`. That is the entire change. `cancel-in-progress: false` on a shared
`github.ref` group is native queue-depth-1: one RUNNING + one PENDING, and a newer arrival
supersedes only the **pending** run while the running one finishes.

## A caveat the directive's rationale does not cover — worth your call

The owner's reasoning is *"a sparse dataset of COMPLETE runs rather than a bunch of cancelled
runs."* That rationale is about **validation data**. `docs.yml` is a **deploy** job — it publishes
to `gh-pages` via `peaceiris/actions-gh-pages`. It produces no per-SHA dataset, so the stated
benefit does not apply to it, and its existing comment gives a deliberate reason for the current
setting: *"Supersede older runs for the same ref so documentation deploys do not accumulate behind
newer commits."*

So applying the directive here would be following its letter past its purpose. **However, I think
the change is still right, for a different reason:** cancelling a docs job *mid-push to `gh-pages`*
risks a partially-deployed docs tree. Under queue-depth-1 the final state is identical — the newest
commit's docs still win, because run 2 is superseded while pending — but no push is ever
interrupted. Same end state, strictly less risk.

That is a judgement about deploy safety rather than about datasets, so I am flagging it rather than
assuming it. If you would rather keep newest-wins-immediately for docs, the correct conclusion is
**"no workflow needs changing"** — everything on the main-push data path is already queue-depth-1.

## Status of the owner's verify-by-observation

Still **pending and unrunnable here.** The directive's acceptance test is to land three commits to
main in quick succession and confirm run 1 completes, run 2 is superseded while pending, and zero
RUNNING jobs are cancelled. That needs GitHub egress, which has been refused all session
(`api.github.com not allowlisted for agent_id agent:claude_code`). It cannot be satisfied locally by
any means — the semantics belong to GitHub's scheduler, not to the YAML.

## Why nothing was edited

`.github/workflows/` lives in the **hermit primary checkout**, and CLAUDE.md Hard Invariant 1 is
*"Never do feature development in a primary checkout"* — all edits belong in an assigned worktree
slot. No slot is assigned to this task, and with egress down the change could not be pushed,
PR'd, or observed anyway. The patch above is one line and ready to apply in a slot.

## Provenance

| Claim | Status |
|---|---|
| All 9 workflows' `concurrency` blocks and `on:` triggers | **read this session** @ `b64d893a` |
| `docs.yml` is the sole main-push canceller | **derived this session** from the above |
| PR #1575 / `d5fcdbe8` fixed ci-portable, ci-privileged, demo-hot-path | inherited from task notes; the *resulting config* is verified above, the PR state is not (egress down) |
