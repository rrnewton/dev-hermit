# What wrote `state: red` onto an obligation whose stored jobs say `no_result`

Obligation `20260805-042454-64ffb5147829-7790e3` carried `github.state: "red"` over a
jobs array containing a single `no_result` job. `protocol.py:1988-1999` operating on
that array yields `no_result`, so an unidentified writer produced the verdict.

**It was found, and the investigation inverted the premise: the red is correct.**
The action the task anticipated — re-derive and correct the unsupported verdict —
must NOT be taken. Doing so would erase a true red and un-gate a commit whose
required CI job genuinely failed.

## 1. The writer

| what | where |
|---|---|
| builds the patch | `ci-hub/remediation/protocol.py:888-903` `_github_patch(run)` |
| produces `"red"` | `ci-hub/remediation/protocol.py:880-886` `_github_state(run)` |
| writes it as `github-polled` | `ci-hub/remediation/protocol.py:1638` `poll_obligation` |

**These line numbers are not on `origin/main`.** They are in the parent working
tree at `ac943078bbeb6f31dc366cd81b4b8358dbf7fad3`, which is what the live poller
executes.

## 2. Why the writer was hard to find: two classifier generations, one store

`git merge-base --is-ancestor HEAD origin/main` → **rc=1**. The primary is
*diverged*, not merely behind.

```
working tree  ci-hub/remediation/protocol.py   2305 lines, 0 × required_positive_count
origin/main   ci-hub/remediation/protocol.py   longer, job-level, 11 × required_positive_count
```

Grepping the working tree for `required_positive_count` returns nothing while
`git grep` on `origin/main` returns eleven hits. Searching the checkout instead of
the ref produces "the writer does not exist".

* **old (running):** classifies the **run's** conclusion → partial patch, 8 keys,
  no `jobs` / `positive_count` / `required_positive_count`
* **new (main):** classifies the required **job set** → full patch, 11 keys, with `jobs`

`obligations.transition` → `_merge` (`ci-hub/history/obligations.py:249-291`) is a
**deep merge**, so the old writer's 8-key patch leaves the new writer's `jobs` array
in place underneath a state it never derived from it. That is the entire mechanism.

## 3. Reproducible from the stored data

Events 1768 → 1769 differ in **exactly two of eleven** keys:

| key | 1768 | 1769 |
|---|---|---|
| `state` | `no_result` | `red` |
| `finished_at` | `null` | `2026-08-07T06:50:48Z` |

`jobs`, `positive_count`, `required_positive_count` are inherited byte-identical —
the merge signature. Both changed keys are ones the old writer sets; every other key
it sets already matched, including `workflow_name == DEFAULT_WORKFLOW`
(`protocol.py:32`, hardcoded) and `run_ids` as the single-element
`[int(run["databaseId"])]`.

Live: run `30975093747` **attempt 4** = `completed` / `failure`, `updated_at`
`2026-08-07T06:50:48Z` — matching `finished_at` to the second.
`classify_check("completed","failure")` → `FAILED` → `"red"`.

## 4. The premise is refuted — the red is true

Attempt 4 **contains** the required job. 34 jobs, conclusions `{success: 32, failure: 2}`:

```
Regular tests (GitHub-managed portable)   failure   06:50:40Z   <- the required gate job
test: unit                                failure   06:50:26Z
```

Re-deriving `protocol.py:1988-1999` over attempt 4's real data:
`states=["red"]`, `expected=1`, `positive=0` → **verdict = red**. Both classifiers
agree. `64ffb514` is on hermit main (`behind_by=0`, main ahead 41) and its required
gate job failed; main is not healthy at that SHA.

## 5. The artifact miss does not explain the red

They are two **sequential states of one run** — not two bugs, and not one:

```
06:49:41-06:50:44  attempt 4 IN FLIGHT; gate job not yet created
                   -> job-level classifier correctly records
                      "required job missing from no_result workflow run"
06:50:48           attempt 4 completes; gate job present and FAILED
06:51:05           run-level classifier reads conclusion=failure -> red
```

The miss is a **transient precursor**, not a cause. Merging them concludes "the
required job never appears, so the red is spurious" — false, and it reverts a
genuinely broken commit. Fully separating them sends you hunting a second defect
that does not exist. The gate job is `if: always()` with 9 `needs`
(`ci-portable.yml:823`), so its absence is always transient-until-created, never
structural.

## 6. Sweep — 13 obligations checked

`ignored/ci-hub/obligations.jsonl`, 9083 lines, 13 distinct obligations, latest
record each:

| count | classification |
|---|---|
| 8 | `github.state != red` |
| 4 | red with **no** `jobs` array (old-writer-only records) — all terminal `remediated`, none live |
| 1 | red whose stored jobs do not support it → this one → **re-derived as correct** |

**0 obligations carry a wrong verdict.** 5 of 13 carry evidence the current
job-level policy cannot verify from stored data. Only 1 of 13 is in a live
remediation state, and its red is true.

## 7. The defect that is real

The record is self-contradicting: `state: red` over
`jobs: [{state: no_result, reason: "required job missing…"}]`. The verdict was right
**by luck of timing** — the old writer happened to poll just after a genuinely
failing completion.

The generic hazard is unchanged: a partial patch from the run-level writer can set
any verdict over evidence that does not record it. `cancelled` is not in
`FAIL_CONCLUSIONS` (`check_outcome.py:33`), so cancelled runs still yield
`no_result` and that path is safe; `failure`, `timed_out`, `error`, and
`startup_failure` are the ones that can land a verdict its stored evidence does not
support.

**Root cause and the only durable fix: the parent primary is running diverged code.**
While two classifier generations share one store, evidence and verdict can disagree
at any moment. Reconciling the primary is a coordinator action, not a record edit —
a manual correction would be overwritten by the next old-writer poll, and here it
would also be wrong.
