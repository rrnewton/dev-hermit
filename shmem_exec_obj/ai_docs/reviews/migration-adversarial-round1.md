# Migration adversarial review, round 1

Date: 2026-07-28

Reviewed implementation lineage: `476524d`

Reviewer role: `[adversarial-reviewer agent, gpt-5.6-sol]`

Verdict: **REJECT**

## Major finding

The successful target-ready examples did not satisfy their own freeze
contract. The allocator example retained `target_region`, and the typed mapping
test retained `target_owner`, after moving a lightweight wrapper into
`mark_target_ready`. Safe code could therefore allocate, drain, mutate, or
close the target between target-ready validation and the commit CAS. The
backing capability needed to own those mutation authorities through commit.

## Minor findings

- The admission example claimed the gate was the only source path while
  retaining direct source region and descriptor access.
- Crash tests covered `Copying` but not `TargetReady` or survival of an
  independently held recovery handle after a post-commit migrator death.
- The stated strict Clippy gate failed on migration and allocator lints.

## Reproduced evidence

Focused debug/release migration tests passed 7/7; migration unit tests passed
2/2. The Rust 1.85 package suite, example, all-target/all-feature and
no-default checks, formatting, and rustdoc otherwise passed.

The protocol's exact identity binding, monotonic/nonreuse caveats,
process-crash scope, release/acquire commit, reclamation CAS, different-address
behavior, and non-GC policy were coherent, but the release examples did not
demonstrate the required capability ownership.
