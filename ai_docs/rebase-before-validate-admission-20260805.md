# Mechanical rebase-before-validate admission

**Task:** `never-test-a-pr-without-rebasing-first`

**Date:** 2026-08-05

**Status:** local implementation complete; live execution deferred for egress

## Finding

The prior ci-hub admission checked only fixed historical floors from
`rebase-base-floors.json`. That proved a PR's own `validate.sh` was new enough to
emit a qualifying receipt, but it did not prove the PR contained today's
`origin/main`. A head could therefore validate green at an obsolete integration
state and still require a rebase before landing. The SHA-keyed green was real
for the tested tree and fake as landing authorization.

The missing authority is moving, not another fixed floor:

```text
fresh_base = fetch origin/main
admit iff git merge-base --is-ancestor fresh_base exact_head returns 0
```

Exit 1 from `merge-base` is `REFUSED`; a fetch, identity, or comparison error is
also fail-closed. The verdict carries both exact SHAs and how ancestry was
checked.

## One composite authority

`ci-hub/validate/preflight_validate.py` is the receipt-producing validation
authority. It returns one record containing:

- the exact candidate head;
- every fixed producer/merge-gate floor result;
- the freshly fetched `origin/main` SHA;
- the moving-base ancestry verdict and check mechanism; and
- one combined allow/refuse result.

The moving base is refreshed through `with-proxy git fetch` immediately before
the check. If both commits exist locally, the verifier uses `git merge-base
--is-ancestor`; the existing GitHub compare fallback handles an exact PR head
not yet present in the local object database. It never treats cached
`origin/main` or an unresolved fetch as current.

All ci-hub receipt producers consume the composite authority:

- `validate-run` checks synchronously before creating a systemd unit;
- `validate-lock run/acquire` checks again at the final admission chokepoint;
- `parallel-prevalidate.sh` checks the PR before reserving work and rechecks the
  exact fetched slot head immediately before `validate.sh`.

The parallel producer previously continued cautiously on preflight errors. It
now refuses them. This closes both fail-open error handling and the race between
an initial PR lookup and the exact slot checkout.

## No qualifying bypass

Low-level `validate-lock` now refuses a Validate target unless it is an exact
lowercase 40-hex commit. Its legacy `--skip-base-check` option produces a named
refusal rather than disabling admission. Either bypass would break the binding
between the ancestry fact and the commit recorded by the child.

The owner's exception remains possible without weakening the authority:
after a rebased head fails, an engineer may run focused historical comparisons
to determine whether the failure pre-existed the rebase. That is differential
debugging, not validation. It runs outside `validate-run` and cannot mint, copy,
or reuse a qualifying landing receipt.

## Interaction with drain planning

The consolidated PR-planning process now makes this mechanical admission a
precondition in both fresh-flow and stale-drain. The staging O(1) design applies
the same predicate to the final batch head; because the landing epoch freezes
main, one green remains bound to the integration base it will land on.

The process also records **land clusters as they ripen**: an independently
authorized component lands as soon as it has an executable current-base green.
It does not wait for unrelated sibling components or the whole backlog. After
each landing, the coordinator fetches the new main and replans remaining
components, while preserving explicit ordering constraints such as
patching-backend-last.

## Local verification contract

The network boundary is mocked in unit tests. Required brackets are:

- stale head: moving base absent from head -> rc 2 with both exact SHAs and a
  rebase remedy;
- current head: moving base is an ancestor -> rc 0 (proves the gate is not
  refuse-all/inert);
- fixed floor missing while moving base passes -> still refused;
- fetch failure -> rc 3 at the authority and fail-closed at callers;
- ambiguous target and former skip flag -> validate-lock refusal before child;
- stale synchronous `validate-run` -> no systemd unit; and
- stale `validate-lock run` -> no child and no box lease.

No live validation is part of this task. The implementation and tests are local;
live PR/base proof remains deferred while egress is unavailable.
