#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "$0")" && pwd)
DEV_ROOT=$(cd -- "$ROOT/../../.." && pwd)
REVERIE="$DEV_ROOT/worktrees/slot330/reverie"
GVISOR="$DEV_ROOT/experiments/gvisor"
MANIFEST="$ROOT/Cargo.toml"
CRITERION_HOME=${CRITERION_HOME:-/tmp/criterion-syscall-v3}
RESULTS_DIRECTORY=${1:-$ROOT/results/latest}
: "${SYSCALL_BENCH_CPU:?set SYSCALL_BENCH_CPU to an idle logical CPU}"

export CRITERION_HOME
export RUNSC_BIN=${RUNSC_BIN:-$GVISOR/bazel-bin/runsc/runsc_/runsc}
export COUNTER2=${COUNTER2:-$REVERIE/target/release/counter2}
export DRRUN=${DRRUN:-$($REVERIE/target/release/reverie-dbi-dynamorio-path drrun)}
export DBI_CLIENT=${DBI_CLIENT:-$REVERIE/target/release/reverie-dbi-native/libreverie_dbi_client.so}
export KVM_COUNTER=${KVM_COUNTER:-$REVERIE/target/release/reverie-kvm-counter2}
export SABRE_RUNNER=${SABRE_RUNNER:-$REVERIE/target/release/reverie-sabre-strace}
export SABRE_PLUGIN=${SABRE_PLUGIN:-$REVERIE/target/release/libreverie_sabre_strace_plugin.so}
export SABRE=${SABRE:-$REVERIE/target/sabre-v3/sabre}
export DEV_HERMIT_ROOT=$DEV_ROOT
export SYSCALL_BENCH_REQUIRE_ALL=true
export SYSCALL_BENCH_CAPABILITIES=$CRITERION_HOME/capabilities.tsv
export SYSCALL_BENCH_HELPER_OVERRIDE=$CRITERION_HOME/syscall-server-v3

mkdir -p "$CRITERION_HOME" "$RESULTS_DIRECTORY"
cc -O3 -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror -fno-plt \
  "$ROOT/fixtures/syscall_server.c" -o "$SYSCALL_BENCH_HELPER_OVERRIDE"

for artifact in \
  "$RUNSC_BIN" "$COUNTER2" "$DRRUN" "$DBI_CLIENT" "$KVM_COUNTER" \
  "$SABRE_RUNNER" "$SABRE_PLUGIN" "$SABRE" "$SYSCALL_BENCH_HELPER_OVERRIDE"
do
  [[ -e $artifact ]] || { echo "missing artifact: $artifact" >&2; exit 2; }
done

"$ROOT/preflight.sh" "$CRITERION_HOME/preflight.tsv"
with-proxy cargo bench --locked --manifest-path "$MANIFEST" --bench marginal_syscalls
with-proxy cargo run --locked --release --manifest-path "$MANIFEST" --bin summarize -- \
  "$CRITERION_HOME" "$RESULTS_DIRECTORY"

cp "$CRITERION_HOME/preflight.tsv" "$RESULTS_DIRECTORY/preflight.tsv"
cp "$CRITERION_HOME/idle-gates.tsv" "$RESULTS_DIRECTORY/idle-gates.tsv"
cp "$CRITERION_HOME/backend-order.tsv" "$RESULTS_DIRECTORY/backend-order.tsv"
mkdir -p "$RESULTS_DIRECTORY/criterion-raw"
(
  cd "$CRITERION_HOME"
  find . -type f \( \
    -name benchmark.json -o -name estimates.json -o -name sample.json -o -name tukey.json \
  \) -print0 | tar --null -T - -cf -
) | tar -C "$RESULTS_DIRECTORY/criterion-raw" -xf -

