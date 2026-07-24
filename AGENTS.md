## PR Comment Convention

ALL PR descriptions and comments MUST start with a role tag:

- `[impl agent, MODEL]` - for implementation agents
- `[adversarial-reviewer agent, MODEL]` - for review agents
- `[coordinator, MODEL]` - for coordinator agents
- `[Human]` - for the human owner

Examples: `[impl agent, gpt-5.6-sol]`, `[adversarial-reviewer agent, opus-4.8]`

## Primary Checkout Invariant

**~/work/dev-hermit/hermit and ~/work/dev-hermit/reverie must ALWAYS be on the latest main branch.**

- NEVER detach HEAD on the primary checkout
- NEVER checkout feature branches on the primary checkout
- All PR validation, testing, and feature work happens in worktree slots only
- After ANY operation that touches the primary checkout, verify: `git branch --show-current` returns `main`
- If you need to validate a PR, use a worktree slot - never the primary checkout
- After finishing any work involving the primary checkout, immediately return it to latest main: `git checkout main && with-proxy git pull origin main`

# dev-hermit Parent Workspace Guide

This is the single canonical policy source for the `dev-hermit` parent
repository and every agent launched from it. `CLAUDE.md` is a symlink to this
file so all agent entry points receive the same rules. The `hermit-dev` ORC
plugin also reads this file at activation time rather than duplicating it.

The more specific `AGENTS.md` files inside `hermit/` and `reverie/` also apply
when working in those repositories; use them for product architecture, build,
test, and style rules. The stricter rule wins. The Hermit pull request workflow
in this file supersedes legacy guidance that routed ordinary Hermit changes
through a local `devbig-lead` branch.

## Role Boundary

This parent guide is for the **coordinator role**: task dispatch, slot and
checkout ownership, cross-repository dependency order, PR landing, parent
gitlinks, and evidence-based status rollups. It must not grow into a second
Hermit or Reverie implementation manual.

Product implementation agents follow the repository-root `hermit/AGENTS.md`
or `reverie/AGENTS.md` for architecture, source conventions, test selection,
and per-run evidence. `.llms/skills/` contains task skills; it is not a second
`AGENTS.md` policy location. Do not duplicate product guides there.

When both scopes apply, this guide owns workspace coordination and publication;
the product guide owns implementation and product validation. A coordinator
must preserve exact implementation evidence when aggregating it, not replace
product-specific requirements with a summary.

## Project Overview

`~/work/dev-hermit/` is a multi-agent development harness. It is **not** the
Hermit or Reverie code project. The parent repository coordinates two pinned
Git submodules:

- `hermit/`: the primary Hermit product checkout.
- `reverie/`: the Reverie instrumentation/runtime checkout used for reference,
  compatibility work, and coordinated changes.

The parent owns orchestration policy, worktree registries, reproducible
experiments, AI research notes, and exact submodule pins. Product source,
product tests, build definitions, and product documentation stay in the
appropriate submodule.

Hermit product work uses the fork's pull request workflow:

```text
feature branch -> pull request -> rrnewton/hermit:main
```

The parent harness works directly on shared `main`. Parent-only policy work may
be committed there when a task explicitly names the parent files and authorizes
the commit. `worktrees/ACTIVE.md` is ignored machine-local coordination state;
do not commit or merge it. Confirm the intended destination before publishing
Reverie work. Stale references to `integration`, `devbig-lead`, or per-machine
parent branches do not override this model or the Hermit workflow below.

## Vocabulary

- **Parent**: `~/work/dev-hermit/`, the harness repository containing the
  submodule gitlinks and workspace state.
- **Primary checkout**: `~/work/dev-hermit/hermit/` or
  `~/work/dev-hermit/reverie/`. A primary checkout is coordinator-owned and is
  used for integration, pinning, inspection, and cache donation.
- **Submodule**: a repository recorded by the parent as an exact gitlink SHA.
  The parent records a commit, not a branch and not uncommitted contents.
