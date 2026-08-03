# Measuring the wait of a run that hasn't started (ci-hub history)

**Date:** 2026-08-03
**Agent:** hermit-ci
**Question (owner):** `ci-hub history` shows `queue_s=0` for a run stuck in the
queue for hours — a silent wrong reading. Can we measure the wait for a run that
has *not started*? `now - created_at` is a lower bound on its eventual wait and is
the number a human triaging a backlog actually wants ("3h and counting" beats
"0"). Is that a stored column, a display-only field, or "leave it, `--slowest`
covers it, document harder"?

## What the data actually says (measured, hermit, 168 queued runs)

The store computes `queue_s = run_started_at - created_at`, blank/0 until start.
The mechanism behind the misleading `0` is NOT a missing field — it is a GitHub
**placeholder**:

- **168/168 queued rows have `run_started_at == created_at`.** GitHub returns the
  creation time as a placeholder start time until the run truly starts, so
  `queue_s` computes to exactly 0 for every still-queued run.
- `updated_at - created_at` (purely GitHub-recorded fields): median **2342s**,
  p95 **17342s**, max **19583s**; **85/168 exceed 300s** — but it is **0** for the
  rows GitHub has not re-touched since creation (e.g. a run created and updated at
  the same second). A true lower bound, but a weak one.
- `mtime(gha-runs.csv) - created_at`: median **14812s (4.1h)**, **139/168 exceed
  1h**. This is "the run was still queued as of our last refresh, and it was
  created N seconds before that refresh, so it waited **at least** N."

## The three candidate signals, ranked by honesty

| signal | meaning | overstates? | offline? | verdict |
|---|---|---|---|---|
| `queue_s` (current) | measured completed wait | no | yes | correct for TERMINAL runs; **0 is misleading for queued** |
| `updated_at - created_at` | last GitHub touch − creation | no (true LB) | yes | sound but weak (0 when GitHub hasn't re-touched) |
| `mtime - created_at` | still-queued **as of snapshot** − creation | no (anchored to observation) | yes | **best honest LB**; must state the snapshot time |
| `now - created_at` (owner's phrasing) | still-queued **as of query time** − creation | **YES if status is stale** | requires a live check | **reject in the offline reader** |

### Why `now - created_at` is the one to reject *in `query.py`*

`query.py` is contractually offline (read-only, no network). It reads a
**snapshot**. A row's `status=queued` is only true *as of the last refresh*. If
the run has since started or finished, `now - created_at` reports a *growing
phantom wait for a run that is no longer waiting* — reintroducing the exact
silent-wrong-reading class we are trying to kill, just inverted.

`mtime - created_at` avoids this because it anchors the "as of" to the moment the
status was actually observed (the snapshot write), not to an unverifiable "now".
It is a **true lower bound**: it can only *understate* the real current wait
(the run may still be queued past the snapshot), and understating is the safe
error direction for a queue-health signal — it never makes a stuck queue look
healthy.

A live "3h and counting" number is legitimate and useful, but it requires a
**fresh GitHub query** to confirm the run is still queued. That belongs in a
live path (ingest, or an explicit `ci-hub history --live` over `with-proxy`),
clearly separated from the offline store reader. Not taken here.

## Recommendation (and what shipped)

1. **Keep `queue_s` pure.** It stays the *measured, terminal-only* wait, and the
   summary's `queue_s median/p95/max` stays a clean completed-run distribution.
   Never merge a lower bound into that column or its percentiles — two kinds of
   number in one channel is the drift pattern we spend our time eliminating.
2. **In the recent-runs listing, replace the misleading `0` for a still-queued
   run with `≥N`**, where `N = snapshot_mtime - created_at`, marked with a `≥`
   prefix so a measured wait and a lower bound are never confused. The legend
   states the snapshot time as the freshness basis — the same "declare your
   basis" discipline as the COST lines.
3. **Flag queued outliers too.** The `!` outlier test now uses the *effective*
   wait (measured `queue_s` for terminal runs, the `≥` lower bound for queued),
   so a run sitting in the queue for hours is flagged inline instead of reading
   `0` and looking healthy.
4. **JSON** gains `queue_lower_bound_s` (null for terminal runs) and
   `snapshot_ts`; `queue_s` is untouched. Consumers can tell the two apart by
   field, not by guessing.
5. **Do NOT add `now - created_at` to the offline reader.** If a live counter is
   wanted later, add it as a separate network-touching path.

This turns the caveat ("`queue_s=0` on a run stuck for hours is a silent wrong
reading") into a correct, self-describing reading, without inventing a completed
wait we did not measure.

## Reproduce

```bash
cd ~/work/dev-hermit
ci-hub history --repo rrnewton/hermit --status queued --slowest --limit 10
# QUEUE(s) column shows ≥N for still-queued runs; ! marks lower-bound outliers;
# legend names the snapshot time. queue_s and its percentiles are unchanged.
```
