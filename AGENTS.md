# MISSION: This is an AUTONOMOUS, forward-driving, SELF-HEALING SWE team. The coordinator replaces broken/degraded/stuck agents immediately and autonomously (close+respawn, no permission needed), drives all work forward without stalling on approval for routine operations, keeps main green + PRs near zero, and heals the fleet continuously.

On every hourly status update, call scripts/status-log.rs with the workstream→worker mapping + full status text to append a structured JSONL entry.

## PR Comment Convention

ALL PR descriptions and comments MUST start with a role tag:

- `[impl agent, MODEL]` - for implementation agents
- `[adversarial-reviewer agent, MODEL]` - for review agents
- `[coordinator, MODEL]` - for coordinator agents
- `[Human]` - for the human owner

Examples: `[impl agent, gpt-5.6-sol]`, `[adversarial-reviewer agent, opus-4.8]`

## Mechanism Tags

When a task or pull request changes a load-bearing mechanism, apply the same
stable `mechanism:<slug>` tag to both (for example,
`mechanism:cancel-in-progress`, `mechanism:CI_DAG_JOBS`, or
`mechanism:locally-validated`). Create the repository label when needed. Before
landing, run `ci-hub pr-status`: any mechanism shared by two open PRs requires
coordinator review and must appear beside file conflicts in the landing plan.
The shared tag only exposes semantic overlap; it does not claim the intentions
conflict.

## Primary Checkout Invariant

**~/work/dev-hermit/hermit and ~/work/dev-hermit/reverie must ALWAYS be on the latest main branch.**

- NEVER detach HEAD or checkout a feature branch on a primary checkout.
- All PR validation, testing, and feature work happens in worktree slots only.
- After ANY operation touching a primary checkout, verify `git branch --show-current` returns `main`.
- After finishing, return it to latest main: `git checkout main && with-proxy git pull origin main`.

# dev-hermit Parent Workspace Guide

This is the single canonical policy source for the `dev-hermit` parent
repository and every agent launched from it. `CLAUDE.md` symlinks to this file,
and the `hermit-dev` ORC plugin reads it at activation, so all entry points
receive the same rules.

The `hermit/AGENTS.md` and `reverie/AGENTS.md` files also apply when working in
those repositories — use them for product architecture, build, test, and style
rules. The stricter rule wins. The Hermit pull request workflow below supersedes
legacy guidance that routed ordinary Hermit changes through a local lead branch.

## Role Boundary

This parent guide is for the **coordinator role**: task dispatch, slot and
checkout ownership, cross-repository dependency order, PR landing, parent
gitlinks, and evidence-based status rollups. It must not grow into a second
Hermit or Reverie implementation manual.

Product implementation agents follow `hermit/AGENTS.md` or `reverie/AGENTS.md`
for architecture, source conventions, test selection, and per-run evidence.
`.llms/skills/` holds task skills, not a second policy location; do not duplicate
product guides there.

When both scopes apply, this guide owns workspace coordination and publication;
the product guide owns implementation and product validation. A coordinator must
preserve exact implementation evidence when aggregating it, never replace
product-specific requirements with a summary.

## Project Overview

`~/work/dev-hermit/` is a multi-agent development harness — **not** the Hermit,
Reverie, or LiteInst2 code project. The parent repository coordinates three
product submodules and one optional tooling submodule, all pinned by exact
gitlinks:

- `hermit/`: the primary Hermit product checkout.
- `reverie/`: the Reverie instrumentation/runtime checkout, used for reference,
  compatibility work, and coordinated changes.
- `liteinst2/`: the standalone LiteInst2 instrumentation checkout.
- `agent-utils/`: shared agent tooling, including `tick-hub`; `update = none`
  keeps it out of ordinary recursive initialization and project scripts
  materialize its exact pin on demand.

The parent owns orchestration policy, worktree registries, reproducible
experiments, AI research notes, and exact submodule pins. Product source, tests,
build definitions, and documentation stay in the appropriate submodule.

Hermit product work uses the fork's pull request workflow:

```text
feature branch -> pull request -> rrnewton/hermit:main
```

The parent harness works directly on shared `main`. Parent-only policy work may
be committed there when a task explicitly names the parent files and authorizes
the commit. `worktrees/ACTIVE.md` is ignored machine-local state; never commit or
merge it. Confirm the intended destination before publishing Reverie work. Stale
references to `integration`, legacy lead branches, or per-machine parent branches
do not override this model or the Hermit workflow below.

## Vocabulary

- **Parent**: `~/work/dev-hermit/`, the harness repository holding the submodule
  gitlinks and workspace state.
- **Primary checkout**: `~/work/dev-hermit/{hermit,reverie,liteinst2}/`.
  Coordinator-owned; used for integration, pinning, inspection, and cache
  donation.
- **Submodule**: a repository recorded by the parent as an exact gitlink SHA — a
  commit, not a branch and not uncommitted contents.
- **Slot**: one opaque paired workspace named `slotNN` under
  `~/work/dev-hermit/worktrees/`.
- **Active worktree**: a slot assigned to live work and recorded in
  `worktrees/ACTIVE.md`.
- **Parked slot**: a clean, detached slot retained for cache reuse and omitted
  from `ACTIVE.md`.
- **Legacy slot**: a pre-policy, non-canonical worktree listed in `ACTIVE.md`. It
  may finish its current task but must be removed instead of reused.
- **Product worktree**: one nested submodule checkout inside a parent slot, e.g.
  `slot02/hermit`.
- **Feature branch**: a task-specific branch checked out in one product worktree.
  Slot names are deliberately unrelated to branch names.
- **Hermit base**: current `rrnewton/hermit:main`, unless a task explicitly names
  another reviewed base.
- **Hermit upstream**: `facebookexperimental/hermit`, the public source reference
  — not this workspace's default landing target.
- **Shared slot**: an active slot used by multiple research-only agents, or by
  mutating agents with explicitly disjoint file ownership recorded in
  `ACTIVE.md`. No two agents may edit the same file concurrently.
- **Handoff SHA**: the exact commit tested and offered for integration. A branch
  name alone is not sufficient evidence.
- **3pai agent sandbox**: the agent execution environment identified at runtime
  by `META_3PAI_*` variables and the `3pai_sandbox.slice` cgroup. On these hosts,
  its file and network policy is enforced by BpfJailer. No authoritative
  expansion of `3pai` is documented here; do not invent one from the name.

Capacity caps (see Hard Invariant 13): at most **twelve active worktrees**,
**five parked slots**, and **fifteen agents**. Count each separately; active work
does not consume the parked-slot allowance.

## Stable Descriptive Naming

Use a stable, descriptive, lowercase-hyphenated slug for every option, wave,
workstream, phase, task, and other semantic unit of coordinated work. Name the
work or outcome, as task slugs do (e.g. `btrfs-flood-fix`), and keep that slug
unchanged across status updates.

