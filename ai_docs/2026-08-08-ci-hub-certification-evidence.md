# ci-hub certification evidence — re-derived 2026-08-08

Evidence table that `certify-ci-hub-reliable` closes against. Assembled by task
`assemble-ci-hub-certification-evidence`.

**This table exists because the certification must NOT close merely because its subtasks
closed.** That would be the exact defect it exists to eliminate. Every row below was
re-derived from the repository, not copied from a task note. Where a note and the
repository disagreed, the repository won and the disagreement is recorded.

## The bar

A condition is **MET** only when a planted violation is **REFUSED** *and* a legitimate case
still **PASSES**, with counts stated for both. **A landed fix is not evidence; a bracketed
fix is.** A bracketed fix that is not landed is not in effect.

## Verdict

| # | Condition | Verdict |
| --- | --- | --- |
| 1 | No verdict survives absent evidence | **PARTIAL** |
| 2 | No gate binds to an unrecorded moving reference | **PARTIAL** |
| 3 | One verifier per authority, called by every consumer | **NOT MET** |
| 4 | Admission is real — one non-overridable production boundary, box-global exclusion proven | **NOT MET** |

**Certification must not close today.** Conditions 3 and 4 each fail on their *defining*
clause, not on a detail: the receipt-admission verifier is written but unpublished, and
box-global exclusion has no code change at all.

## How to re-derive this table

```bash
git merge-base --is-ancestor <sha> origin/main            # landed on dev-hermit main?
git -C hermit merge-base --is-ancestor <sha> origin/main  # landed on hermit main?
with-proxy gh pr view <n> -R rrnewton/hermit --json state,mergedAt,mergeCommit
```

**Do not verify a fix by the presence of a file or a path.** See *Method traps* below;
the first check this audit ran returned the wrong answer that way.

---

## Condition 1 — no verdict survives absent evidence: **PARTIAL**

| Contributing task | Reference | Landed? | Negative bracket | Positive bracket |
| --- | --- | --- | --- | --- |
| `ci-hub-coverage-label-overstates-receipt` | `fde6376f` | **YES**, dev-hermit main | schema-4 receipt reports `coverage_satisfied=null` / `grandfathered-unknown`, 1/1 | schema-5 declared per-node reports `coverage=full` / `satisfied`, 1/1; suite 7/7 |
| `orphaned_task_detector_fails` | `cda96d60` | **YES**, dev-hermit main | 3 of 7 cases refuse (`rc=2` could-not-measure) | 4 of 7 measure a real population |
| `decide-tg-db-naming-and-avoid-symlink-aliasing` | `a20ca763` + `5ee7959c` | **YES**, dev-hermit main | resolver negatives **6/6** refuse; 4 previously fail-OPEN paths now fail closed | positives **5/5** resolve; suites 301 passed, 0 failed |
| `rust-validate-driver-fails-open-zero-measured-reads-as-pass` | hermit **PR #1635**, head `40241d7f` | **NO** — PR `state=OPEN`, `mergedAt=null`, head not an ancestor of hermit `main` (`f65f7446`) | e2e: executed 4 / failed 1 / measured 0 → **exit 1** | e2e: executed 192 / failed 0 / measured 187 / passing 187/187 |
| `rr-lane-lost-its-verdict-zero-evidence-run-passes` | hermit **PR #1971**, head `5fa90faa` | **NO** — PR `state=OPEN`, `mergedAt=null`, not on hermit `main` | *(not re-derived; see gap)* | *(not re-derived; see gap)* |

**Gaps blocking MET.**

1. **The two lane fixes are published, not landed.** Both PRs are OPEN. The owner's summary
   ("Rust driver fail-open fixed; R/R lane verdict restored") reads as done; in effect,
   hermit `main` still carries both fail-open lanes. `#1635` is additionally blocked by
   `validate-1635-hosted-cgroup-allowance-fix`, whose own owner reports its positive
   bracket as *"PARTIAL SO FAR, AND I AM NOT CLAIMING IT AS SATISFIED"*.
2. **The resolver sweep is unfinished.** `complete_the_taskgraph_resolver` is IN_PROGRESS
   with **seven** `tg` callers still unwired, four pinned to a hardcoded filename.
3. **The "ten measured fail-open instances across six subsystems" figure is UNVERIFIED.**
   No task or note in the graph enumerates it. Four instances are tracked above; the
   remaining six, if they exist, have no owning task and are therefore untracked.

## Condition 2 — no gate binds to an unrecorded moving reference: **PARTIAL**

