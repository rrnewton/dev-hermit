# Audit: `implemented` tags vs real commits

**Date:** 2026-08-06 · **Task:** `audit-implemented-tags-vs-real-commits`
**Refs kept distinct:** HERMIT main `4c70658e785834737cbe1524f77330c781a6f5ea` ·
PARENT main `bf017055596fce31750dac7ec62e9140adc7f41b`
**Method:** explicit SHAs + ancestry only. No API `MERGED` flag, no `FETCH_HEAD`.

## Verdict: the premise is REFUTED. It is not systemic.

Population: **209** tasks with `status=IN_PROGRESS` AND tag `implemented` (the owner's "~207";
the other 633 implemented-tagged tasks are CLOSED and went through the close-task gateway).

| class | count | share |
|---|---:|---:|
| **LANDED** (ancestry-confirmed against the correct main) | **163** | 78.0% |
| COMMITTED-UNVERIFIABLE (commit exists; outside the shallow window) | 24 | 11.5% |
| ARTIFACT-ONLY (doc / evidence / experiment, present on disk) | 16 | 7.7% |
| ARTIFACT-CLAIMED-MISSING (cited a path that does not exist) | 4 | 1.9% |
| **NOTHING** (tagged, no commit, no PR, no artifact) | **1** | 0.5% |
| PR-REFERENCED-ONLY | 1 | 0.5% |

**The NOTHING list is one task:** `hermit_readme_links_reverie`.

**ARTIFACT-CLAIMED-MISSING (4)** — tagged implemented citing a path that is not on disk;
weaker than NOTHING but still unbacked:
`adv-review-process-infra-artifacts` ·
`define-the-heap-as-guest-allocated-pages-only-code-and-static-excluded` ·
`parity-against-ptrace-cannot-detect-a-shared-bug-needs-a-correctness-oracle` ·
`scorecard-double-run-determinism`

**PR-REFERENCED-ONLY (1):** `microbench-ceilings-must-be-confirmed-on-the-real-path-before-driving-work`

Landed hits by repo: hermit 377, parent 248.

## Three method caveats, each of which changed the answer

**1. Both repos are SHALLOW clones** — parent 505 commits from HEAD, hermit 1550, each with a
`shallow` marker. A `rev-list <tip>` therefore truncates, so **absence from the ancestor set is
not evidence of not-landed**; presence is definitive. That asymmetry is why 24 tasks are
`COMMITTED-UNVERIFIABLE` rather than being reported as unlanded. Reporting them as failures
would have inflated the finding ~24x.

**2. Seven "NOTHING" entries were parse artifacts, not tasks.** My first pass keyed on SQL
output rows and produced ids like `0-3`, `10-13`, `14+`, `bucket`, and even
`lib/qualifying_receipt.rs`. Validating every key against the `tasks` table dropped all seven.
Unchecked, the headline would have been "7 NOTHING" instead of 1 — a 7x false finding in the
exact direction the audit was looking for.

**3. Hand-verification caught that the parent had been pushed mid-audit.** I re-ran three
sampled LANDED claims by hand rather than trusting my own script
(`8f15ea4b`, `6e5bb82e` → ancestors of parent main; `b64d893a` → ancestor of hermit main; all
confirmed). The counter-check — one of my own session commits, which I expected to be
*unlanded* — came back LANDED. Cause: the parent advanced `afba7b17 → bf017055` by a real push
while the audit ran, and `afba7b17` is an ancestor of `bf017055`, so it was a fast-forward, not
a rewind.

## Consequence: my own earlier divergence alarm is superseded

Earlier today I reported parent main **diverged 121 ahead / 42 behind** and recommended a
commit freeze before any rebase. That was accurate when measured. It is now **resolved**: the
parent is at `bf017055`, my HEAD is behind=44 / ahead=1, and my work — including
`7080d68` (the scorecard determinism fix) and `b8d4c82` — is ancestry-confirmed landed. No
freeze is needed. Recording this so the stale alarm does not keep circulating.

## What to actually do

1. **`hermit_readme_links_reverie`** — strip the `implemented` tag; it is the only genuinely
   unbacked claim in 209.
2. **The 4 ARTIFACT-CLAIMED-MISSING** — either the path moved or the artifact was never written.
   Each needs its note corrected or the tag stripped.
3. **Do not treat the 24 COMMITTED-UNVERIFIABLE as failures.** They need a deeper fetch
   (`--unshallow`) to adjudicate, which needs egress. Until then they are unknown, not bad.
4. **The tagging discipline is basically sound** — 78% ancestry-confirmed landed, 7.7% honestly
   artifact-only. The reporting problem the premise feared is not present at scale.
