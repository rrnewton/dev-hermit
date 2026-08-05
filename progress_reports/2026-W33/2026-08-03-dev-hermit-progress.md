# Progress - Monday, August 3, 2026

**Headline:** SaBRe verification began consuming real syscall DETLOGs, the backend-parity backlog moved onto the shared E2E manifest, and validation records became tied to an exact commit and clean tree.

## What shipped
- **Made SaBRe verification consume syscall DETLOG evidence.** SaBRe now forwards syscall DETLOG records into Hermit, requires them during `--verify`, and consumes them in the comparison path. The timed-progress busy-wait cells were honestly gated because SaBRe does not yet determinize that polling pattern.
- **Moved backend parity onto the shared manifest.** A 19-contract group and additional legacy rows were retargeted from `matrix.tsv` into schema-v2 E2E manifests. Test footprints are derived from Cargo metadata, checked for freshness, and used by affected-test selection.
- **Bound validation to source identity.** `validate.sh` now records the commit, refuses dirty trees, and preserves local-validation evidence when labels change. The DAG runner resolver prefers tracked source over an untracked prebuilt binary.
- **Prepared the crates for a real 0.2 line.** Hermit workspace crates moved from placeholder `0.0.0` versions to the 0.2 floor, with descriptions, lockfiles, and private internal crates reconciled.
- **Fixed a main-branch compile break.** A Detcore happens-before change referenced an out-of-scope `config`; the hotfix replaced it with `guest.config()` and restored workspace compilation.

## What it means
SaBRe passes can now be backed by the same DETLOG concept used by the ptrace reference, and a validation result names the exact source tree it tested. Those are prerequisites for trusting either parity or landing evidence.

## What's stuck
The shared manifest migration was not complete, and many old validation records predated commit anchoring. Rebasing a PR still changed its head SHA, so any receipt earned before the final push could not authorize the new head.
