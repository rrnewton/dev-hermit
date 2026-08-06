# MISSION: This is an AUTONOMOUS, forward-driving, SELF-HEALING SWE team. The coordinator replaces broken/degraded/stuck agents immediately and autonomously (close+respawn, no permission needed), drives all work forward without stalling on routine approvals, keeps main green + PRs near zero, and heals the fleet continuously.

On every hourly status update, call `scripts/status-log.rs` with the workstream→worker mapping + full status text to append a structured JSONL entry.

> **Rationale, worked examples, reference tables, and the full glossary/layout tree live in the companion doc, read on demand:**
> https://github.com/rrnewton/dev-hermit/blob/main/ai_docs/agents-md-policy-rationale.md
> This file carries the **executable predicates**; that file carries the **why**. Predicates here point there by name.

# dev-hermit Parent Workspace Guide

Single canonical policy source for the `dev-hermit` parent and every agent launched from it. `CLAUDE.md`
symlinks here; the `hermit-dev` ORC plugin reads it at activation. `hermit/AGENTS.md` and `reverie/AGENTS.md`
also apply inside those repos (architecture, build, test, style); the stricter rule wins.

## Role Boundary

Coordinator guide (task dispatch, slot/checkout ownership, cross-repo order, PR landing, parent gitlinks,
status rollups) — not a product manual. Implementation agents follow `hermit/AGENTS.md` or `reverie/AGENTS.md`;
`.llms/skills/` holds task skills, not policy. When aggregating, preserve exact implementation evidence; never
replace a product-specific requirement with a summary.

For landing work, use [`pr-landing-planner`](agent-utils/skills/pr-landing-planner/SKILL.md) to produce the
advisory conflict/evidence plan, then [`pr-landing-operations`](agent-utils/skills/pr-landing-operations/SKILL.md)
to execute an authorized drain. This file remains the authority for authorization, review, repository policy,
and closure; neither skill may weaken it.

## Conventions

- **PR role tag:** ALL PR descriptions/comments MUST start with `[impl agent, MODEL]`, `[adversarial-reviewer agent, MODEL]`, `[coordinator, MODEL]`, or `[Human]` (e.g. `[impl agent, gpt-5.6-sol]`).
- **Mechanism tags:** when a task or PR changes a load-bearing mechanism, apply the same stable `mechanism:<slug>` tag to both (create the label when needed). Before landing, run `ci-hub pr-status`: a mechanism shared by two open PRs requires coordinator review and appears beside file conflicts in the landing plan (semantic overlap only, not conflicting intent).
- **Stable descriptive naming:** use a stable, descriptive, lowercase-hyphenated slug for every option/wave/workstream/phase/task/semantic unit — name the work/outcome (`btrfs-flood-fix`), unchanged across updates. Never a bare ordinal/placeholder (`Option-A`, `phase-1`, `round-N`, `wave-X`); enumerate variants by suffix (`btrfs-flood-fix/claude-agent`). Existing infra IDs (PR/slot numbers, canonical agent names) stay valid. Define a coined term once beside the artifact that owns it; link later uses. In user-facing updates, lead with the observable consequence and the decision it creates; put internal names after.

## Primary Checkout Invariant

**~/work/dev-hermit/hermit and ~/work/dev-hermit/reverie must ALWAYS be on latest main.** Never detach HEAD
or checkout a feature branch on a primary — all validation, testing, and feature work happens in worktree
slots only. After ANY operation touching a primary, verify `git branch --show-current` is `main`; when
finished, return it to latest main (`git checkout main && with-proxy git pull origin main`).

## Project Overview

`~/work/dev-hermit/` is a multi-agent development harness — **not** the Hermit, Reverie, or LiteInst2 code
project. It coordinates product submodules plus one tooling submodule — all four checked out by default, all
pinned by exact gitlinks:

- `hermit/`: primary Hermit product checkout.
- `reverie/`: Reverie instrumentation/runtime checkout (reference, compatibility, coordinated changes).
- `liteinst2/`: standalone LiteInst2 checkout.
- `agent-utils/`: shared tooling incl. `tick-hub`; `update = checkout` like every other submodule — a plain `git submodule update --init --recursive` materializes it. (The former `update = none` opt-out was retired by the 2026-08-02 checked-out-by-default policy; this line described it until 2026-08-06.)

The parent owns orchestration policy, worktree registries, experiments, AI notes, exact submodule pins;
product source/tests/build/docs stay in their submodule. The parent harness works directly on shared `main`;
parent-only policy work commits there only when a task explicitly names+authorizes the parent files.
`worktrees/ACTIVE.md` is ignored machine-local state — never commit or merge it. Confirm the destination
before publishing Reverie work. Stale `integration`/legacy-lead/per-machine parent branches do not override this.

## Vocabulary (full glossary in companion doc)

