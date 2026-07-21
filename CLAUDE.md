# dev-hermit Parent Workspace Guide

## Scope And Authority

`~/work/dev-hermit/` is the development harness around Hermit and Reverie. It
is not either product repository. This guide applies when Claude starts in the
parent directory or manages work across the two repositories.

Read `AGENTS.md` and `WORKTREES.md` before changing workspace state. Read the
more specific `AGENTS.md` in `hermit/` or `reverie/` before changing product
code. The stricter rule wins. The Hermit pull request workflow in this file is
current and supersedes older parent text that routes Hermit changes through a
local `devbig-lead` branch.

## Project Overview

Hermit provides reproducible x86_64 Linux execution. It runs a guest under the
Reverie instrumentation layer, intercepts syscalls and CPU events, and uses
Detcore to replace or sanitize nondeterministic time, randomness, scheduling,
metadata, and related host state. Deterministic preemption uses hardware PMU
retired-branch counters; CPUID behavior is also host-sensitive.

Reverie is the lower-level process instrumentation and container runtime. It
owns guest event delivery, syscall interception and injection, register and
memory access, process lifecycle handling, and namespace/container plumbing.
Hermit is a consumer of those APIs, not a place to duplicate Reverie internals.

The parent repository owns workspace policy, exact submodule pins, durable AI
research, reproducible experiments, and worktree coordination. Product source,
tests, build files, and product documentation belong in the owning submodule.

## Repository Layout

```text
~/work/dev-hermit/
|-- CLAUDE.md                 # this guide
|-- AGENTS.md                 # detailed parent policy
|-- WORKTREES.md              # nested worktree procedure
|-- slot-init.sh              # provisions a permanent slot
|-- hermit/                   # primary Hermit checkout; integration only
|-- reverie/                  # primary Reverie checkout; integration only
|-- ai_docs/                  # durable textual research and handoffs
|-- experiments/              # reproducible evidence with commands and SHAs
|-- scratch/                  # ignored transient output
`-- worktrees/
    |-- ACTIVE.md             # live ownership registry
    |-- ARCHIVED.md           # completed slot history, when present
    |-- slot01/
    |   |-- hermit/           # slot-private Hermit checkout
    |   `-- reverie/          # slot-private Reverie checkout
    `-- slotNN/
