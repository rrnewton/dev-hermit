#!/usr/bin/env bash
set -Euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/adversarial-check.sh [quick|full]

Run bounded concurrency, crash-stop, parser, and dynamic-analysis gates.

  quick  Developer adversarial gate, bounded to 20 minutes by default.
  full   Extended state-space and fuzz gate, bounded to 90 minutes by default.

Environment:
  ADVERSARIAL_TOTAL_TIMEOUT    Whole-run deadline in seconds.
  ADVERSARIAL_COMMAND_TIMEOUT  Default per-command deadline in seconds.
  ADVERSARIAL_LONG_TIMEOUT     Model/fuzz/dynamic per-command deadline in seconds.
  ADVERSARIAL_NIGHTLY          Pinned nightly with Miri, rust-src, and sanitizers.
  ADVERSARIAL_FUZZ_SECONDS     Per-target libFuzzer time budget.
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
    echo "unknown adversarial-check mode: $mode" >&2
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
  default_total_timeout=1200
  default_command_timeout=120
  default_long_timeout=300
  loom_permutations=10000
  loom_preemptions=2
  default_fuzz_seconds=15
else
  default_total_timeout=5400
  default_command_timeout=600
  default_long_timeout=1200
  loom_permutations=100000
  loom_preemptions=3
  default_fuzz_seconds=120
fi

total_timeout=${ADVERSARIAL_TOTAL_TIMEOUT:-$default_total_timeout}
command_timeout=${ADVERSARIAL_COMMAND_TIMEOUT:-$default_command_timeout}
long_timeout=${ADVERSARIAL_LONG_TIMEOUT:-$default_long_timeout}
nightly=${ADVERSARIAL_NIGHTLY:-nightly-2026-06-01}
fuzz_seconds=${ADVERSARIAL_FUZZ_SECONDS:-$default_fuzz_seconds}

require_positive_integer() {
  local name=$1
  local value=$2
  if [[ ! $value =~ ^[1-9][0-9]*$ ]]; then
    echo "$name must be a positive integer, got: $value" >&2
    exit 2
  fi
}

require_positive_integer ADVERSARIAL_TOTAL_TIMEOUT "$total_timeout"
require_positive_integer ADVERSARIAL_COMMAND_TIMEOUT "$command_timeout"
require_positive_integer ADVERSARIAL_LONG_TIMEOUT "$long_timeout"
require_positive_integer ADVERSARIAL_FUZZ_SECONDS "$fuzz_seconds"

for tool in cargo rustc timeout uname; do
  if ! command -v "$tool" >/dev/null; then
    echo "UNAVAILABLE: core tooling ($tool is not installed)"
    exit 2
  fi
done

started=$SECONDS
gate_number=0
pass_count=0
fail_count=0
unavailable_count=0
unsupported_count=0
timeout_count=0

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
corpus_root="$tmpdir/corpus"

print_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

limit_for() {
  local class=$1
  local configured=$command_timeout
  if [[ $class == long ]]; then
    configured=$long_timeout
  fi
  local elapsed=$((SECONDS - started))
  local remaining=$((total_timeout - elapsed))
  if ((remaining <= 0)); then
    echo "FAIL: whole-run deadline (${total_timeout}s) exhausted" >&2
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

  set +e
  timeout --signal=TERM --kill-after=10s "${limit}s" "$@"
  local status=$?
  set -e
  if ((status == 0)); then
    echo "PASS: $label"
    pass_count=$((pass_count + 1))
  elif ((status == 124 || status == 137)); then
    echo "FAIL: $label (timeout after ${limit}s)" >&2
    timeout_count=$((timeout_count + 1))
  else
    echo "FAIL: $label (exit $status)" >&2
    fail_count=$((fail_count + 1))
  fi
}

unavailable() {
  local label=$1
  local reason=$2
  echo "UNAVAILABLE: $label ($reason)"
  unavailable_count=$((unavailable_count + 1))
}

unsupported() {
  local label=$1
  local reason=$2
  echo "UNSUPPORTED: $label ($reason)"
  unsupported_count=$((unsupported_count + 1))
}