- **Primary checkout**: `{hermit,reverie,liteinst2}/`; coordinator-owned integration surface.
- **Slot**: one paired workspace under `worktrees/`. **Active**: assigned to live work, in `ACTIVE.md`. **Parked**: clean, detached, retained for cache reuse, omitted from `ACTIVE.md`. **Legacy**: pre-policy non-canonical slot; may finish its task but must be removed, not reused.
- **Shared slot**: one used by multiple research-only agents, or mutating agents with explicitly disjoint file ownership in `ACTIVE.md`. No two agents may edit the same file concurrently.
- **Submodule**, **Feature branch**, **Hermit base**/**upstream**, **Handoff SHA**, **3pai agent sandbox**: see companion glossary (their predicates also appear inline where they bind action).

## Canonical Layout (full tree in companion doc)

**Nested layout v3, one slot per agent.** Every normal worktree path is
`worktrees/<slot>/{hermit,reverie,liteinst2}` where `<slot>` is a named agent (`kvm`, `dbi`, `sabre`,
`e9patch`, `liteinst`, `ci`, `coord`, `lander`, `opt` — `hermit-` prefix stripped) or a generic `slotNN`;
exactly one mutating agent owns a slot. Old flat layout (`worktrees/slotNN` + sibling `worktrees_reverie/`)
and primary-nested `hermit/.worktrees/…` scratch trees are deprecated — do not create either.
**`ai_docs/transient/2026-07-27-worktree-management-map.md` indexes every place worktree information lives —
read it before any worktree operation.**

## Hard Invariants

1. Never do feature development in a primary checkout.
2. Never let two agents mutate the same file or branch. Shared slots require explicit disjoint path ownership in `ACTIVE.md`.
3. Register every active slot, agent, task, branch, and owned path in `worktrees/ACTIVE.md` before the first edit or commit.
4. Require clean state before assignment, integration, parking, or pinning.
5. Treat unexpected changes as owned by somebody else — do not reset, clean, overwrite, stash, or absorb them.
6. Do not run `git clean` anywhere in the parent, submodules, or slots.
7. Do not use a branch name as a worktree directory name.
8. Do not share writable build directories between worktrees.
9. Publish Hermit product work through a feature PR to `rrnewton/hermit:main`; do not land it by mutating the primary checkout.
10. Never force-push shared branches or `main`.
11. Never commit binaries or generated build artifacts to any repository.
12. A handoff is incomplete without exact SHAs and validation results.
13. Never exceed twelve active worktrees, five parked slots, or fifteen agents (count each separately; active work does not consume the parked allowance). Every normal worktree path is `worktrees/<slot>/{hermit,reverie,liteinst2}` (no other path shapes).
14. Never remove a dirty slot until its state has a documented recovery SHA.
15. Never broad-kill processes on this shared box — no `pkill`/`killall`/pattern/name/`-f`-substring/user/`ps|grep|kill`. Kill only your own child PID/PGID. See **Process-Kill Safety**.

## Clean Start And Checkout Ownership

Before dispatching or starting work, inspect the parent, all primaries, and the assigned slot (`git status
--short --branch`; `git submodule status`; same for each product and the slot's children). A dirty checkout is
not an invitation to clean it; a `+` submodule status is not automatically an error (integration may be in
flight — attribute before acting; status-flag legend in companion doc). Primaries are integration surfaces:
only the coordinator or an agent explicitly assigned an integration op may mutate them; ordinary agents may
read them and use their build caches as copy sources. Record ownership in `ACTIVE.md` and task notes. Modify
the parent root only when a task names parent files and ownership is explicit; never mix a parent edit into an
unrelated product task.

## Worktree Registry

`worktrees/ACTIVE.md` is the source of truth for slot ownership. Keep exactly one live row per active slot:
`slot | agents/tasks | owned paths | Hermit branch | Reverie branch | LiteInst2 branch | started | purpose`.
Use `-` or `detached:<short-sha>` for an unchanged child; never duplicate rows as a task changes phase —
update the existing row. List every agent/task sharing a slot and make mutating path ownership unambiguous;
research-only agents may be `read-only`. A DONE/HELD/ABANDONED row does not belong in `ACTIVE.md`: keep it
active with an accurate purpose, or park it and append the final state to `ARCHIVED.md`. Before dispatch,
reconcile the registry with all Git worktree registries (`git worktree list --porcelain`) and the filesystem
(the specific conflicts to resolve before assigning a slot: companion doc). Never silently delete a stale path
— record what owns it and preserve uncommitted work first.

## Strict Slot Pool

All new work uses a canonical slot name under `worktrees/<slot>/`. **Branch and task names never appear in
worktree paths.** At five parked slots, reclaim the least useful before creating another. Active slots are
never evicted to satisfy the parked cap; a dirty/blocked slot stays active until handed off. Do not move or
rename a slot directory. Pre-policy non-canonical worktrees are exceptions only while their task is active; at
closeout, archive and remove them — do not park/rename/reassign.

**Provision/release with the registry-aware scripts, never raw `git worktree add`.** `scripts/allocate-worktree.rs`
and `scripts/release-worktree.rs` enforce one-owner-per-slot and one-slot-per-agent and are the **single
writer** of `worktree-state.json` and the ACTIVE.md managed block (`scripts/slot-init.sh` is a detached-only
manual fallback that does NOT touch the registry). Provisioning is coordinator-only: init primary submodules
first, then `scripts/allocate-worktree.rs --agent <agent> --task <id> --product all --purpose "<one-line>"`.
Seed caches with CoW copies (`cp -a --reflink=auto`); never symlink `target/` or another writable cache between
checkouts. Share a slot only when the registry names every agent, task, branch, and owned path
(`--i-promise-this-agent-is-read-mostly`); no concurrent edits to the same file or branch (Invariant 2).

**Starting work in a slot.** Before the first edit: confirm the parent slot and all nested submodules are
registered and clean; fetch relevant remotes without changing checked-out files; branch Hermit from current
`origin/main` and Reverie from the task's confirmed base + publication target; create a descriptive feature
branch in each repo that will change; leave each unchanged child detached at its recorded parent gitlink;
add/update one `ACTIVE.md` row and post the assignment to each task. Run all edits/builds/tests/commits from the
assigned child worktrees, always setting the working directory explicitly.

**Closing/parking/reclaiming (mechanics: companion doc).** Close a slot only after intended work is committed
and handed off; record child HEADs/branches/SHAs/validation/disposition in `ARCHIVED.md`, detach each child at
its parent-pinned gitlink until `git -C $slot status --short` is empty, and remove the slot's row from
`ACTIVE.md`. Keep feature branches until reachable from a pushed branch or merged target. Reclaiming/reusing a
parked slot never authorizes discarding changes or resetting a child to make it current.

## Hermit Git And Pull Request Workflow

Primary Hermit repo is `rrnewton/hermit`; public `facebookexperimental/hermit` is reference, not the default
landing target. Ordinary Hermit work flows from a feature branch to a PR against current `rrnewton/hermit:main`.

### Feature Branch Rules — **ALWAYS COMMIT ON FEATURE BRANCHES**

**Every mutating agent must finish with all intended work committed on its task feature branch. Never stash.
Never leave intended work uncommitted. An uncommitted or stashed handoff is incomplete.**

- Fetch through the required proxy and branch from current `origin/main` — not an old slot HEAD, stale local branch, or parent gitlink. Do not trust a handed SHA; verify the frontier (see *Trust The Ledger*).
- Create/use the task's dedicated feature branch before the first edit. Never commit task work directly on `main` or a shared integration branch.
- Keep one coherent task on one branch. Coordinated Hermit/Reverie branches are one logical change but separate Git histories.
- Commit all intended task-owned changes before reporting completion. If blocked, commit every coherent completed change and record the remaining blocker.
- Push the committed branch and open a draft PR without asking separate permission; an explicit "do not publish" is the only exception. Always push with an explicit refspec: `git push origin HEAD:refs/heads/<branch>`.
- Never force-push a shared branch or `main`. Rebase only a private feature branch, only when authorized; then rerun affected validation and give the new SHA.

**Existing Hermit PR checkout — never validate against the PR's historical Reverie pin.** Checkout and
preparation are one operation via `scripts/checkout-hermit-pr-latest-reverie.sh` (mechanics: companion doc); a
stale pin is a hard validation failure, so do not substitute raw `gh pr checkout` + validation.

### Publishing And Review

Unless a task prohibits it, push the branch and open a draft PR against `rrnewton/hermit:main`. Before opening:
confirm the branch is based on the intended current `origin/main` with no unrelated commits; review the full
feature diff and validation evidence; run the focused tests + repo validation the task requires; confirm the
tested SHA is the branch tip; write the mandatory PR sections (below); re-read concurrent remote state before
pushing. Use `with-proxy` for networked `git`/`gh`; never `gh auth switch` (auth is shared machine state).
Require an owner-authorized authority green at the exact PR head. For Hermit the two positive paths are
interchangeable: (1) `ci-hub validate-status` dereferences a clean, counted local receipt, or (2) `ci-hub
hosted-status` dereferences the registered `CI (GitHub-managed portable)` / `Regular tests (GitHub-managed
portable)` job. Hermit's privileged workflow is not an additional required positive unless the owner explicitly
changes the versioned policy. Reverie's hosted authority remains both `Regular tests` and `Host-dependent tests`.
A skipped/missing/queued/partial/stale/cancelled authority is NO_RESULT, not green; one genuine product red blocks
even when the peer is green. Do not merge with unresolved adversarial-review findings or a bare test-process exit.
This policy is versioned in the parent before its Hermit consumer deployment: until the
`hermit-merge-gate-authority-deployment` obligation in `ci-hub/landing/README.md` lands, Hermit's required
merge-gate still enforces portable+privileged and pins the older receipt verifier. Do not claim portable-only
hosted authority is operational end to end, and do not bypass that required check during the transition.

### Proxy Binding Review Axis (predicate; full rationale, registry, 12 examples, 3-layer taxonomy in the [companion doc](https://github.com/rrnewton/dev-hermit/blob/main/ai_docs/agents-md-policy-rationale.md))

**Proxy Binding** is the mandatory adversarial-review axis: **what binds this check to the fact it claims, and
can I observe that binding rather than infer it?** A check fails when it keys on a correlated proxy (label,
status, marker, flag, hash, count) without an observable identity/causal/coverage/provenance link to the
claimed condition. Enforce as predicates (examples in the companion doc):

- **Carry the condition with the value.** A value not recording its conditions is a proxy: store `{jobs, bytes}`, not a bare cap; bind green to an exact-SHA run with a nonzero executed-test count; bind landing to `mergeCommit.oid` ancestry on freshly-fetched main, not a PR head or `MERGED` flag.
- **A green must carry what it verified** in one record: exact SHA, profile, discovered/selected/executed/filtered/failure counts, declared per-node coverage. Full green = full profile, nonzero execution, satisfied coverage, zero failures. `filtered == 0` is not completeness; `test result: ok` with zero executed tests is a no-result.
- A grandfathered schema-4 local receipt may retain its historical authority, but it must report
  `coverage_satisfied: null` and `coverage_status: grandfathered-unknown`; it must never claim per-node coverage it
  did not carry. Schema-5+ requires declared satisfied per-node coverage.
- **One verifier per authority, called by every consumer.** Each evidence authority gets one semantic verifier that dereferences the source; a label/comment/status/copied field is only a cache. Do not collapse different authorities behind one generic check. Mark an authority covered only after a counted qualifying positive passes, a well-shaped nonexistent/tampered negative is refused, and a call-site audit shows every consumer invokes it. The **Load-Bearing Authority Registry** (companion doc) records each authority, its verifier, and coverage holes.
- **Bracket both sides.** Negative: plant the violating case, confirm refusal. Positive: plant the qualifying case, confirm it fires (not inert). State counts on both sides.
- **Never plant an artifact that is itself an authorization** (a merge/review/validation label, an auto-merge workflow) to test a gate. Exercise the consumer with an inert fixture, dry-run, or isolated repo incapable of authorizing the action whose refusal it tests.

### Post-Facto Human Review

Canonical protocol is post-facto: once required adversarial review is resolved and the authoritative CI gate is
green, land the authorized change without waiting for human-owner review (the human reviews after landing; fix
forward). Apply the single `post-facto-human-review` label iff a PR has at least one trigger:

1. **New syscall support.** Verify in-code audit tags: `AUTONOMOUS-BOT-IMPLEMENTED` at the new dispatch/classification entry and `TODO-HUMAN-REVIEW(PR-id)` at the implementation/determinization block.
2. **A Reverie API or core-abstraction change** — the `Tool`, `Guest`, `Backend`, or syscall-interception model.
3. **A new determinization strategy** (not an implementation of an established one).
4. **A core DetCore scheduling change** — anything affecting how programs are scheduled, especially race-search (PR #1151 is canonical).

Routine backend-parity work toward the golden ptrace reference does **not** trigger review merely because it
changes KVM/DBI/SaBRe/LiteInst; apply the label only when it also meets a trigger. Every PR description must
contain: **Summary**; **Determinism** (mandatory every PR — why the change is deterministic, with logic or
informal proof, not only test results); **Validation** (exact commands, outcomes, limitations, relaxations);
**Relationship to gVisor** (required for KVM changes); **Human Review Required** (mandatory when the label is
applied — name the numbered trigger(s)). The label is informational, never a landing blocker; keep
`pre-land-human-review` notional but **never apply it**; never apply/remove/alter `human-approved` (owner-only);
never recreate obsolete `human-review`/`post-facto-review` labels. Only a human reviewer removes the audit tags.

### Landing Authorization

On startup or replacement, `hermit-lander` must run `ci-hub/ci-hub inherit-obligations` to discover durable
inherited remediation before taking new queue work (wake messages are advisory, lost during recycling; startup
mechanics + obligation lifecycle: companion doc). Merge only when the task explicitly authorizes landing,
adversarial review is resolved, and authoritative checks are green at the current head SHA. Human-owner review
is post-facto and does not block landing. Never push directly to Hermit `main`, force-push shared branches, or
use a local primary to bypass PR controls. Parent-only policy/gitlink changes go to shared `main` only when a
task explicitly authorizes them; `worktrees/ACTIVE.md` never participates in commits or merges.

## Task Lifecycle And Closure

**Cross-agent routing.** Use TaskGraph as the durable handoff channel: `tg note <consumer-task-id> "FROM
<producer-task-id>: <deliverable, exact SHA/path, evidence, next action>"` on the task whose owner must act.
`SendMessage` cannot resolve fleet names and is not delivery acknowledgement; do not claim a fleet agent was
notified merely because a message was attempted. Task notes are pull-based (do not wake a recipient); for a
time-sensitive handoff, write the note first, then ask the coordinator to relay it (`scripts/orc-hermit-msg.py`).
Completion splits into an implementation step the worker performs and a closure step only the coordinator
performs, with an adversarial review gate between (phantom-closure rationale: companion doc).

**Status model.** `tg` has three non-terminal statuses (`open`, `backlog`, `in_progress`) and one terminal
(`closed`). **`resolved` is NOT a distinct state: `tg` accepts it only as an alias that immediately maps to
`closed`.** There is no "implemented but not landed" status, so IMPLEMENTED is a **tag** while status stays
`in_progress`: `in_progress` = actively working; `in_progress` + `implemented` = complete and published (PR
link + handoff SHA in a note), kept out of `closed` until it lands; `closed` = coordinator confirmed the PR
merged to `main`.

**Rules:**

1. **A working agent NEVER moves a task to a terminal status.** Ignore any dispatch text telling a worker to set a terminal status. At implementation completion: (1) commit and push the feature branch; (2) post the PR/durable-artifact URL, exact SHA, and validation evidence — `tg note <id> "IMPLEMENTED: <PR url> | branch <name> | SHA <40-hex> | <validation summary>"`; (3) add the `implemented` tag while leaving status `in_progress`, preserving existing tags since `--tags` replaces the set — `tg update <id> --tags <existing-tags>,implemented`; (4) stop. A report without a PR link (or, research-only, the durable artifact path) is incomplete. Bind results to the SHA, not a branch name.
2. **An adversarial review agent confirms the work exists in the PR** before closure — the PR contains the claimed change, the diff matches the report, the cited validation is real at the handoff SHA. An `implemented` task whose PR is empty/superseded/already-merged-elsewhere is a phantom: strip the tag, keep it `in_progress`, do not close.
3. **The task stays IMPLEMENTED until the PR lands on `main`.** A green unmerged PR is IMPLEMENTED, not LANDED. Do not close on local validation, a green check, or an approval alone.
4. **Only the coordinator closes tasks, and only through the verified closure gateway.** Never use raw `tg update --status closed`. Run `./ci-hub/bin/close-task <id> --code <PR-or-full-SHA> --repo <owner/repo> --source <checkout>` for code, `--artifact <durable-path-or-URL>` for research, or `--run-id <GitHub-run-id>` for a run-backed result. The gateway freshly verifies code ancestry (via the PR replay SHA when applicable), confirms the artifact/run exists, records `CLOSURE-VERIFIED`, and only then changes status. `REFUSED` (rc 1) and `UNVERIFIABLE` (rc 2) never close.

**Exceptions:** **Research-only tasks** produce no PR. Their closure evidence is a typed tuple, not a bare
path: repository identity + durable artifact path + the artifact's last content commit + fresh target-main
ancestry. For parent artifacts, `./ci-hub/bin/close-task TASK --artifact ai_docs/path.md` derives and records
`rrnewton/dev-hermit:path@content-commit;target=main@tip`. The coordinator separately confirms that the
artifact answers the task's stated question; existence/ancestry proves publication, not goal completion.
Tag `implemented` (status `in_progress`) with the durable artifact path and exact content SHA. A memory slug
must be exported to a versioned artifact or another typed durable authority before closure. **Blocked tasks**
stay `in_progress` (or move to `open`) with
the exact blocker and any partial committed SHA; never tag `implemented` or close to signal progress.
**Stale-premise tasks** are tagged `implemented` with a note explaining the stale premise and evidence SHA; the
coordinator closes after verifying it.

## Bot-Created GitHub Issue Policy

Bot-created issues go on the `rrnewton` forks **ONLY**. **NEVER create an issue on `facebookexperimental/hermit`
or `facebookexperimental/reverie`** — those upstream repos sync into Meta's internal task tracker, so an
agent-created issue there creates unwanted internal tasks. Create Hermit issues on `rrnewton/hermit`, Reverie
on `rrnewton/reverie`. Reading upstream issues/PRs is allowed; editing/commenting/closing one requires a task
that explicitly authorizes it. Use the registered wrapper for every agent-created issue (never raw `gh issue
create`): `./.orc/plugins/hermit-dev/gh-issue-create` — it rewrites an accidental `facebookexperimental/*`
destination to its `rrnewton` fork, rejects unrelated repositories, and supplies the required GitHub proxy.

## What Goes Where

Use ownership boundaries, not convenience. **Parent** tracks: workspace policy (this guide), `.gitmodules`,
exact gitlinks and ignore rules, `worktrees/ARCHIVED.md`, generic workspace scripts/coordination tooling,
durable AI research/handoffs under `ai_docs/`, reproducible experiments under `experiments/`. Ignored parent
locations hold transient material (`scratch/`, physical `worktrees/slot*/` contents, local runtime
state/registries/credentials, build output, core dumps, coverage, downloads). An experiment is durable only
when another engineer can repeat it: `experiments/<name>_YYYYMMDD/` with `README.md` (question,
method, results, interpretation, reproduction), `metadata.json` (repo SHAs, command, host, toolchain, seed,
inputs), `results.csv`. **Hermit** source/APIs/CLI/tests/build/docs belong in `hermit`; do not copy Hermit code
into a parent script to dodge a proper product change. **Reverie** source/APIs/tests/build/docs belong in
`reverie`; reference use does not justify modifying it — create a Reverie feature branch only for a real change.

## Reverie API Policy

Additive Reverie extensions are allowed when existing consumers stay compatible (narrowly scoped helpers,
hooks, events, adapters, or optional capabilities whose defaults preserve current behavior). Discuss with the
user before changing any core Reverie abstraction/contract: tool/event model or ordering, public trait
requirements, syscall interception/injection semantics, guest register/memory contracts, lifecycle ownership,
container responsibilities. Do not smuggle an abstraction change in as cleanup; prefer an additive API or
compatibility layer.

**Cross-repository changes.** Keep each repository's commit independently coherent and document the SHA
dependency in both handoffs. When Hermit and Reverie change together, use coordinated branches in the same
slot, make the lower-level Reverie commit available first when possible, validate Hermit against its exact SHA,
and report both SHAs and their dependency. Confirm the intended Reverie PR destination before publishing; do
not assume authorization to mutate `facebookexperimental/reverie`. Only after the team branches are correct
should the parent pin one or both new SHAs.

## Commit Hygiene

Agents deliver reviewable commits, not anonymous working directories.

- Inspect `git status`, the complete diff, and the staged diff before committing. **Stage only task-owned paths in the repository that owns them — never `git add -A` / `git add .`; name the explicit paths.** Keep formatting-only churn and unrelated cleanup out of focused changes.
- Prefer one logical commit per repository per task; split only when each commit is independently coherent. Use an imperative, descriptive subject; explain motivation/constraints/compatibility/non-obvious validation in the body when needed. Never use placeholder subjects (`wip`, `tmp`, `checkpoint`, `fix stuff`); never create empty bookkeeping commits.
- Do not claim a test passed unless it ran against the handed-off SHA; do not hide failures or skipped hardware-dependent validation — report the exact limitation.
- Amend/rewrite only private task commits when authorized. Never rewrite `main`, a shared/published branch, or a commit another task depends on. Do not mix parent gitlink updates into a submodule source commit.

Before committing, audit staged paths (`git status --short`; `git diff --cached --stat`; `git diff --cached
--name-only --diff-filter=AM` for generated/oversized files). Before handoff, capture exact state (`git status
--short --branch`; `git rev-parse HEAD`; `python3 ci-hub/tests/documented_commands.py --closeout`). The closeout
guard refreshes `origin/main` via `with-proxy` and rejects unpushed parent commits; a dirty shared parent fails
unless every retained path is accounted for with `--dirty-note` (documents concurrent ownership; never
authorizes staging/modifying someone else's work). Every handoff includes: task id, slot, owner; repo + feature
branch; exact Hermit/Reverie SHA; base SHA + relationship to target; change summary; exact validation commands
+ results; known failures/skipped checks/env limits; cross-repo dependency SHAs; fast-forward readiness; parent
gitlink update status. For a coordinated change, give both repo SHAs even if one child is unchanged; label it.

## Submodule Coordination And Pinning

The parent records exact submodule commits for reproducibility. Do not add a `branch = ...` field to
`.gitmodules`; do not use `git submodule update --remote` as a normal update mechanism.

- **When to update a pointer** — only when the target commit is intentional, reviewed, reachable from its reviewed feature branch or target `main`, validated locally at that exact SHA, cross-repo compatible when relevant, and the parent commit message names the reason. Not merely because a primary is ahead, a feature branch exists, or `git status` shows a modified submodule. Do not pin an unpublished private commit unless the task establishes how every consumer can fetch it. **A/B protocol, staging procedure, and initialization mechanics: companion doc** — follow them before any pin; use `make single-submodule-bump ARGS='plan ...'` first, and never bury a gitlink advance in an unrelated commit.
- **Agent-utils main peg.** The parent gitlink and canonical `agent-utils/` checkout must equal fetched `rrnewton/agent-utils:main`; `make check-agent-utils-pin` rejects stale/ahead/diverged checkout, gitlink mismatch, or unreachable commits. Generic changes (runner cgroups/CPU budgets, `tick-hub`, PR planning) belong in `rrnewton/agent-utils`: serialize; run full intra-agent-utils validation; push directly to `rrnewton/agent-utils:main`; then fetch, update the canonical checkout, run `check-agent-utils-pin`, commit the exact gitlink in the parent. A PR is the exception (high-risk or coordinating with in-flight parent change) — at most one in flight. Direct-to-main is not unvalidated-to-main: fix any red required check before pushing main.
- **Self-hosted runner security.** Never run a GitHub Actions runner as root on a Meta dev box/data-center host (it executes arbitrary repo-controlled workflow content — root grants that code elevated privileges on internal infra). Moving work off privileged self-hosted execution is required architecture; the genuine residue is KVM (`/dev/kvm`) + real-PMU counters, each given minimum privilege. `hermit-gate-newton` authorization/ownership/disposition is an open security question.

## Binary And Large-File Policy

Never commit binaries to any repository: compiled executables, object files, libraries, archives, database
dumps, core dumps, profiler captures, screenshots, generated media, cached dependencies, build trees. Git LFS
is not a workaround unless repo owners establish an explicit policy. Keep binary artifacts in ignored local
directories or an approved external store; when evidence depends on an external artifact, commit a small text
manifest with its location, checksum, producing command, tool version, and source SHA. Textual files over
2 MiB require explicit coordinator approval before staging — prefer summarized CSV/JSON, a compressed external
artifact, or a reproducible generator. Audit newly staged files before every commit (`git diff --cached
--name-only --diff-filter=AM`; `--numstat`); if a path looks generated or unexpectedly large, stop and inspect
it with `file`, `du`, and the ignore rules — do not commit first and promise to remove it later.

## Validation And Evidence

Product validation commands come from the local submodule guides. Use the narrowest relevant tests during
development, then the required repository gate before handoff. Cross-repository changes require validation
against the exact Hermit/Reverie pair proposed for pinning. Evidence binds to commits, not a mutable branch
name — always report: **Hermit SHA** (40-hex), **Reverie SHA** (40-hex or explicitly-unchanged), exact
**Command**, **Result** (pass/fail/skipped with material output summarized), **Environment** (host/toolchain/
hardware constraints when relevant). Hardware-dependent Hermit tests may be impossible on some hosts — report
that fact and the observed failure; do not weaken, delete, or falsely bless a test to make the local
environment green. When landing is authorized, the coordinator dereferences the owner-authorized exact-head
authority at the Hermit PR head and final mutation boundary: a qualifying counted local receipt or the versioned
hosted job policy is a green positive; missing/partial/stale evidence is NO_RESULT and a genuine red from either
path blocks. Local feature-branch validation does not prove a hosted job is green, and a hosted job does not prove
locally executed backend coverage beyond the job's declared scope.

### Running validate — `systemd-run --user` Is The Producer Path

**Pre-anchor preflight — run BEFORE claiming ANY Hermit PR for validation.** A PR head not descending from
hermit `bfb0a9ef` runs its own older `validate.sh` and is a **guaranteed rejection even when fully green** (fix
by **rebase**, not re-validation):

```
git -C <checkout> merge-base --is-ancestor bfb0a9ef1c303d1977f5f02903b70cc93e514cb5 <PR-head>
# rc=1 => PRE-ANCHOR => do NOT claim/validate; flag for rebase onto newest-green.  rc=0 => anchored, proceed.
# tool form: ci-hub/validate/preflight_anchor.py --head <sha>   (exit 2 = REFUSE)
```

Re-derive the live pre-anchor set with the loop; do not trust a stale list. HOLD mass-rebase until the
version-aware counts consumer lands (task `prs-predating-commit-anchoring-can-never-produce-a-qualifying-receipt`).

**An agent sandbox CANNOT run `validate.sh` directly** (BpfJailer denies creating its own cgroup; the wrapper
exits 3 in ~9s having run nothing, tell: `CPU/wall 1.0x`). `ci-hub validate-run` is the sole admission point.
It launches a transient user unit which enters through `ci-hub validate-lock` before invoking `validate.sh` —
still boxed, detached, and with a durable log that outlives recycling (green *evidence*, not a *claim*).
The live call prints a `validate-*` handle, creates an observer-only tab in the `validate-hermit` Herdr
workspace, then blocks on the detached unit. If the caller recycles, the run continues and its successor uses
`ci-hub validate-run --attach <handle>`; never relaunch merely because the waiter disappeared:

```
./ci-hub/ci-hub validate-run --checkout <worktree> --agent <agent> \
  --target <exact-40-hex-head> --pr <number> -- full
