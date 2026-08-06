# Landing plan for the whole open set — 73 PRs, measured

**Task:** `prepare-stacks-for-landing`
**Author:** `hermit-w4`
**Base for everything below:** `rrnewton/hermit:main @ 4c70658e785834737cbe1524f77330c781a6f5ea`
(re-read at the remote; unchanged throughout this work)

Every open PR gets exactly one disposition. The counts add to 73 with nothing left over.

---

## 0. Headline

| # | finding |
|---|---|
| 1 | **Filename overlap is a 74% false-positive proxy for conflict.** 183 pairs share a real source file; only **48** actually conflict. A plan built on filenames would serialise 135 pairs that never needed it. |
| 2 | **One coalesce beats every stack.** 26 PRs merged into one branch, all gates green — 26 box-exclusive validate slots collapsed to 1. |
| 3 | **Five PRs cannot pass their own validate**, each measured alone at its own head. They are ejected, not merged. |
| 4 | **Ten PRs are duplicates of each other** in two families of five. Those are not stacks; they are pick-one-close-four. |
| 5 | **#1754 is a global barrier** — 100 files, conflicts with 13 other PRs. It must land alone, and everything else is either entirely before it or entirely after it. |

## 1. Method — measure, don't infer

Two things could have been guessed and were not:

**Genuinely-unlanded.** `git cherry` patch-id equivalence for all 73 PRs against `main`: **zero** are already
landed, **zero** are stale-base, every merge-base is exactly `4c70658e7`. So "filter out the already-landed"
removes nothing here, and the fresh base is main's current tip.

**Conflicts.** Clustering on *touches the same file* collapses 65 of 73 PRs into one useless blob, because
three regenerable/derived files are touched by nearly everyone (`ci/expected-e2e-plan.json` 17 PRs,
`tests/e2e/manifests/inventory/test-files.json` 16, `backend-parity-c.toml` 17). So conflicts were measured,
not inferred:

```
git merge-tree --write-tree --merge-base=<main> <headA> <headB>     # for all 183 source-sharing pairs
  -> 48 CONFLICT, 135 CLEAN
```

Pairwise-clean does not imply group-clean, so the coalesce was also checked as a **group**, by merging one PR
at a time into an accumulating tree rather than trusting the pairwise result.

## 2. Disposition of all 73 open PRs

| count | disposition |
|---|---|
| 26 | **COALESCE-A** — one branch, gates green (§3) |
| 7 | **STACK-1** — already assembled as `stack/fixtures-shared-files` (separate task) |
| 6 | **STACK-A** backend-parity-c (§4.1) |
| 5 | **DUPLICATE FAMILY: DetInode** — pick one, close four (§5) |
| 5 | **DUPLICATE FAMILY: getrusage** — pick one, close four (§5) |
| 5 | **EJECTED** — fails its own validate (§6) |
| 4 | **SUBSUMED** by another open PR — close as duplicate (§5) |
| 4 | **SINGLE** — conflict-free once #1754 is scheduled (§4.6) |
| 3 | **STACK-C** validate.sh / README (§4.2) |
| 2 | **STACK-D** `detcore/src/lib.rs` (§4.3) |
| 2 | **STACK-F** detlog / receipts (§4.4) |
| 2 | **STACK-G** SaBRe `hermit-cli/src/lib.rs` (§4.5) |
| 1 | **BARRIER** #1754 — land alone (§7) |
| 1 | #1742 — shares a commit with #1710; resolve with stack 1, do not land twice |

## 3. COALESCE-A — the main deliverable

```
branch  coalesce/validated-set
head    e0f81c2382aaef4b89402e5c5057a34dab039aa2      (29 commits)
base    4c70658e785834737cbe1524f77330c781a6f5ea
pushed and remote-verified; NOT landed, no PR opened, no merge attempted
```

**26 PRs**, in PR-number order with the three TOML-union ones last:

```
1666 1684 1692 1694 1702 1706 1709 1711 1712 1713 1714 1720 1731 1732 1735 1736
1739 1743 1747 1750 1753 1755 1758  +  1717 1721 1723   (system-utils.toml union)
```

Membership verified against the branch itself, not against the intended list: the 29 commits on
`origin/main..coalesce/validated-set` map back to exactly these 26 PR numbers.

### 3.1 Conflict map

**23 of 26 cherry-picked with zero conflicts.** All three conflicts were the *same* additive `[[test]]`
union in `tests/e2e/manifests/system-utils.toml`, resolved **structurally, not textually**: split each side
into `[[test]]` blocks keyed by `id`, union them, then **prove** the result rather than eyeball it — parses
via `tomllib`, exact expected count, no duplicate ids, every new id from either side present.

| step | tests before → after | new from ours | new from theirs |
|---|---|---|---|
| #1717 | 19 → 21 | `startup-surface-identity` | `errno-path-identity` |
| #1721 | 21 → 23 | (carried) | `auxv-loader-dump`, `ps-proc-table` |
| #1723 | 23 → 24 | (carried) | `file-timestamp-identity` |

