#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/release-check.sh [quick|full]

Run the bounded shmem-pod release gate from any directory.

  quick  Complete developer gate with reduced process workloads (default).
  full   Release-candidate gate with the full MSRV matrix and all examples.

Environment:
  RELEASE_CHECK_TOTAL_TIMEOUT    Whole-run deadline in seconds.
  RELEASE_CHECK_COMMAND_TIMEOUT  Default per-command deadline in seconds.
  RELEASE_CHECK_LONG_TIMEOUT     Build/process per-command deadline in seconds.
  RELEASE_CHECK_ADVERSARIAL_TIMEOUT  Adversarial-suite deadline in seconds.
  RELEASE_CHECK_SKIP_PROCESS=1   Skip Linux process evidence (not release-green).
  RELEASE_CHECK_REQUIRE_CLEAN=1  Reject changes under v2 before running.
  RELEASE_CHECK_DRY_RUN=1        Print the selected gate without executing it.
EOF
}

mode=${1:-quick}
case "$mode" in
  quick | full) ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    echo "unknown release-check mode: $mode" >&2
    usage >&2
    exit 2
    ;;
esac
if (($# > 1)); then
  usage >&2
  exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

if [[ $mode == quick ]]; then
  default_total_timeout=3600
  default_command_timeout=300
  default_long_timeout=900
  default_adversarial_timeout=1200
else
  default_total_timeout=10800
  default_command_timeout=600
  default_long_timeout=1800
  default_adversarial_timeout=5400
fi

total_timeout=${RELEASE_CHECK_TOTAL_TIMEOUT:-$default_total_timeout}
command_timeout=${RELEASE_CHECK_COMMAND_TIMEOUT:-$default_command_timeout}
long_timeout=${RELEASE_CHECK_LONG_TIMEOUT:-$default_long_timeout}
adversarial_timeout=${RELEASE_CHECK_ADVERSARIAL_TIMEOUT:-$default_adversarial_timeout}
skip_process=${RELEASE_CHECK_SKIP_PROCESS:-0}
require_clean=${RELEASE_CHECK_REQUIRE_CLEAN:-0}
dry_run=${RELEASE_CHECK_DRY_RUN:-0}

require_positive_integer() {
  local name=$1
  local value=$2
  if [[ ! $value =~ ^[1-9][0-9]*$ ]]; then
    echo "$name must be a positive integer, got: $value" >&2
    exit 2
  fi
}

require_boolean() {
  local name=$1
  local value=$2
  if [[ $value != 0 && $value != 1 ]]; then
    echo "$name must be 0 or 1, got: $value" >&2
    exit 2
  fi
}

require_positive_integer RELEASE_CHECK_TOTAL_TIMEOUT "$total_timeout"
require_positive_integer RELEASE_CHECK_COMMAND_TIMEOUT "$command_timeout"
require_positive_integer RELEASE_CHECK_LONG_TIMEOUT "$long_timeout"
require_positive_integer RELEASE_CHECK_ADVERSARIAL_TIMEOUT "$adversarial_timeout"
require_boolean RELEASE_CHECK_SKIP_PROCESS "$skip_process"
require_boolean RELEASE_CHECK_REQUIRE_CLEAN "$require_clean"
require_boolean RELEASE_CHECK_DRY_RUN "$dry_run"

for tool in cargo git jq rustc timeout uname; do
  command -v "$tool" >/dev/null || {
    echo "release-check requires $tool" >&2
    exit 1
  }
done

if [[ $require_clean == 1 ]] && [[ -n $(git status --porcelain -- .) ]]; then
  echo "release-check requires a clean v2 tree:" >&2
  git status --short -- . >&2
  exit 1
fi

started=$SECONDS
gate_number=0

print_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

limit_for() {
  local class=$1
  local configured
  case "$class" in
    long) configured=$long_timeout ;;
    adversarial) configured=$adversarial_timeout ;;
    *) configured=$command_timeout ;;
  esac

  local elapsed=$((SECONDS - started))
  local remaining=$((total_timeout - elapsed))
  if ((remaining <= 0)); then
    echo "release-check exceeded its ${total_timeout}s whole-run deadline" >&2
    exit 124
  fi
  if ((configured < remaining)); then
    printf '%s\n' "$configured"
  else
    printf '%s\n' "$remaining"
  fi
}

run_gate() {
  local class=$1
  local label=$2
  shift 2
  gate_number=$((gate_number + 1))
  local limit
  limit=$(limit_for "$class")
  printf '\n[%02d] %s (timeout %ss)\n' "$gate_number" "$label" "$limit"
  print_command "$@"
  if [[ $dry_run == 1 ]]; then
    return
  fi

  set +e
  timeout --signal=TERM --kill-after=30s "${limit}s" "$@"
  local status=$?
  set -e
  if ((status != 0)); then
    if ((status == 124 || status == 137)); then
      echo "FAIL: $label exceeded its ${limit}s deadline" >&2
    else
      echo "FAIL: $label exited with status $status" >&2
    fi
    exit "$status"
  fi
}

