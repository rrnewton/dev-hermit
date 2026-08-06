# Owner tooling directive obligations

TaskGraph tasks tagged `owner-directive` are the single authority for owner
tooling, configuration, and repository obligations. A quotation, dispatch,
design document, branch, asserted task status, or open pull request is not
completion. `check.py` reports `satisfied` only after the claimed full commit
SHA or pull request `mergeCommit.oid` is an ancestor of the freshly fetched
named target branch.

Run the pure primer before adding or revising a typed record:

```bash
./ci-hub/directives/check.py --quickstart
```

Each accountable task carries one or more typed notes. The prefix and payload
are deliberately machine-readable:

```text
OWNER-DIRECTIVE-V1: {"revision":1,"id":"...","task":"...",...}
```

Each record requires:

- the date the directive was asked or first durably recorded;
- the GitHub repository, local checkout, and target branch;
- an accountable TaskGraph task and owner;
- a full implementing commit SHA or pull request number, or explicit `null`
  while no implementation has been claimed;
- `parent_id` for every cross-repository or incomplete-scope remainder; and
- an optional `gate` naming the external condition a deliberately deferred
  directive is waiting on (for example, "zero open Hermit PRs"). A `gate` must
  name its blocking condition: an empty `gate` is rejected as `invalid`, because
  a bare "gated" is only a quieter form of unknown.

TaskGraph notes are append-only. To update an obligation, add the complete
replacement payload with a higher integer `revision`. The highest revision for
an `id` wins. Conflicting payloads at the same revision, a tag without a typed
note, an unknown `parent_id`, a parent cycle, or an empty population all fail
closed. This retains child obligations that a single task-level SHA would hide.

The checked result is written to `ignored/ci-hub/directives/latest.json` with
the resolved implementation SHA, freshly fetched target tip, and terminal
ancestry verdict. The states distinguish what is being handled from genuine
drift, so the signal does not cry wolf:

- `satisfied` / `partial` — ancestry-confirmed on fresh main (partial when a
  child obligation is still incomplete);
- `open` — no implementation yet, but owned and carried by a resolvable task,
  i.e. actively in progress, not drift;
- `gated` — no implementation yet and deliberately deferred on the named `gate`
  condition, not drift;
- `needs_owner` — no accountable owner recorded (genuine drift);
- `missing_task` — no resolvable accountable task (genuine drift);
- `not_landed` — claimed evidence is not an ancestor of fresh main (genuine
  drift);
- `invalid` / `unverifiable` — malformed metadata, or an ambiguous verifier
  verdict, never green;
- `fetch_failed` — the verifier reached the checkout but could not fetch the
  target to compare against (a network/Git/proxy failure). This is kept
  distinct from `not_checked` and from `not_landed`: the checker never reached
  the evidence, so the verdict is genuinely unknown (exit 2), never a clean
  pass. Conflating a broken fetch with "nothing to check" lets a broken checker
  read as green, which is worse than an error.

The `drift=` field of the terminal summary counts `needs_owner`,
`missing_task`, `not_landed`, `invalid`, `unverifiable`, and `fetch_failed`;
`open` and `gated` are reported separately as in-progress and deferred work.

For a PR implementation, the checker asks GitHub for the merged PR's
`mergeCommit.oid` and then checks that replay commit against freshly fetched
target ancestry. The mutable pre-rebase PR head is never treated as the landed
identity.

The hourly `owner_tooling_directives` health tick runs the same TaskGraph-view
command and wakes the coordinator for every state other than complete
fresh-main ancestry.
The former `ledger.json` seed was migrated into typed notes; it is no longer a
second mutable status store. Parent pins and child obligations therefore cannot
be hidden by one partially landed directive.
