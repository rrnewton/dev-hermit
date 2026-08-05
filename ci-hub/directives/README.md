# Owner tooling directive obligations

`ledger.json` turns owner tooling, configuration, and repository imperatives
into obligations. A quotation, dispatch, design document, branch, or open pull
request is not completion. `check.py` reports `satisfied` only after the claimed
full commit SHA or pull request `mergeCommit.oid` is an ancestor of the freshly
fetched named target branch.

Run the pure primer before editing the ledger:

```bash
./ci-hub/directives/check.py --quickstart
```

Each record requires:

- the date the directive was asked or first durably recorded;
- the GitHub repository, local checkout, and target branch;
- an accountable TaskGraph task and owner;
- a full implementing commit SHA or pull request number, or explicit `null`
  while no implementation has been claimed; and
- `parent_id` for every cross-repository or incomplete-scope remainder.

The checked result is written to `ignored/ci-hub/directives/latest.json` with
the resolved implementation SHA, freshly fetched target tip, and terminal
ancestry verdict. Missing implementation remains `open`; non-ancestral evidence
is `not_landed`; missing task or owner is named explicitly; network or Git
failures are `unverifiable`, never green.

The hourly `owner_tooling_directives` health tick runs the same command and
wakes the coordinator for every state other than complete fresh-main ancestry.
The versioned seed preserves the 16-row 2026-08-04 audit, expands its
cross-repository legs, and records this tracking directive itself. Parent pins
and child obligations therefore cannot be hidden by one partially landed
directive.