echo "shmem-pod adversarial check"
echo "  mode: $mode"
echo "  root: $root"
echo "  whole-run timeout: ${total_timeout}s"
echo "  nightly: $nightly"
echo "  Loom: permutations=$loom_permutations preemptions=$loom_preemptions"
echo "  fuzz budget: ${fuzz_seconds}s per target"

atomic64=0
if rustc --print cfg | grep -Fxq 'target_has_atomic="64"'; then
  atomic64=1
fi
linux=0
if [[ $(uname -s) == Linux ]]; then
  linux=1
fi
x86_64_linux=0
if [[ $linux == 1 && $(uname -m) == x86_64 ]]; then
  x86_64_linux=1
fi

if [[ $atomic64 == 1 ]]; then
  run_gate long "Loom production protocol models" \
    env LOOM_MAX_PERMUTATIONS="$loom_permutations" \
    LOOM_MAX_PREEMPTIONS="$loom_preemptions" RUSTFLAGS="--cfg shmem_pod_loom" \
    cargo test --locked -p shmem-pod --lib model_checks \
    --no-default-features --features linux-futex -- --test-threads=1
else
  unsupported "Loom production protocol models" "target lacks 64-bit atomics"
fi

if [[ $linux == 1 && $atomic64 == 1 ]]; then
  run_gate long "serial actual-RMW process crash cuts" \
    cargo test --locked -p shmem-pod --lib fault_checks \
    --no-default-features --features linux-futex -- --test-threads=1
  run_gate normal "killed mapping initializer and participant" \
    cargo test --locked -p shmem-pod --test mapping_lifecycle \
    killed_initializer_and_admitted_process_fail_stuck_until_supervisor_poison \
    -- --exact --test-threads=1
  run_gate normal "killed allocator transaction" \
    cargo test --locked -p shmem-pod --test reloc_allocator \
    killed_transaction_stays_bounded_until_supervisor_poison \
    -- --exact --test-threads=1
  run_gate normal "killed migration owner in Copying" \
    cargo test --locked -p shmem-pod --test migration \
    killed_migrator_leaves_copying_state_and_never_steals_transaction \
    -- --exact --test-threads=1
  run_gate normal "killed migration owner in TargetReady" \
    cargo test --locked -p shmem-pod --test migration \
    killed_target_ready_migrator_leaves_source_authoritative \
    -- --exact --test-threads=1
  run_gate normal "killed migration owner after commit" \
    cargo test --locked -p shmem-pod --test migration \
    killed_post_commit_migrator_leaves_recovery_backing_available \
    -- --exact --test-threads=1
else
  unsupported "serial actual-RMW process crash cuts" "requires Linux and 64-bit atomics"
  unsupported "killed mapping initializer and participant" "requires Linux and 64-bit atomics"
  unsupported "killed allocator transaction" "requires Linux and 64-bit atomics"
  unsupported "migration crash phase matrix" "requires Linux and 64-bit atomics"
fi

run_gate long "compile locked standalone fuzz workspace" \
  cargo check --locked --manifest-path fuzz/Cargo.toml --bins
run_gate normal "generate deterministic fuzz corpus" \
  cargo run --locked --manifest-path fuzz/Cargo.toml \
  --bin generate-corpus -- "$corpus_root"

fuzz_targets=(
  image_header
  pod_artifact
  bootstrap_context
  layout_descriptor
  offset_resolution
)
nightly_available=0
if command -v rustup >/dev/null && rustup run "$nightly" rustc --version >/dev/null 2>&1; then
  nightly_available=1
fi
cargo_fuzz_available=0
if cargo fuzz --version >/dev/null 2>&1; then
  cargo_fuzz_available=1
fi

for target in "${fuzz_targets[@]}"; do
  if [[ $nightly_available == 0 ]]; then
    unavailable "fuzz target $target" "toolchain $nightly is not installed"
  elif [[ $cargo_fuzz_available == 0 ]]; then
    unavailable "fuzz target $target" "cargo-fuzz is not installed"
  else
    run_gate long "fuzz target $target" \
      env CARGO_TARGET_DIR=target/adversarial/fuzz \
      cargo +"$nightly" fuzz run "$target" "$corpus_root/$target" -- \
      -max_total_time="$fuzz_seconds" -max_len=1048576 -timeout=5
  fi