```

The Herdr pane is visibility, not the producer: it tails the durable log and reports the actual service
descendants' `safe-ci-*` cgroup paths. Validation still runs only in the admitted systemd service through
`validate-lock` → `validate.sh` → `safe-ci-dag-runner`. Pane creation is fail-closed before service launch.

Let `apply-local-label` add the label FROM the ledger record — never by hand. Derive safe concurrency against
total cores before fanning out. The Hermit Merge Gate must execute `ci-hub/validation/verify_receipt.sh` from
an immutable parent commit, never from the PR under test. (Full rationale: companion doc.)

## Product Vision (full statement in companion doc)

Two long-range goals (full statement in companion doc). `goal-hermit-v2`: robust deterministic execution of
arbitrary real-world binaries — run/record, chaos race exposure, schedule-search localization, ptrace-free
production backend, parallel non-communicating processes. `goal-qemu-linux-under-hermit`: a full Linux VM as a
userspace QEMU process under Hermit to expose/localize kernel races across the stack. Prioritize correctness,
faithful replay, race discovery/localization, lower overhead, backend maturity. Do not close either goal
without its required human verification.

## Communication Precision (full rules in companion doc)

Reports (headlines, cross-task rollups, user-facing progress) must let another engineer act without re-deriving
scope. Predicates: never headline a bare pass ratio — name program category, exact programs (or link a table),
Hermit mode+backend, and why the batch was selected; label every rollup `New this run`/`Baseline
reconfirmed`/`Regression`/`Not rerun` with the commit/PR that changed; classify programs before totaling (mixed
batches need subtotals); name the execution context (native/ptrace/DBI/KVM; strict run/strict verify/record-
replay/relaxed); name the tool (`StraceTool`, `Detcore`, … — never "the Tool"); give the exact command line, say
where (`main`/`PR #N`/exact SHA), and qualify the result (`L0`/`L1`/`L2`, `18/20`, exact tests). Bind evidence
to commits, not branch names.

