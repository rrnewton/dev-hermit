#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/run-benchmarks.sh [OPTIONS]

Build the real executable pod image and run the reproducible benchmark suite.

Options:
  --smoke              Run every workload once with tiny bounded counts.
  --output DIR         Result directory (default: target/benchmark-results/RUN).
  --warmup N           Untimed operations per worker (default: 5000; smoke: 8).
  --iterations N       Timed operations per worker (default: 50000; smoke: 64).
  --samples N          Repeated timing samples (default: 5; smoke: 1).
  --workers N          Contending threads/processes (default: min(CPUs, 8)).
  --timeout SECONDS    Per build/run deadline (default: 1800; smoke: 300).
  -h, --help           Show this help.

Outputs:
  environment.json     Completion marker and exact run provenance.
  results.jsonl        One verified timing record per sample.
  results.csv          The same records in tabular form.
  artifacts/           Immutable pod compiler outputs for this run.
  bin/                 Exact compiler and benchmark harness executables.
  provenance/          Exact runner, harness source, manifests, and lockfiles.

Counts are explicit in environment.json. Results describe only this host/run;
the suite deliberately makes no portable performance claim.
EOF
}

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

mode=standard
output_dir=
output_claimed=0
run_succeeded=0
temporary=
runner_owner_token=
warmup=
iterations=
samples=
workers=
run_timeout=
source_paths=(
  Cargo.toml
  Cargo.lock
  src
  crates/macros
  poc/api
  poc/code
  poc/compiler
  poc/runtime
  benchmarks/harness.rs
  scripts/run-benchmarks.sh
)

cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n $temporary && -d $temporary ]]; then
    rm -rf -- "$temporary"
  fi
  if ((output_claimed == 1 && run_succeeded == 0)) && [[ -n $output_dir && -d $output_dir ]]; then
    local owner_path="$output_dir/runner-owner"
    if [[ -n $runner_owner_token && -f $owner_path && $(<"$owner_path") == "$runner_owner_token" ]]; then
      rm -rf -- "$output_dir"
    else
      echo "refusing to clean output directory without this run's owner token: $output_dir" >&2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT

environment_value() {
  local name=$1
  if [[ -v $name ]]; then
    printf '%s' "${!name}"
  else
    printf '<unset>'
  fi
}

status_fingerprint() {
  git status --porcelain=v2 --untracked-files=all -- "${source_paths[@]}" |
    sha256sum | awk '{print $1}'
}

source_tree_fingerprint() {
  while IFS= read -r -d '' path; do
    local digest=missing
    if [[ -f $path || -L $path ]]; then
      digest=$(sha256sum -- "$path" | awk '{print $1}')
    fi
    printf '%s\0%s\0' "$path" "$digest"
  done < <(git ls-files -co --exclude-standard -z -- "${source_paths[@]}" | LC_ALL=C sort -z)
}

verify_sha256() {
  local path=$1
  local expected=$2
  local actual
  actual=$(sha256sum -- "$path" | awk '{print $1}')
  if [[ $actual != "$expected" ]]; then
    echo "benchmark bundle digest changed for $path" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    exit 1
  fi
}

verify_source_stable() {
  local revision status_sha256 tree_sha256
  revision=$(git rev-parse HEAD)
  status_sha256=$(status_fingerprint)
  tree_sha256=$(source_tree_fingerprint | sha256sum | awk '{print $1}')
  if [[ $revision != "$source_revision_before" ||
        $status_sha256 != "$source_status_sha256_before" ||
        $tree_sha256 != "$source_tree_sha256_before" ]]; then
    echo "benchmark source changed while the run was in progress" >&2
    echo "  revision: $source_revision_before -> $revision" >&2
    echo "  status:   $source_status_sha256_before -> $status_sha256" >&2
    echo "  tree:     $source_tree_sha256_before -> $tree_sha256" >&2
    exit 1
  fi
}

