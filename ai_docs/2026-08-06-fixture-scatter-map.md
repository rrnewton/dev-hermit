# Fixture scatter map, 2026-08-06: where today's 14 backend-parity fixtures live

**Task:** `consolidate-fixtures-scattered-across-four-branches` · **Agent:** hermit-audit
(`[impl agent, opus-5]`) · **2026-08-06** · hermit base `4c70658e7` (= `origin/main` tip at
derivation time).

## Headline

**The count is right; the mechanism in the premise is wrong; nothing is lost.**

- **14** fixtures were added under `tests/backend-parity/fixtures/` by commits **authored**
  today — confirmed independently. **7 on main, 7 off.**
- The 7 off-main fixtures are **not** on the four named branches. They are on **seven
  distinct branches, one fixture each**, and **every one has its own PR**.
- **Zero** fixtures are merged-then-orphaned, and **zero** sit on a branch with no remote
  pointer. Every off-main fixture is reachable from a live remote ref.
- The real gap is elsewhere: **three PRs were closed citing successor TaskGraph tasks that
  did not exist.** That, not branch loss, is how this work would have been dropped.

## The map — today's 14

### On main (7)

`cwd_roundtrip.c` · `fcntl_owner.c` · `membarrier_query.c` · `o_tmpfile_anon.c` ·
`personality_domain.c` · `pipe_capacity.c` · `record_lock.c`

### Off main (7) — each on its own branch, each with a PR

| fixture | branch | PR | state |
| --- | --- | --- | --- |
| `pipe_multiwriter_ordering.c` | `fix/pipe-multiwriter-ordering-fixture` | #1691 | OPEN (draft) |
| `socket_epoll_ordering.c` | `fixture/socket-epoll-ordering` | #1701 | OPEN (draft) |
| `stat_metadata_identity.c` | `fixture/stat-metadata-identity` | #1707 | OPEN (draft) |
| `short_io_split_identity.c` | `wip/short-io-split-identity` | #1712 | OPEN (draft) |
| `timer_family_identity.c` | `fixture/timer-family-identity` | #1698 | **CLOSED, unmerged** |
| `readdir_order_identity.c` | `fix/readdir-order-identity-fixture` | #1700 | **CLOSED, unmerged** |
| `mmap_layout_pointer_order.c` | `fix/mmap-layout-pointer-order` | #1703 | **CLOSED, unmerged** |

All three CLOSED PRs have `mergedAt = null`, and each fixture's **blob hash appears nowhere
in `origin/main`'s fixture tree** — so they are genuinely unlanded, not merged-then-rewound.

## Why the premise said "four branches"

The premise came from `git log --since=2026-08-06 --diff-filter=A` run per branch. Two
artifacts inflate that:

1. **Descendant inheritance.** A branch based on main *contains main's commits*, so main's 7
   adds are counted again on every descendant. Three of the four named branches
   (`landing/overnight-detlog-validate-corpus-dev`, `coalesce/conflicting-onto-4c70658e`,
   `fix/determinize-filecontents-inode`) each report "7 fixtures added today" while carrying
   **zero** fixtures that are not already on main.
2. **`--since` filters on COMMITTER date, which rebase resets.** The same logical change
   exists as four commits with author date `2026-08-05` preserved and committer date rewritten
   to `2026-08-06`:

   | ref | commit | author | committer |
   | --- | --- | --- | --- |
   | `feat/parity-mutation-harness` | `e4ae0400a` | 08-05 | 08-05 |
   | `landing/coalesce-clean7-onto-4c70658e7` | `236f0762d` | 08-05 | **08-06** |
   | `landing/coalesced-ci-validate-tooling` | `7f69585b5` | 08-05 | **08-06** |
   | `stack/ci-validate-tooling` | `fc165a738` | 08-05 | **08-06** |

**Use author date and existence, not committer date**, for any "added today" question.
Existence-based derivation (`ls-tree` per ref, diffed against main) is rebase-proof; the
time-based one is not.

`stack/ci-validate-tooling` is the one named branch that does carry off-main fixtures —
`parity_probe.h`, `rlimit_identity.c`, `sched_getaffinity_identity.c` — but they were
**authored 2026-08-05**, not today, and belong to CLOSED PR #1672.

## Wider sweep (#213) — the task's scope undercounted

Across **all 2288 remote refs** (936 branches + 1352 `pr/*` heads):