```

The primary checkouts are coordinator-owned integration and cache-donor
surfaces. Do not perform feature development in them. Parent-only policy work
is exceptional and is allowed only when the task explicitly names parent
files.

## Worktree Discipline

Every mutating task gets one exclusive opaque slot named `slotNN`. Never use a
branch or task name as a worktree directory. One owner controls the slot's
parent checkout and both nested submodules, even if only Hermit changes.

### Slot Pool

- Keep at most **five warm slots total**, counting active and parked slots.
- Prefer `slot01`, `slot02`, and so on. The number is an identity, not a task
  label.
- Reuse a clean parked slot before creating another one.
- `slot-init.sh` is the normal provisioning path for the permanent slots it
  supports. A fifth or temporary slot requires coordinator ownership and does
  not become an excuse to keep stale worktrees.
- If five warm slots already exist, reclaim or reuse one before provisioning
  another. Do not quietly raise the ceiling.

A slot has exactly one of these states:

- **Active**: assigned to a live task and recorded in `worktrees/ACTIVE.md`.
- **Parked**: clean, detached, kept in place for cache reuse, and absent from
  `ACTIVE.md`.
- **Disposable**: explicitly temporary. Remove it after its committed work is
  pushed, merged or archived, and proven recoverable.

Do not leave completed work in an active slot. Park idle permanent slots and
reclaim disposable or duplicate worktrees aggressively. Never reclaim a slot
merely because its owner is not currently running: first prove that it is
clean and that all commits are reachable from a pushed branch or merged ref.

### ACTIVE.md Is Mandatory

`worktrees/ACTIVE.md` is the source of truth for live ownership, even on a
machine where physical worktrees and the registry are ignored by Git. Create
it if absent. Add the row before the first edit and update the existing row as
the task changes phase.

Use one row per slot with at least:

```text
slot | owner/task | parent branch | Hermit branch | Reverie branch | started | purpose
```

Use `detached:<short-sha>` for an unchanged repository. Completed, held, or
abandoned rows do not remain in `ACTIVE.md`; record their final branches, exact
SHAs, validation, and disposition in `ARCHIVED.md` when that registry exists.

Before assigning or reclaiming a slot, reconcile all three views:

```bash
git -C ~/work/dev-hermit worktree list --porcelain
git -C ~/work/dev-hermit/hermit worktree list --porcelain
git -C ~/work/dev-hermit/reverie worktree list --porcelain
find ~/work/dev-hermit/worktrees -maxdepth 2 -type d -print | sort
```

Unexpected directories, branches, or modifications belong to somebody else
until proven otherwise. Do not reset, clean, stash, overwrite, absorb, or
delete them. Never run `git clean` in the parent, a product checkout, or a
slot.

### Starting And Parking

Before editing in a slot:

1. Confirm the parent slot and both nested submodules are registered and
   clean.
2. Fetch the intended remote refs without altering checked-out files.
3. Create a descriptive task branch in every repository that will change.
4. Leave an unchanged submodule detached at a recorded SHA.
5. Register the slot in `ACTIVE.md` and in the task notes.
6. Run every edit, build, format, test, and commit from the assigned slot.

Do not share writable `target/` directories. A reflink or ordinary copy from a
clean donor cache is acceptable; a symlink or shared target directory is not.

Park a permanent slot only after the intended work is committed and handed
off. Confirm all three statuses are clean, record exact SHAs and test evidence,
detach the nested repositories and parent worktree at recoverable commits, and
remove the `ACTIVE.md` row. Keep the slot directory in place so its build cache
stays warm. If nested submodule HEADs no longer match the parent gitlinks,
reconcile them only after proving their commits are pushed; never discard
uncommitted product work to make the parent look clean.

Removal is coordinator-only. Before removing a disposable slot, verify clean
state, preserve every needed branch and SHA, remove it through the Git
repository that registered it, and then verify the worktree registries again.

## Hermit Git And Pull Request Workflow

The primary Hermit repository is `rrnewton/hermit`. The public source project
`facebookexperimental/hermit` is the upstream reference, not the default
landing target for this workspace.

For Hermit product changes:

1. Fetch `rrnewton/hermit` and branch from the current `origin/main` unless the
   task explicitly specifies another base.
2. Keep one coherent task on one feature branch in an assigned slot.
3. Run focused tests while iterating, then the validation required by the
   Hermit-local guide. Use `./validate.sh` for a full pre-merge run when the
   task calls for it.
4. Commit reviewable changes, push the feature branch, and open a pull request
   against `rrnewton/hermit:main`.
5. Keep the PR current and review the complete diff. Do not merge with
   unresolved review findings or a stale head SHA.
6. Require both GitHub Actions jobs to be green at the PR head:
   **Regular tests (GitHub-hosted)** and
   **Host-dependent tests (self-hosted)**.
7. Squash-merge only when the task authorizes landing. Verify the resulting
   `main` CI run is green.

Do not push directly to Hermit `main`, force-push shared branches, or merge a
PR merely because local tests pass. A skipped, missing, queued, or cancelled CI
job is not green. Report infrastructure limitations explicitly; do not weaken
hardware-sensitive assertions to make a restricted host pass.

Use the required proxy for every networked Git or GitHub CLI operation:

```bash
HTTPS_PROXY=http://fwdproxy:8080 git fetch origin
HTTPS_PROXY=http://fwdproxy:8080 git push origin <feature-branch>
HTTPS_PROXY=http://fwdproxy:8080 gh pr view <number> -R rrnewton/hermit
```

Do not use `gh auth switch`; authentication is shared machine state. Do not
create, edit, close, or merge GitHub objects unless the task explicitly calls
for that repository-side action.

Parent policy changes are committed in the parent repository when explicitly
requested. A Hermit or Reverie product commit does not automatically justify a
parent submodule-pin update; pin only reviewed commits that the task intends to
make reproducible.

## Reverie API Policy

Additive Reverie extensions are allowed when a task needs them and existing
consumers remain compatible. Examples include a new narrowly scoped helper,
hook, event, adapter, or optional capability whose default preserves current
behavior. Put the implementation and tests in Reverie, then validate Hermit
against the exact Reverie commit.

Discuss the design with the user **before implementation** when a proposal
changes a core Reverie abstraction or contract. This includes:

- changing the tool/event model or event ordering,
- changing public trait method signatures or mandatory implementations,
- redefining syscall interception or injection semantics,
- changing guest register/memory access contracts,
- changing process, task, signal, or lifecycle ownership,
- changing namespace/container responsibilities,
- removing, renaming, or repurposing APIs used by existing tools.

Do not smuggle an abstraction change in as a cleanup. Prefer a compatibility
layer or an additive API when it is technically sound. If Hermit and Reverie
must change together, use coordinated branches in the same slot, land or make
available the lower-level Reverie commit first, and report both exact SHAs and
their dependency. Confirm the intended Reverie PR destination before
publishing; do not assume permission to mutate `facebookexperimental/reverie`.

## Engineering And Validation

- Follow the repository-local build, test, formatting, and lint commands.
- Hermit supports x86_64 Linux and uses the pinned nightly Rust toolchain.
- PMU, CPUID interception, namespaces, and CPU features are real environment
  dependencies. Include host limitations in failure reports.
- Hermit does not make a changing filesystem or external network
  deterministic. Tests must provide stable inputs and avoid external network
  dependencies.
- Preserve determinism: avoid guest-visible wall time, uncontrolled random
  state, and iteration-order or host-state dependencies.
- Start with the narrowest reproduction and regression test, then broaden
  validation according to blast radius.
- Silent skipping is a defect. If a test, CI job, artifact, push, issue update,
  or expected side effect cannot happen, say so with enough detail to debug it.

## Commit And Handoff Hygiene

- Inspect status, the full diff, and the staged diff before committing.
- Stage only task-owned paths. Do not include generated `Cargo.lock`, build
  output, binaries, logs, credentials, or unrelated concurrent changes.
- Use a clear commit subject that states what changed; do not leave `wip`,
  `tmp`, or validation-only placeholder commits on a published branch.
- Do not rebase, amend, reset, revert, or force-push unless the task explicitly
  authorizes it.
- A handoff includes the exact commit SHA, branch and PR, focused and broad test
  results, CI status, hardware/environment caveats, and any coordinated
  Reverie SHA.
- Keep task notes current with slot assignment, findings, decisions, progress,
  blockers, and the final disposition.
