#!/bin/bash
# Boxed per-node peak-RSS + CPU-seconds measurement for the portable DAG,
# at a PINNED inner job count. Produces the cgroup-RECORDED evidence needed to
# set hard_mem_max_bytes = rss_baseline + per_worker*pinned_jobs + headroom
# (task: memory-caps-must-scale-with-job-count-not-be-constants).
#
# Method (recorded with the artifact per the "in-file derivation" requirement):
#   * PINNED_JOBS=8. Rationale: results-real-build.csv (2026-08-03, devbig014,
#     cgroup memory.peak) shows clean-DEBUG `cargo build -p hermit` peak RSS
#     saturates by j8 (2.53 GiB@j1 -> 3.24@j8 -> FLAT 3.2-3.3 GiB j8..j316:
#     concurrent rustc caps at the crate-DAG width ~27). j8 is also the value
#     hermit-ptw validated for third-party build.dbi_release (522 MiB/proc x8 +
#     22.5% headroom = 6.20 GiB peak, PASS, 47/47).
#   * Each node boxed in its OWN systemd --user transient scope with
#     MemoryAccounting=1; "Memory peak" from the --wait summary == cgroup
#     memory.peak == cgroup-RECORDED (NOT sampled). CPU-seconds == the invariant
#     that a cpu_timeout should key on (wall collapses ~6x j1->j64; CPU-s ~flat).
#   * No inner mem cap during measurement, so a cap never truncates the peak we
#     are trying to observe.
#   * build.workspace runs first and doubles as the --features third-party-backends
#     (DynamoRIO) feasibility probe. If it fails (sandbox lacks DynamoRIO), the
#     feature nodes are marked UNMEASURABLE-IN-SANDBOX (need a runner-equipped
#     host) and Rust-only nodes are still measured COLD (a safe upper bound).
set -u
WT=/home/newton/work/dev-hermit/worktrees/lander/hermit
OUT=/home/newton/work/dev-hermit/experiments/dag-mem-caps-pinned-jobs_20260804
CSV=$OUT/results.csv
LOG=$OUT/measure.log
J=8
export CARGO_BUILD_JOBS=$J THIRD_PARTY_BUILD_JOBS=$J

echo "node,feature,target_state,result,memory_peak,cpu_time,wall_s" > "$CSV"

run_node() {
  local name="$1" target_state="$2" feature="$3" cmd="$4"
  local unit="mc-${name//./-}"
  local nlog="$OUT/node-${name//./-}.log"
  echo "=== $(date -u +%H:%M:%S) START $name (target=$target_state feature=$feature) ===" | tee -a "$LOG"
  local t0=$SECONDS
  systemctl --user reset-failed "$unit" 2>/dev/null
  systemd-run --user --unit="$unit" -p MemoryAccounting=1 \
    --working-directory="$WT" \
    --setenv=HOME="$HOME" --setenv=PATH="$PATH" \
    --setenv=CARGO_BUILD_JOBS=$J --setenv=THIRD_PARTY_BUILD_JOBS=$J \
    --setenv=CARGO_TARGET_DIR="$TDIR" --wait \
    /bin/bash -c "exec with-proxy $cmd" \
    > "$nlog" 2>&1
  local rc=$?
  local wall=$((SECONDS - t0))
  local peak cpu
  peak=$(grep -oiE 'Memory peak: [0-9.]+[KMGT]?' "$nlog" | tail -1 | sed 's/Memory peak: //I')
  cpu=$(grep -oiE 'CPU time consumed: [0-9a-z. ]+' "$nlog" | tail -1 | sed 's/CPU time consumed: //I' | tr -d ' ')
  local result=PASS; [ $rc -ne 0 ] && result="FAIL(rc=$rc)"
  echo "$name,$feature,$target_state,$result,${peak:-NA},${cpu:-NA},$wall" | tee -a "$CSV"
  echo "=== $(date -u +%H:%M:%S) END   $name -> $result peak=${peak:-NA} cpu=${cpu:-NA} wall=${wall}s ===" | tee -a "$LOG"
  systemctl --user reset-failed "$unit" 2>/dev/null
  return $rc
}

# ---- Shared warm target for feature nodes; built once. ----
TDIR=$(mktemp -d /tmp/mc-target-XXXX)
echo "shared warm target: $TDIR" | tee -a "$LOG"

# 1. build.workspace  (feature) -- also the DynamoRIO feasibility probe
run_node "build.workspace" warm-shared third-party \
  "cargo build --workspace --features third-party-backends"
WS_OK=$?

if [ $WS_OK -eq 0 ]; then
  # Feature build works here: measure feature + rust-only nodes warm in shared target.
  run_node "build.dbi_release" warm-shared third-party \
    "cargo build --release --locked -p hermit --features third-party-backends -p detcore-dbi -p hermit-install"
  run_node "build.sabre_release" warm-shared third-party \
    "cargo build --release --locked -p detcore-sabre && cargo build --release --locked -p hermit-install"
  run_node "doc.doctests" warm-shared third-party \
    "cargo test --workspace --features third-party-backends --doc"
  run_node "test.hermit_unit" warm-shared third-party \
    "cargo test -p hermit --features third-party-backends --lib --bins -- --test-threads=1"
  run_node "lint.clippy" warm-shared rust-only \
    "cargo clippy --workspace --all-targets -- -D warnings"
  run_node "doc.rustdoc" warm-shared rust-only \
    "cargo doc --workspace --no-deps"
  run_node "test.regular_crates" warm-shared rust-only \
    "cargo nextest run --workspace --exclude detcore --exclude hermit --exclude hermetic_infra_hermit_flaky-tests"
  run_node "build.flaky_harnesses" warm-shared rust-only \
    "cargo test -p hermetic_infra_hermit_flaky-tests --no-run"
  run_node "test.detcore_unit" warm-shared rust-only \
    "cargo test -p detcore --lib --bins"
else
  echo "build.workspace FAILED (third-party/DynamoRIO not buildable in sandbox)." | tee -a "$LOG"
  echo "Feature nodes marked UNMEASURABLE-IN-SANDBOX; measuring Rust-only nodes COLD (upper bound)." | tee -a "$LOG"
  for fn in build.dbi_release build.sabre_release doc.doctests test.hermit_unit; do
    echo "$fn,third-party,n/a,UNMEASURABLE-IN-SANDBOX,NA,NA,0" | tee -a "$CSV"
  done
  # Rust-only nodes: fresh COLD target each (no feature => no DynamoRIO).
  for spec in \
    "lint.clippy|cargo clippy --workspace --all-targets -- -D warnings" \
    "doc.rustdoc|cargo doc --workspace --no-deps" \
    "test.regular_crates|cargo nextest run --workspace --exclude detcore --exclude hermit --exclude hermetic_infra_hermit_flaky-tests" \
    "build.flaky_harnesses|cargo test -p hermetic_infra_hermit_flaky-tests --no-run" \
    "test.detcore_unit|cargo test -p detcore --lib --bins" ; do
    name="${spec%%|*}"; cmd="${spec#*|}"
    TDIR=$(mktemp -d /tmp/mc-target-XXXX)
    run_node "$name" cold-fresh rust-only "$cmd"
  done
fi

echo "=== $(date -u +%H:%M:%S) MEASUREMENT COMPLETE ===" | tee -a "$LOG"
