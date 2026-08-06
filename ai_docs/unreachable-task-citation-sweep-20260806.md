# Sweep: unreachable TaskGraph citations in closed-without-landing PRs

Follow-on to `audit_port_epoll_fixture`. PR #1701 was closed on the promise that a
TaskGraph task preserved the work, and the cited id could never resolve. #1698 had
the same defect. Two occurrences is a pattern, so I swept the rest.

Scope: the 47 PRs closed **without merging** among the last 80 closed in
`rrnewton/hermit`, body + all comments, 2026-08-06. Citations extracted by regex
(`TaskGraph|task|tg` followed by a slug-shaped token) and validated with
`scripts/tg-id-check.py` against the live graph (4537 tasks).

## Result: 11 of 22 citations (50%) do not resolve

### A. Mis-cited, but the work IS preserved — 4

The cited slug is unreachable, but the four-token truncation exists. Fixable by
correcting the citation.

| PR | cited | actually resolves to |
|---|---|---|
| #1698 | `timerfd-determinism-fixture-rework` | `timerfd_determinism_fixture_rework` |
| #1700 | `compat-timeout-policy-evidence` | `compat_timeout_policy_evidence` |
| #1701 | `audit-port-epoll-fixture-duplication` | `audit_port_epoll_fixture` |
| #1703 | `patch-site-inventory-positive-control` | `patch_site_inventory_positive` |

### B. No such task, and no near match in 4537 — 4 distinct promises, LIKELY LOST

These are the #1701 shape without the happy ending: a PR closed on a preservation
promise, and nothing in the graph preserves it. Each needs a human decision —
refile or accept the loss.

| PR | cited | near match |
|---|---|---|
| #1635 | `complete_rust_validate_driver` | none |
| #1656 | `publish_fail_closed_reverie` | none |
| #1685, #1696 | `dbi-log-file-cli-routing` | none (same promise cited twice) |
| #1716 | `e2e-requirement-admission-fail-closed` | none |

### C. No such id, but the work plausibly survives elsewhere — 2

| PR | cited | plausible home |
|---|---|---|
| #1699 | `audit-host-state-leak-fixture` | `fixture-proc-sys-read-identity`, `fixture-stat-metadata-identity` |
| #1726 | `epoll-io-uring-fixture-supersession` | `fixture-io-uring-and-epoll-edge-level` (closed) |

Confirm before treating either as covered — a plausible title is not the same
claim as the one the PR made.

## Why this keeps happening

`tg` derives an id from the title: slugify, then keep the **first four underscore
tokens**, `_N` on collision. Of 292 auto-derived ids, **252 are truncated** — for
86% the full-title slug is unreachable. So writing the natural-language title as
a slug is wrong far more often than it is right, and the result looks like a valid
citation.

A second, independent drift: 22 auto-derived ids no longer match their titles at
all, because the **title was rewritten after creation** ("LANDED …", "ANSWERED:
NO …", "DUPLICATE of …"). The id freezes; the title moves. So deriving an id from
a task's *current* title is unreliable even when the title is short.

## Recommendation

1. Fix the four **A** citations (cheap, mechanical).
2. Triage the four **B** promises — that is real work closed against nothing.
3. Confirm or refile the two **C** cases.
4. Run `scripts/tg-id-check.py <id>` before citing a task in a closure comment.
   It is **not wired into any gate**; the wiring point is whatever closes a PR
   while citing a preserving task.

## Caveat on this sweep's own numbers

The extraction is a regex over prose, so it under-counts (a citation phrased
unusually is missed) and can over-count (a slug-shaped phrase that was never
meant as an id). The **A** rows are certain — the truncation is mechanical. The
**B** rows are "no near match found by token overlap", not proof of absence;
each was checked against all 4537 tasks, but a task preserving that work under
wholly different words would not be found.
