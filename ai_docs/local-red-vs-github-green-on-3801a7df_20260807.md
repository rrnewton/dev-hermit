# `local=red` vs `github=green` on `3801a7df`: the local side never had a verdict

**Date:** 2026-08-07 · **Agent:** hermit-w17 · **Task:**
`local-red-github-green-on-the-same-sha-3801a7df` (P1) · **Obligation:**
`20260804-221543-3801a7dfb9b9-7a545c` · **SHA:**
`rrnewton/hermit@3801a7dfb9b9faa7aa8e02196dfa3035b7d43585`

The watcher's first typed verdict flagged two authorities disagreeing on one commit
and correctly classified it `investigation_required` rather than picking a side.

Three explanations were on the table: different test sets, a local-only
environmental failure, or a vacuously-passing GitHub side. **The cheapest
discriminator — the test count on the green side — rules the third one out
immediately, and the second one holds.** But the disagreement as stated is wrong in
a way that matters more than which side "wins".

## The green side is not vacuous

Run [`30955755219`](https://github.com/rrnewton/hermit/actions/runs/30955755219),
`event=workflow_dispatch`, 34 jobs, all `success`.

The selector short-circuits for any non-PR event:

```
selection: FULL matrix (reason: event=workflow_dispatch is not a pull_request)
```

so nothing was subsetted. Executed counts:

| measure | count |
| --- | --- |
| cargo tests executed | **759** (unit 557, integration 130, modes 25, liteinst 23, strict-verify 13, unit-parallel 5, docs 3, sabre 3) |
| e2e cells, 14 jobs | **72** |
| dbi-parity cells | **17** (16 PASS + 1 declared GAP, `dbi/pthread_lifecycle`) |
| failures | **0** |

> **Counting caveat.** These logs emit each `test result:` line **twice** — once
> inline and again inside a detail block. A naive tally reads **970**; the first
> pass of this investigation did exactly that and was wrong. The 759 figure is
> deduped on `running N tests`, which is emitted once per test binary.

## The local side failed four times, each differently

| attempt | failing node | failure |
| --- | --- | --- |
| 1 | `[doc.rustdoc]` | `failed to build and install DynamoRIO: exit status: 2` (`reverie-dbi/build.rs:339`) |
| 2 | `[test.sabre_examples]` | `timed out: true` |
| 3 | `[build.runtime_release]`, `[build.privileged_tests]` | `failed to configure DynamoRIO: exit status: 1` |
| 4 | `[test.command_strict_verify]` | `kernel_activity_commands_are_deterministic_under_strict_verify` — *did not reach L2 under strict verification* |

Three of four are **DynamoRIO build** failures — a known host-toolchain blocker on
this box — one is a timeout, and the last asserts on L2, which is separately known to
be unattainable on this host. Per-node rebuttal on the green side: the exact failing
test reads `kernel_activity_commands_are_deterministic_under_strict_verify ... ok`,
sabre is 3/3, dbi-parity is 16 PASS.

So explanation (2), environmental and local-only, holds.

## But the disagreement is mis-stated: local is a NO-RESULT, not a red

The local leg's own receipt authority already refused every record:

```
local.receipt_verification.state = "refused"   reason: "canonical verifier exited 4"
report: verdict = NEEDS-RERUN
        qualifying_count = 0        disqualified_count = 5
        withheld_nonpass_record_count = 4    failed_record_count = 0
```

`ci-hub validate-status` — the canonical authority — says there is **no qualifying
receipt** for this SHA. `local.state="red"` is therefore derived from a **raw exit
code**, not from the authority that adjudicates receipts.

The real disagreement is **`local = NO-RESULT (needs rerun)` vs `github = green`**,
not red vs green. An exit code without a qualifying receipt is a no-result, and the
obligation promoted an absent result into a red verdict. That is the same
proxy-binding error as a `test result: ok` with zero executed tests, run in the
opposite direction.

**Second defect.** `local.classification_reason = "test-failure"` is wrong for three
of the four attempts — those were **build** failures. Remediation routing keys on
this field.

**Unit mismatch.** The local side counts *DAG nodes*, not tests: `Validation summary
[full]` read `(4 passed, 1 failed)` ×3 and `(3 passed, 2 failed)` ×1. Five nodes
against 759 tests — the two sides are not denominated in the same unit, so no
arithmetic comparison between them is meaningful.

## Is the either/or landing rule safe? No

Not because local was wrong this time — because **neither side is a superset**.

- This obligation's policy requires exactly **one** job:
  `Regular tests (GitHub-managed portable)`, `required_positive_count: 1`. That job
  is an **aggregator**: it runs no tests. It downloads 14 parity artifacts and
  asserts every upstream result is `success` **or `skipped`**.
- The green run contains **zero** privileged jobs — it is *CI (GitHub-managed
  portable)*. The local full profile runs a portable lane **and** a
  `privileged CI DAG lane` (observed `(1 passed, 0 failed, 56s)`).

Local covers the privileged dimension GitHub does not; GitHub covers the
host-independence local does not. So "either" does not resolve to *the stronger
authority* — it resolves to *the first one that says yes*, which is the permissive
answer.

Sharper still: because the aggregator counts `skipped` as non-failing, on a
`pull_request` event — where selection **can** subset, unlike this
`workflow_dispatch` — the required job stays green while jobs are removed from the
run. A green from that single required job does not carry what it verified.

## Recommendation

1. The landing rule must name a **coverage set per authority** rather than accepting
   either one. State which set authorises a land.
2. The obligation must distinguish **local-no-result** from **local-red**, so a
   refused receipt stops reading as a product failure.
3. `classification_reason` must not report `test-failure` for a build failure.