Never use a bare ordinal or placeholder such as `Option-A`, `phase-1`, `round-N`,
or `wave-X` as an identifier, in coordinator communications, task names/notes,
dispatch instructions, or agent messages. When enumerating variants, retain the
slug and add a descriptive suffix, e.g. `btrfs-flood-fix/claude-agent`. Existing
infrastructure identifiers (PR numbers, slot numbers, canonical agent names)
remain valid and do not replace the work slug.

### Load-Bearing Shorthand

Define a coined term beside the artifact that owns the concept: infrastructure
names beside their implementation or configuration, product modes in the
product architecture document, and workspace/environment terms in this guide.
This binds every writer, including coordinators, implementation agents,
reviewers, and status aggregators. Define likely-to-recur shorthand when it is
coined; at the latest, a term that appears in more than one task, document, or
instruction must have exactly one canonical, one-sentence definition. Spell the
literal term in that definition so repository search finds it, and link every
later durable use to the definition instead of copying it. If no owning artifact
exists, define the term beside its first durable use; do not create a separate
glossary merely to hold it.

A definition does not make shorthand acceptable in a user-facing update. Lead
with the observable consequence and the decision it creates, in plain language;
put internal names, fields, formulas, and mechanisms afterward as supporting
detail. Do not make the reader follow a link or decode a reference to understand
the point. Brevity that loses comprehension is a private note, not a concise
status update.

## Canonical Layout

```text
~/work/dev-hermit/
|-- AGENTS.md
|-- CLAUDE.md -> AGENTS.md
|-- .orc/plugins/hermit-dev/       # project coordinator policy plugin
|-- .gitmodules
|-- hermit/                         # primary; coordinator only
|-- reverie/                        # primary; coordinator only
|-- liteinst2/                      # primary; coordinator only
|-- agent-utils/                    # optional shared tooling; exact pin, on demand
|-- worktree-state.json             # machine-local slot->owner map (gitignored)
|-- worktrees/
|   |-- ACTIVE.md                   # human notes + script-managed slot table
|   |-- ARCHIVED.md                 # append-only completed-slot history
|   |-- kvm/                        # named-agent slot (agent hermit-kvm)
|   |   |-- hermit/                 # Hermit worktree
|   |   |-- reverie/                # Reverie worktree
|   |   `-- liteinst2/              # LiteInst2 worktree
|   |-- dbi/
|   |   `-- ...
|   |-- slot01/                     # generic slot for an unnamed agent
|   |   `-- ...
|   `-- slotNN/                     # up to 12 active, plus 5 parked
|-- ai_docs/                        # durable textual research and handoffs
|   `-- transient/
|       `-- 2026-07-27-worktree-management-map.md   # index of every worktree-info source
|-- experiments/                    # durable reproducible evidence
`-- scratch/                        # ignored transient material
```

**Nested layout v3, one slot per agent.** Each slot is `worktrees/<slot>/` and by
default holds `hermit`, `reverie`, and `liteinst2` children. `<slot>` is either a
**named agent** (`kvm`, `dbi`, `sabre`, `e9patch`, `liteinst`, `ci`, `coord`,
`lander`, `opt` — the `hermit-` prefix is stripped) or a generic `slotNN`.
Exactly one mutating agent owns a slot. Single-product allocations remain
available for exceptional lightweight use. This deprecates the old flat layout
(`worktrees/slotNN` + sibling `worktrees_reverie/slotNN`) and primary-nested
`hermit/.worktrees/…` scratch trees — do not create either; the scripts only
produce the nested form.

**Provision and release slots with the registry-aware scripts**, never raw
`git worktree add`:

```bash
scripts/allocate-worktree.rs --agent hermit-kvm --task <id> --product all
scripts/release-worktree.rs  --slot kvm --clean
```

These enforce one-owner-per-slot and one-slot-per-agent, and are the **single
writer** of `worktree-state.json` and the ACTIVE.md managed table block, so those
two never drift. `scripts/slot-init.sh` is a quick manual fallback that creates
detached worktrees only and does NOT touch the registry.

A slot may be shared by research-only agents, or by agents with explicitly
disjoint file ownership, when the registry names every agent, task, branch, and
owned path (use `--i-promise-this-agent-is-read-mostly`). Never allow concurrent
edits to the same file or branch.

Physical worktrees, their build output, `worktree-state.json`, and `ACTIVE.md`
are machine-local and gitignored; `ARCHIVED.md` is the durable history.
**`ai_docs/transient/2026-07-27-worktree-management-map.md` is the authoritative
index of every place worktree information lives and how those places stay
consistent — read it before any worktree operation.**

## Hard Invariants

1. Never do feature development in a primary checkout.
2. Never let two agents mutate the same file or branch. Shared slots require
   explicit disjoint path ownership in `ACTIVE.md`.
3. Register every active slot, agent, task, branch, and owned path in
   `worktrees/ACTIVE.md` before the first edit or commit.
4. Require clean state before assignment, integration, parking, or pinning.
5. Treat unexpected changes as owned by somebody else. Do not reset, clean,
   overwrite, stash, or absorb them.
6. Do not run `git clean` anywhere in the parent, submodules, or slots.
7. Do not use a branch name as a worktree directory name.
8. Do not share writable build directories between worktrees.
9. Publish Hermit product work through a feature PR to `rrnewton/hermit:main`; do
   not land it by mutating the primary checkout.
10. Never force-push shared branches or `main`.
11. Never commit binaries or generated build artifacts to any repository.
12. A handoff is incomplete without exact SHAs and validation results.
13. Never exceed twelve active worktrees, five parked slots, or fifteen agents.
    Every normal worktree path must be
    `worktrees/<slot>/{hermit,reverie,liteinst2}` where `<slot>` is a named agent
    or `slotNN` (no other path shapes).
14. Never remove a dirty slot until its state has a documented recovery SHA.

## Clean Start And Checkout Ownership

Before dispatching or beginning work, inspect the parent, all primaries, and the
assigned slot. A dirty checkout is not an invitation to clean it.

```bash
cd ~/work/dev-hermit
git status --short --branch
git submodule status
for d in hermit reverie liteinst2 \
         worktrees/slot0X/hermit worktrees/slot0X/reverie worktrees/slot0X/liteinst2; do
  git -C "$d" status --short --branch