- **24** fixtures exist off-main in total. 7 authored today (above), 3 authored 08-05, and
  **14 authored 07-31…08-03** on `codex/*` and `dbt/*` branches. Every one of those 14 has a
  CLOSED, unmerged PR (#1253, #1284, #1303, #1311, #1312, #1320, #1321, #1324, #1348, #1349,
  #1351, #1358, #1374). None is merged-then-orphaned.
- **10 further test files** were added today *outside* `tests/backend-parity/fixtures/` and are
  also off-main — the task's scope missed them. All are on OPEN PRs except the last two:

  | file | branch | PR |
  | --- | --- | --- |
  | `tests/c/errno_path_identity.c` | `fixture/errno-path-identity` | #1717 OPEN |
  | `tests/c/hardware_trap_identity.c` | `fix/fixture-hardware-trap-identity` | #1714 OPEN |
  | `tests/c/proc_sys_identity.c` | `fixture/proc-sys-read-identity` | #1699 OPEN |
  | `tests/c/signal_waitstatus_identity.c` | `fix/fixture-signal-waitstatus-identity` | #1706 OPEN |
  | `tests/c/startup_surface_identity.c` | `fixture/startup-surface-identity` | #1702 OPEN |
  | `tests/e2e/determinism-stress/pid_tid_identity.c` | `fixture/pid-tid-virtualization-identity` | #1711 OPEN |
  | `tests/e2e/data-handling/zstd-multithread.sh` | `landing/overnight-detlog-validate-corpus-dev` | #1679 OPEN |
  | `tests/e2e/language-runtimes/node-v8-jit.sh` | `landing/overnight-detlog-validate-corpus-dev` | #1679 OPEN |
  | `tests/e2e/manifests/backend-parity-c/getpriority-identity.toml` | `stack/ci-validate-tooling` +4 | #1672 CLOSED |
  | `tests/e2e/manifests/inventory/explicit-test-files.json` | `stack/ci-validate-tooling` +4 | #1672 CLOSED |

## The actual integrity gap: closure comments citing tasks that do not exist

Each of #1698 / #1700 / #1703 was closed **for cause** by the coordinator, and each closure
comment asserted *"TaskGraph `<slug>` preserves the goal."* **None of the three tasks existed**
— verified absent by `local_id` and by `title` across **4435** tasks in `~/.tg/hermit.db`.

This is a Proxy Binding failure: the closure comment is a *label* claiming preservation, with
nothing dereferencing it. A reader sees a named successor and assumes the goal survived.

| PR | closed because | successor (created 2026-08-06) |
| --- | --- | --- |
| #1698 | fixture **times out under the ptrace reference** while still claiming timer-family correctness; e9patch differs cold vs warm | `timerfd_determinism_fixture_rework` |
| #1700 | **raises the timeout to 1200s** with no completed verify run while the ordering gap persists — "a larger timeout cannot stand in for progress evidence" (#140) | `compat_timeout_policy_evidence` |
| #1703 | **vacuous fixture**: no discriminating positive control — native == ptrace, e9patch `candidate_sites=0`, DBI/KVM not run | `patch_site_inventory_positive` |

**A second, mechanical trap:** `tg` normalises hyphens to underscores **and truncates** —
`patch-site-inventory-positive-control` became `patch_site_inventory_positive`. So the
hyphenated slug in a closure comment can *never* resolve, even once the task exists. Cite the
underscore id. Each closed PR now carries a comment with the resolvable id and the recovery
pointers (branch, head SHA, path, blob).

## Why no fixture was consolidated

Consolidation was predicated on rescue, and **there is nothing to rescue** — every off-main
fixture is reachable from a live remote ref with an associated PR. Acting anyway would do harm:

- **The 4 open draft PRs** are tracked and in the serial landing queue. Collapsing them into
  one branch would discard their individual review state and PR history, and would contend
  with whichever agent owns each branch (Invariant 2).
- **The 3 closed PRs were rejected on the merits** — a vacuous fixture, a timeout-masking
  change, and a fixture that times out under the reference backend. Re-landing them on a
  "landable branch" would reintroduce exactly what the coordinator refused, including two
  textbook #140 violations.

The corrective action that *does* discharge the stated risk is making the preservation claims
real, which is what was done. If the owner still wants a physical consolidation for the
small-number-of-PRs goal, the defensible unit is the **10 open one-fixture PRs**, and that is a
coordinator call because it closes other agents' PRs.

## Method (reproduce)

```bash
# on-main adds today (author-dated)
git log --since=2026-08-06T00:00:00 --diff-filter=A --name-only --format="" origin/main

# existence-based off-main sweep, rebase-proof
git ls-tree -r --name-only origin/main -- tests/backend-parity/fixtures/ | sort > main.txt
for r in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin); do
  git ls-tree -r --name-only "$r" -- tests/backend-parity/fixtures/ | sort \
    | comm -13 main.txt - | sed "s|\$| $r|"
done

# orphan detection: merged PR whose content is absent from main
gh pr list --repo rrnewton/hermit --state all --limit 900 \
   --json number,state,headRefName,mergedAt
git ls-tree -r origin/main -- tests/backend-parity/fixtures/ | awk -v b="$BLOB" '$3==b'
```

**Caveat on `--limit`:** at `--limit 400` three branches appeared to have **no PR at all**;
at `--limit 900` all three resolved (#1253, #1284, #1303). A truncated PR window reads exactly
like orphaned work. Always widen the window before concluding a branch is untracked.

## Hazard encountered (disclosed)

`git fetch --prune origin` in this repo **deletes the entire `refs/remotes/origin/pr/*`
mirror** (2235 → 936 refs). Cause: `remote.origin.fetch` is
`+refs/heads/*:refs/remotes/origin/*`, so the `pr/*` mirror — populated separately from
`refs/pull/*/head` — falls inside the prune scope and matches no remote *branch*. This repo is
shared by the primary and every slot. It was recoverable only because GitHub retains
`refs/pull/*/head` permanently; **no reflog survives a pruned ref.** Restored with
`git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'` (1352 refs).

**Rule:** never run a bare `git fetch --prune` in the hermit repo; scope it
(`--prune origin +refs/heads/*:refs/remotes/origin/*`) or use plain `git fetch origin`.

## Not done

- Did not move, rebase, or consolidate any fixture branch (see rationale above).
- Did not assess whether each of the 24 off-main fixtures is individually *worth* landing —
  this map answers "where is it and is it reachable", not "is it good".
- The 14 older `codex/*` / `dbt/*` fixtures have CLOSED PRs but their closure comments were
  not audited for the same dangling-successor defect; that check is worth repeating there.