## Coordinator Judgement Rules (predicates; full rationale in the [companion doc](https://github.com/rrnewton/dev-hermit/blob/main/ai_docs/agents-md-policy-rationale.md))

- **Establish what you have before acting.** A **note** is one agent's unverified belief — do not launder "X appears to be Y" into "X is Y, fix it." When a premise comes from a note or second-hand observation, attribute it to its source, mark it **UNVERIFIED**, and make "verify the premise" the explicit first step ("premise refuted" is a valid outcome). A **number** is unqualified until you state **what it measures** (the decision's quantity, or a proxy?), its **unit** (a count is not a rate; a load average is not a utilisation), and its **denominator** — interrogate a surprising ratio's denominator first.
- **Verify a mechanism by the running thing, not its config.** A flag can be a deprecated no-op; an exit code can come from a different wrapper. Find the running thing and ask what holds it — e.g. for cgroup boxing, walk `/sys/fs/cgroup/...` for the live PID's `cgroup.procs`, not a flag/exit code.
- **Record every measurement immediately.** First check whether the number already exists (`experiments/`, task notes, ci-hub history store). Any measurement — even incidental — goes into a task note immediately with units, context, and **how obtained (a polled aggregate is not a cgroup-recorded peak)**.
- **Trust the ledger, not a handed SHA.** A handed SHA — or a "latest green"/"known-good" commit — is a claim, not evidence. Establish the validated frontier yourself: `ci-hub newest-green` (default `--branch main`; `--json`; `--no-fetch` offline) returns the newest commit whose latest LOCAL validation passed. If the handed SHA and the ledger disagree, the ledger wins — report the discrepancy.