done
```

Interpret parent submodule status carefully:

- leading space — checkout matches the recorded gitlink.
- `+` — checkout HEAD differs from the recorded gitlink.
- `-` — submodule not initialized.
- `U` — submodule merge conflict.

A `+` is not automatically an error; the coordinator may be integrating a new
submodule commit. Attribute it before acting. Do not erase it with a submodule
update unless that exact reset is explicitly intended.

The primary checkouts are integration surfaces. Only the coordinator, or an agent
explicitly assigned an integration operation, may mutate them. Ordinary agents
may read them and use their build caches as copy sources.

Use `.agent-locked` files when the harness provides them: a mutating agent owns
the lock at its slot root and at each checkout it will modify; integration owns
the parent and relevant primary locks; in a shared slot, explicit path ownership
supplements the checkout lock. Missing lock tooling does not relax the no-overlap
rule — record ownership in `ACTIVE.md` and task notes.

Parent-only policy or harness work is exceptional because product slots do not
isolate the parent repository. Modify the parent root only when the task names
parent files and ownership is explicit; never mix a parent edit into an unrelated
product task.

## Worktree Registry

`worktrees/ACTIVE.md` is the source of truth for current slot ownership. Keep
exactly one live row per active slot, with at least:

```text
slot | agents/tasks | owned paths | Hermit branch | Reverie branch | LiteInst2 branch | started | purpose
```

Use `-` or `detached:<short-sha>` for an unchanged child; never create duplicate
rows as a task changes phase — update the existing row. List every agent and task
sharing a slot and make mutating path ownership unambiguous; research-only agents
may be marked `read-only`. A row marked DONE, HELD, or ABANDONED does not belong
in `ACTIVE.md`: either keep it active with an accurate current purpose, or park it
and append the final state to `ARCHIVED.md`.

Before dispatch, compare the registry with all Git worktree registries and the
filesystem:

```bash
git worktree list --porcelain
git -C hermit worktree list --porcelain
git -C reverie worktree list --porcelain
git -C liteinst2 worktree list --porcelain
find worktrees -mindepth 1 -maxdepth 3 -name .git -print | sort
```

The parent worktree list owns canonical nested slots. The product worktree lists
expose old direct product worktrees and must normally contain only the primary
checkout; any legacy exception must have a live registry row.

Resolve all of these before assigning a slot:

- a physical checkout not registered by its owning repository,
- a registered worktree whose directory is missing,
- a live slot absent from `ACTIVE.md`,
- an `ACTIVE.md` row for a parked or missing slot,
- duplicate rows for one slot,
- a branch checked out by more than one physical worktree,
- any new worktree path not using the `worktrees/slotNN` form.

Never silently delete a stale path. Record what owns it and preserve any
uncommitted work before the coordinator decides its disposition.

## Strict Slot Pool

All new work uses a canonical slot name — a named agent (`kvm`, `dbi`, …) or a
generic `slotNN` — under `worktrees/<slot>/`. **Branch and task names never appear
in worktree paths**; an agent name may name its slot, but its branch may not.

A canonical slot is either:

- **Active**: registered to one or more listed agents and tasks; at least one
  child may be on a feature branch. Shared mutating work requires disjoint paths;
  shared research access stays read-only.
- **Parked**: all children clean and detached in place, their caches and Git
  registrations available for the next task.

Parking is optional cache retention, not permanence. When the pool is at five
parked slots, reclaim the least useful before creating another; reclaim idle
slots earlier under disk pressure. Active slots are never evicted to satisfy the
parked cap; a dirty or blocked slot stays active until its work is handed off or
its state is recoverable.

Do not move or rename a slot directory — nested submodule metadata records its
path, and moving the outer worktree can invalidate the children. Pre-policy
non-canonical worktrees are exceptions only while their current task remains
active in `ACTIVE.md`; at closeout, archive and remove them — do not park,
rename, or reassign them.

### Provisioning A Missing Slot

Provisioning is a coordinator operation. Initialize all primary submodules first,
then use the registry-aware allocator. It enforces the canonical name, requires
agent metadata, enforces one-owner-per-slot and one-slot-per-agent, creates the
nested product worktrees, and writes both `worktree-state.json` and the ACTIVE.md
managed table block:

```bash
cd ~/work/dev-hermit
git submodule update --init --checkout -- hermit
git -c submodule.reverie.update=checkout \
  submodule update --init --checkout -- reverie
git submodule update --init --checkout -- liteinst2

scripts/allocate-worktree.rs --agent <agent> --task <task-id> \
  --product all --purpose "<one-line purpose>"
```

`scripts/slot-init.sh <slot> [hermit|reverie|liteinst2|both|all] [start-point]`
is a quick manual fallback that creates detached worktrees only and does NOT
update the registry. Do not invoke `git worktree add` directly for agent work.

Seed build caches with copy-on-write copies when useful:

```bash
cp -a --reflink=auto hermit/target/ "worktrees/$slot/hermit/target/"
cp -a --reflink=auto reverie/target/ "worktrees/$slot/reverie/target/"
cp -a --reflink=auto liteinst2/target/ "worktrees/$slot/liteinst2/target/"
```

Skip a missing or stale donor cache. Never symlink `target/` or another writable
cache between checkouts; correctness must not depend on cached output.

### Starting Work In A Slot

The coordinator may assign a parked slot, provision an active slot within the
twelve-worktree limit, or authorize sharing with research-only or disjoint-path
ownership. Before editing:

1. Confirm the parent slot and all nested submodules are registered and clean.
2. Fetch the relevant remotes without changing checked-out files.
3. For Hermit, branch from current `origin/main`; for Reverie, confirm the task's
   intended base and publication target.
4. Create a descriptive feature branch in each repository that will change.
5. Leave an unchanged child detached at a recorded base SHA.
6. Add or update one `ACTIVE.md` row before the first edit, including every
   sharing agent/task and owned path, and post the assignment to each task.

Example Hermit-only assignment:

```bash
slot=worktrees/slot01
git -C "$slot/hermit" fetch origin main
git -C "$slot/hermit" switch -c codex/<task-name> origin/main
git -C "$slot/reverie" switch --detach \
  "$(git -C "$slot" rev-parse HEAD:reverie)"
git -C "$slot/liteinst2" switch --detach \
  "$(git -C "$slot" rev-parse HEAD:liteinst2)"
```

For a coordinated change, create task branches in each changed child. They may
share the same descriptive branch name because they live in separate
repositories. Record every changed branch and base SHA.

Run all edits, formatting, builds, tests, and commits from the assigned child
worktrees. Always set the command working directory explicitly; similar paths
under the primary and slots make accidental edits easy.

### Closing, Parking, And Reclaiming A Slot

Close a slot only after intended work is committed and handed off. First capture
all child states (status must be empty, and record each HEAD):

```bash
for c in hermit reverie liteinst2; do
  git -C worktrees/slot0X/$c status --short
  git -C worktrees/slot0X/$c rev-parse HEAD
