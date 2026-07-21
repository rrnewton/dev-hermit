# dev-hermit Parent Workspace Guide

This file governs the `dev-hermit` parent repository and every agent launched
from it. It defines workspace ownership, branch flow, submodule coordination,
and evidence requirements. The more specific `AGENTS.md` files inside
`hermit/` and `reverie/` also apply when working in those repositories; use
them for product architecture, build, test, and style rules.

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

The shared development branch is `devbig-lead`. The stable branch is `main`.
For work dispatched from this parent, the branch flow is:

```text
feature branch -> devbig-lead -> main
```

All landings and promotions are fast-forward only. If a submodule-local guide
still names `integration` as the coordinator branch, that is legacy guidance
for this harness: use `devbig-lead` unless the task explicitly says otherwise.

## Vocabulary

- **Parent**: `~/work/dev-hermit/`, the harness repository containing the
  submodule gitlinks and workspace state.
- **Primary checkout**: `~/work/dev-hermit/hermit/` or
  `~/work/dev-hermit/reverie/`. A primary checkout is coordinator-owned, stays
  on `devbig-lead`, and is used for integration, pinning, and cache donation.
- **Submodule**: a repository recorded by the parent as an exact gitlink SHA.
  The parent records a commit, not a branch and not uncommitted contents.
- **Slot**: one permanent paired workspace at
  `~/work/dev-hermit/worktrees/slot01/` through `slot04/`.
- **Product worktree**: one child checkout inside a slot, registered with the
  corresponding primary repository, for example `slot02/hermit`.
- **Feature branch**: a task-specific branch checked out in one product
  worktree. Slot names are deliberately unrelated to branch names.
- **Team branch**: `devbig-lead`, the continuously integrated branch in the
  parent and each actively developed submodule.
- **Stable branch**: `main`, advanced only to a reviewed, validated
  `devbig-lead` commit by fast-forward.
- **Active slot**: a slot assigned to exactly one task/owner and recorded in
  `worktrees/ACTIVE.md` before the first edit.
- **Parked slot**: a clean, detached slot pair retained in place for reuse and
  omitted from `ACTIVE.md`.
- **Handoff SHA**: the exact commit tested and offered for integration. Branch
  names alone are not sufficient evidence.

## Canonical Layout

The intended parent layout is:

```text
~/work/dev-hermit/
|-- AGENTS.md
|-- .gitmodules
|-- hermit/                         # primary; devbig-lead; coordinator only
|-- reverie/                        # primary; devbig-lead; coordinator only
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
|   `-- slot04/
|       |-- hermit/
|       `-- reverie/
|-- ai_docs/                        # durable textual research and handoffs
|-- experiments/                    # durable reproducible evidence
`-- scratch/                        # ignored transient material
```

Each slot is one ownership unit even when a task changes only one product. A
Hermit-only task leaves the slot's Reverie worktree clean and detached, but
that worktree remains reserved for the same owner until the slot is parked.
Never assign the two children of one slot to different agents.

Physical worktrees and their build output are machine-local and must be
ignored by the parent repository. `ACTIVE.md` and `ARCHIVED.md` are durable
coordination records; the checkout directories are not.

## Hard Invariants

1. Never do feature development in a primary checkout.
2. Never let two agents mutate the same slot, checkout, or branch.
3. Register a slot in `worktrees/ACTIVE.md` before its first edit or commit.
4. Require clean state before assignment, integration, parking, or pinning.
5. Treat unexpected changes as owned by somebody else. Do not reset, clean,
   overwrite, stash, or absorb them.
6. Do not run `git clean` anywhere in the parent, submodules, or slots.
7. Do not use a branch name as a worktree directory name.
8. Do not share writable build directories between worktrees.
9. Integrate and promote with `git merge --ff-only`; do not create
   convenience merge commits.
10. Never force-push `devbig-lead` or `main`.
11. Never commit binaries or generated build artifacts to any repository.
12. A handoff is incomplete without exact SHAs and validation results.

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
owns the parent and relevant primary locks. Missing lock tooling does not
relax the one-owner rule; record ownership in `ACTIVE.md` and task notes.

Parent-only policy or harness work is exceptional because product slots do not
isolate the parent repository. Modify the parent root only when the task names
parent files and ownership is explicit. Do not mix a parent edit into an
unrelated product task.

## Worktree Registry

`worktrees/ACTIVE.md` is the source of truth for current slot ownership. Keep
exactly one live row per active slot pair, with at least:

```text
slot | owner/task | Hermit branch | Reverie branch | started | purpose
```

Use `-` or `detached:<short-sha>` for an unchanged child; do not create
duplicate rows as a task changes phase. Update the existing row. A row marked
DONE, HELD, or ABANDONED does not belong in `ACTIVE.md`: either keep it active
with an accurate current purpose or park it and append the final state to
`ARCHIVED.md`.

Before dispatch, compare the registry with both Git worktree registries and
the filesystem:

```bash
git -C hermit worktree list --porcelain
git -C reverie worktree list --porcelain
find worktrees -mindepth 2 -maxdepth 2 -type d \
  \( -name hermit -o -name reverie \) -print | sort