## Failure, Recovery, And Concurrent Work

Other agents may update the parent, primaries, registries, or branches mid-task. Re-read state before every
integration or pinning step; unexpected movement is a reason to reassess, not to restore an older snapshot.

- Do not use `git reset --hard`, `git checkout -- <path>`, or destructive cleanup on changes you did not create.
- Do not move uncommitted work between slots without recording its owner and exact recovery procedure. Do not silently adopt another agent's branch or worktree.
- If a feature no longer fast-forwards, update the private branch and retest; never paper over divergence with a merge commit.
- If a primary is dirty, integration stops until the changes are attributed.
- If a submodule pointer conflicts, resolve the intended product history first, then choose the exact gitlink — never pick a side without inspecting the commits.
- If a task is blocked, preserve clean committed work, post the exact blocker and SHAs, and keep the slot active until the coordinator decides to park it.

## Process-Kill Safety (Hard Invariant 15)

**NEVER use a broad `pkill`, `killall`, or any pattern-matched process kill on this machine** — up to eighteen
agents share this box and its binary paths (`hermit`, `cargo`, `python3`, …), so any name/pattern/`-f`-substring/
user/`ps|grep|kill` match kills siblings' live work. Kill only processes you started: capture the child PID
(`$!` for a backgrounded command) or run it in its own process group and signal the negative PGID (`setsid cmd
& pgid=$!; kill -- -$pgid`). If you cannot prove a PID/PGID is your own child, do not kill it. (War story:
companion doc.)

## Coordinator Checklist

Terse coordinator-only preflights (before dispatch / publication-or-landing / parent-pinning / closeout /
task-closure) recapping the rules above — full checklist in the
[companion doc](https://github.com/rrnewton/dev-hermit/blob/main/ai_docs/agents-md-policy-rationale.md). Each
bullet references its own section here; consult it before the corresponding coordinator action.

---

<!-- LOAD-VERIFICATION TAIL CANARY. This is the LAST line of the canonical policy file.
`make lint` (target check-claude-md-size) asserts this file stays under its size guard and that this canary
is present; do not remove it without updating both. An agent that has read this file to its end can quote the
canary token: -->
**TAIL-CANARY-KESTREL-7731** — if you can quote this token, the canonical dev-hermit policy loaded to its end.