while (($#)); do
  case "$1" in
    --smoke)
      mode=smoke
      shift
      ;;
    --output | --warmup | --iterations | --samples | --workers | --timeout)
      (($# >= 2)) || { echo "$1 requires a value" >&2; usage >&2; exit 2; }
      option=$1
      value=$2
      shift 2
      case "$option" in
        --output) output_dir=$value ;;
        --warmup) warmup=$value ;;
        --iterations) iterations=$value ;;
        --samples) samples=$value ;;
        --workers) workers=$value ;;
        --timeout) run_timeout=$value ;;
      esac
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

for tool in awk cargo cat cp date diff dirname git jq mkdir mktemp mv nproc rm rustc sed sha256sum sort sync tail timeout uname wc; do
  command -v "$tool" >/dev/null || { echo "run-benchmarks.sh requires $tool" >&2; exit 1; }
done
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "the executable-image benchmark currently requires Linux x86-64" >&2
  exit 1
fi

available_cpus=$(nproc 2>/dev/null || echo 1)
if [[ ! $available_cpus =~ ^[1-9][0-9]*$ ]]; then
  available_cpus=1
fi
default_workers=$available_cpus
if ((default_workers > 8)); then
  default_workers=8
fi

if [[ $mode == smoke ]]; then
  warmup=${warmup:-8}
  iterations=${iterations:-64}
  samples=${samples:-1}
  workers=${workers:-$((default_workers < 2 ? default_workers : 2))}
  run_timeout=${run_timeout:-300}
else
  warmup=${warmup:-5000}
  iterations=${iterations:-50000}
  samples=${samples:-5}
  workers=${workers:-$default_workers}
  run_timeout=${run_timeout:-1800}
fi

require_uint() {
  local name=$1
  local value=$2
  if [[ ! $value =~ ^[0-9]+$ ]]; then
    echo "$name must be a nonnegative integer, got: $value" >&2
    exit 2
  fi
}
require_positive() {
  require_uint "$1" "$2"
  if [[ $(canonical_uint "$2") == 0 ]]; then
    echo "$1 must be nonzero" >&2
    exit 2
  fi
}

