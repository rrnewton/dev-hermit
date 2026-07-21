# Active worktree slots

This is the host-local source of truth for live work under
`/home/newton/work/dev-hermit/worktrees`. Add one row before the first edit in
a slot. Remove it only after the final state is recorded in `ARCHIVED.md` and
the slot is clean enough to park or reclaim.

At most twelve active slot rows may exist. New work must use a canonical
top-level path from `worktrees/slot01` through `worktrees/slot12`. No more
than five additional clean slots may be parked outside this registry. The
legacy paths below predate this policy; do not move them while their agents are
active, and remove them instead of parking them when their listed tasks finish.

| Slot | Owner / task | Hermit branch | Reverie branch | Started | Purpose |
| --- | --- | --- | --- | --- | --- |
| `worktrees/slot01` | `hermit-sabre` / `impl-sabre-runtime-stabilize` | `impl-test-port-batch-a` (pre-existing child) | `impl-sabre-runtime-stabilize-slot01` | 2026-07-21 | Stabilize the in-process SaBRe runtime. |
| `worktrees/slot02` | `hermit-kvm` / `impl-pr-schedule-search-ci` | `impl-schedule-search-ci-slot02` | `detached:9669339` | 2026-07-21 | Publish PR 7 and resolve its CI overlap with current main. |
| `worktrees/slot-stress` | `hermit-clippy` / `research-hermit-stress-testing` | `research-hermit-stress-testing` | `n/a (legacy Hermit-only)` | 2026-07-21 | Run the Hermit chaos/stress research matrix. |
| `worktrees/slot03` | `hermit-issues` / `impl-pr-ci-expansion` | `impl-pr-ci-expansion-slot03` | `detached:9669339` | 2026-07-21 | Repair CI dependency setup and monitor PR 8. |
| `worktrees/slot04` | `hermit-buck` / `impl-qemu-syscall-fixes` (Hermit); `hermit-fbfix` / `impl-amd-pmu-skid-optimize` (Reverie: `reverie-ptrace/src/perf.rs`, `reverie-ptrace/src/timer.rs`) | `detached:c3d7014` | `impl-amd-pmu-skid-optimize-slot04` | 2026-07-21 | Implement QEMU TCG syscall compatibility fixes and optimize AMD PMU skid in disjoint child repositories. |