| Contributing task | Reference | Landed? | Negative bracket | Positive bracket |
| --- | --- | --- | --- | --- |
| `primary-checkout-snapshot-gate-chases-a-moving-reference` | `0e145592` | **YES**, dev-hermit main | 3 cases must NOT hard-warn (parent 1–2 commits behind an advancing main) | 3 cases must STILL block: dirty primary, inconsistent Reverie pin, primary off main — each asserts hard warning present AND `DEFERRED` absent; 13 new tests pass |

That is a genuine two-way bracket, and the fix is the right shape: it deleted a **second,
weaker copy** of a currency check performed outside the lock that `scripts/parent-main-write`
already performs atomically inside it — one verifier per authority, applied correctly.

**Gap blocking MET: 1 of a claimed 5.** The certification context cites **five**
moving-reference gates. Exactly **one** task exists in the graph on this axis, and it is the
one above. **The other four are UNVERIFIED and untracked** — no task, no inventory note, no
owner. Either the figure is wrong or four gates are unfixed and unrecorded; both are
findings, and neither can be resolved from the graph as it stands.

## Condition 3 — one verifier per authority, called by every consumer: **NOT MET**

| Authority | Verifier | Consumers routed | Landed? |
| --- | --- | --- | --- |
| Receipt **coverage** | `qualifying_receipt::coverage_verdict` via shared `evidence()` | **4/4** (newest-green JSON, cache, first-bad/failure JSON, human renderer); external rebase consumer reads SHA only | **YES** `fde6376f` |
| Receipt **admission provenance** | `admission_verdict` in `ci-hub/qualifying_receipt.py`, Rust delegating via `--admission-only` | claimed 6 legs / 7 certifier sites | **NO — UNCOMMITTED** |
| Ledger **producer** | `ci-hub/validate/qualifying-receipt.json` `producer` block | 2 of 4 writers | **INERT** |

**Gap 1 — the crux fix is not published.** `receipt-predicates-must-require-admission-provenance`
is IN_PROGRESS. Measured directly: `admission_verdict|admission-only` occurs **0 times** in
`origin/main:ci-hub/qualifying_receipt.py` and **8 times** in the working-tree copy, with
**+295/-27 uncommitted** across `ci-hub/lib/qualifying_receipt.rs`, `ci-hub/qualifying_receipt.py`
and `ci-hub/validate/qualifying-receipt.json`. Its local brackets are strong and its owner's
reporting is honest — forged shape refused 6/6 legs and 7/7 sites, genuine admitted schema-5
accepted 6/6 and 7/7, each clause independently 6/6 and 7/7, schema-4 grandfathered on 5/5
authority legs, mutation matrix 15/15, Rust suite 188/188 — but **none of it is in effect on
`main`.** Until it lands, the audited forgery still qualifies 1/1 downstream and every
producer-side gate remains decorative.

**Gap 2 — the producer predicate is declared but INERT, and 2 of 4 writers do not emit.**
Verified on `origin/main`: `"applies_from_finished_at": null`. Writer state measured at the
current pins:

| Writer | Emits `producer`? |
| --- | --- |
| `ci-hub/validate/finalize_receipt.py` | yes — `ci-hub-finalize-receipt` |
| `hermit/scripts/validate.rs` | yes (14 refs) |
| `hermit/validate.sh` | **no** — its only 2 `producer` matches are comments about `bitwise_parity` |
| `reverie/validate.sh` | **no** — 0 occurrences of the string |

`validate-sh-emits-no-producer-field-in-either-repo` is IN_PROGRESS with no code yet.
The inert epoch is a *deliberate, documented* staging choice, not an oversight, and the
mutation suite proves the refusal fires when the epoch is set in a fixture — but an inert
predicate is not an enforced authority, so this condition cannot read MET on it.

## Condition 4 — admission is real: **NOT MET**

| Half | Task | Reference | Landed? | Negative | Positive |
| --- | --- | --- | --- | --- | --- |
| Non-overridable production boundary | `remove-production-admission-overrides-from-validate-lock` | `c6767e06` | **YES** | nonexistent SHA, 3 cells (unset/`=false`/`=true`): **3/3** exit 3, 0 children (was 1 of 3 admitted with 1 child). Lock target, 2 cells: **2/2** used the workspace lock (was root=0/ALT=2) | real admissible head, 3 cells: **3/3** exit 0, 1 child, ACQUIRED+RELEASED. Test build 188 passed, 0 failed |
| Box-global exclusion | `validate-lock-exclusion-is-per-file-not-box-global` | none | **NO CODE** | — | — |

