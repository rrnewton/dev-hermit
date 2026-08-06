# Tasks filed without a verify field: 34% of the graph, 70% of live work, 88% of what is in flight

**Task:** `tasks-filed-without-a-verify-field-cannot-be-goal-checked` ·
**Agent:** hermit-audit (`[impl agent, opus-5]`) · **2026-08-06** · local SQL only, no egress.

## Answer

The premise holds, and two structural facts make it worse than a filing-discipline problem:

1. **There is no `verify` column.** I checked every column of all 20 tables in
   `/home/newton/.tg/hermit.db`: nothing named `verif*`. The "Verify field" is a **prose convention
   inside the free-text `description`**. You cannot query, enforce, or gate on it; every measurement of
   it — including this one — is a regex over prose. And `tg add` makes `--description` **optional**
   while `--impact`, `--effort` and `--tags` are **required**, so the graph is structurally happier to
   lose the goal than the ROI estimate. **603 tasks have no description at all.**
2. **The closure gateway never reads the description.** `ci-hub/closure/verified_close.py` verifies
   that a PR/artifact/run reference exists and is ancestry-bound, then closes. It contains no reference
   to the task's stated goal (`grep -nE 'verify|goal'` → only the argparse help string). So the
   gateway can certify **LANDED** and can never certify **GOAL-MET** — the third state the task names
   is *absent from the tooling*, not merely unwritten.

Together: the field can't be enforced because it isn't a field, and the closure step couldn't check it
even if it were written.

## Numbers

Denominator **4232 tasks** at the start of the audit (**4235** at the end — three were filed by other
agents while I ran; noted rather than hidden).

### The robust measure: is there a verify clause at all? (structural, no judgement)

| population | no verify clause | |
| --- | ---: | --- |
| whole graph | **1443 / 4232** | **34.1%** |
| created 2026-07 | 780 / 3463 | 22.5% |
| **created 2026-08** | **663 / 769** | **86.2%** |
| CLOSED | 1092 / 3734 | 29.2% |
| **OPEN + IN_PROGRESS + BACKLOG** (actionable) | **352 / 501** | **70.3%** |
| **IN_PROGRESS** (in flight right now) | **37 / 42** | **88.1%** |

**The headline is not the level, it is the collapse.** Verify coverage went from **77.5% in July to
13.8% in August**. The hermit-ptw sample that opened this task (24% coverage over 38 recently-landed
tasks) is not the steady state — it is the August regime, measured correctly. And the gradient runs the
wrong way: the *more active* a task is, the less likely it states a success condition
(CLOSED 71% have one → BACKLOG 34% → OPEN 27% → IN_PROGRESS 12%).

### Reflexive control

Of the **four** tasks dispatched to me today, **three have no verify field**
(`vacuous-test-audit-hermit-staging-candidates`, `drain-blocked-no-green-exists-at-or-above-the-gate-floor`,
`cmake-trusts-mtime-not-content-so-a-truncated-artifact-is-permanent`). Only this one — the task
*about* verify fields — carries one. In each of the other three I had to infer the success condition
from the prose and state it back in my own notes. That is first-hand evidence of the failure mode, not
archaeology.

## The part where my own measurement was a fake-green

I first tried to grade the *quality* of the 2789 verify-bearing tasks automatically: a clause is
"checkable" if it contains a command, a file path, an exit code, an N/M ratio, a numeric threshold, a
SHA, or a PR reference. It reported 2029 VACUOUS / 760 CHECKABLE.

**Then I hand-adjudicated a random sample of 30 and the classifier failed.** 18 of 30 clauses it called
vacuous are in fact checkable by a third party — a **60% false-negative rate**. Examples it threw away:

* `Verify: hermit run --strict --verify --backend kvm -- uname -n returns 'hermetic-container.local'` —
  a command *and* an expected output; missed only because `hermit run` was not in my command list.
* `Verify: 5+ new C tests pass DBI --strict --verify beyond batch 1. Post exact test names.`
* `Verify: SaBRe uses reverie-ptrace, no direct nix::sys::ptrace calls` — checkable by `grep`.

Corrected estimate from hand adjudication: **~40% of verify-bearing tasks are genuinely vacuous
(95% CI 22–58%)**, i.e. roughly **1115 tasks, band 626–1604** — not the 2029 my regex claimed. The
regex over-counted by ~900.

I am reporting this rather than quietly fixing the regex because it is the same defect the task is
about, one level up: **I built a proxy for "is this criterion real", it correlated poorly with the
fact, and it would have produced a confident wrong number.** It also determines the guard design below.

## Offenders

`offenders-actionable.csv` — all **352** actionable offenders (BACKLOG 181, OPEN 134, IN_PROGRESS 37)
with status, priority, creation date, owner, and title. Closed tasks are deliberately excluded: the
task's own instruction is *do not retrofit archaeology*, and a Verify field invented after the fact by
someone who did not do the work is theatre.

