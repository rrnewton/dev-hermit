---
name: shmem-pod-blind-review
description: Adversarially audit a packaged shmem-pod release as a source-blind crates.io consumer using generated rustdoc only. Use after all implementation beads are complete and repeat with fresh reviewers until no blocking or major findings remain.
---

# Shmem-Pod Blind Release Review

## Purpose

This protocol tests whether `shmem-pod` is safe, understandable, and useful as
an independent published library. Passing unit tests in the source tree is not
enough. A reviewer must be able to build a substantial consumer from packaged
artifacts and generated documentation without reading implementation source or
project history.

## Coordinator Preparation

1. Confirm every implementation child of release epic `pod-1` is closed and
   commit-bound. Leave the blind-audit bead open.
2. From `v2/`, run the complete release gate and package both crates. Publish
   neither crate during the audit.
3. Generate rustdoc with all public features on the declared MSRV.
4. Create a fresh temporary review directory containing only:
   - the two `.crate` package artifacts and their SHA-256 hashes;
   - generated rustdoc;
   - the exact dependency stanza a crates.io user would write;
   - a short statement of the supported OS/architecture envelope.
5. Do not provide the repository README, examples, source paths, prior review
   reports, architecture notes, or an implementation summary unless those are
   embedded in the packaged rustdoc itself.

Cargo must eventually unpack Rust source to compile a dependency. The reviewer
may allow Cargo to do so but must not open, search, quote, or reason from that
source. Record the reviewer's shell history or command log so this restriction
is auditable.

## Reviewer Isolation

Spawn a fresh agent with no conversation context (`fork_turns="none"`). Give it
write access only to its temporary consumer directory and whatever build cache
is required. The prompt must state that reading the project checkout, package
source, examples, git history, `ai_docs/`, or previous reviews invalidates the
round.

Do not tell the reviewer which APIs to praise or which previous bugs were
fixed. Give it goals, not implementation hints.

## Required Consumer Exercise

The reviewer must design its own nontrivial program and, using rustdoc only:

1. Define and validate default-layout shared state without relying on
   `#[repr(C)]` unless the public ABI actually requires it.
2. Initialize, attach to, admit users into, drain, and tear down a typed shared
   mapping from independent exec'd processes mapped at different addresses.
3. Allocate, grow, read, mutate, and free relocatable shared collections.
4. Exercise atomic, sleeping-lock, timeout, owner-death, poisoning, and fencing
   behavior without assuming that a timeout grants ownership.
5. Use closeable SNZI admission as a reclamation barrier and test a killed
   participant.
6. Declare and call a generated executable-pod method API, then prove that an
   incompatible client or image is rejected before execution.
7. Use one documented connector path with an otherwise unaware guest.
8. Repeat contention and failure runs enough times to expose hangs or flaky
   totals. Every subprocess test must have an external timeout and cleanup.

If a capability is explicitly outside the supported release envelope, the
reviewer must evaluate whether the boundary and failure mode are clear instead
of inventing support.

## Documentation Audit

For every public unsafe function or unsafe trait, verify that rustdoc states:

- caller obligations;
- validity and initialization requirements;
- cross-process synchronization assumptions;
- mapping lifetime and fork/exec constraints;
- crash and owner-death behavior;
- whether persisted pointers or strict provenance are involved.

Also check feature flags, platform availability, error actionability, package
metadata, docs.rs links, and the absence of private-project terminology.

## Verdict

Classify findings as:

- **Blocking**: unsoundness, corruption, security boundary failure, hang,
  incompatible image execution, or advertised workflow cannot be completed.
- **Major**: core workflow is materially confusing, requires undocumented
  unsafe reconstruction, or lacks essential failure handling.
- **Minor**: localized naming, discoverability, or documentation defect with a
  clear workaround.
- **Observation**: non-actionable tradeoff or explicitly unsupported feature.

The round passes only with zero blocking and zero major findings. The reviewer
must not waive a finding because this is experimental software.

## Durable Report And Iteration

Save each report as `ai_docs/reviews/blind-rustdoc-round<N>.md` with:

- reviewer identity/model and isolation statement;
- package hashes and implementation commit;
- every command and test result;
- the original consumer design;
- findings ordered by severity with rustdoc links;
- residual risks and final pass/fail verdict.

Create a minibead for every blocking or major finding, make the next audit bead
depend on those fixes, and use a new no-context reviewer for the next round.
Close the final blind-audit bead only after a passing report is committed.