canonical_uint() {
  local value=$1
  while [[ ${#value} -gt 1 && ${value:0:1} == 0 ]]; do
    value=${value:1}
  done
  printf '%s' "$value"
}

require_at_most() {
  local name=$1
  local value=$2
  local maximum=$3
  if ((${#value} > ${#maximum})) ||
     [[ ${#value} -eq ${#maximum} && $value > $maximum ]]; then
    echo "$name must not exceed $maximum, got: $value" >&2
    exit 2
  fi
}

require_uint --warmup "$warmup"
require_positive --iterations "$iterations"
require_positive --samples "$samples"
require_positive --workers "$workers"
require_positive --timeout "$run_timeout"
warmup=$(canonical_uint "$warmup")
iterations=$(canonical_uint "$iterations")
samples=$(canonical_uint "$samples")
workers=$(canonical_uint "$workers")
run_timeout=$(canonical_uint "$run_timeout")
require_at_most --warmup "$warmup" 140737488355327
require_at_most --iterations "$iterations" 140737488355327
require_at_most --samples "$samples" 1000000
require_at_most --workers "$workers" 64
require_at_most --timeout "$run_timeout" 604800
max_rate_operations=9223372036
if ((iterations > max_rate_operations / workers ||
     iterations > max_rate_operations / 2)); then
  echo "--iterations is too large for exact rate validation with $workers workers" >&2
  exit 2
fi

target_dir=$(
  timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
    cargo metadata --locked --format-version 1 --no-deps |
    jq -er .target_directory
)
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
source_revision_before=$(git rev-parse HEAD)
source_status_sha256_before=$(status_fingerprint)
source_tree_sha256_before=$(source_tree_fingerprint | sha256sum | awk '{print $1}')
if [[ -n $(git status --porcelain --untracked-files=all -- "${source_paths[@]}") ]]; then
  source_dirty=1
else
  source_dirty=0
fi
if [[ -z $output_dir ]]; then
  output_dir="$target_dir/benchmark-results/$run_id"
fi
output_parent=$(dirname "$output_dir")
mkdir -p -- "$output_parent"
if ! mkdir -- "$output_dir"; then
  echo "--output must name a new directory: $output_dir" >&2
  exit 1
fi
output_claimed=1
output_dir=$(cd "$output_dir" && pwd -P)
runner_owner_token=$run_id
printf '%s\n' "$runner_owner_token" >"$output_dir/runner-owner"
artifact_dir="$output_dir/artifacts"
binary_dir="$output_dir/bin"
provenance_dir="$output_dir/provenance"
mkdir "$artifact_dir" "$binary_dir" "$provenance_dir"

cp scripts/run-benchmarks.sh "$provenance_dir/run-benchmarks.sh"
cp benchmarks/harness.rs "$provenance_dir/harness.rs"
cp Cargo.toml "$provenance_dir/workspace-Cargo.toml"
cp Cargo.lock "$provenance_dir/workspace-Cargo.lock"
runner_sha256=$(sha256sum "$provenance_dir/run-benchmarks.sh" | awk '{print $1}')
harness_source_sha256=$(sha256sum "$provenance_dir/harness.rs" | awk '{print $1}')
workspace_manifest_sha256=$(sha256sum "$provenance_dir/workspace-Cargo.toml" | awk '{print $1}')
workspace_lock_sha256=$(sha256sum "$provenance_dir/workspace-Cargo.lock" | awk '{print $1}')

temporary=$(mktemp -d)

echo "shmem-pod benchmark suite"
echo "  mode: $mode"
echo "  output: $output_dir"
echo "  warmup/iterations/samples/workers: $warmup/$iterations/$samples/$workers"
echo "  per-command timeout: ${run_timeout}s"

echo "building executable-pod compiler and runtime"
compiler_target="$temporary/compiler-target"
CARGO_TARGET_DIR="$compiler_target" \
  timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  cargo build --locked --release \
  -p shmem-pod-image-compiler \
  -p shmem-pod-runtime
compiler_build="$compiler_target/release/shmem-pod-image-compiler"
[[ -x $compiler_build ]] || { echo "compiler executable is missing: $compiler_build" >&2; exit 1; }
compiler="$binary_dir/shmem-pod-image-compiler"
cp "$compiler_build" "$compiler"
compiler_sha256=$(sha256sum "$compiler" | awk '{print $1}')

compiler_args=(
  --source "$root/poc/code/src/lib.rs"
  --sdk-manifest "$root/Cargo.toml"
  --sdk-source "$root/src/lib.rs"
  --sdk-rlib "$artifact_dir/libshmem_pod.rlib"
  --linker-script "$root/poc/code/pod.ld"
  --output "$artifact_dir/pod.bin"
  --object "$artifact_dir/pod.o"
  --elf "$artifact_dir/pod.elf"
  --manifest "$artifact_dir/pod.manifest"
)
if [[ -n ${POD_RUSTC:-} ]]; then
  compiler_args+=(--rustc "$POD_RUSTC")
fi
compiler_work="$temporary/compiler-work"
mkdir "$compiler_work"
(
  cd "$compiler_work"
  timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
    "$compiler" "${compiler_args[@]}"
)
artifact_sha256=$(sed -n 's/^artifact_sha256=//p' "$artifact_dir/pod.manifest")
if [[ ! $artifact_sha256 =~ ^[0-9a-f]{64}$ ]]; then
  echo "compiler manifest lacks a valid artifact SHA-256" >&2
  exit 1
fi
actual_artifact_sha256=$(sha256sum "$artifact_dir/pod.bin" | awk '{print $1}')
if [[ $actual_artifact_sha256 != "$artifact_sha256" ]]; then
  echo "compiler manifest artifact digest does not match pod.bin" >&2
  exit 1
fi

mkdir -p "$temporary/src"
cp "$provenance_dir/harness.rs" "$temporary/src/main.rs"
cp "$provenance_dir/workspace-Cargo.lock" "$temporary/Cargo.lock"
cat >"$temporary/Cargo.toml" <<EOF
[package]
name = "shmem-pod-benchmark-harness"
version = "0.0.0"
edition = "2024"
publish = false

[dependencies]
shmem-pod = { path = "$root", default-features = false, features = ["linux-futex"] }
shmem-pod-runtime = { path = "$root/poc/runtime" }

[workspace]

[profile.release]
opt-level = 3
debug = false
strip = "none"
debug-assertions = false
overflow-checks = false
lto = "thin"
panic = "abort"
incremental = false
codegen-units = 1
rpath = false
EOF

SHMEM_POD_BENCH_RUN_ID=$run_id
SHMEM_POD_BENCH_GIT_SHA=$source_revision_before
SHMEM_POD_BENCH_GIT_DIRTY=$source_dirty
SHMEM_POD_BENCH_SOURCE_STATUS_SHA256=$source_status_sha256_before
SHMEM_POD_BENCH_SOURCE_TREE_SHA256=$source_tree_sha256_before
SHMEM_POD_BENCH_LOCK_SHA256=$workspace_lock_sha256
SHMEM_POD_BENCH_WORKSPACE_MANIFEST_SHA256=$workspace_manifest_sha256
SHMEM_POD_BENCH_RUNNER_SHA256=$runner_sha256
SHMEM_POD_BENCH_HARNESS_SOURCE_SHA256=$harness_source_sha256
SHMEM_POD_BENCH_COMPILER_SHA256=$compiler_sha256
SHMEM_POD_BENCH_HOSTNAME=$(uname -n)
SHMEM_POD_BENCH_KERNEL=$(uname -srvmo)
cpu_model=$(awk -F: '/^model name/{sub(/^[[:space:]]+/, "", $2); print $2; exit}' /proc/cpuinfo)
SHMEM_POD_BENCH_CPU_MODEL=${cpu_model:-unknown}
rustc_executable=${RUSTC:-rustc}
SHMEM_POD_BENCH_RUSTC=$("$rustc_executable" --version --verbose)
SHMEM_POD_BENCH_CARGO=$(cargo --version --verbose)
SHMEM_POD_BENCH_RUSTFLAGS=$(environment_value RUSTFLAGS)
SHMEM_POD_BENCH_CARGO_ENCODED_RUSTFLAGS=$(environment_value CARGO_ENCODED_RUSTFLAGS)
SHMEM_POD_BENCH_RUSTC_OVERRIDE=$(environment_value RUSTC)
SHMEM_POD_BENCH_POD_RUSTC=$(environment_value POD_RUSTC)
SHMEM_POD_BENCH_RUSTC_WRAPPER=$(environment_value RUSTC_WRAPPER)
SHMEM_POD_BENCH_RUSTC_WORKSPACE_WRAPPER=$(environment_value RUSTC_WORKSPACE_WRAPPER)
SHMEM_POD_BENCH_CARGO_HOME=$(environment_value CARGO_HOME)
SHMEM_POD_BENCH_CARGO_BUILD_TARGET=$(environment_value CARGO_BUILD_TARGET)
if [[ -n ${POD_RUSTC:-} ]]; then
  SHMEM_POD_BENCH_POD_RUSTC_VERSION=$("$POD_RUSTC" --version --verbose)
else
  SHMEM_POD_BENCH_POD_RUSTC_VERSION='<unset>'
fi
SHMEM_POD_BENCH_WORKSPACE_PROFILE='release: opt-level=3, debug=false, strip=none, debug-assertions=false, overflow-checks=false, lto=thin, panic=abort, incremental=false, codegen-units=1, rpath=false'
SHMEM_POD_BENCH_HARNESS_PROFILE=$SHMEM_POD_BENCH_WORKSPACE_PROFILE
for profile_key in OPT_LEVEL DEBUG STRIP DEBUG_ASSERTIONS OVERFLOW_CHECKS LTO PANIC INCREMENTAL CODEGEN_UNITS RPATH; do
  profile_variable="CARGO_PROFILE_RELEASE_${profile_key}"
  printf -v "SHMEM_POD_BENCH_PROFILE_${profile_key}" '%s' "$(environment_value "$profile_variable")"
  export "SHMEM_POD_BENCH_PROFILE_${profile_key}"
done

# Cargo prunes workspace-only packages from the copied lockfile. Normalize that
# temporary lock without network access, then make the measured build immutable.
harness_target="$temporary/harness-target"
CARGO_TARGET_DIR="$harness_target" \
  timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  cargo metadata --offline --manifest-path "$temporary/Cargo.toml" \
    --format-version 1 >/dev/null
SHMEM_POD_BENCH_HARNESS_LOCK_SHA256=$(sha256sum "$temporary/Cargo.lock" | awk '{print $1}')
cp "$temporary/Cargo.toml" "$provenance_dir/harness-Cargo.toml"
cp "$temporary/Cargo.lock" "$provenance_dir/harness-Cargo.lock"
SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256=$(sha256sum "$provenance_dir/harness-Cargo.toml" | awk '{print $1}')

echo "building exact benchmark harness"
CARGO_TARGET_DIR="$harness_target" \
  timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  cargo build --locked --offline --release --manifest-path "$temporary/Cargo.toml"
harness_build="$harness_target/release/shmem-pod-benchmark-harness"
[[ -x $harness_build ]] || { echo "benchmark harness executable is missing: $harness_build" >&2; exit 1; }
harness="$binary_dir/shmem-pod-benchmark-harness"
cp "$harness_build" "$harness"
SHMEM_POD_BENCH_HARNESS_BINARY_SHA256=$(sha256sum "$harness" | awk '{print $1}')
export SHMEM_POD_BENCH_RUN_ID SHMEM_POD_BENCH_GIT_SHA SHMEM_POD_BENCH_GIT_DIRTY
export SHMEM_POD_BENCH_SOURCE_STATUS_SHA256 SHMEM_POD_BENCH_SOURCE_TREE_SHA256
export SHMEM_POD_BENCH_LOCK_SHA256 SHMEM_POD_BENCH_HARNESS_LOCK_SHA256
export SHMEM_POD_BENCH_WORKSPACE_MANIFEST_SHA256 SHMEM_POD_BENCH_RUNNER_SHA256
export SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256
export SHMEM_POD_BENCH_HARNESS_SOURCE_SHA256 SHMEM_POD_BENCH_HARNESS_BINARY_SHA256
export SHMEM_POD_BENCH_COMPILER_SHA256
export SHMEM_POD_BENCH_HOSTNAME SHMEM_POD_BENCH_KERNEL SHMEM_POD_BENCH_CPU_MODEL
export SHMEM_POD_BENCH_RUSTC SHMEM_POD_BENCH_CARGO
export SHMEM_POD_BENCH_RUSTFLAGS SHMEM_POD_BENCH_CARGO_ENCODED_RUSTFLAGS
export SHMEM_POD_BENCH_RUSTC_OVERRIDE SHMEM_POD_BENCH_POD_RUSTC
export SHMEM_POD_BENCH_POD_RUSTC_VERSION SHMEM_POD_BENCH_RUSTC_WRAPPER
export SHMEM_POD_BENCH_RUSTC_WORKSPACE_WRAPPER SHMEM_POD_BENCH_CARGO_HOME
export SHMEM_POD_BENCH_CARGO_BUILD_TARGET SHMEM_POD_BENCH_WORKSPACE_PROFILE
export SHMEM_POD_BENCH_HARNESS_PROFILE

echo "running verified benchmark harness"
set +e
timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  "$harness" \
    --artifact "$artifact_dir/pod.bin" \
    --sha256 "$artifact_sha256" \
    --output-dir "$output_dir" \
    --warmup "$warmup" \
    --iterations "$iterations" \
    --samples "$samples" \
    --workers "$workers" \
    --mode "$mode" \
    --timeout-seconds "$run_timeout" \
    --defer-completion 1
status=$?
set -e
if ((status != 0)); then
  if ((status == 124 || status == 137)); then
    echo "benchmark harness exceeded its ${run_timeout}s deadline" >&2
  fi
  exit "$status"
fi

verify_source_stable
verify_sha256 "$artifact_dir/pod.bin" "$artifact_sha256"
verify_sha256 "$binary_dir/shmem-pod-image-compiler" "$compiler_sha256"
verify_sha256 "$binary_dir/shmem-pod-benchmark-harness" "$SHMEM_POD_BENCH_HARNESS_BINARY_SHA256"
verify_sha256 "$provenance_dir/run-benchmarks.sh" "$runner_sha256"
verify_sha256 "$provenance_dir/harness.rs" "$harness_source_sha256"
verify_sha256 "$provenance_dir/workspace-Cargo.toml" "$workspace_manifest_sha256"
verify_sha256 "$provenance_dir/workspace-Cargo.lock" "$workspace_lock_sha256"
verify_sha256 "$provenance_dir/harness-Cargo.toml" "$SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256"
verify_sha256 "$provenance_dir/harness-Cargo.lock" "$SHMEM_POD_BENCH_HARNESS_LOCK_SHA256"
if ! jq -e --arg run_id "$run_id" \
  '.schema == "shmem-pod-benchmark-owner-v1" and .run_id == $run_id' \
  "$output_dir/harness-owner.json" >/dev/null; then
  echo "benchmark harness ownership validation failed" >&2
  exit 1
fi

expected_rows=$((22 * samples))
if ! jq -e \
  --arg run_id "$run_id" \
  --arg source_revision "$source_revision_before" \
  --arg source_status_sha256 "$source_status_sha256_before" \
  --arg source_tree_sha256 "$source_tree_sha256_before" \
  --arg workspace_lock_sha256 "$workspace_lock_sha256" \
  --arg harness_lock_sha256 "$SHMEM_POD_BENCH_HARNESS_LOCK_SHA256" \
  --arg workspace_manifest_sha256 "$workspace_manifest_sha256" \
  --arg runner_sha256 "$runner_sha256" \
  --arg harness_source_sha256 "$harness_source_sha256" \
  --arg harness_manifest_sha256 "$SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256" \
  --arg harness_binary_sha256 "$SHMEM_POD_BENCH_HARNESS_BINARY_SHA256" \
  --arg compiler_sha256 "$compiler_sha256" \
  --arg artifact_sha256 "$artifact_sha256" \
  --argjson warmup "$warmup" \
  --argjson iterations "$iterations" \
  --argjson samples "$samples" \
  --argjson workers "$workers" \
  --argjson timeout "$run_timeout" \
  '.schema == "shmem-pod-benchmark-environment-v1"
   and .run_id == $run_id
   and .complete == true
   and .result_rows == $samples * 22
   and .source_revision == $source_revision
   and .source_status_sha256 == $source_status_sha256
   and .source_tree_sha256 == $source_tree_sha256
   and .configuration.warmup_operations_per_worker == $warmup
   and .configuration.iterations_per_worker == $iterations
   and .configuration.samples == $samples
   and .configuration.workers == $workers
   and .configuration.timeout_seconds == $timeout
   and .cargo_lock_sha256 == $workspace_lock_sha256
   and .harness_lock_sha256 == $harness_lock_sha256
   and .provenance.workspace_manifest.sha256 == $workspace_manifest_sha256
   and .provenance.runner.sha256 == $runner_sha256
   and .provenance.harness_source.sha256 == $harness_source_sha256
   and .provenance.harness_manifest.sha256 == $harness_manifest_sha256
   and .provenance.harness_binary.sha256 == $harness_binary_sha256
   and .provenance.compiler_binary.sha256 == $compiler_sha256
   and (.host.numa_nodes_online | type == "string" and length > 0)
   and (.host.numa_nodes_possible | type == "string" and length > 0)
   and (.execution_limits.cpu_affinity_list | type == "string" and length > 0)
   and (.execution_limits.memory_affinity_list | type == "string" and length > 0)
   and (.execution_limits.cgroup_v2_path | type == "string" and length > 0)
   and (.execution_limits.inherited_cpu_max | type == "string" and length > 0)
   and (.execution_limits.inherited_cpu_max_source | type == "string" and length > 0)
   and (.execution_limits.inherited_memory_max | type == "string" and length > 0)
   and (.execution_limits.inherited_memory_max_source | type == "string" and length > 0)
   and (.execution_limits.inherited_memory_swap_max | type == "string" and length > 0)
   and (.execution_limits.inherited_memory_swap_max_source | type == "string" and length > 0)
   and (.execution_limits.effective_cpuset | type == "string" and length > 0)
   and (.execution_limits.effective_cpuset_mems | type == "string" and length > 0)
   and (.build_environment.rustflags | type == "string")
   and (.build_environment.cargo_encoded_rustflags | type == "string")
   and (.build_environment.rustc_override | type == "string")
   and (.build_environment.pod_rustc | type == "string")
   and (.build_environment.workspace_profile | type == "string" and length > 0)
   and (.build_environment.harness_profile | type == "string" and length > 0)
   and .artifact.sha256 == $artifact_sha256
   and .artifact.bundle_path == "artifacts/pod.bin"' \
  "$output_dir/environment.json.pending" >/dev/null; then
  echo "benchmark environment provenance validation failed" >&2
  exit 1
fi
if ! jq -s -e \
  --arg run_id "$run_id" \
  --argjson iterations "$iterations" \
  --argjson samples "$samples" \
  --argjson workers "$workers" \
  '
   def expected($sample; $category; $benchmark; $variant; $topology; $row_workers; $operations):
     {schema: "shmem-pod-benchmark-result-v1", run_id: $run_id,
      category: $category, benchmark: $benchmark, variant: $variant,
      topology: $topology, workers: $row_workers, sample: $sample,
      operations: $operations, verified: true};
   [range(0; $samples) as $sample |
     expected($sample; "latency"; "call"; "direct_rust_atomic_increment"; "single_thread"; 1; $iterations),
     expected($sample; "latency"; "kernel_entry"; "gettid_syscall"; "single_thread"; 1; $iterations),
     expected($sample; "latency"; "call"; "authenticated_executable_pod_upsert"; "single_process_rx_code_rw_state"; 1; $iterations),
     expected($sample; "latency"; "mutex"; "process_spin_mutex"; "single_thread_uncontended"; 1; $iterations),
     expected($sample; "latency"; "mutex"; "process_futex_mutex"; "single_thread_uncontended"; 1; $iterations),
     expected($sample; "throughput"; "mutex"; "process_spin_mutex"; "forked_processes_hot"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "mutex"; "process_futex_mutex"; "forked_processes_hot"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "counter_table"; "coarse_futex_lock"; "forked_processes_sharded_keys_one_lock"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "counter_table"; "fine_grained_futex_locks"; "forked_processes_hot_key"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "counter_table"; "fine_grained_futex_locks"; "forked_processes_sharded_keys_padded"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "counter_table"; "atomic_fetch_add"; "forked_processes_hot_key"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "counter_table"; "atomic_fetch_add"; "forked_processes_sharded_keys_padded"; $workers; ($iterations * $workers)),
     expected($sample; "latency"; "kernel_ipc"; "unix_stream_8_byte_round_trip"; "two_threads_one_process"; 2; $iterations),
     expected($sample; "throughput"; "presence_cycle"; "snzi"; "threads_hot_leaf"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "presence_cycle"; "closeable_snzi"; "threads_hot_leaf"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "presence_cycle"; "csnzi"; "threads_hot_leaf"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "presence_cycle"; "snzi"; "threads_sharded_leaves"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "presence_cycle"; "closeable_snzi"; "threads_sharded_leaves"; $workers; ($iterations * $workers)),
     expected($sample; "throughput"; "presence_cycle"; "csnzi"; "threads_sharded_leaves"; $workers; ($iterations * $workers)),
     expected($sample; "latency"; "reloc_allocator"; "shared_box_allocate_destroy_pair"; "single_thread_exclusive"; 1; ($iterations * 2)),
     expected($sample; "latency"; "shared_box"; "checked_get"; "single_thread_shared_read"; 1; $iterations),
     expected($sample; "latency"; "shared_vec"; "checked_push_pop_pair"; "single_thread_exclusive"; 1; ($iterations * 2))
   ] as $expected |
   map({schema, run_id, category, benchmark, variant, topology, workers, sample, operations, verified}) as $actual |
   ($actual | sort_by(.sample, .variant, .topology)) == ($expected | sort_by(.sample, .variant, .topology))
   and all(.[]; .elapsed_ns > 0
                    and .elapsed_ns <= 9007199254740991
                    and .operations_per_second >= 0
                    and .operations_per_second <= 9007199254740991)
  ' \
  "$output_dir/results.jsonl" >/dev/null; then
  echo "benchmark JSONL does not match the exact configured matrix" >&2
  exit 1
fi
rate_validation_failed=0
validated_rates=0
while IFS=$'\t' read -r sample variant operations elapsed_ns actual_rate; do
  validated_rates=$((validated_rates + 1))
  expected_rate=$((operations * 1000000000 / elapsed_ns))
  if ((actual_rate != expected_rate)); then
    echo "benchmark rate mismatch for sample $sample, $variant" >&2
    echo "  expected: $expected_rate" >&2
    echo "  actual:   $actual_rate" >&2
    rate_validation_failed=1
    break
  fi
done < <(jq -r '[.sample, .variant, .operations, .elapsed_ns, .operations_per_second] | @tsv' "$output_dir/results.jsonl")
if ((rate_validation_failed != 0 || validated_rates != expected_rows)); then
  if ((rate_validation_failed == 0)); then
    echo "benchmark rate validation saw $validated_rates rows, expected $expected_rows" >&2
  fi
  exit 1
fi
if [[ $(wc -l <"$output_dir/results.csv") -ne $((expected_rows + 1)) ]]; then
  echo "benchmark CSV has the wrong result-row count" >&2
  exit 1
fi
expected_header='schema,run_id,category,benchmark,variant,topology,workers,sample,operations,elapsed_ns,operations_per_second,verified'
IFS= read -r actual_header <"$output_dir/results.csv"
if [[ $actual_header != "$expected_header" ]]; then
  echo "benchmark CSV has the wrong header" >&2
  exit 1
fi
if ! diff -u \
  <(tail -n +2 "$output_dir/results.csv") \
  <(jq -r '[.schema,.run_id,.category,.benchmark,.variant,.topology,.workers,.sample,.operations,.elapsed_ns,.operations_per_second,.verified] | map(tostring) | join(",")' "$output_dir/results.jsonl"); then
  echo "benchmark CSV and JSONL rows differ" >&2
  exit 1
fi

verify_source_stable
if [[ -e $output_dir/environment.json || -L $output_dir/environment.json ]]; then
  echo "refusing to replace an existing benchmark completion marker" >&2
  exit 1
fi
mv -- "$output_dir/environment.json.pending" "$output_dir/environment.json"
sync -f "$output_dir/environment.json"
run_succeeded=1
output_claimed=0

echo "PASS benchmark artifacts validated in $output_dir"