{
  printf 'field\tvalue\n'
  printf 'timestamp_utc\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'hostname\t%s\n' "$(hostname -f)"
  printf 'kernel\t%s\n' "$(uname -srvm)"
  printf 'cpu_model\t%s\n' "$(lscpu | awk -F: '$1 == "Model name" {sub(/^[[:space:]]+/, "", $2); print $2; exit}')"
  printf 'logical_cpus\t%s\n' "$(awk '$1 ~ /^cpu[0-9]+$/ {count++} END {print count}' /proc/stat)"
  printf 'benchmark_cpu\t%s\n' "$SYSCALL_BENCH_CPU"
  printf 'smt_siblings\t%s\n' "$(cat /sys/devices/system/cpu/cpu${SYSCALL_BENCH_CPU}/topology/thread_siblings_list)"
  printf 'sample_size\t%s\n' "${SYSCALL_BENCH_SAMPLE_SIZE:-20}"
  printf 'warmup_seconds\t%s\n' "${SYSCALL_BENCH_WARMUP_SECS:-2}"
  printf 'measurement_seconds\t%s\n' "${SYSCALL_BENCH_MEASUREMENT_SECS:-5}"
  printf 'bootstrap_resamples\t%s\n' "${SYSCALL_BENCH_RESAMPLES:-50000}"
  printf 'confidence_level\t0.95\n'
  printf 'idle_gate_seconds\t%s\n' "${SYSCALL_BENCH_IDLE_GATE_SECS:-10}"
  printf 'minimum_cpu_idle_percent\t%s\n' "${SYSCALL_BENCH_MIN_CPU_IDLE_PERCENT:-95}"
  printf 'maximum_load_per_logical_cpu\t%s\n' "${SYSCALL_BENCH_MAX_LOAD_PER_CPU:-0.25}"
  printf 'order_seed\t%s\n' "${SYSCALL_BENCH_ORDER_SEED:-20260726}"
  printf 'parent_head\t%s\n' "$(git -C "$DEV_ROOT" rev-parse HEAD)"
  printf 'parent_commit_depth\t%s\n' "$(git -C "$DEV_ROOT" rev-list --count HEAD)"
  printf 'harness_source_sha256\t%s\n' "$(sha256sum "$ROOT/benches/marginal_syscalls.rs" | awk '{print $1}')"
  printf 'helper_source_sha256\t%s\n' "$(sha256sum "$ROOT/fixtures/syscall_server.c" | awk '{print $1}')"
  printf 'reverie_head\t%s\n' "$(git -C "$REVERIE" rev-parse HEAD)"
  printf 'reverie_commit_depth\t%s\n' "$(git -C "$REVERIE" rev-list --count HEAD)"
  printf 'reverie_diff_sha256\t%s\n' "$(git -C "$REVERIE" diff --binary | sha256sum | awk '{print $1}')"
  printf 'gvisor_head\t%s\n' "$(git -C "$GVISOR" rev-parse HEAD)"
  printf 'gvisor_commit_depth\t%s\n' "$(git -C "$GVISOR" rev-list --count HEAD)"
  printf 'gvisor_diff_sha256\t%s\n' "$(git -C "$GVISOR" diff --binary | sha256sum | awk '{print $1}')"
  printf 'rustc\t%s\n' "$(rustc --version)"
  printf 'cargo\t%s\n' "$(cargo --version)"
} >"$RESULTS_DIRECTORY/metadata.tsv"

{
  printf 'artifact\tsha256\n'
  for artifact in \
    "$SYSCALL_BENCH_HELPER_OVERRIDE" "$RUNSC_BIN" "$COUNTER2" "$DRRUN" \
    "$DBI_CLIENT" "$KVM_COUNTER" "$SABRE_RUNNER" "$SABRE_PLUGIN" "$SABRE" \
    "$REVERIE/reverie-examples/counter2_tool.rs" \
    "$GVISOR/pkg/sentry/platform/counter2/counter2.go"
  do
    printf '%s\t%s\n' "$artifact" "$(sha256sum "$artifact" | awk '{print $1}')"
  done
} >"$RESULTS_DIRECTORY/artifact-sha256.tsv"

printf 'Criterion HTML: %s\n' "$CRITERION_HOME/report/index.html"
printf 'Summary: %s\n' "$RESULTS_DIRECTORY/SUMMARY.md"
