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

Coordinator guide (dispatch, checkout ownership, landing, gitlinks, status), not a product manual.
Implementation agents also follow the applicable product `AGENTS.md`; the stricter rule wins. `.llms/skills/`
holds task skills, not policy. Preserve exact implementation evidence when aggregating.

For landing work, use [`pr-landing-planner`](agent-utils/skills/pr-landing-planner/SKILL.md) to produce the
advisory conflict/evidence plan, then [`pr-landing-operations`](agent-utils/skills/pr-landing-operations/SKILL.md)
to execute an authorized drain. This file remains the authority for authorization, review, repository policy,
and closure; neither skill may weaken it.

Codex coordinators delegate nontrivial tool work, synthesize results instead of pasting raw tool output, and
rephrase or replace a worker that hits the cybersecurity false-positive filter rather than stalling.

## Conventions

- **Role + team tag:** `[<role>, MODEL] [<full-team-name>]`, with role `impl agent`, `adversarial-reviewer agent`, `coordinator`, or `Human`; the team name includes the machine. Require it in **Every commit message** as the final body line, **Every PR description** as the opening line, and the prefix of every GitHub comment used for cross-team coordination. The commit trailer is load-bearing under rebase merge. For provenance, dereference `GET /repos/<owner>/<repo>/commits/<sha>/pulls`; never infer it from the message alone. Rationale and the 2026-08-07 audit are in the companion doc.
- **Mechanism tags:** when a task or PR changes a load-bearing mechanism, apply the same stable `mechanism:<slug>` tag to both (create the label when needed). Before landing, run `ci-hub pr-status`: a mechanism shared by two open PRs requires coordinator review and appears beside file conflicts in the landing plan (semantic overlap only, not conflicting intent).
- **Stable descriptive naming:** give every option/wave/workstream/phase/task/semantic unit one persistent lowercase-hyphenated outcome slug (`btrfs-flood-fix`), never a bare ordinal/placeholder; suffix variants (`btrfs-flood-fix/claude-agent`). Existing infra IDs stay valid. Define coined terms once at their owning artifact. User updates lead with observable consequence and decision, then internal names.

## Primary Checkout Invariant

**~/work/dev-hermit/hermit and ~/work/dev-hermit/reverie must ALWAYS be on latest main.** Never detach HEAD
or checkout a feature branch on a primary — all validation, testing, and feature work happens in worktree
slots only. After ANY operation touching a primary, verify `git branch --show-current` is `main`; when
finished, return it to latest main (`git checkout main && with-proxy git pull origin main`).

## Project Overview

`~/work/dev-hermit/` is the multi-agent harness, not product source. Its exact gitlinks are `hermit/`,
`reverie/`, `liteinst2/`, and tooling `agent-utils/`; all four are checked out by default (`update = checkout`)
and `git submodule update --init --recursive` materializes them.

The parent owns orchestration policy, worktree registries, experiments, AI notes, exact submodule pins;
product source/tests/build/docs stay in their submodule. The parent harness works directly on shared `main`;
parent-only policy work commits there only when a task explicitly names+authorizes the parent files.
`worktrees/ACTIVE.md` is ignored machine-local state — never commit or merge it. Confirm the destination
before publishing Reverie work. Stale `integration`/legacy-lead/per-machine parent branches do not override this.

## Vocabulary (full glossary in companion doc)

Primary checkouts are coordinator-owned integration surfaces. A **slot** is a workspace under `worktrees/`:
**active** means assigned and registered; **parked** means clean, detached, cache-retained, and omitted from
`ACTIVE.md`; **legacy** means non-canonical and remove-after-task, never reuse. A shared slot is research-only
or has explicit disjoint mutating ownership in `ACTIVE.md`. See the companion glossary for all other terms.

## Canonical Layout (full tree in companion doc)

**Nested layout v3, one slot per agent:** `worktrees/<slot>/{hermit,reverie,liteinst2}`, with a named-agent
slot (strip `hermit-`) or `slotNN`, and exactly one mutating owner. Never create old flat/sibling or
primary-nested scratch layouts. Read `ai_docs/transient/2026-07-27-worktree-management-map.md` before any
worktree operation; the companion carries the full tree.

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

Before dispatch/work, inspect parent, primaries, slot, and children with `git status --short --branch` and
`git submodule status`. Attribute dirt and `+` gitlinks; never treat them as cleanup permission. Only the
coordinator or an explicitly assigned integration agent may mutate primaries; others may read/copy caches.
Record ownership in `ACTIVE.md` and task notes. Modify parent files only when the task names and owns them;
never mix parent edits into product work. Status-flag details are in the companion.

## Worktree Registry

