# Branch protection across the three repos — proposal, 2026-08-08

**Decision document. Nothing was applied.** Changing who can merge is the
owner's call; this exists so that call can be made on evidence.

> **The headline, stated first because it inverts the premise:** branch
> protection with required status checks **would not have prevented today's
> incident**. `rrnewton/hermit` already has required status checks, they were
> **satisfied**, and `592f7abd` landed red anyway. The mechanism that caught it
> was post-merge verification, not pre-merge gating.

---

## 1. Scope — measured per repo, not assumed

The premise was "dev-hermit has no branch protection; do the others?" The answer
is that **all three use different mechanisms**, and only one of the three has no
check gating at all.

| | `hermit` | `reverie` | `dev-hermit` |
|---|---|---|---|
| classic branch protection | **none** (`Branch not protected`) | **present** | **none** (`Branch not protected`) |
| rulesets | 2 | 0 | 1 |
| required status checks | `merge-gate-v4` (strict=**false**) | `merge-gate-v2` (strict=**true**) | **none** |
| history rules | deletion, non-fast-forward, linear history | deletions blocked; **force-push allowed** | deletion, non-fast-forward |
| bypass | `RepositoryRole`, mode **always** | `enforce_admins=false` | none declared |

Hermit's rulesets: `main check gating (admin-bypassable)` (id 20244443) and
`main history protection` (id 20307165). dev-hermit's:
`parent-main-no-history-loss` (id 20548990).

**So:**

- **dev-hermit is the only repo with zero check gating** — the original finding
  holds for that repo.
- **hermit is not protected in the classic sense either**; it is governed by a
  ruleset. Anyone checking `branches/main/protection` alone gets a 404 and
  concludes "unprotected", which is wrong.
- **reverie is the outlier in the other direction** — the only classically
  protected repo, and the only one requiring the branch be up to date
  (`strict=true`). It also *permits force-push to main*, which is a larger hole
  than the one this task was opened about.

---

## 2. What would have been prevented today — nothing, and here is the proof

`592f7abd` is the squash commit of `#1746`, landed into hermit main and red.

| | `merge-gate-v4` | `Preflight (fmt+abstraction+portability)` | `Reverie pin is latest main` |
|---|---|---|---|
| PR head `16cbdbb11925` | **success** | — | — |
| squash commit `592f7abd` | **skipped** | **failure** | **failure** |

Required status checks gate **the pull request head**. The squash commit is a
*different object*, synthesised at merge time, and it is never gated by them —
`merge-gate-v4` reads `skipped` on it. Hermit had the control, the control
passed, and the bad commit landed regardless.

**Corollary: the class of failure seen today is not addressable by branch
protection.** It is addressable by:

1. **Post-merge verification of main after each merge** — already policy, and it
   is what caught this at merge one of a planned forty-seven.
2. **Forcing rebase merges over squash**, so the landed commit carries the tested
   patch. Hermit's `main history protection` already requires linear history but
   does not force *which* linear strategy; squash and rebase both satisfy it.

A proposal claiming protection would have stopped `592f7abd` would be wrong, and
would buy friction for a benefit it does not deliver.

### What protection *would* plausibly prevent

- A merge into `dev-hermit` main on a **red** authority. Nothing there checks
  anything today, so this is a real, currently-unguarded path.
- Force-push to `reverie` main — currently **allowed**.

---

## 3. Cost, priced against live state

**Measured while writing this, not projected:**

- **hermit tip is `NO_RESULT` with 2 check runs still `in_progress`.** Under a
  non-bypassable required check, main is unmergeable for the whole of every such
  window. The fleet hit exactly this repeatedly today.
- **dev-hermit's registered authority is `required_positive_count=4`**, but at
  the current tip only **2 of the 4** contexts are emitted — `Parent tooling
  shard` and `ci-hub bounded operations shard` are absent. Sampling the last 8
  main commits, those two appear on **7 of 8**, so this is a mid-flight tip
  rather than a missing job. **But that is the exact deadlock shape:** require a
  context, have a run skip or not emit it, and the branch is permanently
  unmergeable with no way forward that is not a bypass.
- **`strict=true` (require branch up to date) forces serial rebasing.** reverie
  already runs this way. With a 47-PR campaign and main moving every few
  minutes, it converts a parallel merge campaign into a rebase treadmill —
  today's campaign was already re-deriving mergeability between consecutive
  merges without it.
- **Hermit's gating ruleset is bypassable by `RepositoryRole` with mode
  `always`.** It is advisory for anyone with the role. Making it
  non-bypassable is the *actual* decision on the table; leaving the bypass makes
  the control cosmetic.

**Blunt version:** protection would have blocked several of today's *good*
merges — every one landed during an authority window that was pending rather
than green — and would not have blocked the bad one.

---

## 4. Proposal

Presented as three separable decisions, cheapest and least contested first.

**P1 — Close the force-push hole on reverie main.** `allow_force_pushes` is
currently `true`. This is unrelated to the merge-gating debate, has no
merge-throughput cost, and is the only place in the three repos where history on
main can be destroyed. *Recommended regardless of what is decided below.*

**P2 — Give dev-hermit main the history rules the other two already have,
without required checks.** It has `deletion` + `non_fast_forward` already;
add `required_linear_history` to match hermit. Zero throughput cost. Does not
address the red-merge path, and should not be described as if it does.

**P3 — Required status checks on dev-hermit main.** This is the contested one.
- If adopted, require **only contexts proven to be emitted on every main
  commit**. On today's evidence that is `Reject owner-specific build paths` and
  `Demo-touching commits require a green-demo attestation` (2/2 at the tip);
  the other two of the registered four are emitted 7/8 and would need their
  skip-behaviour understood first.
- Keep `strict=false` (do not require branch-up-to-date). `strict=true` is what
  turns a merge queue into a rebase treadmill.
- Decide the bypass explicitly. A bypassable rule is documentation; a
  non-bypassable one will stop the fleet during authority outages, which
  occurred several times today.

**Not proposed:** required reviews. The fleet is autonomous by design and
post-facto human review is the standing model; a review requirement would halt
everything.

---

## 5. Open question this document cannot settle

`592f7abd`'s two failures are `Preflight` and `Reverie pin is latest main`. The
pin gate compares hermit's recorded Reverie pin against **reverie's live main
tip**, and reverie main moved today (`108f9ab4` → `5bf9e0b5`). So one of the two
failures may be a moving-reference artefact affecting *any* hermit commit rather
than anything `#1746` did.

**I could not test this.** The five preceding main commits have those check runs
*absent* entirely — only `592f7abd` got a full run — so the comparison population
is empty and the hypothesis is untested, n=1.

It matters for the revert decision that is being escalated separately: **if the
pin gate is failing environmentally, reverting `#1746` fixes nothing.** One clean
run at `35d76a5859d3` would settle both that and the attribution question.

---

*Produced by `fleet-forensics` for task
`dev-hermit-main-has-no-branch-protection`. Read-only: no protection, ruleset,
or repository setting was created, modified, or deleted.*