done
```

Record feature branches, exact SHAs, validation, and integration disposition in
`ARCHIVED.md`. Detach each child at the exact gitlink pinned by its parent slot so
the parent becomes clean:

```bash
slot=worktrees/slot0X
git -C "$slot/hermit" switch --detach "$(git -C "$slot" rev-parse HEAD:hermit)"
git -C "$slot/reverie" switch --detach "$(git -C "$slot" rev-parse HEAD:reverie)"
git -C "$slot/liteinst2" switch --detach "$(git -C "$slot" rev-parse HEAD:liteinst2)"
git -C "$slot" status --short   # must be empty
```

Remove the slot's single row from `ACTIVE.md`. Keep feature branches until their
commits are reachable from a pushed branch or merged target, or the coordinator
explicitly archives them. A non-clean slot remains active even if its agents are
idle.

Keep a clean slot parked only when its cache justifies the disk and fewer than
five slots are parked. Otherwise reclaim it through the parent repository:

```bash
git worktree remove --force worktrees/slot0X
git worktree prune
```

`--force` is required because the parent worktree contains initialized submodules;
it does not authorize discarding changes. For a registered legacy Hermit-only
exception, use `git -C hermit worktree remove <path>` after the same archive and
clean-state gates; use the owning Reverie repository for a Reverie-only exception.

To reuse a parked canonical slot, repeat the clean-start audit and create new
branches from the current intended base. Never reset a parked child to make it
current; explicit branch creation keeps its previous SHA auditable.

## Hermit Git And Pull Request Workflow

The primary Hermit repository is `rrnewton/hermit`. Public
`facebookexperimental/hermit` is the upstream reference, not this workspace's
default landing target. Ordinary Hermit work flows from a feature branch to a
pull request against current `rrnewton/hermit:main`.

### Feature Branch Rules

#### **ALWAYS COMMIT ON FEATURE BRANCHES**

**Every mutating agent must finish its task with all intended work committed on
its task feature branch. Never stash work. Never leave intended work
uncommitted. An uncommitted or stashed handoff is incomplete.**

- Fetch through the required proxy and branch from current `origin/main` — not an
  old slot HEAD, stale local branch, or parent gitlink.
- Create or use the task's dedicated feature branch before the first source or
  policy edit. Never commit task work directly on `main` or a shared integration
  branch.
- Keep one coherent task on one branch. Coordinated Hermit/Reverie branches form
  one logical change but remain separate Git histories.
- Commit all intended task-owned changes before reporting completion, even when
  the task does not repeat the instruction. If blocked, commit every coherent
  completed change and record the remaining blocker.
- Push the committed feature branch and open a draft pull request without asking
  for separate permission. An explicit task instruction not to publish is the
  only exception.
- Always push with an explicit refspec: `git push origin HEAD:refs/heads/<branch>`.
  The global `push.default=current` is a convenience, not permission to omit the
  destination.
- Never force-push a shared branch or `main`.
- Rebase only a private feature branch, and only when the task authorizes it;
  after rebasing, rerun affected validation and provide the new SHA.

### Publishing And Review

Unless a task explicitly prohibits publication, push the feature branch and open a
draft pull request against `rrnewton/hermit:main`. Before opening the PR:

1. Confirm the branch is based on the intended current `origin/main` and contains
   no unrelated commits.
2. Review the complete feature diff and validation evidence.
3. Run focused tests and the repository-level validation the task requires.
4. Inspect status, the complete diff, and the staged/committed paths.
5. Confirm the tested SHA is the feature branch tip.
6. Write the PR description with the mandatory sections defined below (`Summary`,
   `Determinism`, `Validation`, `Relationship to gVisor` for KVM, and `Human
   Review Required` when the post-facto label applies), including exact tests,
   failures, hardware limitations, and cross-repository dependency SHAs.
7. Re-read concurrent remote state before pushing.

```bash
with-proxy git fetch origin main
with-proxy git push origin HEAD:refs/heads/<feature-branch>
with-proxy gh pr create -R rrnewton/hermit --base main
```

In Meta environments, use appropriate proxies for web access.

Require the repository-defined authoritative gates green at the exact PR head. For
Hermit, `Regular tests (GitHub-hosted)` is authoritative; handle a
known-environmental self-hosted failure per current documented repository policy
and never bypass a genuine product failure. For Reverie, both `Regular tests` and
`Host-dependent tests` are authoritative. A skipped, missing, queued, stale, or
cancelled authoritative check is not green. Do not merge with unresolved
adversarial-review findings or merely because local tests pass. Report
infrastructure failures explicitly rather than weakening hardware-sensitive
assertions. Use `with-proxy` for networked `git` and `gh`, and never use
`gh auth switch` (authentication is shared machine state).

### Post-Facto Human Review

The canonical protocol is post-facto: once required adversarial review is resolved
and the authoritative CI gate is green, land the authorized change without waiting
for human-owner review. The human reviews after landing and corrections fix
forward.

Apply the single `post-facto-human-review` label if and only if a PR contains at
least one of these four triggers:

1. **New syscall support.** Verify the in-code determinization audit tags are
   present: `AUTONOMOUS-BOT-IMPLEMENTED` at the new dispatch/classification entry
   and `TODO-HUMAN-REVIEW(PR-id)` at the implementation/determinization block.
2. **A Reverie API or core-abstraction change**, including the `Tool`, `Guest`,
   `Backend`, or syscall-interception model.
3. **A new determinization strategy** (not an implementation of an already
   established strategy).
4. **A core DetCore scheduling change**: anything affecting how programs are
   scheduled, especially race-search behavior. Always labeled.
   [Hermit PR #1151](https://github.com/rrnewton/hermit/pull/1151), which moved
   slowdown into virtual-time/epoch scheduling, is the canonical good example.

Routine backend-parity work toward the golden ptrace reference does **not**
trigger review merely because it changes KVM, DBI, SaBRe, LiteInst, or another
non-ptrace backend. Apply the label only when that work also meets one of the four
triggers; "backend parity change" is not a valid rationale by itself.

Every PR description must contain these sections:

- **Summary**.
- **Determinism** — mandatory for every PR; explain why the change is
  deterministic and give the logic or informal proof, not only test results.
- **Validation** — exact commands, outcomes, limitations, and relaxations.
- **Relationship to gVisor** — required for KVM changes; state the relevant
  comparison or explicitly explain why none applies.
- **Human Review Required** — mandatory whenever `post-facto-human-review` is
  applied. Name the specific numbered trigger(s); vague prose such as "backend
  change" is insufficient.

Label rules:

- The label is informational and never a landing blocker.
- Keep `pre-land-human-review` defined as a notional opposite, but **never apply
  it** under the canonical protocol.
- Never apply, remove, or alter `human-approved`; it is owner-only.
- Never recreate or apply the obsolete `human-review` or `post-facto-review`
  labels.

The syscall audit tags verify trigger 1; they are not blanket markers for
bot-authored code or backend-parity work. Keep them at the smallest new syscall
entry and implementation region; only a human reviewer removes them.

### Landing Authorization

On startup or replacement, `hermit-lander` must discover durable inherited
remediation before taking new queue work; wake messages are advisory and may be
lost during recycling. Run:

```bash
ci-hub/ci-hub inherit-obligations --agent hermit-lander \
  --session "${ORC_AGENT_SESSION_ID:-$(hostname -s):$$}"
