---
name: core-memory-good-hermit-binary-for-tests
description: "use hermit/target/debug/hermit for determinism tests; target-nondet-test-framework build hangs on make (CORE-MEMORY mirror of memory/good-hermit-binary-for-tests.md)"
---

# CORE-MEMORY: good-hermit-binary-for-tests

<!-- GENERATED MIRROR of core memory `good-hermit-binary-for-tests`. Source of truth is the memory
     file `good-hermit-binary-for-tests.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: good-hermit-binary-for-tests.md) -->
For ad-hoc determinism testing on this workspace, use **`hermit/target/debug/hermit`** (the maintained main checkout, rev 15fb99f). Confirmed working for the full P3 determinism test battery (2026-07-22/23).

AVOID `target-nondet-test-framework/debug/hermit` (built 06:14) and `hermit/target/release/hermit` (built 10:10) for anything involving **make**: both HANG on make's per-recipe child spawn (even a trivial single `true` recipe hangs, in both default and `--strict` mode). Those same stale binaries pass every other coreutils/pipeline test — the hang is specific to make's vfork/posix_spawn recipe-launch path. The current `hermit/target/debug/hermit` (22:55) runs make fine and passes `--strict --verify` 3/3.

Lesson: when a workload hangs, re-test on `hermit/target/debug/hermit` before concluding it's a real hermit gap — it may be a stale build. See [[strict-mode-frontier-regresses-real-workloads]], [[missing-strict-syscalls-landed-main-pr207]].
<!-- END CORE-MEMORY-MIRROR -->
