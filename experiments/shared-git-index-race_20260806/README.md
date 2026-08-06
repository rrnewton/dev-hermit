# The shared git index in the dev-hermit parent, and which fix actually works

Date: 2026-08-06 · Agent: hermit-verify · Task: `shared-git-index-race-in-parent-repo`

## Question

Every agent in the parent shares one working tree and therefore one
`.git/index`, so `git add` is not private and a bare `git commit` sweeps up
another agent's staged paths. It happened for real: six staged files went into
commit `0b40af7`. Three fixes were proposed — pathspec commits, an index
lock/serialization, per-agent `GIT_INDEX_FILE`. Which one survives contact?

## Method

`race_harness.sh` plants a two-agent race: agent **A** stages and is slow to
commit, agent **B** commits while A's paths are still staged. 23 checks across 12
scenarios.

**Every scenario runs in a throwaway repo, never the parent.** Planting a
two-writer race on the live parent would put ~19 agents' real work at risk in
order to test a hypothesis about losing work. Each scenario gets a fresh repo so
they cannot contaminate each other.

```bash
./race_harness.sh     # 23/23 PASS
```

## Results

| # | scenario | result |
|---|---|---|
| S1 | bare commit while another agent has staged | **sweeps** — bug reproduced |
| S2/S3 | `git commit -- <paths>` | commit contains **only** own paths; other agent's staging survives |
| S4 | victim uses the rule, other agent commits bare | **still swept** — the rule must be universal |
| S5 | pathspec commit after editing a staged file | commits the **worktree** version, not the staged one |
| S6 | per-agent `GIT_INDEX_FILE` | own paths only; other agent untouched |
| S7 | same, after a concurrent commit | **silently REVERTED** the concurrent change |
| S8 | pathspec commit of a never-`add`ed file | refused; new files need `git add` first |
| S9 | `git add … && git commit -m … -- …` as one step | own paths only; other agent survives |
| S10 | can a hook tell the two apart? | **yes** — bare sees `.git/index`, pathspec sees `.git/next-index-<pid>.lock` |
| S11 | hook refusing bare, allowing pathspec | refuses / allows / only own paths / bystander survives |
| S12 | the shipped guard, all four modes | warn allows **and names the foreign path**; pathspec silent; block refuses; off silent |

Measured separately: **bare `git commit --amend` sweeps identically** — the
bystander's file was absorbed into the amended commit and left unstaged.

## Verdict

**Adopt `git add <paths> && git commit -m "msg" -- <paths>` as one step.** It is
the cheapest option, needs no coordination primitive, and S2/S3/S9 show it
contains only what you named while leaving other agents' staging intact.

**Reject per-agent `GIT_INDEX_FILE` (S7).** It does deliver private staging, but
an index seeded by an earlier `read-tree HEAD` **silently reverts** whatever was
committed in between — no conflict, no warning, the change is simply gone. That
is strictly worse than the sweeping it prevents. The tempting fix (re-`read-tree`
immediately before committing) just shrinks the same window; `git commit --
<paths>` is the built-in, correct version of that dance because git rebuilds the
temporary index from current `HEAD` itself.

**An index lock was not implemented.** It requires a coordination primitive and
universal cooperation, and it is strictly more expensive than the pathspec rule
while solving no additional case: the whole `add`→`commit` sequence would have to
sit inside the lock, which is exactly the atomic step the pathspec rule already
gives for free.

### Why this needed a mechanism, not another warning

S4 is the crux: the pathspec rule protects *others from you*, never *you from
others*. One agent forgetting re-arms the hazard for everyone. Agents have been
told "explicit paths, never `git add -A`" repeatedly and it is still violated, so
the rule was wired into `.githooks/pre-commit`, which is already active
repo-wide via `core.hooksPath=.githooks`.

### The guard now BLOCKS by default (flipped 2026-08-06)

It first shipped warn-only on the strength of a census that said **12 call sites,
0 with a pathspec**. **That census was wrong twice**: it counted error-message
strings as call sites, and its grep missed the `--only` / `-o` plus separate-`--`
argv form. Re-derived, every site that commits in the *parent* working tree was
already safe, so **no conversion was needed at all**:

| site | form |
|---|---|
| `ci-hub/ci-hub.rs` ×2 | `git -C <root> commit -m MSG -o -- <path>` |
| `scripts/primary_checkout.py` | `git -C <root> commit --only -m MSG -- <paths>` |

The union-rebase amends are not parent commits: both scripts take
`WT=${1:?hermit worktree path}` and `cd "$WT"`, so they use that worktree's own
index and cannot race the parent.

**Verified BEFORE flipping (S13)**, because a flip that broke `make
checkout-fresh` or ci-hub's state files would be exactly the outage this
sequencing exists to avoid: `-o -- <path>` and `--only -- <paths>` are both
ALLOWED under block, commit only the named path, and leave a bystander's staged
file untouched.

**Verified both ways after flipping**, against the real hook and the real index:

- bare commit → `rc=1`, BLOCKED, reported 113 staged paths
- pathspec commit → guard SILENT (the `rc=1` there was the pre-existing Reverie
  pin-drift guard; zero occurrences of this guard's marker)
- `HERMIT_SHARED_INDEX_GUARD=warn` → `rc=0`; `=off` → `rc=0` and silent
- `HERMIT_PIN_DRIFT_OVERRIDE=1` unaffected — used for the real commit, since the
  hermit primary was mid-change by another agent

The live bare-commit test was deliberately run **through the hook** rather than
by creating a commit: 113 other-agent paths were staged, so a guard failure would
have swept real work. Testing a data-loss guard by risking data loss is
self-defeating.

In warn/off mode the guard is still fail-safe: a defect in it cannot block a
commit unless blocking was explicitly requested.

## Usability, which is a correctness property here

The guard caps its listing at 15 paths plus a count. With 113 paths staged it
would otherwise print a wall of text on every refusal, and a guard that floods
the terminal gets switched off within a day — at which point it protects nobody.

## Files

| file | contents |
|---|---|
| `race_harness.sh` | the planting harness, 23 checks, throwaway repos only |
| `metadata.json` | git version, host, parent SHA, call-site census |

Shipped alongside, outside this directory:
`.githooks/pre-commit` (the guard) and `.githooks/hygiene-policy.md` (the rule).