```

Resolve all of these before assigning a slot:

- a physical child checkout not registered by its primary repository,
- a registered worktree whose directory is missing,
- a live slot absent from `ACTIVE.md`,
- an `ACTIVE.md` row for a parked or missing slot,
- duplicate rows for one slot,
- a branch checked out by more than one task.

Never silently delete a stale path. Record what owns it and preserve any
uncommitted work before the coordinator decides its disposition.

## Permanent Slot Pool

There are exactly four permanent paired slots: `slot01`, `slot02`, `slot03`,
and `slot04`. Reuse them. Do not turn `slot05` or later into another permanent
slot.

A permanent slot is either:

- **Active**: both child worktrees are reserved to one task; at least one may
  be on a feature branch.
- **Parked**: both children are clean and detached in place; their caches and
  Git registrations remain available for the next task.

Do not move a permanent slot directory. Each child `.git` file points into a
different submodule's common Git directory, and moving the outer slot can
invalidate both registrations. Do not use `git worktree move` on the slot
container. Do not remove permanent worktrees merely to make the registry look
tidy.

Temporary over-provisioning requires explicit coordinator approval. A
temporary `slot05+` pair must be registered while active and removed after its
committed work is integrated or archived; it never joins the cached pool.

### Provisioning A Missing Slot

Provisioning is a coordinator operation. Initialize both primary submodules
first. If `reverie` is configured with `update = none`, override that setting
only for the explicit initialization:

```bash
cd ~/work/dev-hermit
git submodule update --init --checkout -- hermit
git -c submodule.reverie.update=checkout \
  submodule update --init --checkout -- reverie
```

Create the paired worktrees with absolute paths or paths relative to each
primary. From the parent root:

```bash
slot=slot01
mkdir -p "worktrees/$slot"
git -C hermit fetch origin
git -C reverie fetch origin
git -C hermit worktree add --detach \
  "../worktrees/$slot/hermit" devbig-lead
git -C reverie worktree add --detach \
  "../worktrees/$slot/reverie" devbig-lead
```

Do not run `git worktree add` from the parent repository for these children.
The Hermit child must be registered by `hermit`; the Reverie child must be
registered by `reverie`.

Build caches may be seeded with copy-on-write copies when useful:

```bash
cp -a --reflink=auto hermit/target/ "worktrees/$slot/hermit/target/"
cp -a --reflink=auto reverie/target/ "worktrees/$slot/reverie/target/"
```

Skip a missing or stale donor cache. Never symlink `target/` or another
writable cache between checkouts. Correctness must not depend on cached output.

### Starting Work In A Slot

The coordinator assigns one parked slot to one task. Before editing:

1. Confirm both child worktrees are registered and clean.
2. Fetch the relevant remotes without changing checked-out files.
3. Confirm local `devbig-lead` in each changed repository matches the intended
   integration base.
4. Create a descriptive feature branch in each repository that will change.
5. Leave an unchanged child detached at a recorded base SHA.
6. Add one `ACTIVE.md` row and post the slot/branch assignment to the task.

Example Hermit-only assignment:

```bash
slot=slot01
git -C "worktrees/$slot/hermit" fetch origin
git -C "worktrees/$slot/hermit" switch \
  -c codex/<task-name> devbig-lead