- **Slot**: one opaque paired workspace named `slotNN` under
  `~/work/dev-hermit/worktrees/`.
- **Active worktree**: a slot assigned to live work and recorded in
  `worktrees/ACTIVE.md`. At most twelve may be active.
- **Parked slot**: a clean, detached slot retained for cache reuse and omitted
  from `ACTIVE.md`. At most five may be parked.
- **Legacy slot**: a pre-policy, non-canonical worktree listed in `ACTIVE.md`.
  It may finish its current task but must be removed instead of reused.
- **Product worktree**: one nested submodule checkout inside a parent slot,
  for example `slot02/hermit`.
- **Feature branch**: a task-specific branch checked out in one product
  worktree. Slot names are deliberately unrelated to branch names.
- **Hermit base**: current `rrnewton/hermit:main`, unless a task explicitly
  names another reviewed base.
- **Hermit upstream**: `facebookexperimental/hermit`, used as the public source
  reference rather than this workspace's default landing target.
- **Shared slot**: an active slot used by multiple research-only agents or by
  mutating agents with explicitly disjoint file ownership. Shared access must
  be recorded in `ACTIVE.md`; no two agents may edit the same file concurrently.
- **Handoff SHA**: the exact commit tested and offered for integration. Branch
  names alone are not sufficient evidence.

## Canonical Layout

The intended parent layout is:

```text
~/work/dev-hermit/
|-- AGENTS.md
|-- CLAUDE.md -> AGENTS.md
|-- .orc/plugins/hermit-dev/       # project coordinator policy plugin
|-- .gitmodules
|-- hermit/                         # primary; coordinator only
|-- reverie/                        # primary; coordinator only
|-- worktrees/
|   |-- ACTIVE.md                   # exactly one row per active slot pair
|   |-- ARCHIVED.md                 # append-only completed-slot history
|   |-- slot01/
|   |   |-- hermit/                 # Hermit worktree
|   |   `-- reverie/                # Reverie worktree
|   |-- slot02/
|   |   |-- hermit/
|   |   `-- reverie/
|   |-- slot03/
|   |   |-- hermit/
|   |   `-- reverie/
|   `-- slotNN/                    # up to 12 active, plus 5 parked
|-- ai_docs/                        # durable textual research and handoffs
|-- experiments/                    # durable reproducible evidence
`-- scratch/                        # ignored transient material
```

A slot is normally one ownership unit. It may be shared by research-only
agents or by agents with explicitly disjoint file ownership when the registry
names every agent, task, branch, and owned path. A Hermit-only task leaves the
slot's Reverie worktree clean and detached unless coordinated Reverie work is
explicitly assigned. Never allow concurrent edits to the same file or branch.

Physical worktrees, their build output, and `ACTIVE.md` are machine-local and
ignored by the parent repository. `ARCHIVED.md` remains the durable history.

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
9. Publish Hermit product work through a feature PR to `rrnewton/hermit:main`;
   do not land it by mutating the primary checkout.
10. Never force-push shared branches or `main`.
11. Never commit binaries or generated build artifacts to any repository.
12. A handoff is incomplete without exact SHAs and validation results.
13. Never exceed twelve active worktrees, five parked slots, or fifteen agents;
    never create a non-`slotNN` worktree path.
14. Never remove a dirty slot until its state has a documented recovery SHA.

## Clean Start And Checkout Ownership

Before dispatching or beginning work, inspect the parent, both primaries, and
the assigned slot. A dirty checkout is not an invitation to clean it.

```bash
cd ~/work/dev-hermit
git status --short --branch
git submodule status
git -C hermit status --short --branch
git -C reverie status --short --branch
git -C worktrees/slot0X/hermit status --short --branch
git -C worktrees/slot0X/reverie status --short --branch
```

Interpret parent submodule status carefully:

- A leading space means the checkout matches the recorded gitlink.
- `+` means the checkout HEAD differs from the recorded gitlink.
- `-` means the submodule is not initialized.
- `U` means a submodule merge conflict.

A `+` is not automatically an error; the coordinator may be integrating a new
submodule commit. Attribute it before acting. Do not make it disappear with a
submodule update unless that exact reset is explicitly intended.

The primary checkouts are integration surfaces. Only the coordinator, or an
agent explicitly assigned an integration operation, may mutate them. Ordinary
agents may read them and may use their build caches as copy sources.

Use `.agent-locked` files when the harness provides them. A mutating agent
owns the lock at its slot root and at each checkout it will modify. Integration
owns the parent and relevant primary locks. In a shared slot, explicit path
ownership supplements the checkout lock. Missing lock tooling does not relax
the no-overlap rule; record ownership in `ACTIVE.md` and task notes.

Parent-only policy or harness work is exceptional because product slots do not
isolate the parent repository. Modify the parent root only when the task names
parent files and ownership is explicit. Do not mix a parent edit into an
unrelated product task.

## Worktree Registry

`worktrees/ACTIVE.md` is the source of truth for current slot ownership. Keep
exactly one live row per active slot pair, with at least:

```text
slot | agents/tasks | owned paths | Hermit branch | Reverie branch | started | purpose
```

Use `-` or `detached:<short-sha>` for an unchanged child; do not create
duplicate rows as a task changes phase. List every agent and task sharing a
slot and make mutating path ownership unambiguous; research-only agents may be
marked `read-only`. Update the existing row. A row marked DONE, HELD, or
ABANDONED does not belong in `ACTIVE.md`: either keep it active with an accurate
current purpose or park it and append the final state to `ARCHIVED.md`.

Before dispatch, compare the registry with both Git worktree registries and
the filesystem:

```bash
git worktree list --porcelain
git -C hermit worktree list --porcelain
git -C reverie worktree list --porcelain
find worktrees -mindepth 1 -maxdepth 3 -name .git -print | sort
```

The parent worktree list owns canonical nested slots. The product worktree
lists expose old direct Hermit/Reverie worktrees and must normally contain
only the primary checkout; any legacy exception must have a live registry row.
The workspace may have at most twelve active worktrees, five parked slots, and
fifteen agents. Count each category separately; active work does not consume
the parked-slot allowance.

Resolve all of these before assigning a slot:

- a physical checkout not registered by its owning repository,
- a registered worktree whose directory is missing,
- a live slot absent from `ACTIVE.md`,
- an `ACTIVE.md` row for a parked or missing slot,
- duplicate rows for one slot,
- a branch checked out by more than one physical worktree,
- any new worktree path that does not use the `worktrees/slotNN` form.

Never silently delete a stale path. Record what owns it and preserve any
uncommitted work before the coordinator decides its disposition.

## Strict Slot Pool

All new work uses a top-level canonical `slotNN` name. At most twelve
worktrees may be active and at most five clean slots may be parked. Up to
fifteen agents may work concurrently, including agents explicitly sharing a
slot for research-only or disjoint-file work. Branch names, task names, and
agent names never appear in worktree paths.

A canonical slot is either:

- **Active**: the slot is registered to one or more listed agents and tasks; at
  least one child may be on a feature branch. Shared mutating work requires
  disjoint paths, and shared research access remains read-only.
- **Parked**: both children are clean and detached in place; their caches and
  Git registrations remain available for the next task.

Parking is optional cache retention, not permanence. Reclaim the least useful
parked slot before creating another slot when the pool is at five, and reclaim
idle slots earlier when disk pressure warrants it. The five-slot cap applies
only to parked slots; active worktrees have their separate twelve-worktree cap.
Active slots are never evicted to satisfy the parked cap. A dirty or blocked
slot remains active until its work is handed off or its state is recoverable.

Do not move or rename a slot directory. Nested submodule metadata records its
path, and moving the outer worktree can invalidate the children. Pre-policy
non-canonical worktrees are temporary exceptions only while their current
task remains active in `ACTIVE.md`. At closeout, archive and remove them; do
not park, rename, or assign them to another task.

### Provisioning A Missing Slot

Provisioning is a coordinator operation. Initialize both primary submodules
first, then use the tracked helper. The helper enforces the canonical name,
requires agent/task metadata, enforces active and parked capacity separately,
initializes the nested submodules, and appends the registry row:

```bash
cd ~/work/dev-hermit
git submodule update --init --checkout -- hermit
git -c submodule.reverie.update=checkout \
  submodule update --init --checkout -- reverie