```

This acknowledges discovery, not completion. Every listed obligation remains
open in `ci-hub health` until its fix-forward or revert SHA is recorded with
`ci-hub resolve-obligation`.

Merge only when the task explicitly authorizes landing, required adversarial
review is resolved, and the authoritative checks are green at the current head
SHA. Human-owner review is post-facto and does not block landing. After landing,
verify the resulting `main` workflow when the task requires it. Never push
directly to Hermit `main`, force-push shared branches, or use a local primary
checkout to bypass the pull request controls.

Parent-only policy and gitlink changes are committed to shared `main` when the
task explicitly authorizes them. `worktrees/ACTIVE.md` is ignored local state and
never participates in commits or merges.

## Task Lifecycle And Closure

### Cross-Agent Routing

Ordinary agents do not have a reliable fleet-wide peer-message channel. The
agent-side `SendMessage` name registry is scoped to agents spawned in the same
tool session; it cannot resolve fixed or numeric ORC fleet names such as
`hermit-lander` or `hermit-247`. Do not claim that another fleet agent was
notified merely because a message was attempted.

Use TaskGraph as the durable handoff channel:

```bash
tg note <consumer-task-id> \
  "FROM <producer-task-id>: <deliverable, exact SHA/path, evidence, next action>"
```

Put the note on the task whose owner must act, so a replacement agent discovers
it by reading its assigned task. Task notes are pull-based: they preserve a
plan, measurement, verdict, or handoff across recycling, but they do not wake a
recipient and are not delivery acknowledgement.

For a time-sensitive handoff, first write the durable task note, then ask the
coordinator to relay it immediately:

```bash
scripts/orc-hermit-msg.py \
  "URGENT RELAY: <consumer-task-id> needs <agent>; durable details are in the task note"
```

The coordinator owns global fleet routing and must record relay confirmation on
the consumer task. An urgent handoff is incomplete until the coordinator
confirms the relay or the recipient acknowledges the task note. A direct
message to a same-session subagent may be used as an optimization, but its
result still belongs in a task note because the subagent and its ID disappear
on recycling.

Phantom closures — a task marked done while its work never landed on `main` — are
a recurring failure mode. To prevent them, task completion splits into an
implementation step the working agent performs and a closure step only the
coordinator performs, with an adversarial review gate between them.

### Status Model

`tg` has three non-terminal statuses — `open`, `backlog`, `in_progress` — and one
terminal status, `closed`. **`resolved` is NOT a distinct state: `tg` accepts it
only as an alias that immediately maps to `closed`** (`--help` groups them as
`RESOLVED/CLOSED`; `tg update --status resolved` prints `Closed:`). There is no
built-in "implemented but not landed" status, so IMPLEMENTED is represented with a
tag while status stays `in_progress`:

```text
open/backlog -> in_progress -> in_progress + `implemented` tag (IMPLEMENTED)
             -> closed (LANDED, coordinator only, after landing on main)
```

- **`in_progress`**: an agent is actively working the task.
- **`in_progress` + `implemented` tag** (IMPLEMENTED): implementation complete and
  published (PR link and handoff SHA in a note), deliberately kept out of the
  terminal `closed` bucket until it lands.
- **`closed`** (LANDED): the coordinator confirmed the PR merged to `main`.

### Rules

1. **A working agent NEVER moves a task to a terminal status.** Do not run
   `tg update --status closed` or `--status resolved` (it aliases to `closed`).
   When implementation is complete, add the `implemented` tag — preserving
   existing tags, since `--tags` replaces the set — leave status `in_progress`,
   and post the PR link plus exact handoff SHA as a note:

   ```bash
   tg note <task-id> "IMPLEMENTED: <PR url> | branch <name> | SHA <40-hex> | <validation summary>"
   tg update <task-id> --tags <existing-tags>,implemented   # status stays in_progress
   ```

   A completion report without a PR link (or, for a research-only task, the
   durable artifact path) is incomplete. State the level, backend, and any
   relaxations per the evidence rules, bound to the SHA, not a branch name.

2. **An adversarial review agent confirms the work exists in the PR** before the
   task is eligible for closure — the PR contains the claimed change, the diff
   matches the report, and the cited validation is real at the handoff SHA, not
   merely that a branch or note exists. An `implemented` task whose PR is empty,
   superseded, or already merged elsewhere is a phantom: strip the tag and keep it
   `in_progress`, do not close it.

3. **The task stays IMPLEMENTED until the PR lands on `main`.** A green PR that has
   not merged is still IMPLEMENTED, not LANDED. Do not close on local validation, a
   green check, or an approval alone.

4. **Only the coordinator closes tasks, and only after landing confirmation.**
   Closure requires the merge commit reachable from `origin/main` (Hermit or
   Reverie) or, for parent-only policy, the committed parent `main` SHA. Record the
   landed SHA in the task and `ARCHIVED.md`, then close:

   ```bash
   tg note <task-id> "LANDED: main <40-hex> | merged <PR url> | closed by coordinator"
   tg update <task-id> --status closed
   ```

### Exceptions

- **Research-only tasks** produce no PR. The agent tags `implemented` (status
  `in_progress`) with the durable artifact path (`ai_docs/…`, `experiments/…`, or
  a memory slug) as the handoff link; the coordinator closes after confirming the
  artifact exists and answers the question. Never close from a chat assertion
  alone.
- **Blocked tasks** stay `in_progress` (or move back to `open`) with the exact
  blocker and any partial committed SHA recorded; never tag `implemented` or close
  a blocked task to signal progress.
- **Stale-premise tasks** (the change already landed, or the target no longer
  exists) are tagged `implemented` with a note explaining the stale premise and
  the evidence SHA; the coordinator closes after verifying it.

## Bot-Created GitHub Issue Policy

Bot-created issues go on the `rrnewton` forks **ONLY**. **NEVER create an issue on
`facebookexperimental/hermit` or `facebookexperimental/reverie`** — those upstream
repositories sync into Meta's internal task tracker, so an agent-created issue
there creates unwanted internal tasks.

- Create Hermit issues on `rrnewton/hermit`, Reverie issues on `rrnewton/reverie`.
- Reading upstream issues and PRs is allowed. Editing, commenting on, or closing
  an upstream issue requires a task that explicitly authorizes that action.
- Use the registered wrapper for every agent-created issue; do not invoke raw
  `gh issue create`:

```bash
./.orc/plugins/hermit-dev/gh-issue-create \
  --repo rrnewton/hermit --title "..." --body "..."