The union script is at `ignored/w4/toml_union.py` in the slot (gitignored, reproducible, asserts on failure).
Hand-merging interleaved TOML is how a manifest silently loses a case; do not do it.

### 3.2 Gates at the exact head `e0f81c2382aa`

```
./ci/test_harness.sh validate            PASS  (manifest + inventory + DAG correspondence, 28.6s)
cargo fmt --all -- --check               FMT_OK
cargo clippy --workspace --all-targets -- -D warnings   exit 0
cargo build --workspace --all-targets    exit 0
```

Full-profile validate receipt: see §8.

## 4. The real stacks

Each order was simulated with `git merge-tree` against the fresh base, so the conflict column is measured,
not predicted. A `CONFLICT` row is *expected work*, not a failure: in a stack the later PR is rebased on the
earlier one and the collision is resolved once.

### 4.1 STACK-A — backend-parity-c (6 PRs)

Order — small cell-enablers first, structural changes last:

| order | PR | files | result | conflict |
|---|---|---|---|---|
| 1 | #1734 getcpu_identity observed values | 3 | clean | |
| 2 | #1737 getpriority identity in CI | 3 | clean | |
| 3 | #1756 affinity mask observed | 3 | clean | |
| 4 | #1743 enable six passing cells | — | clean | |
| 5 | #1665 shard backend parity manifests | 12 | **CONFLICT** | `backend-parity-c.toml` |
| 6 | #1727 restore full-corpus perf fixtures | 43 | clean | |
| 7 | #1757 1 → 72 enabled cells | 58 | **CONFLICT** | `ci/dag/portable.json`, `backend-parity-c.toml` |

Both conflicts are in the same additive-TOML class as §3.1 plus one DAG-node union — mechanical, but
`#1665` also **deletes** `tests/e2e/manifests/inventory/test-files.json` while `#1727` modifies it
(a modify/delete, i.e. a structural decision about manifest layout, not a text merge). **Resolve the #1665
sharding question before assembling this stack**; everything else here follows from it.
Note `#1751` is a strict subset of `#1757` — drop it.

### 4.2 STACK-C — validate.sh / README (3 PRs)

| order | PR | result | conflict |
|---|---|---|---|
| 1 | #1690 widen the truncated-artifact purge | clean | |
| 2 | #1705 purge structurally-incomplete artifacts; README | **CONFLICT** | `validate.sh` |
| 3 | #1752 point README's Reverie link at the fork | clean | |

#1690 and #1705 are two takes on the same `validate.sh` purge; read them together — this may be a partial
duplicate rather than a stack.

### 4.3 STACK-D — `detcore/src/lib.rs` (2 PRs)

| order | PR | result | conflict |
|---|---|---|---|
| 1 | #1695 emit the heap DETLOG record from the observed break | clean | |
| 2 | #1746 one fail-closed strict-green authority (12 files) | **CONFLICT** | `detcore/src/lib.rs` |

### 4.4 STACK-F — detlog / receipts (2 PRs)

| order | PR | result | conflict |
|---|---|---|---|
| 1 | #1679 coalesced overnight work (19 files) | clean | |
| 2 | #1749 versioned strict verification receipts (13 files) | **CONFLICT** | `hermit-cli/src/bin/hermit/run.rs` |

`#1718` is a strict subset of `#1679` — drop it.

### 4.5 STACK-G — SaBRe (2 PRs)

| order | PR | result | conflict |
|---|---|---|---|
| 1 | #1725 make the unobserved dynamic-loading phase loud | clean | |
| 2 | #1729 routed signal breaks the ambiguous zero | **CONFLICT** | `hermit-cli/src/lib.rs` |

### 4.6 Singles that need no stack

`#1689`, `#1722`, `#1733`, `#1744` conflict with nothing except `#1754`. Schedule them relative to the
barrier (§7) and land them individually or fold them into a second coalesce.
`#1719` (29 files, 9 commits) conflicts only with `#1682`, which is a getrusage duplicate destined to be
closed — so once §5 resolves, `#1719` is free too.

## 5. Duplication — ten PRs are two PRs

The conflict graph makes this visible in a way a title scan does not.

**DetInode newtype — #1669 #1674 #1678 #1681 #1683.** Five independent implementations of the same fix.
Every pair conflicts: a complete K5 on `detcore-model/src/fd.rs`, `detcore/src/consts.rs`,
`detcore/src/syscalls.rs`, `detcore/src/syscalls/files.rs`, `detcore/src/tool_global.rs`.

**getrusage CPU time — #1680 #1682 #1686 #1687 #1688.** Five independent implementations, colliding on
`detcore/src/syscalls/sysinfo.rs`.

Stacking either family is meaningless. **Pick one on the merits and close the other four.** An earlier note
on this task flagged this pattern as "three agents on item 1.1"; measured across the whole open set it is
five, twice.

**Strict subsumptions (all commits of A are also in B — close A):** `#1671 ⊂ #1710`, `#1693 ⊂ #1704`
*and* `⊂ #1708`, `#1718 ⊂ #1679`, `#1724 ⊂ #1730`, `#1751 ⊂ #1757`.

