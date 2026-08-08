# Green receipts already carry {sha, resolved_at} — under different names

**Date:** 2026-08-07
**Author:** impl agent, opus-5 (task `green-receipts-store-sha-and-resolved-at`, agent hermit-w28)
**Verdict:** premise **refuted by measurement**. The property the policy demands is present and
enforced; only the *vocabulary* differs. **No code change is warranted, and the change as literally
specified would make the store worse.**

## The denominator

Source of truth is ci-hub's own qualified view (`ci-hub ledger qualified-rows`), **not** a
hand-rolled `result==PASS` filter — that filter yields 360 and is the wrong denominator, because
"qualified" is far stricter than "pass".

**Z = 114 qualified receipts.**

| | count |
|---|---|
| lacking literal `sha` | **114 / 114** |
| lacking literal `resolved_at` | **114 / 114** |
| lacking a resolved SHA (`commit`) | **0 / 114** |
| lacking a resolve time (`finished_at`) | **0 / 114** |
| `commit` is full 40-hex | 114 / 114 |
| `commit_anchored` true | 113 / 114 (1 outlier, uninvestigated) |
| schema_version | v3=52, v4=25, v5=37 |

## Both directions, proven

A green receipt *is* an authorization, so the plant went into an **inert fixture** via
`validate-status --ledger <PATH>`, using a real schema-5 qualifying row with its commit rewritten
to a synthetic 40-hex that exists in no repository. Never the live ledger.

**Positive — required, or a refusal proves nothing:**

| probe | result |
|---|---|
| planted receipt at its own full SHA | `rc=0` `VALIDATED` `qualifying_count=1` |
| unambiguous prefix of that SHA | `rc=0` `VALIDATED` |
| two-receipt ledger, each queried exactly | `rc=0` each resolves to **its own** row |

**Negative:**

| probe | result |
|---|---|
| unrelated full SHA | `rc=4` `NOT-VALIDATED` |
| SHA differing in the **last character only** | `rc=4` `NOT-VALIDATED` (no fuzzy match) |
| prefix ambiguous across 2 commits | `rc=2` *"commit prefix '11111111' is ambiguous across 2 distinct ledger commits"* — fail-closed, does not pick one |

**The decisive observation.** Querying a different SHA *while a qualifying receipt sits in that
same ledger* returns `qualifying_count=0` and **`newest_qualifying=null`** — null even though the
ledger's only row is qualifying and trivially the newest. There is no fallback-to-latest path.
This is what separates *stale* (receipt exists, wrong commit) from *absent* (no receipt).

**Enforcement points**, both exact equality:
`ci-hub/qualifying_receipt.py:122` — `row.get("commit") == sha`
`ci-hub/lib/validate_status.rs:463` — `if row.commit.as_deref() != Some(sha) { continue; }`

## It is already locked by tests

Run at head: `rust-script --test ci-hub/ci-hub.rs` → **159 passed / 6 failed**. The 6 are the known
pre-existing baseline (`validate_lock::tests` ×5 + `root_help_groups_commands_for_first_time_users`);
none is in `validate_status`, and nothing was changed.

- `clean_full_pass_validates` — positive
- `no_record_is_not_validated` — the stale negative
- `failures_on_a_different_commit_are_not_attributed_here` — cross-commit isolation for reds
- `resolve_prefix_is_unambiguous_or_errors` — prefix path fail-closed

## One real defect: a test name that hid its own coverage

`no_record_is_not_validated` misdescribes its fixture. There *is* a record — `clean_full_pass(OTHER_SHA)`
builds a fully qualifying receipt at a different commit. The test asserts precisely the stale-green
case, but its name says "no record".

That name cost two agents. It is why an earlier pass concluded there was "no positive-side proof
that the refusal fires on a receipt that EXISTS but describes a superseded commit", and why this
pass nearly added a duplicate test before opening the body.

**Proposed (1 line, not applied):** rename to
`a_qualifying_green_on_a_different_commit_does_not_validate_this_one`, and add a separate
`empty_ledger_is_not_validated` if the genuinely-absent case is wanted. Not applied because this
task carries no commit authorization, and an uncommitted edit to a *tracked* file in the shared
parent tree can be swept into another agent's pathspec commit or silently revert landed lines.

## Why not add `{sha, resolved_at}`

Adding them as new fields creates **two names for one authority** — the duplicate-dereference shape
the originating gate audit was written against, and the same family as the two-reads-disagree race
measured the same day on the reverie pin gate
(`reverie-pin-equals-latest-main-is-a-race-against-a-moving-ref`).

Two dispositions, coordinator's call:

- **(A) Recommended.** Accept "property present, vocabulary differs"; close as stale-premise.
- **(B)** If the policy's vocabulary is required: a *single atomic rename* (`commit`→`sha`,
  `finished_at`→`resolved_at`) across the writer and every consumer in one change, then re-run the
  bracket above. This touches a load-bearing authority consumed by `validate-status`,
  `newest-green`, `pr-status` and the merge gate — not a small edit.

## Reproduction

```bash
ci-hub ledger qualified-rows | wc -l                       # denominator
# inert fixture: one real qualifying row, commit rewritten to a synthetic 40-hex
ci-hub validate-status --ledger <fixture> --sha <planted>  --json   # rc=0 VALIDATED
ci-hub validate-status --ledger <fixture> --sha <other>    --json   # rc=4 NOT-VALIDATED, newest_qualifying=null
rust-script --test ci-hub/ci-hub.rs
```

Fixtures retained at `scratch/w28-stale-receipt/{fixture-ledger,ambig-ledger}.jsonl`.
Live ledger untouched throughout.