run_capture() {
  local class=$1
  local label=$2
  local output=$3
  shift 3
  gate_number=$((gate_number + 1))
  local limit
  limit=$(limit_for "$class")
  printf '\n[%02d] %s (timeout %ss)\n' "$gate_number" "$label" "$limit"
  print_command "$@"
  if [[ $dry_run == 1 ]]; then
    return
  fi

  set +e
  timeout --signal=TERM --kill-after=30s "${limit}s" "$@" >"$output"
  local status=$?
  set -e
  if ((status != 0)); then
    echo "FAIL: $label exited with status $status" >&2
    exit "$status"
  fi
}

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

echo "shmem-pod release check"
echo "  mode: $mode"
echo "  root: $root"
echo "  whole-run timeout: ${total_timeout}s"
echo "  process evidence: $([[ $skip_process == 1 ]] && echo skipped || echo required)"

run_gate normal "source revision" git rev-parse HEAD
run_gate normal "host kernel and architecture" uname -a
run_gate normal "current Rust version" rustc --version --verbose
run_gate normal "current Cargo version" cargo --version --verbose
run_gate normal "MSRV Cargo availability" cargo +1.85.0 --version
run_gate normal "format" cargo fmt --all -- --check

run_gate normal "current all-feature workspace check" \
  cargo check --locked --workspace --all-targets --all-features
run_gate long "current all-feature workspace tests" \
  cargo test --locked --workspace --all-features
run_gate normal "current no-default check" \
  cargo check --locked -p shmem-pod --all-targets --no-default-features
run_gate long "current no-default tests" \
  cargo test --locked -p shmem-pod --no-default-features

for feature in derive fixed-allocator linux-futex; do
  run_gate normal "current isolated feature: $feature" \
    cargo check --locked -p shmem-pod --all-targets \
    --no-default-features --features "$feature"
done

run_gate normal "MSRV all-feature workspace check" \
  cargo +1.85.0 check --locked --workspace --all-targets --all-features
run_gate long "MSRV no-default tests" \
  cargo +1.85.0 test --locked -p shmem-pod --no-default-features

if [[ $mode == full ]]; then
  run_gate long "MSRV all-feature workspace tests" \
    cargo +1.85.0 test --locked --workspace --all-features
  for feature in derive fixed-allocator linux-futex; do
    run_gate normal "MSRV isolated feature: $feature" \
      cargo +1.85.0 check --locked -p shmem-pod --all-targets \
      --no-default-features --features "$feature"
  done
fi

run_gate long "clippy" \
  cargo clippy --locked --workspace --all-targets --all-features -- -D warnings
run_gate long "current rustdoc" \
  env RUSTDOCFLAGS="-D warnings" \
  cargo doc --locked --workspace --all-features --no-deps
if [[ $mode == full ]]; then
  run_gate long "MSRV rustdoc" \
    env RUSTDOCFLAGS="-D warnings" \
    cargo +1.85.0 doc --locked --workspace --all-features --no-deps
fi

# Package the proc macro first. The library's dry-run verification then uses a
# local crates.io patch to model the already-published macro version.
run_gate long "package and verify shmem-pod-macros" \
  cargo package --locked --allow-dirty -p shmem-pod-macros
run_gate long "package and verify shmem-pod" \
  cargo package --locked --allow-dirty -p shmem-pod \
  --config 'patch.crates-io.shmem-pod-macros.path="crates/macros"'

macro_list="$tmpdir/macros-package.txt"
main_list="$tmpdir/main-package.txt"
metadata="$tmpdir/metadata.json"
run_capture normal "list shmem-pod-macros package" "$macro_list" \
  cargo package --locked --allow-dirty --list -p shmem-pod-macros
run_capture normal "list shmem-pod package" "$main_list" \
  cargo package --locked --allow-dirty --list -p shmem-pod \
  --config 'patch.crates-io.shmem-pod-macros.path="crates/macros"'
run_capture normal "inspect workspace publication policy" "$metadata" \
  cargo metadata --locked --format-version 1 --no-deps