```

The wrapper rewrites an accidental `facebookexperimental/{hermit,reverie}`
destination to its `rrnewton` fork, rejects unrelated repositories, and supplies
the required GitHub proxy when the caller has not.

## What Goes Where

Use ownership boundaries, not convenience, to choose a repository.

### Parent Repository

Track in the parent:

- workspace policy such as this guide,
- `.gitmodules`, exact submodule gitlinks, and parent ignore rules,
- `worktrees/ARCHIVED.md` (ACTIVE.md remains machine-local),
- generic workspace scripts and coordination tooling,
- durable textual AI research, design comparisons, and handoffs under `ai_docs/`,
- reproducible experiments under `experiments/`, including commands, host facts,
  exact input SHAs, seeds, and text/CSV/JSON results.

Keep transient material in ignored parent locations:

- `scratch/` for disposable notes, patches, logs, profiles, and probes,
- physical `worktrees/slot*/` checkout contents,
- local locks, agent registries, runtime state, credentials, and environment
  files,
- screenshots, build output, core dumps, coverage output, downloaded artifacts.

An experiment is durable only when another engineer can understand and repeat it.
Prefer this structure:

```text
experiments/<descriptive-name>_YYYYMMDD/
|-- README.md       # question, method, results, interpretation, reproduction
|-- metadata.json   # repo SHAs, command, host, toolchain, seed, inputs
`-- results.csv     # textual machine-readable measurements
```

Do not put product implementation in the parent even if it supports an
experiment. Land reusable product code and regression tests in the owning
submodule.

### Hermit Submodule

Hermit source, public APIs, CLI behavior, tests, build configuration, and product
documentation belong in `hermit`. Follow `hermit/AGENTS.md` for architecture and
validation. Do not copy Hermit code into a parent script to avoid a proper
product change.

### Reverie Submodule

Reverie source, instrumentation APIs, tests, build configuration, and product
documentation belong in `reverie`. Follow its local guide. Reference or
exploratory use does not justify modifying it; create a Reverie feature branch
only when the task owns a real Reverie change.

## Reverie API Policy

Additive Reverie extensions are allowed when existing consumers remain compatible:
narrowly scoped helpers, hooks, events, adapters, or optional capabilities whose
defaults preserve current behavior.

Discuss the design with the user before implementation when a proposal changes a
core Reverie abstraction or contract: the tool/event model or ordering, public
trait requirements, syscall interception/injection semantics, guest register or
memory contracts, lifecycle ownership, or container responsibilities.

Do not smuggle an abstraction change in as cleanup; prefer an additive API or
compatibility layer when technically sound. When Hermit and Reverie change
together, use coordinated branches in the same slot, make the lower-level Reverie
commit available first when possible, validate Hermit against its exact SHA, and
report both SHAs and their dependency. Confirm the intended Reverie PR destination
before publishing; do not assume authorization to mutate
`facebookexperimental/reverie`.

### Cross-Repository Changes

Keep each repository's commit independently coherent. Document the dependency
between SHAs in both handoffs. Land the lower-level dependency first when possible,
then update and validate the consumer against that exact commit. Only after the
team branches are correct should the parent pin one or both new SHAs.

## Commit Hygiene

Agents deliver reviewable commits, not anonymous working directories.

- Inspect `git status`, the complete diff, and the staged diff before committing.
- Stage only task-owned paths in the repository that owns them.
- Keep formatting-only churn and unrelated cleanup out of focused changes.
- Prefer one logical commit per repository per task; split only when each commit
  is independently coherent and useful.
- Use an imperative, descriptive subject that says what changed. Explain
  motivation, constraints, compatibility, and non-obvious validation in the body
  when needed.
- Never use placeholder subjects (`wip`, `tmp`, `checkpoint`, `validate`, `fix
  stuff`, `misc changes`), and never create empty bookkeeping commits.
- Do not claim a test passed unless it ran against the handed-off SHA. Do not hide
  failures or skipped hardware-dependent validation in prose; report the exact
  limitation.
- Amend or rewrite only private task commits when authorized. Never rewrite
  `main`, a shared or published branch, or a commit another task depends on.
- Do not mix parent gitlink updates into a submodule source commit; they are
  commits in different repositories.

Before committing, audit staged paths:

```bash
git status --short
git diff --cached --stat
git diff --cached
```

Before handoff, capture the exact state:

```bash
git status --short --branch
git rev-parse HEAD
git log -1 --oneline --decorate
python3 ci-hub/tests/documented_commands.py --closeout
```

The closeout guard refreshes `origin/main` through `with-proxy` and rejects
unpushed parent commits. A dirty shared parent also fails unless every retained
path is explicitly accounted for with `--dirty-note`; that exception documents
concurrent ownership and never authorizes staging or modifying someone else's
work.

Every handoff includes:

- task identifier, slot, and owner,
- repository and feature branch,
- exact commit SHA for Hermit and/or Reverie,
- base SHA and relationship to the intended target branch,
- concise change summary,
- exact validation commands and results,
- known failures, skipped checks, or environment limitations,
- cross-repository dependency SHAs,
- whether the branch is ready for fast-forward integration,
- parent gitlink update status.

For a coordinated change, provide both repository SHAs even if one child is
unchanged; label the unchanged SHA explicitly.

## Submodule Coordination And Pinning

The parent records exact submodule commits for reproducibility. Do not add a
`branch = ...` field to `.gitmodules`, and do not use `git submodule update
--remote` as a normal update mechanism.

### When To Update A Pointer

Update a parent gitlink only when:

- the target commit is intentional and reviewed,
- the submodule commit is reachable from its reviewed feature branch or target
  `main` history,
- required repository-local validation passed at that exact SHA,
- cross-repository compatibility was checked when relevant,
- the parent commit message names the reason for the pin movement.

Do not update a pointer merely because a primary checkout is ahead, a feature
branch exists, or `git status` shows a modified submodule. Do not pin an
unpublished private commit unless the task explicitly establishes how every
consumer can fetch it.

Every ordinary pointer advance follows the single-variable A/B protocol in
`ci-hub/history/SUBMODULE-BUMPS.md`: start at a clean, evidenced-green parent A;
advance exactly one gitlink to fetched submodule `origin/main`; create B whose
commit changes only that gitlink; verify B; and append the result to the ci-hub
history store. Never bury a gitlink advance inside an unrelated source/policy
commit. For determinism-related changes, one passing run is insufficient;
require a powered repeated probe such as the calibrated matched-load multisect
probe. Use `make single-submodule-bump ARGS='plan ...'` before execution.

### Pointer Update Procedure

After landing and validation:

```bash
cd ~/work/dev-hermit
git -C hermit rev-parse HEAD
git -C reverie rev-parse HEAD
git diff --submodule=log -- hermit reverie
git add hermit reverie                 # add only pointers intentionally moved
git diff --cached --submodule=log
```

`git add hermit` records only Hermit's checked-out commit, not uncommitted Hermit
files; verify the submodule is clean and on the intended SHA before staging the
gitlink.

If only one pointer changed, stage only that path. If Hermit and Reverie must move
together for compatibility, validate the exact pair and update both gitlinks in
one parent commit, recording old and new SHAs plus compatibility evidence.

Parent pinning does not replace pushes. Before sharing a parent commit, confirm
the referenced submodule commits are available from their authorized remotes.

### Initialization And Updates

Normal initialization reproduces the recorded commits:

```bash
git submodule update --init --checkout -- hermit
git -c submodule.reverie.update=checkout \
  submodule update --init --checkout -- reverie
```