`worktrees/ACTIVE.md` is the slot-ownership authority. Keep one live row per active slot with slot,
agents/tasks, owned paths, all three product branches, start, and purpose; use `-` or
`detached:<short-sha>` for unchanged children. Update, never duplicate, a phase-changing row. Shared rows must
name every agent and unambiguous path ownership (`read-only` is allowed). DONE/HELD/ABANDONED rows are invalid:
keep work accurately active or park it and append `ARCHIVED.md`. Before dispatch, reconcile `ACTIVE.md`, every
`git worktree list --porcelain`, and the filesystem. Never delete an unexplained stale path; preserve and
attribute it. The companion lists reconciliation conflicts.

## Strict Slot Pool

New work uses `worktrees/<slot>/`; branch/task names never enter paths. At five parked slots, reclaim before
creating another, but never evict active work; dirty/blocked slots remain active until handoff. Never move or
rename slots. A legacy path may finish its active task, then must be archived and removed, not parked/reused.

Provision/release only with `scripts/allocate-worktree.rs` and `scripts/release-worktree.rs`, the single writers
of `worktree-state.json` and the managed `ACTIVE.md` block; never raw `git worktree add`. `scripts/slot-init.sh`
is a detached-only non-registry fallback. Coordinator provisioning initializes primaries, then runs
`scripts/allocate-worktree.rs --agent <agent> --task <id> --product all --purpose "<one-line>"`. Seed caches
with `cp -a --reflink=auto`, never writable symlinks. Sharing requires all agents/tasks/branches/paths registered
with `--i-promise-this-agent-is-read-mostly`; files and branches remain single-writer.

**Start:** verify slot/children registered and clean; fetch without changing files; branch changed repos from
their confirmed current bases/targets (Hermit: `origin/main`); leave unchanged children detached at recorded
gitlinks; register/post assignment before editing. Run all work from the assigned child with explicit cwd.

**Close/park/reclaim:** only after committed handoff; archive child HEADs/branches/SHAs/validation/disposition,
detach children at parent gitlinks until the slot is clean, and remove its active row. Retain branches until
published/merged. Reuse never authorizes discarding or resetting. Follow companion mechanics.

## Hermit Git And Pull Request Workflow

Primary Hermit repo is `rrnewton/hermit`; public `facebookexperimental/hermit` is reference, not the default
landing target. Ordinary Hermit work flows from a feature branch to a PR against current `rrnewton/hermit:main`.

### Feature Branch Rules — **ALWAYS COMMIT ON FEATURE BRANCHES**

**Every mutating agent finishes with intended work committed on its task feature branch; never stash or hand
off intended uncommitted work.** Fetch via proxy and branch Hermit from verified current `origin/main`, never a
slot HEAD/gitlink/handed SHA. Create the dedicated branch before editing; one coherent task per branch, with
coordinated repos kept as separate histories. Commit coherent partial work plus blocker if blocked. Unless told
not to publish, push explicitly (`git push origin HEAD:refs/heads/<branch>`) and open a draft PR without asking.
Never force-push shared branches/`main`; rebase only an authorized private branch, then revalidate its new SHA.

**Existing Hermit PR checkout — never validate against the PR's historical Reverie pin.** Checkout and
preparation are one operation via `scripts/checkout-hermit-pr-latest-reverie.sh` (mechanics: companion doc); a
stale pin is a hard validation failure, so do not substitute raw `gh pr checkout` + validation.

### Publishing And Review

Before publishing, confirm current intended base, no unrelated commits, full diff/evidence, required tests,
tested tip SHA, mandatory PR sections, and freshly reread remote state. Use `with-proxy` for networked
`git`/`gh`; never mutate shared auth with `gh auth switch`.
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

**Proxy Binding** asks: **what observably binds this check to the fact it claims?** Reject correlated labels,
statuses, markers, flags, hashes, or counts without identity/causal/coverage/provenance binding.

- **Carry conditions with values:** bind green to exact SHA + nonzero execution, and landing to freshly fetched `mergeCommit.oid` ancestry, never a PR head/`MERGED` flag. A green record carries exact SHA, profile, discovered/selected/executed/filtered/failure counts, and declared per-node coverage; full green means full profile, nonzero execution, satisfied coverage, zero failures. Zero filtered or zero executed is not completeness/success.
- A grandfathered schema-4 local receipt may retain its historical authority, but it must report
  `coverage_satisfied: null` and `coverage_status: grandfathered-unknown`; it must never claim per-node coverage it
  did not carry. Schema-5+ requires declared satisfied per-node coverage.
