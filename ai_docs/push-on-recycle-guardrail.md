# Push-on-Recycle Durability Guardrail

Status: adopted 2026-08-03 (hermit-coord). Task: `push-on-recycle-structural-guardrail`.

## Problem

A fleet sweep on 2026-08-03 found **three distinct slots with unpushed committed
work in a single day** (sabre 11 commits; `coord-fix` — a coordinator hotfix
branch; `ci`). Agents are recycled constantly; every recycle without a push is a
potential work-loss event (the box can die with the only copy of a branch local).
This is a systemic habit gap, not one bad agent — so the fix must be structural,
not another reminder.

## Fix (three pieces)

### 1. Push-on-recycle wired into `release-worktree.rs`

`--clean` now runs a **pre-recycle guardrail** before removing anything: for each
product child on a branch it verifies HEAD is durable on origin, and refuses to
release otherwise. `--push` pushes at-risk branches (with-proxy), re-verifies,
and only then removes ("push-then-remove"). `--force` is the explicit,
loud override that discards the durability guarantee.

### 2. Pre-recycle hook that refuses silent release

The guardrail is authoritative — it uses `git ls-remote` (like the fleet sweep),
NOT `@{upstream}`. A branch cut from `origin/main` *tracks* `origin/main`, so an
`@{upstream}` count wrongly flags already-pushed feature commits (this is exactly
why the earlier sweep saw "sabre 11 unpushed" after it had been pushed to its
feature branch). Durability rule for a child's HEAD:

- **safe** if HEAD is an ancestor of `origin/main` (no unique commits — already on
  the mainline; checked locally, no network), OR
- **safe** if `origin/<branch>` exists at exactly HEAD, or is strictly ahead of
  HEAD (origin carries all our commits), OR
- **at risk** otherwise → `--clean` refuses unless `--push`/`--force`.

Detached checkouts parked at a pinned gitlink are safe (nothing to push).

### 3. Periodic sweep guardrail — `scripts/verify-slot-pushed.sh`

Standalone authoritative checker (same ls-remote logic) over one slot
(`--slot X`, a reusable pre-recycle assertion) or every slot (fleet sweep). Exit
0 = all durable, 1 = at-risk work found. Installed as an **hourly cron** so drift
is caught even outside the recycle path:

```cron
15 * * * * /home/newton/work/dev-hermit/scripts/verify-slot-pushed.sh --quiet \
  >> /home/newton/.local/state/worktree-pushed-guardrail.log 2>&1
```

(The script is self-locating via `$0`, so it does not depend on cron's cwd.)

## Demonstration (a slot with unpushed commits CANNOT be silently released)

Throwaway slot `guardrail-demo` on `wip/guardrail-unpushed-proof` with one
local-only commit `d6a7ad69`:

1. `verify-slot-pushed.sh --slot guardrail-demo` → **AT-RISK** ("branch not on
   origin"), exit 1.
2. `release-worktree.rs --slot guardrail-demo --clean` → **REFUSED**
   ("REFUSING to release … committed work not on origin"), exit 1; slot and
   commit still present afterward.
3. `release-worktree.rs --slot guardrail-demo --clean --push` → pushed →
   re-verified `d6a7ad69` on origin → *then* removed; exit 0.

Demo artifacts (throwaway slot, local + remote branch) were fully cleaned up.

## Files

- `scripts/release-worktree.rs` — `--push` + ls-remote pre-recycle refusal.
- `scripts/verify-slot-pushed.sh` — standalone authoritative verifier / cron.
- machine-local: hourly crontab entry (not tracked; recorded here + in the task).

Note: honors the hermit-main incident halt — the guardrail only ever pushes
*feature* branches, never `main`, and never discards work.