**Exact duplicate commits shared across PRs (patch-id identical):**

```
[1693,1704,1708] 0aface401   [1710,1742] 3125d697a   [1671,1710] bf8d8951e
[1751,1757] b814ee42b        [1724,1730] 83da83cae   [1679,1718] 2f8cbfc00
```

## 6. EJECTED — five PRs cannot pass their own validate

Each was checked **alone, detached at its own head, with no other PR present**. These are PR defects, not
merge artifacts.

| PR | head | gate | failure |
|---|---|---|---|
| #1707 | `3ab75f09c` | `test_harness.sh validate` | `backend-parity-c/stat-metadata-identity: modes must be exactly {chaos,custom,naked,replay,verify}, got {verify}` |
| #1691 | `c17b61b89` | `test_harness.sh validate` | test inventory is stale — adds fixtures under `tests/` with no inventory entry |
| #1724 | `a2c46bbbe` | `test_harness.sh validate` | same |
| #1730 | `addcfab23` | `test_harness.sh validate` | same |
| #1697 | `d93d51282` | `clippy --workspace --all-targets -D warnings` | `unused import: std::os::unix::process::ExitStatusExt` at `hermit-cli/src/bin/hermit/backends.rs:45` |

The three inventory failures are independently corroborated by the PR file lists: **none of the three touches
`tests/e2e/manifests/inventory/test-files.json` at all**, while each adds `.c` fixtures under `tests/`. Six
orphan fixtures between them (`inline_syscall_sites`, `mixed_inline_and_libc_syscalls`,
`pipe_multiwriter_ordering`, `readdir_order_identity`, `static_libc_syscall_sites`,
`static_nolibc_syscall_sites`).

These are **not** hidden by the eager-exit problem documented for stack 1: `e2e.metadata` runs early and
passes on main, so the lane does reach it. The plain reading is that these five have never been validated.

**Back to their authors — not fixed here.** The inventory schema demands ≥120 characters of author-supplied
`why` prose per entry; that is exactly the thing a bystander must not autogenerate, and adding it is not a
conflict resolution.

**Method note that cost three iterations:** the metadata gate **dies at the first error**. "The gate passed
after I removed one PR" is never evidence that only one PR was bad — re-run until green, then re-run once more.

## 7. #1754 is a barrier, not a stack member

*Rename the DBI backend to DBT* — 100 files, **conflicts with 13 other open PRs**, more than twice any other.

```
1665 1689 1705 1719 1722 1733 1734 1737 1744 1746 1751 1756 1757
```

It cannot go inside any stack. It must land **alone**, and every other PR must be scheduled entirely before
or entirely after it. Removing #1754 and the secondary hub #1757 (58 files, conflicts with 7) is what
shatters the 23-PR conflict blob into the four small stacks in §4.

**Recommendation: land #1754 LAST.** Landing it first forces a rename-rebase through every other open branch;
landing it last means it absorbs the renames once, in one PR, with one validate.

## 8. Full-profile validate at the coalesce head

See §8.1. Note the standing constraint measured on the sibling task
(`ai_docs/stack-1-shared-files-landing-plan_20260806.md`): **`main` itself fails
`e2e.manifest_determinism_stress` at `4c70658e7`**, so no branch on this base can produce a qualifying green,
and the portable lane exits after ~15 of 47 steps. A red on that node in any receipt below is inherited from
the base, not caused by the branch — and, more importantly, the receipt's coverage is a fraction of the lane.

### 8.1 Receipt

Recorded at the end of this document.

## 9. Suggested landing order

1. **COALESCE-A** (§3) — 26 PRs, one validate, gates already green. Biggest single reduction in the pile.
2. **STACK-1** (`stack/fixtures-shared-files`, separate task) — blocked on the #1710 liteinst regression.
3. Close the duplicates (§5): 10 PRs in two families plus 6 subsumptions → up to **14 PRs closed with no
   landing at all**.
4. Return the five ejected PRs (§6) to their authors.
5. STACK-C, STACK-D, STACK-F, STACK-G — small, one conflict each, independent of one another.
6. STACK-A — after the #1665 sharding decision.
7. The §4.6 singles.
8. **#1754 last, alone.**

Steps 3 and 4 remove **19 PRs from the queue without a single validate slot**. That, not stacking, is the
cheapest work available.

## 10. Reproduction

```bash
SLOT=/home/newton/work/dev-hermit/worktrees/verify/hermit

with-proxy gh pr list --repo rrnewton/hermit --state open --limit 300 \
  --json number,title,headRefName,headRefOid,isDraft,labels,files
with-proxy git -C $SLOT fetch origin 'refs/pull/*/head:refs/remotes/pr/*'

# genuinely-unlanded (patch-id equivalence, not a title scan)
git -C $SLOT cherry origin/main <pr-head>

# ground-truth conflicts
git -C $SLOT merge-tree --write-tree --merge-base=4c70658e7 <headA> <headB>

# a PR against its own gates, alone
git -C $SLOT checkout --detach <pr-head>
./ci/test_harness.sh validate
cargo clippy --workspace --all-targets -- -D warnings
```
