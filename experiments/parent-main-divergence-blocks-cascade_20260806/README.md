# The landing cascade cannot start: parent main has diverged, and two of its three premises are stale

**Date:** 2026-08-06 · **Task:** `execute-landing-cascade-via-herdr-run` · **Agent:** hermit-det2

## Verdict

The dispatch's step 1 — *"PUSH THE PARENT first"* — **is not a push.** Parent `main` has diverged:
118 local commits, 40 remote, merge-base `209f7de9`. A fast-forward is impossible and force-push is
barred. Reconciling it means adjudicating **nine conflicts, eight of them genuinely two-sided, all in
the `ci-hub` landing-gate machinery** — the code that decides whether a green is trustworthy.

Two further premises are stale:

* **The herdr-run unblock is partial.** `git` works; **`gh` does not**, so no PR can be merged through
  this channel at all.
* **Stacks 1, 2 and 4 have nothing to land.** The plan that names them wrote no code and opened no PR.

**Nothing was landed.** One safe partial delivery was made: the 118 local parent commits are now
published on a new ref, ancestry-verified, with shared `main` untouched.

## 1. Egress: git yes, gh no

The positive control reproduces exactly. `herdr-run -- with-proxy git ... ls-remote origin main`
returns `4c70658e785834737cbe1524f77330c781a6f5ea`, the SHA the dispatch cites. HTTPS works too
(`ls-remote https://github.com/rrnewton/hermit.git main` → same SHA), and parent fetch **and push**
both succeeded.

`gh` does not:

```
with-proxy gh api /repos/rrnewton/hermit
  => Get "https://api.github.com/repos/rrnewton/hermit":
     dial tcp 140.82.114.6:443: connect: network is unreachable
```

`with-proxy` does not reach gh's Go HTTP client, so `api.github.com` is attempted directly and has no
route. **Consequence: `gh pr merge` is impossible, so no PR can be landed via herdr-run** no matter
what is queued. The whole capability is git-only: fetch, push, ls-remote, ancestry.

**herdr-run bug worth filing.** A bare `gh <subcommand>` is refused —
`program 'api' is not allowlisted. Allowed: gh, git` — because the allowlist reads the *second* token
as the program unless a `with-proxy` prefix is present. Bare `gh` is unusable.

## 2. Parent main has diverged

Measured against a fresh fetch, not a stale ref:

| | |
| --- | --- |
| local `HEAD` | `0d57f5c4a802892888ed38c8feadb9c3590a7914` |
| remote `main` | `40e672bd74a2281d94cc611192906ce4b8669474` |
| merge-base | `209f7de99f0cf6f9f2c878512816dd0ed50dbf15` |
| ahead / behind | **118 / 40** |
| `merge-base --is-ancestor origin/main HEAD` | **false** |