if [[ $dry_run == 0 ]]; then
  for required in Cargo.toml README.md LICENSE-APACHE LICENSE-MIT src/lib.rs src/pod.rs; do
    grep -Fxq "$required" "$macro_list" || {
      echo "macro package is missing $required" >&2
      exit 1
    }
  done
  for required in \
    Cargo.toml Cargo.lock README.md LICENSE-APACHE LICENSE-MIT \
    src/lib.rs src/mapping.rs src/admission.rs src/csnzi.rs src/pod_api.rs \
    src/collections.rs src/reloc_allocator.rs \
    src/injection.rs src/migration.rs \
    docs/locking.md docs/admission.md docs/csnzi.md docs/injection.md \
    docs/migration-and-reclamation.md docs/relocatable-allocation.md docs/support.md \
    examples/README.md examples/typed_mapping.rs examples/closeable_snzi.rs \
    examples/csnzi.rs examples/csnzi_comparison.rs \
    examples/relocatable_collections.rs examples/schema_migration.rs \
    tests/layout.rs tests/closeable_snzi.rs tests/csnzi.rs tests/pod_api.rs \
    tests/bootstrap_connector.rs tests/dynamic_analysis.rs tests/migration.rs \
    tests/reloc_allocator.rs \
    tests/shared_collections.rs; do
    grep -Fxq "$required" "$main_list" || {
      echo "main package is missing $required" >&2
      exit 1
    }
  done
  if grep -Eq '(^|/)(poc|demos|fuzz|scripts|target|crates|ai_docs|\.minibeads)/' "$main_list"; then
    echo "main package contains a private harness or generated path:" >&2
    grep -E '(^|/)(poc|demos|fuzz|scripts|target|crates|ai_docs|\.minibeads)/' \
      "$main_list" >&2
    exit 1
  fi
  if grep -Fxq "docs/benchmarks.md" "$main_list"; then
    echo "main package contains repository-only benchmark documentation" >&2
    exit 1
  fi
  if ! jq -e '
    [.packages[] | select(.publish != []) | .name] | sort
      == ["shmem-pod", "shmem-pod-macros"]
  ' "$metadata" >/dev/null; then
    echo "workspace publication policy must expose exactly shmem-pod and shmem-pod-macros" >&2
    jq -r '.packages[] | "\(.name): publish=\(.publish)"' "$metadata" >&2
    exit 1
  fi
  echo "PASS package contents: private POC, demo, build, and project paths excluded"
fi

if [[ $skip_process == 1 ]]; then
  echo
  echo "SKIP process evidence: RELEASE_CHECK_SKIP_PROCESS=1"
  echo "This run is compile/package evidence only and is not release-green."
else
  if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
    echo "process evidence currently requires Linux x86-64" >&2
    echo "Set RELEASE_CHECK_SKIP_PROCESS=1 for a non-release compile/package run." >&2
    exit 1
  fi

  run_gate adversarial "bounded adversarial validation" \
    ./scripts/adversarial-check.sh "$mode"

  run_gate long "typed mapping lifecycle process smoke" \
    cargo run --locked --example typed_mapping
  run_gate long "low-level layout handshake process smoke" \
    cargo run --locked --example layout_handshake
  run_gate long "coarse/fine/atomic counter process example" \
    cargo run --locked --example shared_counters
  run_gate long "process-shared futex example" \
    cargo run --locked --features linux-futex --example futex_mutex
  run_gate long "SNZI process example" \
    cargo run --locked --example snzi
  run_gate long "closeable SNZI admission process example" \
    cargo run --locked --example closeable_snzi
  run_gate long "C-SNZI admission process example" \
    cargo run --locked --example csnzi
  run_gate long "SNZI topology comparison" \
    cargo run --locked --release --example csnzi_comparison -- 2 2000
  run_gate long "relocatable allocator and collections process example" \
    cargo run --locked --example relocatable_collections
  run_gate long "schema migration and reclamation process example" \
    cargo run --locked --example schema_migration
  run_gate long "reproducible benchmark matrix smoke" \
    ./scripts/run-benchmarks.sh --smoke --output "$tmpdir/benchmark-smoke"

  if [[ $mode == quick ]]; then
    run_gate long "executable pod process smoke" \
      env POD_WORKERS=2 POD_THREADS=2 POD_ITERATIONS=100 \
      ./scripts/run-poc.sh
    run_gate long "LD_PRELOAD unaware-guest process smoke" \
      env POD_DEPTH=1 POD_FANOUT=1 POD_THREADS=1 POD_CALLS=20 \
      ./scripts/run-preload-demo.sh
  else
    run_gate long "relative-offset remapping example" \
      cargo run --locked --example relative_offsets
    run_gate long "fixed allocator fork example" \
      cargo run --locked --features fixed-allocator --example fixed_allocator_fork
    run_gate long "fixed allocator exec example" \
      cargo run --locked --features fixed-allocator --example fixed_allocator_exec
    run_gate long "executable pod process suite" \
      env POD_WORKERS=4 POD_THREADS=4 POD_ITERATIONS=2000 \
      ./scripts/run-poc.sh
    run_gate long "LD_PRELOAD unaware-guest process suite" \
      env POD_DEPTH=2 POD_FANOUT=2 POD_THREADS=2 POD_CALLS=100 \
      ./scripts/run-preload-demo.sh
    run_gate long "ptrace bootstrap and detach process suite" \
      ./scripts/run-ptrace-demo.sh
    run_gate long "connector fail-closed negative suite" \
      ./scripts/test-connector-failures.sh
  fi
fi

elapsed=$((SECONDS - started))
echo
if [[ $dry_run == 1 ]]; then
  echo "PLAN shmem-pod release check mode=$mode gates=$gate_number"
else
  echo "PASS shmem-pod release check mode=$mode gates=$gate_number elapsed=${elapsed}s"
fi
