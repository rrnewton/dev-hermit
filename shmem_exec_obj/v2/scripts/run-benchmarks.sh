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
  --timeout SECONDS    Whole harness deadline (default: 1800; smoke: 300).
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

for tool in awk cargo cp date git jq mktemp rustc sed sha256sum timeout uname wc; do
  command -v "$tool" >/dev/null || { echo "run-benchmarks.sh requires $tool" >&2; exit 1; }
done
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "the executable-image benchmark currently requires Linux x86-64" >&2
  exit 1
fi

online_cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
if [[ ! $online_cpus =~ ^[1-9][0-9]*$ ]]; then
  online_cpus=1
fi
default_workers=$online_cpus
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

target_dir=$(cargo metadata --locked --format-version 1 --no-deps | jq -er .target_directory)
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
echo "  harness timeout: ${run_timeout}s"

echo "building executable-pod compiler and runtime"
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

export SHMEM_POD_BENCH_RUN_ID=$run_id
export SHMEM_POD_BENCH_GIT_SHA=$(git rev-parse HEAD)
export SHMEM_POD_BENCH_LOCK_SHA256=$(sha256sum Cargo.lock | awk '{print $1}')
if [[ -n $(git status --porcelain -- .) ]]; then
  export SHMEM_POD_BENCH_GIT_DIRTY=1
else
  export SHMEM_POD_BENCH_GIT_DIRTY=0
fi
export SHMEM_POD_BENCH_HOSTNAME=$(uname -n)
export SHMEM_POD_BENCH_KERNEL=$(uname -srvmo)
cpu_model=$(awk -F: '/^model name/{sub(/^[[:space:]]+/, "", $2); print $2; exit}' /proc/cpuinfo)
export SHMEM_POD_BENCH_CPU_MODEL=${cpu_model:-unknown}
export SHMEM_POD_BENCH_RUSTC=$(rustc --version --verbose)
export SHMEM_POD_BENCH_CARGO=$(cargo --version --verbose)

# Cargo prunes workspace-only packages from the copied lockfile. Normalize that
# temporary lock without network access, then make the measured build immutable.
CARGO_TARGET_DIR="$target_dir/benchmark-harness" \
  cargo metadata --offline --manifest-path "$temporary/Cargo.toml" \
  --format-version 1 >/dev/null
export SHMEM_POD_BENCH_HARNESS_LOCK_SHA256=$(sha256sum "$temporary/Cargo.lock" | awk '{print $1}')

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

jq -e '.schema == "shmem-pod-benchmark-environment-v1"' \
  "$output_dir/environment.json" >/dev/null
jq -e 'select(.schema == "shmem-pod-benchmark-result-v1" and .verified == true)' \
  "$output_dir/results.jsonl" >/dev/null
[[ $(wc -l <"$output_dir/results.csv") -gt 1 ]] || {
  echo "benchmark CSV contains no result rows" >&2
  exit 1
}

echo "PASS benchmark artifacts validated in $output_dir"
