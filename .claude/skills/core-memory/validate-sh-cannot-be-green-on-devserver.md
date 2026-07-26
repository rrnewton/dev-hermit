---
name: core-memory-validate-sh-cannot-be-green-on-devserver
description: "./validate.sh never exits 0 on this devserver because baseline main fails host-sensitive detcore tests (CORE-MEMORY mirror of memory/validate-sh-cannot-be-green-on-devserver.md)"
---

# CORE-MEMORY: validate-sh-cannot-be-green-on-devserver

<!-- GENERATED MIRROR of core memory `validate-sh-cannot-be-green-on-devserver`. Source of truth is the memory
     file `validate-sh-cannot-be-green-on-devserver.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: validate-sh-cannot-be-green-on-devserver.md) -->
The prescribed local-landing gate is: run `./validate.sh` on a PR's rebased SHA,
and if green apply the `locally-validated` label (the legitimate substitute for
green CI), then merge. **Do NOT `--admin`-merge over red self-hosted CI**, and
never apply `human-approved` (user-only).

**Hang fix LANDED (PR #269, merged 2026-07-24, on main via commit 26cd773):**
`validate.sh` no longer hangs indefinitely — it now enforces configurable
per-gate process-tree timeouts (`GATE_TIMEOUT_SECONDS`, `TIMEOUT_KILL_GRACE_SECONDS`)
and adds `--verbose` (command/PID/heartbeat/live output, `VALIDATE_VERBOSE_INTERVAL_SECONDS`).
So the old "80-minute hang" is bounded now. It still cannot EXIT 0 (see below).

**Trap:** full `./validate.sh` CANNOT exit 0 on the devserver (devbig030), for
reasons NOT attributable to any PR:
- baseline `origin/main` itself fails `cargo test -p detcore --test tests_misc ::
  futex_wait_bitset_timeout_is_absolute_and_removes_waiter` ("Guest exited with
  non-zero status") — a host PMU/timing limitation (AGENTS.md: report the host
  limitation, don't attribute to the PR). Verified identical on a clean
  `origin/main` checkout. Same class as the RDRAND/RDSEED `tests_misc` failures.
- heavy concurrent-agent load (many parallel `cargo`/`nextest`, "Blocking waiting
  for file lock on build directory") stalls the full suite for many minutes.
- `rr` syscall suite is SKIPPED unless `third-party/rr` submodule is initialized.

**How to apply:** validate every check the PR can actually affect (`cargo build
--workspace`, `cargo fmt --all -- --check`, `cargo clippy --all-targets -D
warnings`, `cargo test -p <crate> --no-run`, the hermit smoke/determinism/verify
checks, fast stress) and PROVE any residual failure is baseline-environmental by
running the same failing test on clean `origin/main`. Then apply
`locally-validated` with a comment listing exactly what passed and the documented
host exception. Prefer merging on REAL "Regular tests (GitHub-hosted)" green
(force-push triggers it) rather than `--admin`. Seed a warm `target/` into a slot
with `cp -a --reflink=auto main/hermit/target <slot>/` — build drops from cold to
~2-14s. Relates to [[self-hosted-ci-sigsegv-blocks-all-prs]].
<!-- END CORE-MEMORY-MIRROR -->
