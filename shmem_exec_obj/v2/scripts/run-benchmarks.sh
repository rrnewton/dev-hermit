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
  --samples N          Independent timing samples (default: 5; smoke: 1).
  --workers N          Contending threads/processes (default: min(CPUs, 8)).
  --timeout SECONDS    Per build/run deadline (default: 1800; smoke: 300).
  -h, --help           Show this help.

Outputs:
  environment.json     Host, source, toolchain, artifact, and run configuration.
  results.jsonl        One verified timing record per sample.
  results.csv          The same records in tabular form.

Counts are explicit in environment.json. Results describe only this host/run;
the suite deliberately makes no portable performance claim.
EOF
}

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

mode=standard
output_dir=
warmup=
iterations=
samples=
workers=
run_timeout=

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

for tool in awk cargo cp date git jq mktemp nproc rustc sed sha256sum timeout uname wc; do
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
  if ((10#$2 == 0)); then
    echo "$1 must be nonzero" >&2
    exit 2
  fi
}

require_uint --warmup "$warmup"
require_positive --iterations "$iterations"
require_positive --samples "$samples"
require_positive --workers "$workers"
require_positive --timeout "$run_timeout"
if ((10#$workers > 64)); then
  echo "--workers must not exceed 64" >&2
  exit 2
fi

target_dir=$(
  timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
    cargo metadata --locked --format-version 1 --no-deps |
    jq -er .target_directory
)
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
if [[ -z $output_dir ]]; then
  output_dir="$target_dir/benchmark-results/$run_id"
fi
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd)
artifact_dir="$target_dir/benchmark-pod-image"
mkdir -p "$artifact_dir"

echo "shmem-pod benchmark suite"
echo "  mode: $mode"
echo "  output: $output_dir"
echo "  warmup/iterations/samples/workers: $warmup/$iterations/$samples/$workers"
echo "  per-command timeout: ${run_timeout}s"

echo "building executable-pod compiler and runtime"
timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  cargo build --locked --release \
  -p shmem-pod-image-compiler \
  -p shmem-pod-runtime
compiler="$target_dir/release/shmem-pod-image-compiler"
[[ -x $compiler ]] || { echo "compiler executable is missing: $compiler" >&2; exit 1; }

compiler_args=(
  --source poc/code/src/lib.rs
  --sdk-manifest Cargo.toml
  --sdk-source src/lib.rs
  --sdk-rlib "$artifact_dir/libshmem_pod.rlib"
  --linker-script poc/code/pod.ld
  --output "$artifact_dir/pod.bin"
  --object "$artifact_dir/pod.o"
  --elf "$artifact_dir/pod.elf"
  --manifest "$artifact_dir/pod.manifest"
)
if [[ -n ${POD_RUSTC:-} ]]; then
  compiler_args+=(--rustc "$POD_RUSTC")
fi
timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  "$compiler" "${compiler_args[@]}"
artifact_sha256=$(sed -n 's/^artifact_sha256=//p' "$artifact_dir/pod.manifest")
if [[ ! $artifact_sha256 =~ ^[0-9a-f]{64}$ ]]; then
  echo "compiler manifest lacks a valid artifact SHA-256" >&2
  exit 1
fi

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
mkdir -p "$temporary/src"
cp benchmarks/harness.rs "$temporary/src/main.rs"
cp Cargo.lock "$temporary/Cargo.lock"
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
EOF

SHMEM_POD_BENCH_RUN_ID=$run_id
SHMEM_POD_BENCH_GIT_SHA=$(git rev-parse HEAD)
SHMEM_POD_BENCH_LOCK_SHA256=$(sha256sum Cargo.lock | awk '{print $1}')
if [[ -n $(git status --porcelain -- .) ]]; then
  SHMEM_POD_BENCH_GIT_DIRTY=1
else
  SHMEM_POD_BENCH_GIT_DIRTY=0
fi
SHMEM_POD_BENCH_HOSTNAME=$(uname -n)
SHMEM_POD_BENCH_KERNEL=$(uname -srvmo)
cpu_model=$(awk -F: '/^model name/{sub(/^[[:space:]]+/, "", $2); print $2; exit}' /proc/cpuinfo)
SHMEM_POD_BENCH_CPU_MODEL=${cpu_model:-unknown}
SHMEM_POD_BENCH_RUSTC=$(rustc --version --verbose)
SHMEM_POD_BENCH_CARGO=$(cargo --version --verbose)

# Cargo prunes workspace-only packages from the copied lockfile. Normalize that
# temporary lock without network access, then make the measured build immutable.
CARGO_TARGET_DIR="$target_dir/benchmark-harness" \
  timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  cargo metadata --offline --manifest-path "$temporary/Cargo.toml" \
    --format-version 1 >/dev/null
SHMEM_POD_BENCH_HARNESS_LOCK_SHA256=$(sha256sum "$temporary/Cargo.lock" | awk '{print $1}')
export SHMEM_POD_BENCH_RUN_ID SHMEM_POD_BENCH_GIT_SHA SHMEM_POD_BENCH_GIT_DIRTY
export SHMEM_POD_BENCH_LOCK_SHA256 SHMEM_POD_BENCH_HARNESS_LOCK_SHA256
export SHMEM_POD_BENCH_HOSTNAME SHMEM_POD_BENCH_KERNEL SHMEM_POD_BENCH_CPU_MODEL
export SHMEM_POD_BENCH_RUSTC SHMEM_POD_BENCH_CARGO

echo "running verified benchmark harness"
set +e
CARGO_TARGET_DIR="$target_dir/benchmark-harness" \
  timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  cargo run --locked --offline --release --manifest-path "$temporary/Cargo.toml" -- \
    --artifact "$artifact_dir/pod.bin" \
    --sha256 "$artifact_sha256" \
    --output-dir "$output_dir" \
    --warmup "$warmup" \
    --iterations "$iterations" \
    --samples "$samples" \
    --workers "$workers" \
    --mode "$mode"
status=$?
set -e
if ((status != 0)); then
  if ((status == 124 || status == 137)); then
    echo "benchmark harness exceeded its ${run_timeout}s deadline" >&2
  fi
  exit "$status"
fi

expected_rows=$((22 * samples))
jq -e \
  --argjson warmup "$warmup" \
  --argjson iterations "$iterations" \
  --argjson samples "$samples" \
  --argjson workers "$workers" \
  '.schema == "shmem-pod-benchmark-environment-v1"
   and .configuration.warmup_operations_per_worker == $warmup
   and .configuration.iterations_per_worker == $iterations
   and .configuration.samples == $samples
   and .configuration.workers == $workers
   and (.cargo_lock_sha256 | test("^[0-9a-f]{64}$"))
   and (.harness_lock_sha256 | test("^[0-9a-f]{64}$"))
   and (.execution_limits.cpu_affinity_list | type == "string" and length > 0)
   and (.execution_limits.cgroup_v2_path | type == "string" and length > 0)
   and (.execution_limits.inherited_cpu_max | type == "string" and length > 0)
   and (.execution_limits.inherited_cpu_max_source | type == "string" and length > 0)
   and (.execution_limits.inherited_memory_max | type == "string" and length > 0)
   and (.execution_limits.inherited_memory_max_source | type == "string" and length > 0)
   and (.artifact.sha256 | test("^[0-9a-f]{64}$"))' \
  "$output_dir/environment.json" >/dev/null
jq -s -e --argjson expected "$expected_rows" \
  'length == $expected
   and all(.[]; .schema == "shmem-pod-benchmark-result-v1")
   and all(.[]; .verified == true)
   and all(.[]; .category == "latency" or .category == "throughput")
   and all(.[]; .operations > 0 and .elapsed_ns > 0)
   and ([
     "direct_rust_atomic_increment",
     "authenticated_executable_pod_upsert",
     "gettid_syscall",
     "unix_stream_8_byte_round_trip",
     "process_spin_mutex",
     "process_futex_mutex",
     "coarse_futex_lock",
     "fine_grained_futex_locks",
     "atomic_fetch_add",
     "snzi",
     "closeable_snzi",
     "csnzi",
     "shared_box_allocate_destroy_pair",
     "checked_get",
     "checked_push_pop_pair"
   ] - (map(.variant) | unique) | length == 0)' \
  "$output_dir/results.jsonl" >/dev/null
if [[ $(wc -l <"$output_dir/results.csv") -ne $((expected_rows + 1)) ]]; then
  echo "benchmark CSV has the wrong result-row count" >&2
  exit 1
fi

echo "PASS benchmark artifacts validated in $output_dir"
