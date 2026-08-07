# The Reverie pin gate compares against a moving ref: measured, 2026-08-07

**Question.** `docs/updating-reverie.md` requires hermit's Reverie pin to EQUAL the live tip of
`rrnewton/reverie:main` — ancestry is explicitly declared insufficient. Is that requirement
satisfiable by an author, or is it a race against an external ref?

**Answer: satisfiable in the mean, unsatisfiable in the burst.** The gate's pass rate is a function
of upstream activity, not of the pull request. Nothing the author does changes it.

## Method

Two independent measurements, both against live data on 2026-08-07.

1. **Upstream commit rate** — the last 200 commits on `rrnewton/reverie:main`
   (`git log --format=%cI`), reduced to inter-commit gaps.
2. **Exposure window** — for the 18 most recent `ci-portable` runs on `rrnewton/hermit`, the
   elapsed time from run creation to each pin verdict, via the Actions jobs API. The pin is
   asserted twice per run, so the window that matters is the LATER one.

## Results

### Upstream commit rate (200 commits, 2026-07-28 -> 2026-08-07)

| window | commits | mean inter-commit |
| --- | --- | --- |
| last 1d | 10 | 2.40 h |
| last 3d | 65 | 1.11 h |
| last 7d | 121 | 1.39 h (0.72/h) |
| busiest day (2026-08-04) | 52 | 0.46 h (2.17/h) |

**Median inter-commit gap: 0.01 h (36 seconds), against a mean of 1.13 h.** Reverie does not land
at a steady rate; it lands in merge trains. Any statistic that reports only the mean understates
the hazard by two orders of magnitude during exactly the windows when work is landing.

### Exposure window (18 ci-portable runs, run creation -> pin verdict)

| assertion | median | p90 | max |
| --- | --- | --- | --- |
| `Reverie pin is latest main` | 1.1 min | 7.5 min | 27.9 min |
| `test: strict-compat` (re-asserts the pin) | 13.1 min | 21.7 min | 26.2 min |

Both exclude the author's own read-tip-to-push interval and any pre-run queue delay. Both are
additive, and neither was measured.

### Collision probability

Poisson, P(at least one upstream commit inside the window):

| rate | median window (13.1 min) | p90 (21.7 min) | max (26.2 min) |
| --- | --- | --- | --- |
| 0.72/h (7-day) | 14.5 % | 22.9 % | 26.9 % |
| 2.17/h (busiest day) | 37.8 % | 54.4 % | 61.2 % |

## Interpretation

The exposure window (13.1 min median) does **not** exceed the 7-day mean inter-commit time
(83 min), so the gate is not unwinnable in the general case — it loses roughly one PR in seven on a
typical week. On the busiest measured day the mean inter-commit time falls to 27.7 min against a
26.2 min maximum window, and the loss rate passes 50 % at p90. During a merge train, with 36-second
gaps, an in-flight pin is invalidated with near-certainty.

The claim "structurally unwinnable at any nonzero upstream commit rate" is therefore too strong and
should not be used: at a low enough rate the gate is winnable, and overstating the defect makes it
easy to dismiss. The accurate claim is narrower and harder to argue with.

**The correlated failure matters more than the per-PR odds.** Because the predicate is
`pin == live tip`, a single upstream commit changes the right-hand side for every open hermit PR at
once — 137 were open when this was written. That is one external event reddening the whole queue,
with no hermit commit involved and nothing to bisect.

**Already proven non-deterministic.** On hermit main portable run `31150269149` @ `590fcc9e`, two
jobs in the SAME run, on the SAME commit, evaluating the SAME predicate, returned opposite verdicts:

| job | window (UTC) | verdict |
| --- | --- | --- |
| `Reverie pin is latest main` | 05:37:28 -> 05:38:09 | SUCCESS |
| `test: strict-compat` | 05:46:54 -> 05:50:53 | FAILURE |

`rrnewton/reverie:main` advanced `6144323c` -> `038e9939` at 05:50:32Z, seventeen seconds before the
failing assertion, whose entire content was `Reverie dependency pin equals latest main (0 passed,
1 failed)`. A predicate whose verdict depends on when the job ran is not measuring the commit.

## The satisfiable form, without loosening the pin

`ancestor-of-tip` is monotone: once true it stays true, because history only extends. That is
precisely why it cannot be raced — and also why it is insufficient alone, since an ancestor may be
arbitrarily old, which is what `docs/updating-reverie.md` is defending against.

Add the missing dimension rather than swapping one for the other:

- **(a)** the pin is an ancestor of `rrnewton/reverie:main`, and
- **(b)** it is no more than N commits / T hours behind the tip.

(a) is stable under a moving tip; (b) bounds staleness. Both are checkable at any instant and
neither depends on when the job ran. A planted stale pin still fails — on (b), against a stated
bound, **reporting the distance**. Today's gate fails a one-commit-behind pin and a
four-hundred-commit-behind pin identically and tells the reader nothing about which.

## What this does not do

Ancestry plus a freshness bound makes the gate SATISFIABLE. It does not make the bump automatic.
The owner's stated principle is that something hermetic but automatically and safely bumped is as
good as a mutable always-main pointer; today the pin is hermetic and manually bumped, and the gate
punishes the lag its own manual process creates. The complete answer is automation that opens or
updates the pin PR when Reverie moves, with the freshness bound as the backstop that catches the
automation failing. Hermit PR #1863, "the automatic, safe Reverie pin bump", is in this space and
should be read before anyone builds; this measurement may be the gate half of work already in
flight.

## Reproduction

```sh
# upstream rate
git -C reverie fetch origin main
git -C reverie log origin/main --format=%cI -n 200   # reduce to inter-commit gaps

# exposure window
gh api "repos/rrnewton/hermit/actions/workflows/321832732/runs?per_page=40"
gh api "repos/rrnewton/hermit/actions/runs/<id>/jobs?per_page=100"
# per run: completed_at of "Reverie pin is latest main" and of "test: strict-compat",
# each minus the run's created_at
```

## Limitation

The per-PR pin distribution across the 137 open hermit PRs was NOT measured: both the contents API
and the git-trees API returned unreadable results for the `reverie` gitlink entry (14/14 and 30/30
attempts). The fleet-wide invalidation property above follows from the predicate by definition, and
is not an empirical count.