The first half is genuinely MET, and its bracket includes a causal proof that the default
authority actually ran rather than a stub (the `=true` cell's refusal text is the real
preflight's own `gh api compare … 404`).

**Gap blocking MET — the defining clause has no fix.** `validate-lock-exclusion-is-per-file-not-box-global`
is IN_PROGRESS with findings only. Its owner enumerated every route with counts and
established that `c6767e06` did **not** close it: 4 of 6 routes are closed or nonexistent,
but **differing workspace root is open and is the whole remaining defect** — `LockPaths::for_workspace`
derives the lock from the git toplevel of the `ci-hub.rs` location. Measured: **46 worktrees
of `~/work/dev-hermit`, all 46 carrying a runnable `ci-hub/ci-hub` ⇒ 46 distinct lock files**,
plus a **second independent clone** at `/home/newton/temp/dev-hermit`, so repo-level
canonicalization alone would not be box-global. The original `2/2 simultaneous admissions via
different lock paths` is unrepaired at the authority boundary.

Corroborating that exclusion *does* hold on the single canonical path today: an exact-head
fixture was refused before child launch because the real box lock was held by a live
`main-green` validation (unit `validate-main-green-f65f74462931-…`). That is the running
thing, not a config — but it exercises one path, which is precisely what is not in dispute.

---

## A phantom closure inside the certification's own dependency set

**`close-ungated-validate-admission-bypasses` is CLOSED, and should not be.**

- Its own last substantive note: *"INDEPENDENT ADVERSARIAL REVIEW CONFIRMED 0/4 LANDED …
  Decision: keep task in_progress with no implemented tag; do not represent 71f2cd1 as a
  complete fix."*
- It carries **no `implemented` tag** and **no `CLOSURE-VERIFIED` note** (0 matches in
  `task_notes`).
- Its SHA `71f2cd1d` is on **neither** `main`: present only on
  `origin/close-ungated-validate-admission-bypasses`.

So a task documenting four unlanded requirements was moved to a terminal state without
evidence. Three of its four requirements were subsequently split into the separate tasks
tracked above — (c) landed as `c6767e06`, (b) and (d) are still open — so the *work* is
accounted for, but the closure itself is unevidenced and a reader who trusted the status
would conclude the bypasses were closed. **This is the precise failure mode
`certify-ci-hub-reliable` exists to prevent, and it is inside its own dependency set.**

## Method traps hit while assembling this

1. **File existence is not landing.** `git cat-file -e origin/main:ci-hub/qualifying_receipt.py`
   **succeeds** — the file was created by `9bf11438` ("declare a ledger `producer` column"), a
   different and earlier change. The forgery fix is not there. Bind to the **code**
   (`grep admission_verdict`), never to the path.
2. **`grep -c` is not content.** `hermit/validate.sh` matches `producer` twice; both are
   comments about `bitwise_parity`. A count-only check would have scored a non-emitting
   writer as deployed.
3. **`implemented` is not landed.** Under the 2026-08-08 lifecycle, `implemented` means
   published. Both hermit lane fixes are tagged `implemented` and both PRs are OPEN.
4. **CLOSED is not landed, and here it was not even evidenced.** See the phantom closure above.

## UNVERIFIED — recorded rather than assumed

- "Ten measured fail-open instances across six subsystems" — no enumerating task or note.
  4 are tracked; 6 are unaccounted for.
- "Five moving-reference gates" — 1 task exists; the other 4 are untracked.
- `concurrent_validates=15` at `c71855a`, cited as evidence for the box-global defect —
  flagged UNVERIFIED by the task's own owner and not dereferenced here either.
- The R/R lane fix's bracket counts were not re-derived; only its non-landed state was.

## Live exposure found while auditing (not a certification finding)

The single most important change of the day — the admission-provenance fix — exists **only
as uncommitted edits in the shared parent working tree**, which ~15 agents write to through
one `.git` index. There is no branch, no commit, no recovery SHA. Its author was correctly
blocked by the serialized writer lock (`unpushed_parent_commits` holding it past its own
timeout, tracked as `unpushed-parent-commits-gate-times-out-while-unpushed-work-exists`) and
correctly refused to bypass or signal it. The exposure is that a checkout, reset, or
wholesale rewrite of any of those three files by any other agent destroys it silently.
**Committing it to a branch does not require the parent-main lock and would remove the
exposure without publishing to `main`.**
