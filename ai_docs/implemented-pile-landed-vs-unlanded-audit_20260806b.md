# Implemented pile: LANDED vs UNLANDED (post-teardown cohort)

Date: 2026-08-06 · Agent: `hermit-cc` (coordinator) · Task: `audit-implemented-tags-vs-real-commits`
Supersedes for **this cohort**: `ai_docs/audit-implemented-tags-vs-real-commits_20260806.md` (population 209)
and `ai_docs/ancestry-verified-implemented-pile-20260806.md` (population 298, agent `hermit-verify`).
Neither prior pass measured this population, and neither used the #325 accept paths.

## Population

The teardown flipped `IN_PROGRESS` → `OPEN`, so the cohort is defined by **tag + non-terminal status**, not by
`IN_PROGRESS`:

| status | count |
|---|---|
| OPEN | 153 |
| BACKLOG | 53 |
| IN_PROGRESS | 7 |
| **total non-closed `implemented`** | **213** |
| (CLOSED `implemented`, excluded — went through the close-task gateway) | 853 |

The owner's "~161" is the `OPEN`+`IN_PROGRESS` slice = **160**. Both framings are reported below.

## Provenance (explicit SHAs; never the API `MERGED` flag, never `FETCH_HEAD`)

| repo | `origin/main` at test time | commits reachable |
|---|---|---|
| dev-hermit (parent) | `e272e66a984fbd6eb5812d15ddab8cdafed4a4b9` | 1213 |
| hermit | `4c70658e785834737cbe1524f77330c781a6f5ea` | 1557 |
| reverie | `dd3c178ea9553004d7bf4c494e1b7fd80e7b6ae6` | 877 |
| agent-utils | `16c88e9d7f522a4e8224ecae5a2e6b6cbe19730a` | 181 |
| liteinst2 | `8bf704feb06a62e7a05bee3b237d70793e4e2689` | 19 |

Generated 2026-08-06T20:53Z. PR tables pulled via `herdr-run --agent hermit-cc 'with-proxy gh pr list …'`:
hermit 1390 PRs (range 1–1756), reverie 372 (1–390), parent 54 (1–54), agent-utils 18 (1–18). All well under
the `--limit 2500`, and all ranges start at 1, so **no table is truncated**. In every repo
`MERGED count == count with a non-empty mergeCommit.oid`, so no merged PR is missing its merge commit.

## Method — four accept paths, not one

A task is **LANDED** iff at least one *deliverable-role* commit, or a *deliverable-bound* PR, is demonstrably
on the relevant `origin/main`:

1. **Ancestry** — `git merge-base --is-ancestor <sha> origin/main`.
2. **Subject-line match** — the candidate's exact subject is the subject of a commit on `main` (rebase copy).
3. **Squash-bullet match** — the candidate's exact subject appears as a `* <subject>` bullet in the *body* of a
   `main` commit. This is the shape GitHub squash-merge produces, and it is the path both prior audits missed.
4. **`git patch-id --stable` equivalence** against a full per-repo patch-id index of `main`.

PR evidence resolves to **`mergeCommit.oid` + ancestry**, never the `MERGED` flag.

**Role discipline.** Notes cite base SHAs as well as deliverables (`branch X @ 91131f47 | base origin/main
9c964fce`). A base is trivially an ancestor, so counting every SHA would manufacture LANDED. Each SHA
occurrence is tagged `deliverable` / `unmarked` / `base` from its preceding context; **bases never establish
landing**.

**PR-binding discipline.** A bare `#1234` in prose is a *correlated proxy*, not a binding to the task's
deliverable. Only an explicit `github.com/rrnewton/<repo>/pull/N` URL or a `PR #N`-keyed reference counts.

### Anti-vacuity bracket (the test discriminates in both directions)

| planted case | expected | observed |
|---|---|---|
| tip of hermit `origin/main` | ancestor | ancestor ✅ |
| hermit `origin/main~200` | ancestor | ancestor ✅ |
| parent `origin/main~400` | ancestor | ancestor ✅ (only after unshallow) |
| head of a currently-OPEN hermit PR (`059df5cd`) | NOT ancestor | NOT ancestor ✅ |
| fabricated SHA `deadbeef…` | unresolvable | unresolvable ✅ |