git -C "worktrees/$slot/reverie" switch --detach devbig-lead
```

For a coordinated change, create task branches in both children. They may use
the same descriptive branch name because they live in separate repositories.
Record both names and both base SHAs.

Run all edits, formatting, builds, tests, and commits from the assigned child
worktrees. Always set the command working directory explicitly; similar paths
under the primary and slots make accidental edits easy.

### Parking And Reusing A Slot

Park only after intended work is committed and handed off. For each child:

```bash
git -C worktrees/slot0X/hermit status --short
git -C worktrees/slot0X/reverie status --short
git -C worktrees/slot0X/hermit rev-parse HEAD
git -C worktrees/slot0X/reverie rev-parse HEAD
```

Both status commands must produce no output. Record feature branches, exact
SHAs, validation, and integration disposition in `ARCHIVED.md`, then detach:

```bash
git -C worktrees/slot0X/hermit switch --detach HEAD
git -C worktrees/slot0X/reverie switch --detach HEAD
```

Remove the slot's single row from `ACTIVE.md`. Keep the feature branches until
their commits are reachable from `devbig-lead` or the coordinator explicitly
archives them. A non-clean slot remains active even if its agent is idle.

To reuse a parked slot, repeat the clean-start audit and create new branches
from the current `devbig-lead`. Do not reset a parked child to make it current;
switch or create the new branch explicitly so its previous SHA remains
auditable.

For an approved temporary slot only, remove each child through the repository
that owns it, then remove the empty container:

```bash
git -C hermit worktree remove --force ../worktrees/slot0X/hermit
git -C reverie worktree remove --force ../worktrees/slot0X/reverie
rmdir worktrees/slot0X
```

Move its registry entry to `ARCHIVED.md` before removal. `--force` does not
authorize discarding changes; the preceding clean-state check is mandatory.

## Branch And Merge Strategy

The same linear flow applies independently to Hermit and Reverie:

```text
task feature -> devbig-lead -> main
```

### Feature Branch Rules

- Branch from the current intended `devbig-lead`, not from `main`, an old slot
  HEAD, or the parent gitlink by accident.
- Keep one coherent task on one branch. Coordinated Hermit/Reverie branches
  together form one logical change but remain separate Git histories.
- Commit all intended changes before handoff. The coordinator does not
  integrate uncommitted slot state.
- Push only when the task or coordinator requests it. Never force-push a
  shared branch.
- Rebase only a private feature branch and only when integration requests it.
  After rebasing, rerun affected validation and provide the new SHA.

### Landing On devbig-lead

Only the integration coordinator mutates primary checkouts. Before landing:

1. Acquire the relevant primary and parent locks.
2. Confirm the parent and relevant primary are clean apart from explicitly
   expected gitlink movement.
3. Review the complete feature diff and validation evidence.
4. Confirm the handed-off SHA is the feature branch tip.
5. Fetch refs without changing the worktree.
6. Fast-forward only.

```bash
git -C hermit status --short --branch
git -C hermit switch devbig-lead
git -C hermit merge --ff-only <hermit-feature-branch>
```

Use the equivalent commands for Reverie. If `--ff-only` fails, stop. Do not
make a merge commit. Return the feature branch to its owner to update against
the new `devbig-lead`, resolve conflicts with task context, rerun validation,
and hand off a new exact SHA.

Keep `devbig-lead` green. A combined regression is priority work; do not stack
more unrelated landings on a known-red team branch.

### Promoting To main

Promote only a reviewed, green `devbig-lead` commit. Promotion is a stable
pointer movement, not a place for new edits:

```bash
git -C hermit switch main
git -C hermit merge --ff-only devbig-lead
```

Run the repository's promotion-level validation at the exact promoted SHA and
push only as authorized. Then return the primary to `devbig-lead` for normal
integration work. Never land a feature branch directly on `main`, and never
force `main` to match a divergent history.

The parent repository also keeps `main` stable and uses `devbig-lead` for the
team's current harness and gitlink state. Durable parent changes and validated
pin advances flow to `main`; live slot state stays on `devbig-lead` and must
not replace another team's `ACTIVE.md`. If `.gitattributes` assigns
`worktrees/ACTIVE.md merge=ours`, every clone must also configure:

```bash
git config merge.ours.driver true
```

The attribute alone is not enough.

## What Goes Where

Use ownership boundaries, not convenience, to choose a repository.

### Parent Repository

Track in the parent:

- workspace policy such as this guide,
- `.gitmodules`, exact submodule gitlinks, and parent ignore rules,
- `worktrees/ACTIVE.md` and `worktrees/ARCHIVED.md`,
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
  `devbig-lead`, `main`, or a commit another task depends on.
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
- base SHA or current `devbig-lead` relationship,
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
- the submodule commit is reachable from the appropriate `devbig-lead` or
  promoted `main` history,
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

The coordinator validates the combined `devbig-lead` state after landing.
Worker validation of isolated feature branches is necessary but does not prove
that the combined team branch is green.

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
3. Confirm one owner and one task for the entire slot pair.
4. Confirm the intended `devbig-lead` base SHA in each relevant repository.
5. Register the slot before work begins.

Before integration:

1. Acquire parent/primary ownership and re-read concurrent state.
2. Verify the handoff SHA, diff, test evidence, and repository cleanliness.
3. Land with `--ff-only` or return the branch for update.
4. Validate the combined team branch at its new exact SHA.
5. Push only as authorized and record the landed SHA.

Before parent pinning or promotion:

1. Confirm submodule commits are clean, reviewed, tested, and fetchable.
2. Inspect `git diff --submodule=log` before staging.
3. Stage only intended gitlinks and parent-owned files.
4. Validate a coordinated Hermit/Reverie pair when both pointers move.
5. Promote `devbig-lead` to `main` by fast-forward only.

Before closeout:

1. Ensure each changed repository has a clean committed feature branch.
2. Record exact SHAs and validation in the task and `ARCHIVED.md`.
3. Detach both permanent slot children in place.
4. Remove the slot's single `ACTIVE.md` row.
5. Leave unrelated concurrent work exactly as found.