./slot-init.sh slot01 --owner <agent> --task <task-id> \
  --purpose "<one-line purpose>"
```

Do not invoke `git worktree add` directly for agent work.

Build caches may be seeded with copy-on-write copies when useful:

```bash
cp -a --reflink=auto hermit/target/ "worktrees/$slot/hermit/target/"
cp -a --reflink=auto reverie/target/ "worktrees/$slot/reverie/target/"
```

Skip a missing or stale donor cache. Never symlink `target/` or another
writable cache between checkouts. Correctness must not depend on cached output.

### Starting Work In A Slot

The coordinator may assign a parked slot, provision an active slot within the
twelve-worktree limit, or authorize sharing with research-only or disjoint-path
ownership. Before editing:

1. Confirm the parent slot and both nested submodules are registered and clean.
2. Fetch the relevant remotes without changing checked-out files.
3. For Hermit, branch from current `origin/main`; for Reverie, confirm the
   task's intended base and publication target.
4. Create a descriptive feature branch in each repository that will change.
5. Leave an unchanged child detached at a recorded base SHA.
6. Add or update one `ACTIVE.md` row before the first edit, including every
   sharing agent/task and owned path, and post the assignment to each task.

Example Hermit-only assignment:

```bash
slot=worktrees/slot01
HTTPS_PROXY=http://fwdproxy:8080 git -C "$slot/hermit" fetch origin main
git -C "$slot/hermit" switch -c codex/<task-name> origin/main
git -C "$slot/reverie" switch --detach \
  "$(git -C "$slot" rev-parse HEAD:reverie)"