## Results

| class | count | share |
|---|---|---|
| **LANDED** | **109** | 51.2% |
| **UNLANDED** (the real drain backlog) | **89** | 41.8% |
| **UNKNOWN** | **15** | 7.0% |

Restricted to the owner's `OPEN`+`IN_PROGRESS` slice (160): **LANDED 65 · UNLANDED 83 · UNKNOWN 12**.

| status | n | LANDED | UNLANDED | UNKNOWN |
|---|---|---|---|---|
| OPEN | 153 | 61 | 81 | 11 |
| BACKLOG | 53 | 44 | 6 | 3 |
| IN_PROGRESS | 7 | 4 | 2 | 1 |

### The #325 delta — ancestry alone is wrong 19% of the time

**21 of the 109 LANDED tasks are non-ancestors of `main`.** Under ancestry-only they would have been reported
as UNLANDED, inflating the drain backlog from 89 to 110.

Hand-verified: 29 subject/bullet match pairs re-tested for the rebase-copy signature (same author, same
author-date, same patch-id) — **25 STRONG, 2 MEDIUM, 2 WEAK**, and every MEDIUM/WEAK is independently
corroborated by its PR's `mergeCommit.oid`. For the 7 pure squash-bullet rescues, the candidate's changed
files were checked for containment in the squash; the 2 that were not subsets
(`backend_parity_contract_fixture_2`/`_3`) were resolved against ground truth — their deliverable files
`tests/c/getcpu_identity.c` and `tests/c/sched_getaffinity_identity.c` are **on main byte-identical**
(`matrix.tsv` is absent because #1632 deliberately removed it later).

**`patch-id` added zero rescues beyond paths 2–3** — no candidate landed with a rewritten subject.

## UNLANDED (89) — split by what the drain actually needs

| bucket | count |
|---|---|
| **A** — an OPEN PR exists → landable now | 42 |
| **B** — only a CLOSED-unmerged PR → abandoned, needs refile | 17 |
| **C** — no PR at all → commits stranded locally, needs a PR | 30 |

**Bucket B (abandoned PRs, 17):** `dbi-close-remaining-cells` · `dbi_packs_anonymous_mmaps` ·
`fix-7-boolean-blind-fixtures-emit-observed-values` · `fix_1147_codex_review` · `fix_pr_1147_fail` ·
`fix_pr_1147_failed` · `fix_pr_1147_nonleader` · `fixture-enumeration-order-identity` ·
`fixture-inventory-and-gap-map` · `fold_edit_distance_into` · `liteinst_single_process_ceiling` ·
`make_plugin_detcore_build` · `make_stale_hermit_dir` · `patch_site_inventory_positive` · `pids_axis_real_cgroup` ·
`rename_public_detcore_package` · `sandbox-failure-classifier-misses-ebadf`

**Bucket C (stranded commits, no PR, 30):** `agent-utils-direct-to-main-policy` ·
`assemble-stack-1-seven-prs-five-shared-files` · `batch-bump-the-43-mechanically-disjoint-stale-pins` ·
`build-strace-attach-litmus-harness` · `ci-hub-measure-green-time-percentage` · `e9patch-detlog-heap-stack-parity` ·
`e9patch-inguest-detlog` · `extend-backend-scoped-fixture-verification-beyond-dbi` ·
`fixture-socket-epoll-ordering-identity` · `fixture-stat-metadata-identity` · `fixture-timer-family-identity` ·
`flag-combination-matrix-coverage` · `gitignore-star-log-silently-excludes-golden-logs` ·
`harden-new-measurement-code-at-write-time` · `l3_stack_content_divergence` · `liteinst-close-remaining-cells` ·
`panic-on-unsupported-syscalls-default` · `parity-fixture-family-needs-one-shared-mutation-harness` ·
`per-backend-engagement-invariants-nonzero-work` · `post-landing-ancestry-audit` · `prepare-stacks-for-landing` ·
`publish-two-unpushed-parent-commits-at-risk` · `restate-headline-numbers-with-provenance` ·
`sig-alarm-e9patch-exceeds-wall` · `signal-delivery-determinism` ·
`silent-fallbacks-remaining-liteinst-and-noop-syscall-path` · `standing-check-for-unpushed-parent-commits` ·
`triage-and-reintegrate-45-rescued-orphan-commits` · `vdso-strategy-original-intent-and-cross-backend-viability` ·
`verify-the-five-closed-certification-gaps-independently`

The full 89-row table with per-task repos and open-PR numbers is in
`scratch/audit-impl/unlanded-table.txt` (machine-local); the JSON is `scratch/audit-impl/final2.json`.

## UNKNOWN (15)

**No SHA and no PR anywhere (8)** — tagged `implemented` with zero commit evidence; these are the analogue of
the previous audit's "NOTHING" class and each needs its tag stripped or its evidence supplied:
`cmake-content-hash-elf-magic-not-size` · `equalise-env-blocks-across-preload-and-ptrace-arms` ·
`lu-parity-ships-no-so-runtime-loader-fails` · `make-validate-quick-and-reliable` ·
`sabre-crashes-before-guest-start-decode-invalidbooleanvalue` · `socket-network-syscall-determinism` ·
`stack4-ci-tooling-mixed` · `timer-syscall-determinism`

**Cites only a base SHA, or a SHA that resolves nowhere (7)** — the note never names the task's own
deliverable, so the task is unauditable as written rather than proven bad.

## Three measurement bugs found and fixed — each changed the answer

1. **The parent was a shallow clone (600 commits); it is now unshallowed to 1213.** In a shallow repo
   *absence* from the ancestor set is not evidence of not-landed, only *presence* is evidence. The prior audit
   parked 24 tasks as COMMITTED-UNVERIFIABLE for exactly this reason. That caveat is now retired for the
   parent. Note the prior audit's claim that *both* repos were shallow was wrong: only the parent ever was —
   `git -C hermit rev-parse --git-dir` returns the **relative** `.git`, so a naive
   `cat "$(git -C hermit rev-parse --git-dir)/shallow"` silently reads the *parent's* shallow file. Use
   `--absolute-git-dir`.

2. **`git cat-file --batch-check` in the hermit primary was silently truncating at 253 of 1524 inputs.**
   `hermit/.git/config` contains a promisor remote entry pointing at **reverie's URL** with
   `partialclonefilter=tree:0`. Any missing object triggers a lazy fetch against the wrong repository, which
   fails `upload-pack: not our ref` and aborts the whole batch mid-stream. Resolvable tokens went **700 → 1346**
   once `GIT_NO_LAZY_FETCH=1` was set. Any batch object query against the hermit primary is untrustworthy
   without that env var. **This is a live repo-config defect, not just an audit artifact.**

3. **Loose PR binding manufactured 11 false LANDEDs.** Accepting any bare `#NNNN` found in a task's notes
   marked `add-linux-boot-ci-job` LANDED off a `#1396` merely mentioned in prose, while its actual deliverable
   is draft PR **#1736 (OPEN)**. Requiring an explicit URL or a `PR`-keyed reference corrected 11 tasks.

## Residual caveats

- A task with an older merged PR *and* a newer open PR is scored LANDED. That is right when the merged PR
  carried the work and wrong when the open one supersedes it; the shape is visible in the JSON (`open_prs`
  non-empty on a LANDED row) and is not separately adjudicated here.
- Subject/bullet matching keys on exact subject text. A commit that landed with a rewritten subject *and* a
  rewritten patch would read as UNLANDED. `patch-id` covers the rewritten-subject case and found none.
- The 7 "cites only a base SHA" UNKNOWNs are unauditable from their notes, not proven unlanded.

## Incidental findings (not part of this task)

- **`reverie/` primary is on feature branch `stack-ptracer/liteinst-stats-off-ptrace-crate`**, violating the
  Primary Checkout Invariant (primaries must always be on latest `main`).
- **Parent local `main` is diverged from `origin/main`: 24 ahead / 22 behind.** hermit local `main` is merely
  7 behind (0 ahead), which is benign.
- **In-jail egress currently works for both `git` and `gh`** on this agent (`with-proxy git fetch` rc=0,
  `with-proxy gh api user` → `rrnewton`), bracketed against the herdr-run path which also works. The recorded
  per-destination 403 is not currently in force here.