Use the explicit Reverie override only when initialization is intended and
`.gitmodules` marks it `update = none`. Do not recursively initialize optional or
heavy nested submodules without a task that needs them. Worktree-specific
initialization must run inside the owning child checkout, never by repointing a
shared nested submodule worktree.

### Agent-Utils Main Peg And Contributions

`agent-utils` is shared upstream tooling, not a parent-local patch surface. The
parent gitlink and the canonical `agent-utils/` checkout must equal the fetched
`rrnewton/agent-utils:main` commit. Run `make check-agent-utils-pin`; it fetches
and prunes `origin`, then rejects a stale/ahead/diverged checkout, a parent
gitlink mismatch, or commits unreachable from every fetched `origin/*` ref.

Generic changes such as runner cgroups/CPU-time budgets, `tick-hub`, and PR
planning belong in `rrnewton/agent-utils`:

1. serialize agent-utils work: finish and land one change before starting the
   next so the repository never accumulates a second queue of open changes;
2. run the complete intra-agent-utils validation before landing, including the
   Python tests/typecheck, Rust workspace tests/lints, and the Python-Rust
   differential cross-check;
3. commit and push the validated change directly to `rrnewton/agent-utils:main`;
4. fetch `origin/main`, update the canonical checkout to the landed commit, run
   `make check-agent-utils-pin`, and commit the exact gitlink in the parent.

A PR is an exception, not the default. Open one only when either (a) a genuinely
high-risk change needs review before it reaches main, or (b) the change must be
coordinated with an in-flight parent change and cannot safely land independently.
Record which exception applies in the PR description, keep at most that one
agent-utils PR in flight, and land or close it before starting another change.
Convenience, habit, or ordinary implementation size is not a reason to open a PR.

Never leave generic fixes as uncommitted edits, local-only commits, or copied
implementations under `dev-hermit`. A pushed feature branch is recoverable but
does not satisfy the main peg until its commit reaches main and the parent pin
advances. Direct-to-main is not unvalidated-to-main: if any required check is
red, fix it before pushing main.

### Self-Hosted Runner Security

Never run a GitHub Actions runner as root on a Meta dev box or other Meta
data-center host, even when technically possible: a runner executes arbitrary
repository-controlled workflow content, so root would give that code elevated
privileges on internal infrastructure. This makes moving work off privileged
self-hosted execution the required architecture, not merely an optimization:
user-namespace tests are portable with the required `sysctl`, while the genuine
residue is KVM (`/dev/kvm`) and real-PMU counters, each of which must receive only
its minimum required privilege rather than a root runner. Treat the authorization,
ownership, and disposition of `hermit-gate-newton` as an open security question:
an agent session provisioned it without owner awareness, after which it executed
1,006 gate jobs.

## Binary And Large-File Policy

Never commit binaries to the parent, Hermit, or Reverie: compiled executables,
object files, libraries, archives, database dumps, core dumps, profiler captures,
screenshots, generated media, cached dependencies, and build trees. Git LFS is not
a workaround unless the repository owners establish an explicit policy for it.

Keep binary artifacts in ignored local directories or an approved external
artifact store. When evidence depends on an external artifact, commit a small text
manifest with its location, checksum, producing command, tool version, and source
SHA.

Textual files larger than 2 MiB also require explicit coordinator approval before
staging. Prefer summarized CSV/JSON, compressed external artifacts, or a
reproducible generator over repository bulk; compression does not make a binary
archive acceptable for Git.

Audit newly staged files before every commit:

```bash
git diff --cached --name-only --diff-filter=AM
git diff --cached --numstat
```

If a path looks generated or unexpectedly large, stop and inspect it with `file`,
`du`, and the ignore rules. Do not commit first and promise to remove it later.

## Validation And Evidence

Product validation commands come from the local submodule guides. Use the
narrowest relevant tests during development, then the required repository gate
before handoff. Cross-repository changes require validation against the exact
Hermit/Reverie pair proposed for pinning.

Evidence must bind to commits, not a mutable branch name:

```text
Hermit SHA:  <40-hex commit>
Reverie SHA: <40-hex commit or explicitly unchanged SHA>
Command:     <exact command>
Result:      pass/fail/skipped, with material output summarized
Environment: host/toolchain/hardware constraints when relevant
```

Hardware-dependent Hermit tests may be impossible on some hosts. Report that fact
and the observed failure; do not weaken, delete, or falsely bless a test to make
the local environment green.

The coordinator verifies both required CI jobs at the exact Hermit PR head and the
resulting target commit when landing is authorized. Local feature-branch
validation does not prove hosted and self-hosted CI are green.

## Product Vision

`goal-hermit-v2` is the long-term end state: a robust deterministic execution
engine whose `run` and `record` modes support arbitrary real-world binaries, whose
chaos mode exposes concurrency races, whose schedule search localizes races to
events and stack traces, whose production backend avoids ptrace overhead, and whose
non-communicating processes can execute in parallel.

`goal-qemu-linux-under-hermit` is the QEMU milestone: run a complete Linux VM as a
userspace QEMU process under Hermit so deterministic execution, record/replay,
chaos scheduling, and schedule search can expose and localize kernel races across
the full kernel and userspace stack.

Prioritize correctness, faithful replay, race discovery/localization, lower
overhead, backend maturity, and QEMU/Linux viability. Do not close either
long-range goal without its required human verification.

## Communication Precision

This section governs coordinator headlines, cross-task aggregation, and
user-facing progress reports. Product guides govern the exact commands and per-run
evidence that implementation agents supply. Coordinator reports must be specific
enough that another engineer can act without re-deriving the scope; vague summaries
are unacceptable.

- **Never headline a bare pass ratio.** `10/10 pass` is not a headline. Name the
  program category, the exact programs (or link an immediately adjacent table),
  the Hermit mode and backend, and why that batch was selected. Example: `System
  utilities, ptrace L2: id, whoami, groups, uptime, free, df, ps, time, timeout,
  and nice pass 10/10; this batch probes process metadata after the envp fix.`
- **Separate new results from baseline.** Label every rollup result `New this
  run`, `Baseline reconfirmed`, `Regression`, or `Not rerun`, and state the commit
  or PR that changed between the compared runs. Never present a repeated baseline
  as newly achieved coverage.
- **Classify programs before totaling them.** Use explicit categories (system
  utilities, text-processing utilities, interpreters/runtimes, compilers/build
  tools, databases, network programs, interactive applications,
  virtualization/emulators). Mixed batches require category subtotals; one
  aggregate ratio may not hide which class improved or failed.
- **Name execution context.** Distinguish native baseline, ptrace, DBI, and KVM,
  and distinguish strict run, strict verify, record/replay, and relaxed modes.
  State why the chosen mode/backend answers the batch question.
- **Name the tool.** Never write "the Tool" or "a tool" for a specific one; say
  which: `StraceTool`, `Detcore`, `CounterTool`, etc.
- **Give the exact command and arguments.** Never say "the program passes"; state
  the full command line, e.g. `hermit run --strict --verify -- bash -c 'echo hi |
  gzip | gunzip'`.
