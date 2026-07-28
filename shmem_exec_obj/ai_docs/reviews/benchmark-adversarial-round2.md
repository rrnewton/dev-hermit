# Benchmark adversarial review, round 2

Date: 2026-07-28

Reviewed implementation: `dbacd5f`, `bfde1d0`; evidence `6cc0414`

Reviewer role: `[adversarial-reviewer agent, gpt-5.6-sol]`

Verdict: **REJECT**

## Major findings

1. Source provenance omitted real compiler inputs, including `README.md` and
   `docs/injection.md`, which are included into Rust modules and appeared in
   compiler dependency metadata. Endpoint-only before/after checks could also
   miss a live-tree change followed by restoration during compilation.
2. Completion did not bind the full bundle. The runner verified `pod.bin` and
   selected binaries/provenance, but not the retained ELF, object, SDK rlib,
   manifest, or dependency files. A live probe changed `pod.elf` after compiler
   manifest creation; the runner still exited zero and published
   `complete=true` even though the manifest and actual ELF digests differed.
   Many environment fields and bundle paths also accepted forged values.
3. The retained harness could publish canonical completion in an existing
   directory containing only a stale `runner-owner`. It then claimed a bundled
   artifact path even though the artifact was external and absent from the
   directory.

## Minor findings

- Release integration ran benchmark smoke only with the current toolchain.
- Direct current and Rust 1.85 rustfmt checks failed, contrary to recorded
  evidence.

## Independent evidence

Current smoke passed 22 rows, a two-sample/four-worker run passed 44 rows, and
Rust 1.85 smoke passed 22 rows. Exact matrix/rate/CSV checks, existing-output
rejection, owned cleanup after a forced compiler failure, numeric bounds, Bash
syntax, ShellCheck, and package exclusion otherwise passed.

The required correction is to build solely from a retained and verified source
snapshot, inventory and rehash every completed artifact, bind the entire
environment record, and reserve canonical completion for the runner through an
exact owner-token handshake.
