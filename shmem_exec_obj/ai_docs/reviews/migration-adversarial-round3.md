# Migration adversarial review, round 3

Date: 2026-07-28

Reviewed tip: `07d76a9b137cb319f6fec4f8dba67c81006abca7`

Reviewer role: `[adversarial-reviewer agent, gpt-5.6-sol]`

Verdict: **REJECT**

## Major finding

`AdmissionQuiescence::into_authority` compared a reclamation permit only with
the witness's source generation, not with the complete migration transaction.
An external probe committed two plans with the same source and different
transactions/targets. The permit from transaction B released transaction A's
authority while A's control remained `Committed` rather than `Reclaimed`:

```text
accepted tx 2000 permit for tx 1000; control A remained Committed
```

That bypasses the per-control crash fence and can make cleanup repeat through
the still-committed record. The witness must bind the complete authenticated
plan/control identity, and authority extraction must compare the complete
binding. The contract must also state how plan/control identities remain unique
across live control records.

## Other evidence

No other Blocker or Major was found. Borrowed authority wrappers failed to
compile; static-reference, cloneable, and RAII types required an unsafe impl
which explicitly violated the documented fail-closed contract. `ManuallyDrop`
paths did not double-take or implicitly drop the authority.

Current and Rust 1.85 migration tests passed 9/9, unit tests passed 3/3,
doctests passed 7/7, and the schema example, strict Clippy, and strict rustdoc
passed. Existing tests rejected only permits whose source generation differed.