```

For a coordinated change, create task branches in both children. They may use
the same descriptive branch name because they live in separate repositories.
Record both names and both base SHAs.

Run all edits, formatting, builds, tests, and commits from the assigned child
worktrees. Always set the command working directory explicitly; similar paths
under the primary and slots make accidental edits easy.

### Closing, Parking, And Reclaiming A Slot

Close a slot only after intended work is committed and handed off. First
capture both child states:

```bash
git -C worktrees/slot0X/hermit status --short
git -C worktrees/slot0X/reverie status --short
git -C worktrees/slot0X/hermit rev-parse HEAD
git -C worktrees/slot0X/reverie rev-parse HEAD
```

Both status commands must produce no output. Record feature branches, exact
SHAs, validation, and integration disposition in `ARCHIVED.md`. Detach each
child at the exact gitlink pinned by its parent slot so the parent becomes
clean:

```bash
slot=worktrees/slot0X
git -C "$slot/hermit" switch --detach "$(git -C "$slot" rev-parse HEAD:hermit)"
git -C "$slot/reverie" switch --detach "$(git -C "$slot" rev-parse HEAD:reverie)"
git -C "$slot" status --short
```

The final status command must produce no output. Remove the slot's single row
from `ACTIVE.md`. Keep feature branches until their commits are reachable
from a pushed branch or merged target, or the coordinator explicitly archives
them. A non-clean slot remains active even if its agents are idle.

Keep the clean slot parked only when its cache justifies the disk and fewer
than five slots are parked. Otherwise reclaim it through the parent repository:

```bash
git worktree remove --force worktrees/slot0X
git worktree prune
```

`--force` is required because the parent worktree contains initialized
submodules; it does not authorize discarding changes. For a registered legacy
Hermit-only exception, use `git -C hermit worktree remove <path>` after the
same archive and clean-state gates. Use the owning Reverie repository for a
Reverie-only exception.

To reuse a parked canonical slot, repeat the clean-start audit and create new
branches from the current intended base. Never reset a parked child to make it
current; explicit branch creation keeps its previous SHA auditable.

## Hermit Git And Pull Request Workflow

The primary Hermit repository is `rrnewton/hermit`. The public
`facebookexperimental/hermit` project is the upstream reference, not this
workspace's default landing target. Ordinary Hermit work flows from a feature
branch to a pull request against current `rrnewton/hermit:main`.

### Feature Branch Rules

#### **ALWAYS COMMIT ON FEATURE BRANCHES**

**Every mutating agent must finish its task with all intended work committed on
its task feature branch. Never stash work. Never leave intended work
uncommitted. An uncommitted or stashed handoff is incomplete.**

- Fetch through the required proxy and branch from current `origin/main`, not
  an old slot HEAD, stale local branch, or parent gitlink by accident.
- Create or use the task's dedicated feature branch before the first source or
  policy edit. Never commit task work directly on `main` or a shared integration
  branch.
- Keep one coherent task on one branch. Coordinated Hermit/Reverie branches
  together form one logical change but remain separate Git histories.
- Commit all intended task-owned changes before reporting completion, even when
  the task does not repeat the commit instruction. If the task is blocked,
  commit every coherent completed change and record the remaining blocker.
- Push the committed feature branch and open a draft pull request without
  asking for separate permission. An explicit task instruction not to publish
  is the only exception.
- Always push with an explicit refspec:
  `git push origin HEAD:refs/heads/<branch>`. The global
  `push.default=current` setting is a convenience, not permission to omit the
  destination.
- Never force-push a shared branch or `main`.
- Rebase only a private feature branch and only when the task authorizes it.
  After rebasing, rerun affected validation and provide the new SHA.

### Publishing And Review

Unless a task explicitly prohibits publication, push the feature branch and
open a draft pull request against `rrnewton/hermit:main`. Before opening the PR:

1. Confirm the feature branch is based on the intended current `origin/main`
   and does not contain unrelated commits.
2. Review the complete feature diff and validation evidence.
3. Run focused tests and the repository-level validation required by the task.
4. Inspect status, the complete diff, and the staged/committed paths.
5. Confirm the tested SHA is the feature branch tip.
6. Build a PR description with exact tests, failures, hardware limitations,
   and cross-repository dependency SHAs.
7. Re-read concurrent remote state before pushing.

```bash
HTTPS_PROXY=http://fwdproxy:8080 git fetch origin main
HTTPS_PROXY=http://fwdproxy:8080 \
  git push origin HEAD:refs/heads/<feature-branch>
HTTPS_PROXY=http://fwdproxy:8080 gh pr create -R rrnewton/hermit --base main
```

Require both GitHub Actions jobs to be green at the exact PR head:
**Regular tests (GitHub-hosted)** and **Host-dependent tests (self-hosted)**.
A skipped, missing, queued, stale, or cancelled check is not green. Do not
merge with unresolved review findings or merely because local tests pass.
Report infrastructure failures explicitly rather than weakening
hardware-sensitive assertions. Use `HTTPS_PROXY=http://fwdproxy:8080` for all
networked `git` and `gh` operations, and never use `gh auth switch` because
authentication is shared machine state.

### Landing Authorization

Merge only when the task explicitly authorizes landing, review is resolved,
and both required checks are green at the current head SHA. After landing,
verify the resulting `main` workflow when the task requires it. Never push
directly to Hermit `main`, force-push shared branches, or use a local primary
checkout to bypass the pull request controls.

Parent-only policy and gitlink changes are committed to shared `main` when the
task explicitly authorizes them. `worktrees/ACTIVE.md` is ignored local state
and never participates in commits or merges.