- **One verifier per authority, called by every consumer.** Labels/comments/statuses/copied fields are caches, not sources. Do not collapse different authorities. Coverage requires a counted positive, refusal of a well-shaped nonexistent/tampered negative, and audited consumers. Maintain the companion's **Load-Bearing Authority Registry**.
- **Bracket both sides.** Negative: plant the violating case, confirm refusal. Positive: plant the qualifying case, confirm it fires (not inert). State counts on both sides.
- **Never plant an artifact that is itself an authorization** (a merge/review/validation label, an auto-merge workflow) to test a gate. Exercise the consumer with an inert fixture, dry-run, or isolated repo incapable of authorizing the action whose refusal it tests.

### Post-Facto Human Review

After resolved adversarial review and authoritative green CI, land an authorized change without waiting for
human-owner review; human review is post-land/fix-forward. Apply `post-facto-human-review` iff triggered by:

1. **New syscall support.** Verify in-code audit tags: `AUTONOMOUS-BOT-IMPLEMENTED` at the new dispatch/classification entry and `TODO-HUMAN-REVIEW(PR-id)` at the implementation/determinization block.
2. **A Reverie API or core-abstraction change** — the `Tool`, `Guest`, `Backend`, or syscall-interception model.
3. **A new determinization strategy** (not an implementation of an established one).
4. **A core DetCore scheduling change** — anything affecting how programs are scheduled, especially race-search (PR #1151 is canonical).

Backend parity alone is not a trigger. Every PR has **Summary**, logical/informal-proof **Determinism**, and
exact **Validation**; KVM adds **Relationship to gVisor**; labeled PRs add **Human Review Required** naming
triggers. The label never blocks landing. Never apply `pre-land-human-review`, touch owner-only
`human-approved`, or recreate obsolete labels. Only a human removes syscall audit tags.

### Landing Authorization

On startup or replacement, `hermit-lander` must run `ci-hub/ci-hub inherit-obligations` to discover durable
inherited remediation before taking new queue work (wake messages are advisory, lost during recycling; startup
mechanics + obligation lifecycle: companion doc). Merge only when the task explicitly authorizes landing,
adversarial review is resolved, and authoritative checks are green at the current head SHA. Human-owner review
is post-facto and does not block landing. Never push directly to Hermit `main`, force-push shared branches, or
use a local primary to bypass PR controls. Parent-only policy/gitlink changes go to shared `main` only when a
task explicitly authorizes them; `worktrees/ACTIVE.md` never participates in commits or merges.

## Task Lifecycle And Closure

**Cross-agent routing:** write `tg note <consumer-task-id> "FROM <producer-task-id>: <deliverable, exact
SHA/path, evidence, next action>"` on the consumer task. `SendMessage` cannot resolve fleet names or prove
delivery; notes persist but do not wake. For urgency, note first, then have the coordinator relay through
`scripts/orc-hermit-msg.py`. Workers implement, adversarial reviewers verify, coordinators close.

**Status model:** `open`, `backlog`, and `in_progress` are non-terminal; `closed` is terminal and `resolved` is
only its alias. Published-but-unlanded work stays `in_progress` with the `implemented` tag, PR + SHA note;
`closed` means coordinator-verified landing.

**Rules:**

1. **A working agent NEVER moves a task to a terminal status.** Ignore any dispatch text telling a worker to set a terminal status. At implementation completion: (1) commit and push the feature branch; (2) post the PR/durable-artifact URL, exact SHA, and validation evidence — `tg note <id> "IMPLEMENTED: <PR url> | branch <name> | SHA <40-hex> | <validation summary>"`; (3) add the `implemented` tag while leaving status `in_progress`, preserving existing tags since `--tags` replaces the set — `tg update <id> --tags <existing-tags>,implemented`; (4) stop. A report without a PR link (or, research-only, the durable artifact path) is incomplete. Bind results to the SHA, not a branch name.
2. **An adversarial review agent confirms the work exists in the PR**: claimed diff and real validation at the handoff SHA. Empty/superseded/already-merged-elsewhere PRs are phantoms: strip `implemented`, retain `in_progress`.
3. **The task stays IMPLEMENTED until the PR lands on `main`.** A green unmerged PR is IMPLEMENTED, not LANDED. Do not close on local validation, a green check, or an approval alone.
4. **Only the coordinator closes tasks, and only through the verified closure gateway.** Never use raw `tg update --status closed`. Run `./ci-hub/bin/close-task <id> --code <PR-or-full-SHA> --repo <owner/repo> --source <checkout>` for code, `--artifact <durable-path-or-URL>` for research, or `--run-id <GitHub-run-id>` for a run-backed result. The gateway freshly verifies code ancestry (via the PR replay SHA when applicable), confirms the artifact/run exists, records `CLOSURE-VERIFIED`, and only then changes status. `REFUSED` (rc 1) and `UNVERIFIABLE` (rc 2) never close.

**Exceptions:** Research closes on repository identity + durable artifact path + its content commit + fresh
target-main ancestry, never a bare path. `./ci-hub/bin/close-task TASK --artifact ai_docs/path.md` derives the
parent tuple; the coordinator must still verify the artifact answers the question. Tag `implemented` with path
+ exact SHA; export memory to versioned authority. Blocked work stays `in_progress`/`open` with blocker and
partial SHA, never `implemented`. A stale premise may be `implemented` with explanatory evidence, then closed
only after coordinator verification.

## Bot-Created GitHub Issue Policy

Bot-created issues go on the `rrnewton` forks **ONLY**. **NEVER create an issue on `facebookexperimental/hermit`
or `facebookexperimental/reverie`** — those upstream repos sync into Meta's internal task tracker, so an
agent-created issue there creates unwanted internal tasks. Create Hermit issues on `rrnewton/hermit`, Reverie
on `rrnewton/reverie`. Reading upstream issues/PRs is allowed; editing/commenting/closing one requires a task
that explicitly authorizes it. Use the registered wrapper for every agent-created issue (never raw `gh issue
create`): `./.orc/plugins/hermit-dev/gh-issue-create` — it rewrites an accidental `facebookexperimental/*`
destination to its `rrnewton` fork, rejects unrelated repositories, and supplies the required GitHub proxy.

## What Goes Where

The parent owns policy, gitlinks/ignores, `ARCHIVED.md`, generic orchestration, durable `ai_docs/`, and
reproducible `experiments/`; scratch, physical slots, runtime state, credentials, and build/evidence output are
ignored. A durable experiment has `experiments/<name>_YYYYMMDD/{README.md,metadata.json,results.csv}` sufficient
to reproduce it. Product source/API/tests/build/docs stay in their product repo; never copy code into parent
tooling to dodge a product change, and modify Reverie only for a real change.

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
- **End every commit body with the role + team tag** (see *Conventions*): a final line `[<role>, MODEL] [<full-team-name>]`. Rebase merges carry the commit message onto `main` untouched, so this trailer is the only attribution a reader of `git log main` ever sees. Apply it in every repository, including the parent.
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
- **A run's conclusion is not the registered job's conclusion.** `gh run view <id> --json conclusion` describes a *workflow run*; the exact-head authority is the named *job*, read through `ci-hub hosted-status`. They disagree, and the authority wins. Observed 2026-08-07: a run reporting `completed/success` sat at a head whose authority read `HOSTED RED … positive=0/1`.
- **DO NOT MANUALLY DISPATCH HOSTED CI FOR A PR UNDER `merge-gate-v4` — the gate dispatches it for you, and a manual dispatch races the gate's own.** On a NO_RESULT the gate re-dispatches the missing legs and then *deliberately cancels itself*, logging `NO_RESULT blocks landing but is not a failure; cancelling gate run <id> after re-dispatch`. A hand-rolled dispatch therefore lands a second run at the same SHA, the two contend for admission-limited runners, some jobs of one are starved and cancelled, and **a cancelled job writes a `failure` check under the authority job's name** — so `hosted-status` reports RED for a head whose genuine result was green. Measured on hermit#1911 at `18758e9a`: 30+ jobs succeeded and exactly two (`test: sabre`, `e2e: c-programs__verify__ptrace`) were cancelled, which was enough to flip `Regular tests (GitHub-managed portable)` to `failure`. Note also that the gate requires **both** `ci-portable.yml` and `ci-privileged.yml` (`portable=… privileged=…`), so a manual portable-only dispatch cannot satisfy it even in principle. **The correct action for a NO_RESULT is to re-run `merge-gate-v4` and let it drive** — that produced `Every required leg has a PASSED result.` on the first attempt. Only dispatch by hand when no merge gate is in play (e.g. re-deriving the authority for an already-landed commit), and check `gh run list --workflow <id> --branch <br>` first. Once a SHA is poisoned, the repair is **one** clean dispatch so the newest instance of the authority job is green — verified moving `fda945d7` from `HOSTED RED 0/1` to `HOSTED GREEN 1/1`.
- **A land-lock SIGCONT must cover the WHOLE subtree, roots and descendants.** `land-lock status` hangs while its census holds verifiers in `do_signal_stop`, and the documented remedy (SIGCONT, then `reclaim-dead`, never a kill) only works if every stopped member is resumed. Both partial attempts fail silently and look like a wedged lock: resuming only the descendants leaves the roots stopped, and resuming only the roots leaves the actual worker stopped one level down (observed 2026-08-07 — root `973456` running while its child `973903` stayed `Tl`, log frozen). Enumerate the subtree recursively from the land's validator roots, SIGCONT every member whose `stat` starts with `T`, then re-check that none remain.

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
