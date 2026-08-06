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

### Why the guard ships WARN-only

Measured before choosing the default: **12 `git commit` call sites in this repo's
own tooling, 0 of which pass a pathspec**, including `--amend --no-edit` in
`ci-hub/landing/union-rebase.sh` and `scripts/e2e-union-rebase.sh`. Default-deny
would break landing immediately. So the guard warns, names the staged paths so a
foreign file is visible, and can be switched to blocking with
`HERMIT_SHARED_INDEX_GUARD=block` once those call sites are converted.

The warn path is also fail-safe: a defect in the guard cannot block a commit
unless blocking was explicitly requested.

## Follow-up needed before flipping to `block`

Convert the parent-committing call sites to pathspec form. Most of the 12 are
harmless — they run `git -C <slot-or-fixture>`, which has its own index — so the
real work is the union-rebase amends and the two `ci-hub/ci-hub.rs` commit sites.
Until then `block` is opt-in per shell.

## Files

| file | contents |
|---|---|
| `race_harness.sh` | the planting harness, 23 checks, throwaway repos only |
| `metadata.json` | git version, host, parent SHA, call-site census |

Shipped alongside, outside this directory:
`.githooks/pre-commit` (the guard) and `.githooks/hygiene-policy.md` (the rule).