done

miri_available=0
rust_src_available=0
if [[ $nightly_available == 1 ]]; then
  if cargo +"$nightly" miri --version >/dev/null 2>&1; then
    miri_available=1
  fi
  if rustup component list --toolchain "$nightly" --installed 2>/dev/null \
    | grep -Eq '^rust-src( |$)'; then
    rust_src_available=1
  fi
fi

if [[ $nightly_available == 0 ]]; then
  unavailable "Miri parser and offset subset" "toolchain $nightly is not installed"
elif [[ $miri_available == 0 ]]; then
  unavailable "Miri parser and offset subset" "Miri is not installed for $nightly"
else
  run_gate long "Miri parser and offset subset" \
    env CARGO_TARGET_DIR=target/adversarial/miri \
    MIRIFLAGS="-Zmiri-strict-provenance -Zmiri-symbolic-alignment-check" \
    cargo +"$nightly" miri test --locked -p shmem-pod \
    --test layout --test offset --test bootstrap_connector \
    --no-default-features -- --test-threads=1
fi

if [[ $x86_64_linux == 0 ]]; then
  unsupported "AddressSanitizer thread subset" "validated runner requires x86-64 Linux"
  unsupported "ThreadSanitizer thread subset" "validated runner requires x86-64 Linux"
elif [[ $nightly_available == 0 ]]; then
  unavailable "AddressSanitizer thread subset" "toolchain $nightly is not installed"
  unavailable "ThreadSanitizer thread subset" "toolchain $nightly is not installed"
elif [[ $rust_src_available == 0 ]]; then
  unavailable "AddressSanitizer thread subset" "rust-src is not installed for $nightly"
  unavailable "ThreadSanitizer thread subset" "rust-src is not installed for $nightly"
else
  run_gate long "AddressSanitizer thread subset" \
    env CARGO_TARGET_DIR=target/adversarial/asan \
    RUSTFLAGS="-Zsanitizer=address" RUSTDOCFLAGS="-Zsanitizer=address" \
    ASAN_OPTIONS="detect_leaks=0:halt_on_error=1" \
    cargo +"$nightly" test -Zbuild-std --locked \
    --target x86_64-unknown-linux-gnu -p shmem-pod \
    --test dynamic_analysis --no-default-features -- --test-threads=1
  run_gate long "ThreadSanitizer thread subset" \
    env CARGO_TARGET_DIR=target/adversarial/tsan \
    RUSTFLAGS="-Zsanitizer=thread" RUSTDOCFLAGS="-Zsanitizer=thread" \
    TSAN_OPTIONS="halt_on_error=1:exitcode=66" \
    cargo +"$nightly" test -Zbuild-std --locked \
    --target x86_64-unknown-linux-gnu -p shmem-pod \
    --test dynamic_analysis --no-default-features -- --test-threads=1
fi

if [[ $mode == full ]]; then
  if [[ $x86_64_linux == 1 ]]; then
    run_gate long "connector fail-closed recovery suite" \
      ./scripts/test-connector-failures.sh
  else
    unsupported "connector fail-closed recovery suite" "requires x86-64 Linux"
  fi
fi

elapsed=$((SECONDS - started))
echo
echo "adversarial-check summary: mode=$mode gates=$gate_number pass=$pass_count fail=$fail_count unavailable=$unavailable_count unsupported=$unsupported_count timeouts=$timeout_count elapsed=${elapsed}s"
if ((timeout_count != 0)); then
  echo "FAIL: adversarial-check timed out"
  exit 124
fi
if ((fail_count != 0)); then
  echo "FAIL: adversarial-check found failing gates"
  exit 1
fi
if ((unavailable_count != 0 || unsupported_count != 0)); then
  if ((unavailable_count != 0)); then
    echo "UNAVAILABLE: adversarial-check has selected gates without tooling"
  else
    echo "UNSUPPORTED: adversarial-check has selected gates outside this platform"
  fi
  exit 2
fi
echo "PASS: adversarial-check mode=$mode"