## Bot-Created GitHub Issue Policy

Bot-created issues go on the `rrnewton` forks **ONLY**. **NEVER create an
issue on `facebookexperimental/hermit` or `facebookexperimental/reverie`.**
Those upstream repositories sync into Meta's internal task tracker, so an
agent-created issue there creates unwanted internal tasks.

- Create Hermit issues on `rrnewton/hermit`.
- Create Reverie issues on `rrnewton/reverie`.
- Reading upstream issues and pull requests is allowed. Editing, commenting
  on, or closing an upstream issue requires a task that explicitly authorizes
  that upstream maintenance action.
- Use the registered wrapper for every agent-created issue; do not invoke raw
  `gh issue create`:

```bash
./.orc/plugins/hermit-dev/gh-issue-create \
  --repo rrnewton/hermit --title "..." --body "..."
```

The wrapper also rewrites an accidental `facebookexperimental/hermit` or
`facebookexperimental/reverie` destination to its `rrnewton` fork and rejects
unrelated repositories. It supplies the required GitHub proxy when the caller
has not already set one.

## What Goes Where

Use ownership boundaries, not convenience, to choose a repository.

### Parent Repository

Track in the parent:

- workspace policy such as this guide,
- `.gitmodules`, exact submodule gitlinks, and parent ignore rules,
- `worktrees/ARCHIVED.md` (ACTIVE.md remains machine-local),
- generic workspace scripts and coordination tooling,
- durable textual AI research, design comparisons, and handoffs under
  `ai_docs/`,
- reproducible experiments under `experiments/`, including commands, host
  facts, exact input SHAs, seeds, and text/CSV/JSON results.

Keep transient material in ignored parent locations:

- `scratch/` for disposable notes, patches, logs, profiles, and probes,
- physical `worktrees/slot*/` checkout contents,
- local locks, agent registries, runtime state, credentials, and environment
  files,
- screenshots, build output, core dumps, coverage output, and downloaded
  artifacts.

An experiment is durable only when another engineer can understand and repeat
it. Prefer this structure:

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

Hermit source, public APIs, CLI behavior, Hermit tests, build configuration,
and product documentation belong in `hermit`. Follow the Hermit-local
`AGENTS.md` for architecture and validation. Do not copy Hermit code into a
parent script to avoid making a proper product change.

### Reverie Submodule

Reverie source, instrumentation APIs, Reverie tests, build configuration, and
product documentation belong in `reverie`. Follow its local guide. Reference
or exploratory use of Reverie does not justify modifying it; create a Reverie
feature branch only when the task owns a real Reverie change.

## Reverie API Policy

Additive Reverie extensions are allowed when existing consumers remain
compatible: narrowly scoped helpers, hooks, events, adapters, or optional
capabilities whose defaults preserve current behavior.

Discuss the design with the user before implementation when a proposal changes
a core Reverie abstraction or contract: the tool/event model or ordering,
public trait requirements, syscall interception/injection semantics, guest
register or memory contracts, lifecycle ownership, or container responsibilities.

Do not smuggle an abstraction change in as cleanup. Prefer an additive API or
compatibility layer when technically sound. When Hermit and Reverie change
together, use coordinated branches in the same slot, make the lower-level
Reverie commit available first when possible, validate Hermit against its exact
SHA, and report both SHAs and their dependency. Confirm the intended Reverie
PR destination before publishing; do not assume authorization to mutate
`facebookexperimental/reverie`.

### Cross-Repository Changes

Keep each repository's commit independently coherent. Document the dependency
between SHAs in both handoffs. Land the lower-level dependency first when
possible, then update and validate the consumer against that exact commit.
Only after the team branches are correct should the parent pin one or both new
SHAs.

## Commit Hygiene

Agents deliver reviewable commits, not anonymous working directories.

