# Landing prep: BLOCKED at the fresh-main gate — and there is no product stack to rebase

**Date:** 2026-08-06 · **Tasks:** `coalesce-and-rebase-onto-fresh-main` (my part), gated on
`establish-fresh-main-tip` · **Status:** committed locally, **not pushed** (egress 403)

## Report

| item | value |
|---|---|
| branch | **none created** |
| head SHA | n/a |
| validate receipt | **none — and I consume ZERO of the box-exclusive validate slots** |
| verdict | **BLOCKED — nothing of mine is rebasable onto this target** |

## Gate 1 CLEARED, and it resolves to a target I hold no work for

`FRESH-MAIN-TIP @2026-08-06T13:15Z = 4c70658e785834737cbe1524f77330c781a6f5ea`
("Add fail-closed personality domain contract", 2026-08-06 08:40:41 -0400), fast-forward, not a
rewind. Verified locally rather than assumed:

```
dev-hermit (parent) : object ABSENT
hermit              : object PRESENT -> 4c70658e7 2026-08-06 08:40:41 -0400
reverie / liteinst2 : object ABSENT
```

**The tip is a `hermit` commit.** It is the rebase target for *product* stacks. I hold **zero**
unpushed hermit commits, so there is nothing of mine to rebase onto it. That is not a
scheduling problem to wait out — it is a category mismatch.

## Gate 2 still closed for the repo my work IS in

My 119 unpushed commits are all in the **parent**, whose push path remains
`CONNECT tunnel failed, response 403`. The herdr-run fetch that produced the tip reached
`hermit`; the parent object store never received it, and I have no herdr-run path in reach.

## Why there is nothing to rebase (measured)

Independently measured, not taken from the plan note:

| repo | unpushed commits | branch |
|---|---|---|
| **parent dev-hermit** | **119** | main |
| hermit | **0** | main |
| reverie | **0** | main |
| liteinst2 | **0** | main |
| agent-utils | **0** | main |

**There are zero unpushed product commits.** 100% of the overnight work is parent commits on
shared `main`, and per `CLAUDE.md` the parent harness "works directly on shared `main`" — it does
not use PRs. So this set cannot be coalesced into a rebased PR stack: it needs **one push**.

This corroborates the grouping plan's inverted-premise finding at a different commit count (119
now vs 106 then — the set is still growing as the fleet works).

The plan's Part B — the product stacks that *would* need PRs — **do not exist as code yet**.
They are fixes still to be written, each needing a slot. Rebasing cannot be the next step for
work that has not been written.

## Push-readiness of the parent set (verified, since this is the real deliverable)

| check | result |
|---|---|
| unpushed commits | 119 |
| non-parent paths in the diff | `agent-utils`, `reverie` — **gitlinks only** (`-Subproject commit` / `+Subproject commit`), no product code smuggled in |
| files > 2 MiB | **none** |
| areas | experiments 374, ai_docs 122, ci-hub 92, scripts 19, compat-envelope 5, .claude 4, multisect 2 |
| working tree | 17 dirty paths — **other agents' in-flight work, not mine, untouched** |

The set is hygienic and push-ready. The only blocker is egress.

## Part of the plan's cheapest stack is already implemented and sitting unpushed

The plan lists **Stack 3 scorecard-integrity** as "PARENT-ONLY, NO SLOT AND NO PR NEEDED … the
cheapest real wins in the set, gated on nothing but egress", with the first item being
`deterministic=None unless mode==verify` in `collect-envelope.rs`.

**That is done** — commit `7080d68`, in the unpushed set. It also carries the migration (105
single-run overclaims demoted to unmeasured, 346 genuine verify rows tagged) and a standing
guard with both-direction brackets. So a slice of Stack 3 lands the moment the parent is pushed;
it needs no rebase and no slot.

## Determinism argument for the push set

Required by the owner for the PR description; stated here since the parent set's analogue of a
PR is this digest.

**The set contains no product code.** Every path is `ai_docs/`, `experiments/`, `ci-hub/`,
`scripts/`, `compat-envelope/`, plus two submodule gitlinks. It therefore cannot change the
determinism of any guest execution: no backend, no Detcore path, no syscall handling is touched.

The two gitlink advances (`agent-utils`, `reverie`) are the only entries that could affect
behaviour, and they move pins the fleet already validated; they are not new product changes
authored here.

Where the set *touches* determinism, it does so by **measuring more honestly, never by making a
result look better**:

- `collect-envelope.rs` now **withholds** a determinism claim unless a two-run comparison
  actually ran — it demotes 105 previously-green cells to "unmeasured". This makes the
  scorecard *worse-looking* and more truthful, which is the correct direction and the opposite
  of a fake-green.
- The measurement artifacts record failures (SaBRe's reachability wall, its nondeterministic
  detlog-stack, DBI's host-state env leak, ptrace failing canonical `--verify-strict`) rather
  than suppressing them.

No test was weakened, disabled, or re-baselined to pass. The one guard added
(`check-determinism-earned.sh`) is bracketed in both directions and includes a positive control
that fails if the corpus becomes entirely unmeasured, so it cannot be satisfied by blanking data.

## What I will do the moment each gate clears

1. **Egress returns** → the parent push is the highest-leverage single action available; it
   publishes the evidence every other stack cites and lands the Stack-3 slice already written.
2. **A slot for product work** → the tip `4c70658e7` is already fetchable in `hermit`, so a
   product stack could be *written* onto it today. The SEQUENCING order puts 2.3
   `ptrace SIGTRAP mistranslation` first: smallest, and it is the reference backend, so a bug
   there distorts every comparison ratcheted against it. That is writing new code, not rebasing
   existing work, and it needs an explicit go-ahead plus a slot — I have not started it.

## Note on the coalescing directive

"Coalesce aggressively — every stack you merge into another saves a full validate slot" is sound
given `validate-lock` is box-exclusive at ~528 s median. It does not apply to me: with zero
rebasable stacks I have nothing to coalesce, and I am consuming **no** validate slots. If slot
pressure is the binding constraint, the useful thing I can report is that one fewer agent is
queued for the lock.

I did not merge anything. Landing is serial and `hermit-det2` owns it.
