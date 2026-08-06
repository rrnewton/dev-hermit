# TaskGraph-derived landing state: repository-bound mechanism and migration gate

**Task:** `tg-cannot-verify-landing-which-is-why-the-directives-ledger-exists`<br>
**Date:** 2026-08-06 · **local only, no egress**

## Outcome

`ci-hub/directives/tg_landed.py` now derives landing from TaskGraph evidence without
trusting TaskGraph's asserted status or `implemented` tag. Each verdict carries the
authority tuple:

```text
{repository, checkout, implementation SHA, target, target_freshly_fetched}
```

It supports the canonical parent, Hermit, Reverie, LiteInst2, and agent-utils
repositories. A reference present in exactly one repository is compared only with that
repository's target. A reference with genuinely ambiguous provenance is
`UNVERIFIABLE`; the checker never chooses the first convenient green.

The checker is ready for fresh-target reconciliation. The ledger migration is not:
TaskGraph currently has only **2** tasks tagged `owner-directive`, while
`ci-hub/directives/ledger.json` contains **20 obligations across 13 unique tasks**.
Retiring the ledger before those child obligations, gates, and typed implementation
tuples are represented in TaskGraph would lose state.

## Root causes removed

### One checkout was asked to interpret every repository

The first version checked every SHA in `hermit/`. A measured sample showed 16/25
apparently absent SHAs in the dev-hermit parent and 3/25 in Reverie; another 7/25
apparently unlanded Hermit SHAs belonged to Reverie. Cross-repository tasks therefore
became false `PARTIAL` results.

The resolver now discovers all canonical repositories, binds each SHA to repository
provenance, and records the binding in JSON. Tests cover two-repository positive,
negative, mixed/partial, absent, ambiguity, and comparison-error cases.

### Object visibility was mistaken for repository ownership

Hermit's object database can see Reverie commits. `git cat-file` alone therefore
reported the same SHA in both checkouts even though only Reverie refs reached it.
Ownership discovery now prefers commits reachable from the repository's refs; raw
object presence is only a fallback for a rebased-away/unreferenced commit. The output
records both `repository_candidates` and `object_database_candidates` so the binding is
observable.

### A local probe silently attempted GitHub access

Hermit is a promisor/partial repository. A batch containing absent objects produced
**162 rows and repeated GitHub 403 failures** because `cat-file` tried lazy fetches.
Every Git probe now sets `GIT_NO_LAZY_FETCH=1`. A regression test verifies that both
single and batch probes carry the setting. Local absence no longer means "network
lookup failed and was mistaken for absence."

### Every historical SHA was treated as an implementation

The first version concatenated every task note and extracted every 40-hex value. Base
commits, validation inputs, superseded heads, and unrelated handoffs accumulated as
permanent landing obligations; the full run reported 403 `PARTIAL` tasks.

Note boundaries are now preserved with SQL JSON aggregation. Only
`IMPLEMENTED`/`CLOSURE-VERIFIED` notes are authorities, and only explicitly bound
`SHA`, `commit`, `mergeCommit.oid`, `main`, or closure-tuple `@SHA` values are selected.
Incidental progress-note SHAs are ignored. Ambiguous prose becomes `NO_REFERENCE`
instead of guessed evidence.

### Population probing was too expensive for a health tick

Per-SHA `for-each-ref --contains` and `merge-base` calls made a 50-task sample exceed a
minute. The implementation now performs one batch object query and cached reachable
and target graph scans per repository. The full local population completes in about
**2.6 seconds** on this host.

## Verification

```text
python3 -m pytest -q ci-hub/directives/tests
36 passed in 0.07s

python3 -m py_compile \
  ci-hub/directives/tg_landed.py \
  ci-hub/directives/tests/test_tg_landed.py
PASS

git diff --check -- \
  ci-hub/directives/tg_landed.py \
  ci-hub/directives/tests/test_tg_landed.py
PASS
```

The mutation bracket includes:

- ancestor -> `LANDED`;
- nonancestor/rebased-away -> `NOT_LANDED` even when status says closed and tag says
  implemented;
- three landed controls -> three positives, so the checker is not inert;
- absent -> `UNVERIFIABLE`, not a manufactured negative;
- mixed repository results -> genuine `PARTIAL`;
- same SHA with multiple provenance candidates -> refused;
- foreign object visible in another object database -> owned by the repository whose
  refs reach it;
- stale positive names the specific stale repository;
- progress-note SHAs do not become implementation authorities; and
- local probing cannot lazy-fetch.

## Non-authoritative stale-target population

The local run examined a live denominator of **857** `implemented` tasks in 2.6s:

```text
landed 85 | partial 96 | not_landed 367 | unverifiable 11 | no_reference 298
```

Bindings found: dev-hermit 147, Hermit 526, Reverie 119, agent-utils 27,
LiteInst2 4. There were 20 absent refs and zero ambiguous-repository refs.

**These are diagnostic counts, not current landing facts.** None of the five targets
was freshly fetched under the no-egress constraint, so the CLI exits nonzero and every
definitive affected result carries its stale repository. The 772 assertion gap must not
be quoted as a current metric.

## What remains before the ledger can collapse

1. Fresh-fetch every configured target and rerun the derived view.
2. Represent all 20 current ledger obligations as typed TaskGraph owner-directive
   records, including child/parent relationships, named gates, repository, target, and
   null/open implementations. Merely tagging 13 tasks and extracting SHAs would hide
   the ledger's open and gated child obligations.
3. Reconcile the TaskGraph-derived view against the existing ledger row by row. Positive
   and planted-negative controls must agree.
4. Make `ci-hub/directives/check.py` and the existing hourly health tick consume that
   TaskGraph view; only then demote `ledger.json` from authority to generated view (or
   remove it).
5. Resolve PR references through a recorded merge OID. A PR number alone is mutable and
   cannot authorize landing locally.

Until those steps are complete, this work improves the derivation mechanism but does
not authorize retiring the working ledger or closing the task.