- Inspect `git status`, the complete diff, and staged diff before committing.
- Stage only task-owned paths in the repository that owns them.
- Keep formatting-only churn and unrelated cleanup out of focused changes.
- Prefer one logical commit per repository per task. Split only when each
  commit is independently coherent and useful.
- Use an imperative, descriptive subject that says what changed.
- Explain motivation, constraints, compatibility, and non-obvious validation
  in the body when needed.
- Never use placeholder subjects such as `wip`, `tmp`, `checkpoint`,
  `validate`, `fix stuff`, or `misc changes`.
- Never create empty bookkeeping commits to signal progress.
- Do not claim a test passed unless it ran against the handed-off SHA.
- Do not hide failures or skipped hardware-dependent validation in prose;
  report the exact limitation.
- Amend or rewrite only private task commits when authorized. Never rewrite
  `main`, a shared or published branch, or a commit another task depends on.
- Do not mix parent gitlink updates into a submodule source commit; they are
  commits in different repositories.

Before a commit, audit staged paths:

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
```

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
`branch = ...` field to `.gitmodules` and do not use `git submodule update
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

`git add hermit` records only Hermit's checked-out commit. It does not include
uncommitted Hermit files. Therefore, verify the submodule is clean and on the
intended SHA before staging the gitlink.

If only one pointer changed, stage only that path. If Hermit and Reverie must
move together for compatibility, validate the exact pair and update both
gitlinks in one parent commit. Record old and new SHAs plus compatibility
evidence in the commit or task note.

Parent pinning does not replace pushes. Before sharing a parent commit, confirm
the referenced submodule commits are available from their authorized remotes.

### Initialization And Updates

Normal initialization should reproduce the recorded commits:

```bash
git submodule update --init --checkout -- hermit
git -c submodule.reverie.update=checkout \
  submodule update --init --checkout -- reverie
```

Use the explicit Reverie override only when initialization is intended and
`.gitmodules` marks it `update = none`. Do not recursively initialize optional
or heavy nested submodules without a task that needs them. Worktree-specific
initialization must run inside the owning child checkout, never by repointing a
shared nested submodule worktree.

## Binary And Large-File Policy

Never commit binaries to the parent, Hermit, or Reverie repositories. This
includes compiled executables, object files, libraries, archives, database
dumps, core dumps, profiler captures, screenshots, generated media, cached
dependencies, and build trees. Git LFS is not a workaround unless the
repository owners establish an explicit policy for it.

Keep binary artifacts in ignored local directories or an approved external
artifact store. When evidence depends on an external artifact, commit a small
text manifest containing its location, checksum, producing command, tool
version, and source SHA.

Textual files larger than 2 MiB also require explicit coordinator approval
before staging. Prefer summarized CSV/JSON, compressed external artifacts, or
a reproducible generator over repository bulk. Compression does not make a
binary archive acceptable for Git.

Audit newly staged files before every commit:

```bash
git diff --cached --name-only --diff-filter=AM
git diff --cached --numstat
```

If a path looks generated or unexpectedly large, stop and inspect it with
`file`, `du`, and the repository ignore rules. Do not commit first and promise
to remove it later.

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

Hardware-dependent Hermit tests may be impossible on some hosts. Report that
fact and the observed failure; do not weaken, delete, or falsely bless a test
to make the local environment green.

The coordinator verifies both required CI jobs at the exact Hermit PR head and
the resulting target commit when landing is authorized. Local feature-branch
validation does not prove that hosted and self-hosted CI are green.


## Product Vision

`goal-hermit-v2` is the long-term end state: a robust deterministic execution
engine whose `run` and `record` modes support arbitrary real-world binaries,
whose chaos mode exposes concurrency races, whose schedule search localizes
races to events and stack traces, whose production backend avoids ptrace
overhead, and whose non-communicating processes can execute in parallel.

`goal-qemu-linux-under-hermit` is the QEMU milestone: run a complete Linux VM
as a userspace QEMU process under Hermit so deterministic execution,
record/replay, chaos scheduling, and schedule search can expose and localize
kernel races across the full kernel and userspace stack.