`git merge-tree --write-tree` (non-destructive — the parent working tree is dirty with several other
agents' work and was never touched) returns rc=1 with nine content conflicts, four of them add/add.
Full table in `results.csv`:

| file | base→local | base→remote | class |
| --- | --- | --- | --- |
| `ci-hub/health/pr_status.py` | +136/−4 | +133/−4 | two-sided |
| `ci-hub/health/tests/test_pr_status.py` | +181/−2 | +208/−2 | two-sided |
| `ci-hub/lib/validate_status.rs` | +249/−152 | +48/−54 | two-sided |
| `ci-hub/qualifying_receipt.py` | add/add | add/add | two-sided |
| `ci-hub/tests/documented_commands.py` | +2/−1 | +7/−1 | two-sided |
| `ci-hub/validate/qualifying-receipt.json` | add/add | add/add | two-sided |
| `ci-hub/validate/tests/test_qualifying_receipt_mutation.py` | add/add | add/add | two-sided |
| `ci-hub/validation/publish_receipt.py` | +15/−21 | +20/−23 | two-sided |
| `ci-hub/lib/qualifying_receipt.rs` | add/add | add/add | **rustfmt wrapping only** |

Both fleets independently developed the same landing-gate code. The shape is visible in the history:
the commit *"[coordinator, opus-4.8] ci-hub: one shared qualifying-receipt predicate; five consumers
read it"* exists on **both** sides with different SHAs — local `ccbcf79`, remote `19a219f` — and each
side then built on its own copy.

Only `qualifying_receipt.rs` is trivial: whitespace-insensitive, the two sides are semantically
identical, and the mutation test and malformed-predicate error path are present on **both**. An
earlier line-set comparison of that file suggested 15 remote-only lines; that was a formatting
artifact and is retracted here.

**A methodology note that nearly produced a wrong table.** The first version of `results.csv` compared
against `FETCH_HEAD`, which had by then been repointed at the branch this session pushed — so every
row read "identical". Bind to explicit SHAs, never to a ref that a later command can move.

### Why this merge was not performed

It is materially different work from the authorized *push*, and it is not this agent's to adjudicate:

* ~700 changed lines per side, in the code that gates every future landing. A wrong resolution
  silently corrupts the landing authority — the exact failure the phase-2 inert-guard review exists
  to prevent.
* The remote side is codex-coord's work; its intent was not available here. Neither side is a superset.
* Roughly a dozen agents hold the local history as their live base and were committing throughout this
  session (parent `HEAD` moved `85c05b4` → `b20010e` → `0d57f5c4`).

## 3. Stacks 1, 2 and 4 have nothing to land

`experiments/pr_coalescing_plan_20260806/README.md` (task `coalesce-staged-work-into-topic-prs`) says
under Part B: *"None of these exist yet; each needs a hermit/reverie slot"*, and under Limits: *"This
is a plan… No PR was opened and no product code was written."*

Confirmed independently: **hermit, reverie, liteinst2 and agent-utils all have 0 unpushed commits.**
Stack 3 is likewise unwritten parent edits to `compat-envelope/collect-envelope.rs`, gated on the
parent reconciliation. So dispatch items (2), (3) and (4) have no landable artifact — there is nothing
for *"land one at a time, serialized"* to operate on. The plan itself names the binding constraint:
**slots, not egress.**

## 4. What was delivered

Step 1's stated purpose was *"it publishes the evidence every other stack cites."* That is achievable
without touching shared `main`, so it was done:

* **`staging/parent-cascade-20260806` @ `0d57f5c4a802892888ed38c8feadb9c3590a7914`** — all 118 local
  parent commits, new ref, no force, no rewrite.
* **Verified by ancestry, not by a flag:** fresh `ls-remote` returns `0d57f5c4` for the ref, and after
  re-fetching, `git merge-base --is-ancestor 0d57f5c4 FETCH_HEAD` is true.
* **Shared `main` untouched:** `refs/heads/main` is `40e672bd…` before and after, byte-identical.
* All four published gitlinks (hermit `b4e94ce4`, reverie `04a46b43`, agent-utils `c83bceef`,
  liteinst2 `8bffae9d`) are present locally and contained in a remote branch — no dangling pins.

The 118 commits are now durable and fetchable instead of local-only, and the main reconciliation can be
done from a published ref rather than from one agent's working copy.

## 5. Decisions a coordinator must make

1. **Who adjudicates the eight two-sided `ci-hub` conflicts** — this fleet or codex-coord — and whether
   reconciliation is a merge into `main` or a codex-coord-side replay. Neither side is a superset.
2. **Whether the gh/proxy gap gets fixed.** Until it does, PR landing is impossible from this fleet even
   with git egress working, which invalidates any plan whose steps are `gh pr merge`.
3. **Stacks 1–4 need slots and implementation** before a landing cascade is meaningful.

## Reproduction

```bash
cd ~/work/dev-hermit
agent-utils/bin/herdr-run --agent <you> -- with-proxy git -C $PWD fetch origin main
git merge-tree --write-tree --name-only HEAD 40e672bd74a2281d94cc611192906ce4b8669474 | head
# classify: always use explicit SHAs, never FETCH_HEAD
git diff --ignore-all-space --numstat 209f7de9:<file> 0d57f5c4:<file>
git diff --ignore-all-space --numstat 209f7de9:<file> 40e672bd:<file>
```
