---
title: Build reproducible IPC and synchronization benchmarks
status: in_progress
priority: 2
issue_type: task
assignee: devbig030
depends_on:
  pod-4: blocks
  pod-9: blocks
  pod-7: blocks
  pod-17: blocks
  pod-1: parent-child
  pod-5: blocks
  pod-8: blocks
  pod-6: blocks
created_at: 2026-07-28T03:39:16.035270684+00:00
updated_at: 2026-07-28T11:13:32.429986187+00:00
claimed_at: 2026-07-28T10:35:38.427417925+00:00
claimed_until: 2026-07-28T18:35:38.227858288+00:00
---

# Description

Measure direct calls, pod calls, syscall/IPC baselines, spin/futex/recoverable locks, coarse/fine/atomic counters, SNZI topologies, and allocator/collection operations.

# Acceptance Criteria

Benchmark harness emits machine-readable results with environment metadata and validates totals; docs distinguish latency/throughput tradeoffs without making unsupported performance claims.

# Notes

IMPLEMENTED, awaiting adversarial review and coordinator closure. The repository-only suite lives in v2/benchmarks/{harness.rs,README.md}, v2/scripts/run-benchmarks.sh, and v2/docs/benchmarks.md. It uses the real authenticated PodImage, raw gettid, UnixStream round trips, forked MAP_SHARED process workers for ProcessSpinMutex/ProcessFutexMutex and coarse/fine/atomic tables, hot/sharded Snzi/CloseableSnzi/Csnzi, and relocatable SharedBox/SharedVec operations. JSONL/CSV rows distinguish latency from throughput and validate exact totals; environment JSON records source/dirty state, workspace and normalized harness lock hashes, toolchain/host/artifact, observed affinity, and tightest inherited cgroup CPU/memory/swap limits with source paths. Implementation was captured by parent-main auto-sync commits 4f0eca6 and 945931e (945931e contains the final harness hardening; later main commits contain it unchanged).

Validation: `./scripts/run-benchmarks.sh --smoke --output /tmp/shmem-pod-bench-945931e-smoke` PASS, 22/22 verified rows, source revision 89e36e29931a439e5778ac7960708e495b08d1bc and source_dirty=false; default `./scripts/run-benchmarks.sh --output /tmp/shmem-pod-bench-default-1087150 --timeout 600` PASS, 110 verified rows (5 samples, 8 workers); reduced multi-sample PASS, 44 rows (2 samples, 4 workers); `RUSTUP_TOOLCHAIN=1.85.0 ./scripts/run-benchmarks.sh --smoke ...` PASS, 22 rows; temporary locked harness `cargo clippy --release -- -D warnings` PASS; `shellcheck scripts/run-benchmarks.sh`, bash syntax, rustfmt, and git diff check PASS. No performance ranking is claimed from these host observations.

Integration follow-up owned by the coordinator: Cargo.toml currently packages /docs/**, so add a negative include for docs/benchmarks.md (the guide is explicitly private-repository-only because poc/ and scripts/ are not shipped), and add the bounded smoke command to the release gate. The suite does not invent a recoverable-lock row: both pod mutexes intentionally lack owner-death recovery, which the guide states explicitly.

Final completion-marker follow-up: ffffc49369c9932fe06d96b54b4b068d80b24cff writes environment.json only after all result writers flush, records complete=true and the exact row count, and makes the runner verify both. `./scripts/run-benchmarks.sh --smoke --output /tmp/shmem-pod-bench-complete-marker-164175` PASS (22 rows); shellcheck and bash syntax PASS.