Prioritize correctness, faithful replay, race discovery/localization, lower
overhead, backend maturity, and QEMU/Linux viability. Do not close either
long-range goal without its required human verification.
## Communication Precision

This section governs coordinator headlines, cross-task aggregation, and
user-facing progress reports. Product guides govern the exact commands and
per-run evidence that implementation agents must supply. Coordinator reports
must be specific enough that another engineer can act without re-deriving the
scope; vague summaries are unacceptable.

- **Never headline a bare pass ratio.** `10/10 pass` is not a headline. Name
  the program category, the exact programs (or link an immediately adjacent
  table containing them), the Hermit mode and backend, and why that batch was
  selected. Example: `System utilities, ptrace L2: id, whoami, groups, uptime,
  free, df, ps, time, timeout, and nice pass 10/10; this batch probes process
  metadata after the envp fix.`
- **Separate new results from baseline.** Every rollup labels results as
  `New this run`, `Baseline reconfirmed`, `Regression`, or `Not rerun`. State
  the commit or PR that changed between the compared runs. Never present a
  repeated baseline result as newly achieved coverage.
- **Classify programs before totaling them.** Use explicit categories such as
  system utilities, text-processing utilities, interpreters/runtimes,
  compilers/build tools, databases, network programs, interactive
  applications, and virtualization/emulators. Mixed batches require category
  subtotals; one aggregate ratio may not hide which class improved or failed.
- **Name execution context.** Distinguish native baseline, ptrace, DBI, and KVM,
  and distinguish strict run, strict verify, record/replay, and relaxed modes.
  State why the chosen mode/backend answers the batch question.

- **Name the tool.** Never write "the Tool" or "a tool" when you mean a
  specific one. Say which: `StraceTool`, `Detcore`, `CounterTool`, etc.
- **Give the exact command and arguments.** Never say "the program passes."
  State the full command line, e.g.
  `hermit run --strict --verify -- bash -c 'echo hi | gzip | gunzip'`.
- **Say where.** Always specify the location of a claim: `main`, `PR #N`, or
  the exact feature branch / SHA. A result with no location is unverifiable.
- **Qualify the result.** Always report the determinism level (`L0` / `L1` /
  `L2`), the pass count (e.g. `18/20`, `5/5`), and the exact programs or
  test names the result covers. "It works" is not a result.
- **Bind evidence to commits, not branch names**, per the evidence block
  above. A mutable branch name is not a witness.

## Failure, Recovery, And Concurrent Work

Other agents may update the parent, primary checkouts, registries, or branches
while a task is running. Re-read state before every integration or pinning
step. Unexpected movement is a reason to reassess, not to restore an older
snapshot.

- Do not use `git reset --hard`, `git checkout -- <path>`, or destructive
  cleanup on changes you did not create.
- Do not move uncommitted work between slots without recording its owner and
  exact recovery procedure.
- Do not silently adopt another agent's branch or worktree.
- If a feature no longer fast-forwards, update the private feature branch and
  retest; never paper over divergence with a merge commit.
- If a primary is dirty, integration stops until the changes are attributed.
- If a submodule pointer conflicts, resolve the intended product history
  first, then choose the exact gitlink. Never resolve a gitlink conflict by
  selecting a side without inspecting the submodule commits.
- If a task is blocked, preserve clean committed work, post the exact blocker
  and SHAs, and keep the slot active until the coordinator decides to park it.

## Coordinator Checklist

Before dispatch:

1. Reconcile `ACTIVE.md`, both Git worktree lists, and physical slot children.
2. Check parent, primaries, and candidate slot for unexpected changes.
3. Confirm no more than twelve worktrees are active or fifteen agents assigned.
4. Confirm exclusive ownership or record every sharing agent and disjoint path.
5. Confirm the intended base SHA and publication target for each repository.
6. Register the slot before work begins.

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