- **Say where.** Always specify the location of a claim: `main`, `PR #N`, or the
  exact feature branch / SHA. A result with no location is unverifiable.
- **Qualify the result.** Always report the determinism level (`L0`/`L1`/`L2`), the
  pass count (e.g. `18/20`, `5/5`), and the exact programs or test names covered.
  "It works" is not a result.
- **Bind evidence to commits, not branch names**, per the evidence block above.

## Establish What You Have Before Acting On It

This is a **coordinator** rule. It governs how the coordinator turns an
observation into filed work or a reported conclusion, and it binds the
coordinator specifically: the recurring instances are the coordinator's own, not
an agent's. Both failure modes below are the same mistake wearing different
clothes — acting on a claim or a quantity before establishing what it actually
is. Verifying first costs minutes; acting on the wrong thing costs the
implementation, the rollback, and the confusion in between. When a premise or a
headline number comes from a note or from the first quantity that was easy to
obtain, stop and establish what you have before you act.

### A note is unverified until the coordinator checks it

A note is a snapshot of what one agent believed at one moment, not an
established fact. Do not launder a note into a task premise by rewriting "X
appears to be Y" into the imperative "X is Y, fix it." When a task's premise
originates from a note (or any second-hand observation) rather than from the
coordinator's own direct verification, the task description must:

- **attribute the premise to its source** — the note, the agent, and that it was
  a point-in-time belief;
- **mark it UNVERIFIED** in those words; and
- **make "verify the premise" the explicit first step, with "premise refuted"
  named as a valid and valuable outcome** — the refutation is a deliverable, not
  a failed task.

The originating agent is not at fault: a correctly-hedged observation
("code-inferred, unmeasured") becomes wrong only when the coordinator drops the
hedge. Worked example: `tiocgpgrp_uncanonicalized_in_detcore` was filed from a
code-reading note as an established determinism hole; a runtime test showed the
value is already handled by PID-namespace translation under `--strict`, and the
"obvious fix" would have broken valid job control — premise refuted cheaply,
before any code changed. (A companion instance: the #1518 lint "not wired into
workflows" premise was refuted by injection — the lint was already enforced in
three places.)

### A number is unqualified until the coordinator states what it measures

A number can be arithmetically correct and still measure the wrong thing. The
trap is reaching for the first available quantity, because easily-obtained
quantities are usually **proxies** for the thing actually wanted. Before acting
on a number — filing work against it, or reporting it as evidence — establish:

- **what it measures** — is this the quantity the decision actually needs, or a
  proxy?
- **its unit** — a count is not a rate; an aggregate is not a per-unit; a load
  average is not a utilisation; a source tree is not a shipping artifact;
- **its denominator or comparison base** — against what, and per what?

**When a ratio looks surprising, interrogate the denominator before filing work
against the numerator.** Worked example: "only 1 of 132 validate runs has DAG
profiling" reads as sparse instrumentation until you check the denominator — it
is actually evidence the mechanism is barely used at all, a different problem
needing different work. Other same-day instances: 478 "saturating" processes of
which 415 were zombies (a count is not load); a 920 GB total weighed against a
200 GB **per-worktree** cap (an aggregate is not a per-unit); load average 81
then 134 while the box was ~64% idle (a load average is not a utilisation); a
134 MB DynamoRIO **source submodule** cited while debating embedding a 3.2 MB
gzipped **payload**; "1.58x parallelism" read as a missing scheduler that had in
fact been told to use two lanes; a hardcoded "20–70 minutes" presented as an
estimate. The full list lives in the note on
`task-premises-from-notes-must-be-marked-unverified`.

## Failure, Recovery, And Concurrent Work

Other agents may update the parent, primary checkouts, registries, or branches
while a task is running. Re-read state before every integration or pinning step.
Unexpected movement is a reason to reassess, not to restore an older snapshot.

- Do not use `git reset --hard`, `git checkout -- <path>`, or destructive cleanup
  on changes you did not create.
- Do not move uncommitted work between slots without recording its owner and exact
  recovery procedure.
- Do not silently adopt another agent's branch or worktree.
- If a feature no longer fast-forwards, update the private feature branch and
  retest; never paper over divergence with a merge commit.
- If a primary is dirty, integration stops until the changes are attributed.
- If a submodule pointer conflicts, resolve the intended product history first,
  then choose the exact gitlink. Never resolve a gitlink conflict by picking a
  side without inspecting the submodule commits.
- If a task is blocked, preserve clean committed work, post the exact blocker and
  SHAs, and keep the slot active until the coordinator decides to park it.

## Coordinator Checklist

Before dispatch:

1. Reconcile `ACTIVE.md`, both Git worktree lists, and physical slot children.
2. Check parent, primaries, and candidate slot for unexpected changes.
3. Confirm no more than twelve worktrees active or fifteen agents assigned.
4. Confirm exclusive ownership or record every sharing agent and disjoint path.
5. Confirm the intended base SHA and publication target for each repository.
6. Register the slot before work begins.
7. If the task premise came from a note, or a headline number came from the
   first quantity to hand, apply *Establish What You Have Before Acting On It*:
   attribute the premise and mark it UNVERIFIED with verification as step one;
   state a number's measure, unit, and denominator before filing work against it.

Before Hermit publication or landing:

1. Re-read concurrent local state, remote `main`, and the exact PR head.
2. Verify the handoff SHA, diff, test evidence, and repository cleanliness.
3. Push/open the feature PR only when the task authorizes publication.
4. Require both hosted and self-hosted checks green at the exact head SHA.
5. Merge only when authorized and record the resulting `main` SHA and CI.

Before parent pinning or promotion:

1. Confirm submodule commits are clean, reviewed, tested, and fetchable.
2. Inspect `git diff --submodule=log` before staging.
3. Stage only intended gitlinks and parent-owned files.
4. Validate a coordinated Hermit/Reverie pair when both pointers move.
5. Commit parent changes to `main` only when the task explicitly authorizes it.

Before closeout:

1. Ensure each changed repository has a clean committed feature branch.
2. Record exact SHAs and validation in the task and `ARCHIVED.md`.
3. Detach both canonical slot children at their parent-pinned gitlinks.
4. Remove the slot row, or update it if other sharing agents remain active.
5. Reclaim legacy slots and any parked slot needed to keep at most five parked.
6. Leave unrelated concurrent work exactly as found.

Before closing a task (coordinator only, per Task Lifecycle And Closure):

1. Confirm the task is `in_progress` with the `implemented` tag and carries a PR
   link or artifact path.
2. Confirm the adversarial reviewer verified the work exists in that PR.
3. Confirm the PR merged to `main` — merge commit reachable from `origin/main` (or
   the committed parent `main` SHA for parent-only policy).
4. Record the landed SHA, then `tg update <task-id> --status closed`. Never let a
   working agent close its own task, and treat `--status resolved` as a close.