The 37 in-flight ones are the list worth acting on. A sample of what is being worked right now with no
stated success condition: `cap-concurrent-validates-at-6-measured-knee`,
`never-test-a-pr-without-rebasing-first`, `soft-green-vs-hard-green-is-not-tracked-anywhere-in-ci-hub`,
`validate-then-land-is-unsound-the-push-rewrites-the-head`, `make_unsupported_syscall_panic`.

## Proposed guard

`tg-verify-lint` (prototype in this directory) is **deliberately two-tier, and the second tier is
deliberately weak**:

| tier | rule | reliability |
| --- | --- | --- |
| `NO-VERIFY` | the description carries no Verify / Success-criteria / Done-when clause | structural, zero judgement, 100% reliable |
| `VACUOUS-STOCK` | the clause matches a curated denylist of stock phrases measured to recur in *this* graph (`Pass/fail`, `Results documented`, `Report pushed`, `PR count`, …) | high precision, low recall, by design |

It does **not** attempt general checkability judgement, because I measured what that costs: a 60%
false-negative rate would reject three of every five good Verify lines, and a guard that noisy is
disabled within a day. **Bind the gate to what you can observe (the clause exists; it is a known stock
phrase), not to what you wish you could observe (the clause is meaningful).**

### Mutation matrix (both sides bracketed)

| case | planted description | `tg-verify-lint` | naive "grep for the label" guard |
| --- | --- | --- | --- |
| POSITIVE | `Verify: hermit run --strict --verify -- /bin/true exits 0 and both runs' DETLOG match` | **OK** (fires nothing) | OK |
| NEGATIVE 1 | Files/Action only, no Verify | **NO-VERIFY** | NO-VERIFY |
| NEGATIVE 2 | `Verify: Pass/fail` | **VACUOUS-STOCK** | **OK** ← the naive guard is itself a fake-green |

Live run: actionable population → `OK 149 / NO-VERIFY 352 / VACUOUS-STOCK 0`, exit 1. Whole graph →
`OK 2718 / NO-VERIFY 1444 / VACUOUS-STOCK 73`, exit 1. (VACUOUS-STOCK is 0 in the actionable set: the
stock phrases are a July artifact, so the live problem is purely missing clauses.)

### Where to enforce it — not at `tg add`

`tg` is a compiled ELF at `/home/newton/orc-bin/tg` with no local source, so an in-tool required-field
change is not available to us. A wrapper script around `tg add` is bypassable (agents call `tg add`
directly) and would be the weakest option. Three points we *do* control, in leverage order:

1. **Pre-dispatch (highest leverage).** The failure lands when a task is *handed to an agent*, not when
   it is filed — that is where I paid it three times today. Run `tg-verify-lint --task <id>` before
   dispatch; if it fails, the dispatcher writes the success condition (they have the context; a later
   retrofitter does not).
2. **Closure (makes the third state exist).** Teach `ci-hub/closure/verified_close.py` to read the
   description and stamp the distinction it currently cannot express:
   `CLOSURE-VERIFIED: ... goal=stated` vs `goal=unstated`, and require an explicit
   `--goal-unstated` acknowledgement to close a task that has no verify clause. This is the task's own
   item (2), it is ~15 lines in code we own, and it converts a silent conflation into a recorded one.
   It should **warn, not block** — blocking closure on a field the filer omitted punishes the wrong
   agent.
3. **Standing lint.** `tg-verify-lint --status OPEN --status IN_PROGRESS --status BACKLOG` in the
   hourly status rollup, reporting the actionable count. Cost: one sqlite query, ~40 ms.

### The measurement to re-run

The task asks for a 24 h re-measure. The right metric is **not** whole-graph coverage (dominated by
3463 July tasks and therefore nearly immovable) but **coverage among tasks created since the guard
landed**:

```sql
SELECT COUNT(*) FROM tasks WHERE created_at > '<guard-landing-time>';   -- denominator
-- numerator: same set, minus tg-verify-lint's NO-VERIFY offenders
```

August's current rate is **13.8%**. If it is still near that after a day of instruction alone, the fix
must be structural — which is what items 1–3 above are.

## Reproduction

```bash
cd experiments/task_verify_field_audit_20260806
./tg-verify-lint                                                          # whole graph
./tg-verify-lint --status OPEN --status IN_PROGRESS --status BACKLOG      # actionable
./tg-verify-lint --status IN_PROGRESS --json | jq -r '.offenders[].local_id'
./tg-verify-lint --description 'Verify: Pass/fail'                        # NEG-2 control
```

All read-only against `/home/newton/.tg/hermit.db`. No task was created, modified, or closed by this
audit.

## Files

| file | what |
| --- | --- |
| `tg-verify-lint` | the prototype guard, with its own rationale for refusing to judge checkability |
| `offenders-actionable.csv` | all 352 actionable offenders (status, priority, created, owner, id, verdict, title) |
| `results.csv` | every measurement with its population and value |
| `metadata.json` | db path/size, task counts at start and end, tg binary, repo SHA, seeds |
