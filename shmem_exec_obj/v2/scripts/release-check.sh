#!/bin/bash -p
if [[ $- != *p* ]]; then
  printf '%s\n' 'release-check.sh must be executed directly so /bin/bash -p can protect startup' >&2
  builtin exit 2
fi
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
  SHMEM_POD_RUSTUP_BIN           Trusted absolute rustup executable override.
  SHMEM_POD_CURRENT_TOOLCHAIN    Current rustup toolchain name (default: stable).
  SHMEM_POD_MSRV_TOOLCHAIN       MSRV rustup toolchain name (default: 1.85.0).
  SHMEM_POD_CARGO_HOME           Source for registry/git caches, never active config.
  SHMEM_POD_RUSTUP_HOME          Absolute rustup toolchain store override.
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

script_dir=${BASH_SOURCE[0]%/*}
root=$(cd "$script_dir/.." && pwd -P)
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

readonly ENV_BIN=/usr/bin/env
readonly GETENT_BIN=/usr/bin/getent
readonly GIT_BIN=/usr/bin/git
readonly GREP_BIN=/usr/bin/grep
readonly JQ_BIN=/usr/bin/jq
readonly LN_BIN=/usr/bin/ln
readonly MKDIR_BIN=/usr/bin/mkdir
readonly MKTEMP_BIN=/usr/bin/mktemp
readonly READLINK_BIN=/usr/bin/readlink
readonly RM_BIN=/usr/bin/rm
readonly SHA256_BIN=/usr/bin/sha256sum
readonly STAT_BIN=/usr/bin/stat
readonly TIMEOUT_BIN=/usr/bin/timeout
readonly UNAME_BIN=/usr/bin/uname
readonly CONTROL_PATH=/usr/bin:/bin

verify_root_control_tool() {
  local path=$1
  local owner mode mode_value canonical
  canonical=$("$READLINK_BIN" -f -- "$path") || return 1
  [[ $canonical == /* && -f $canonical && -x $canonical ]] || return 1
  read -r owner mode < <("$STAT_BIN" -Lc '%u %a' -- "$canonical") || return 1
  mode_value=$((8#$mode))
  [[ $owner == 0 ]] && ((!(mode_value & 8#22)))
}

for control_tool in \
  "$ENV_BIN" "$GETENT_BIN" "$GIT_BIN" "$GREP_BIN" "$JQ_BIN" \
  "$LN_BIN" "$MKDIR_BIN" "$MKTEMP_BIN" "$READLINK_BIN" "$RM_BIN" \
  "$SHA256_BIN" "$STAT_BIN" \
  "$TIMEOUT_BIN" "$UNAME_BIN"; do
  if ! verify_root_control_tool "$control_tool"; then
    echo "release-check cannot trust fixed control tool: $control_tool" >&2
    exit 2
  fi
done

passwd_record=$("$GETENT_BIN" passwd "$EUID") || {
  echo "release-check cannot resolve the current account" >&2
  exit 2
}
IFS=: read -r account_name _ _ _ _ account_home _ <<<"$passwd_record"
if [[ -z $account_name || $account_home != /* || ! -d $account_home ]]; then
  echo "release-check cannot resolve a trustworthy account home" >&2
  exit 2
fi
readonly account_name account_home
readonly cargo_home=${SHMEM_POD_CARGO_HOME:-"$account_home/.cargo"}
readonly rustup_home=${SHMEM_POD_RUSTUP_HOME:-"$account_home/.rustup"}
if [[ $cargo_home != /* || $rustup_home != /* ]]; then
  echo "SHMEM_POD_CARGO_HOME and SHMEM_POD_RUSTUP_HOME must be absolute" >&2
  exit 2
fi

canonical_trusted_executable() {
  local label=$1 requested=$2 canonical owner mode mode_value component
  [[ $requested == /* ]] || {
    echo "$label must be an absolute executable path: $requested" >&2
    return 2
  }
  canonical=$("$READLINK_BIN" -f -- "$requested") || return 2
  [[ $canonical == /* && -f $canonical && -x $canonical ]] || {
    echo "$label is not an executable regular file: $canonical" >&2
    return 2
  }
  component=$canonical
  while :; do
    read -r owner mode < <("$STAT_BIN" -Lc '%u %a' -- "$component") || return 2
    mode_value=$((8#$mode))
    if [[ $owner != 0 && $owner != "$EUID" ]] || ((mode_value & 8#22)); then
      echo "$label has an untrusted path component: $component owner=$owner mode=$mode" >&2
      return 2
    fi
    [[ $component == / ]] && break
    component=${component%/*}
    [[ -n $component ]] || component=/
  done
  printf '%s\n' "$canonical"
}

rustup_request=${SHMEM_POD_RUSTUP_BIN:-"$account_home/.cargo/bin/rustup"}
if [[ -z ${SHMEM_POD_RUSTUP_BIN:-} && ! -x $rustup_request ]]; then
  for rustup_fallback in /usr/bin/rustup /usr/local/bin/rustup; do
    if [[ -x $rustup_fallback ]]; then
      rustup_request=$rustup_fallback
      break
    fi
  done
fi
RUSTUP_BIN=$(canonical_trusted_executable rustup "$rustup_request") || exit $?
readonly RUSTUP_BIN
rustup_exec() {
  "$ENV_BIN" -i HOME="$account_home" USER="$account_name" PATH="$CONTROL_PATH" \
    RUSTUP_HOME="$rustup_home" "$RUSTUP_BIN" "$@"
}
resolve_toolchain_binary() {
  local variable=$1 toolchain=$2 binary=$3 requested canonical
  requested=$(rustup_exec which "$binary" --toolchain "$toolchain" 2>/dev/null) || {
    echo "rustup toolchain $toolchain does not provide $binary" >&2
    exit 2
  }
  canonical=$(canonical_trusted_executable "$toolchain/$binary" "$requested") || exit $?
  printf -v "$variable" '%s' "$canonical"
}

readonly current_toolchain=${SHMEM_POD_CURRENT_TOOLCHAIN:-stable}
readonly msrv_toolchain=${SHMEM_POD_MSRV_TOOLCHAIN:-1.85.0}
if [[ ! $current_toolchain =~ ^[A-Za-z0-9._-]+$ || ! $msrv_toolchain =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Rust toolchain names contain invalid characters" >&2
  exit 2
fi
resolve_toolchain_binary CARGO_BIN "$current_toolchain" cargo
resolve_toolchain_binary RUSTC_BIN "$current_toolchain" rustc
resolve_toolchain_binary RUSTDOC_BIN "$current_toolchain" rustdoc
resolve_toolchain_binary CARGO_FMT_BIN "$current_toolchain" cargo-fmt
resolve_toolchain_binary RUSTFMT_BIN "$current_toolchain" rustfmt
resolve_toolchain_binary CARGO_CLIPPY_BIN "$current_toolchain" cargo-clippy
resolve_toolchain_binary CLIPPY_DRIVER_BIN "$current_toolchain" clippy-driver
resolve_toolchain_binary MSRV_CARGO_BIN "$msrv_toolchain" cargo
resolve_toolchain_binary MSRV_RUSTC_BIN "$msrv_toolchain" rustc
resolve_toolchain_binary MSRV_RUSTDOC_BIN "$msrv_toolchain" rustdoc
readonly CARGO_BIN RUSTC_BIN RUSTDOC_BIN CARGO_FMT_BIN RUSTFMT_BIN CARGO_CLIPPY_BIN CLIPPY_DRIVER_BIN
readonly MSRV_CARGO_BIN MSRV_RUSTC_BIN MSRV_RUSTDOC_BIN
source_revision=$("$GIT_BIN" rev-parse HEAD)
readonly source_revision

tmpdir=$("$MKTEMP_BIN" -d)
trap '"$RM_BIN" -rf "$tmpdir"' EXIT
active_cargo_home="$tmpdir/cargo-home"
"$MKDIR_BIN" -m 700 "$active_cargo_home"
for cache_dir in registry git; do
  if [[ -d $cargo_home/$cache_dir ]]; then
    "$LN_BIN" -s -- "$cargo_home/$cache_dir" "$active_cargo_home/$cache_dir"
  fi
done
readonly active_cargo_home

invocation_root="$tmpdir/invocation-root"
"$MKDIR_BIN" -m 700 "$invocation_root"
shopt -s dotglob nullglob
for source_entry in "$root"/*; do
  entry_name=${source_entry##*/}
  [[ $entry_name == .cargo ]] && continue
  "$LN_BIN" -s -- "$source_entry" "$invocation_root/$entry_name"
done
shopt -u dotglob nullglob
readonly invocation_root

config_dir=$invocation_root
while :; do
  for cargo_config in "$config_dir/.cargo/config" "$config_dir/.cargo/config.toml"; do
    if [[ -e $cargo_config || -L $cargo_config ]]; then
      echo "release-check rejects Cargo ancestor configuration: $cargo_config" >&2
      exit 1
    fi
  done
  [[ $config_dir == / ]] && break
  config_dir=${config_dir%/*}
  [[ -n $config_dir ]] || config_dir=/
done

declare -ar CURRENT_ENV=(
  "$ENV_BIN" -i --chdir="$invocation_root"
  HOME="$account_home" USER="$account_name" PATH="$CONTROL_PATH"
  CARGO_HOME="$active_cargo_home" RUSTUP_HOME="$rustup_home" CARGO_TERM_COLOR=never
  CARGO_INCREMENTAL=0 CARGO_NET_OFFLINE=true
  CARGO="$CARGO_BIN" RUSTC="$RUSTC_BIN" RUSTDOC="$RUSTDOC_BIN"
  RUSTFMT="$RUSTFMT_BIN"
)
declare -ar MSRV_ENV=(
  "$ENV_BIN" -i --chdir="$invocation_root"
  HOME="$account_home" USER="$account_name" PATH="$CONTROL_PATH"
  CARGO_HOME="$active_cargo_home" RUSTUP_HOME="$rustup_home" CARGO_TERM_COLOR=never
  CARGO_INCREMENTAL=0 CARGO_NET_OFFLINE=true
  CARGO="$MSRV_CARGO_BIN" RUSTC="$MSRV_RUSTC_BIN" RUSTDOC="$MSRV_RUSTDOC_BIN"
)
declare -ar NESTED_ENV=(
  "$ENV_BIN" -i --chdir="$invocation_root" HOME="$account_home" USER="$account_name"
  PATH="${CARGO_BIN%/*}:$CONTROL_PATH" CARGO_HOME="$active_cargo_home"
  RUSTUP_HOME="$rustup_home" CARGO_TERM_COLOR=never CARGO_INCREMENTAL=0
  CARGO_NET_OFFLINE=true
  SHMEM_POD_SOURCE_REVISION="$source_revision"
)

declare -A TOOL_PATHS=()
declare -A TOOL_DIGESTS=()
tool_digest() {
  local name=$1 path=$2 digest
  read -r digest _ < <("$SHA256_BIN" "$path")
  TOOL_PATHS[$name]=$path
  TOOL_DIGESTS[$name]=$digest
  printf '  %-20s %s sha256=%s\n' "$name" "$path" "$digest"
}
echo "release tool attestation"
tool_digest sha256sum "$SHA256_BIN"
tool_digest env "$ENV_BIN"
tool_digest getent "$GETENT_BIN"
tool_digest grep "$GREP_BIN"
tool_digest mktemp "$MKTEMP_BIN"
tool_digest readlink "$READLINK_BIN"
tool_digest rm "$RM_BIN"
tool_digest stat "$STAT_BIN"
tool_digest uname "$UNAME_BIN"
tool_digest rustup "$RUSTUP_BIN"
tool_digest cargo-current "$CARGO_BIN"
tool_digest rustc-current "$RUSTC_BIN"
tool_digest rustdoc-current "$RUSTDOC_BIN"
tool_digest cargo-fmt "$CARGO_FMT_BIN"
tool_digest rustfmt "$RUSTFMT_BIN"
tool_digest cargo-clippy "$CARGO_CLIPPY_BIN"
tool_digest clippy-driver "$CLIPPY_DRIVER_BIN"
tool_digest cargo-msrv "$MSRV_CARGO_BIN"
tool_digest rustc-msrv "$MSRV_RUSTC_BIN"
tool_digest rustdoc-msrv "$MSRV_RUSTDOC_BIN"
tool_digest git "$GIT_BIN"
tool_digest jq "$JQ_BIN"
tool_digest ln "$LN_BIN"
tool_digest mkdir "$MKDIR_BIN"
tool_digest timeout "$TIMEOUT_BIN"
readonly -A TOOL_PATHS TOOL_DIGESTS

revalidate_attestation() {
  local name path digest
  for name in "${!TOOL_PATHS[@]}"; do
    path=${TOOL_PATHS[$name]}
    [[ -f $path && -x $path ]] || {
      echo "FAIL: release tool disappeared during run: $name ($path)" >&2
      return 1
    }
    read -r digest _ < <("$SHA256_BIN" "$path")
    if [[ $digest != "${TOOL_DIGESTS[$name]}" ]]; then
      echo "FAIL: release tool digest changed during run: $name ($path)" >&2
      return 1
    fi
  done
  echo "ATTESTED: release tools unchanged at end of run"
}

if [[ $require_clean == 1 ]] && [[ -n $("$GIT_BIN" status --porcelain -- .) ]]; then
  echo "release-check requires a clean v2 tree:" >&2
  "$GIT_BIN" status --short -- . >&2
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
  "$TIMEOUT_BIN" --signal=TERM --kill-after=30s "${limit}s" "$@"
  local status=$?
  set -e
  if ((status != 0)); then
    if ((status == 124)); then
      echo "FAIL: $label exceeded its ${limit}s deadline" >&2
    elif ((status == 137)); then
      echo "FAIL: $label exited with SIGKILL status 137; cause may be OOM, supervisor, or timeout escalation" >&2
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
  "$TIMEOUT_BIN" --signal=TERM --kill-after=30s "${limit}s" "$@" >"$output"
  local status=$?
  set -e
  if ((status != 0)); then
    echo "FAIL: $label exited with status $status" >&2
    exit "$status"
  fi
}

echo "shmem-pod release check"
echo "  mode: $mode"
echo "  root: $root"
echo "  source revision: $source_revision"
echo "  kernel: $("$UNAME_BIN" -sr)"
echo "  architecture: $("$UNAME_BIN" -m)"
echo "  current rustc: $("${CURRENT_ENV[@]}" "$RUSTC_BIN" --version)"
echo "  current cargo: $("${CURRENT_ENV[@]}" "$CARGO_BIN" --version)"
echo "  MSRV rustc: $("${MSRV_ENV[@]}" "$MSRV_RUSTC_BIN" --version)"
echo "  MSRV cargo: $("${MSRV_ENV[@]}" "$MSRV_CARGO_BIN" --version)"
echo "  whole-run timeout: ${total_timeout}s"
echo "  process evidence: $([[ $skip_process == 1 ]] && echo skipped || echo required)"

run_gate normal "source revision" "$GIT_BIN" rev-parse HEAD
run_gate normal "host kernel and architecture" "$UNAME_BIN" -a
run_gate normal "current Rust version" "${CURRENT_ENV[@]}" "$RUSTC_BIN" --version --verbose
run_gate normal "current Cargo version" "${CURRENT_ENV[@]}" "$CARGO_BIN" --version --verbose
run_gate normal "MSRV Cargo availability" "${MSRV_ENV[@]}" "$MSRV_CARGO_BIN" --version
run_gate normal "format" "${CURRENT_ENV[@]}" "$CARGO_FMT_BIN" fmt --all -- --check

run_gate normal "current all-feature workspace check" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" check --locked --workspace --all-targets --all-features
run_gate long "current all-feature workspace tests" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" test --locked --workspace --all-features
run_gate normal "current no-default check" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" check --locked -p shmem-pod --all-targets --no-default-features
run_gate long "current no-default tests" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" test --locked -p shmem-pod --no-default-features

for feature in derive fixed-allocator linux-futex; do
  run_gate normal "current isolated feature: $feature" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" check --locked -p shmem-pod --all-targets \
    --no-default-features --features "$feature"
done

run_gate normal "MSRV all-feature workspace check" \
  "${MSRV_ENV[@]}" "$MSRV_CARGO_BIN" check --locked --workspace --all-targets --all-features
run_gate long "MSRV no-default tests" \
  "${MSRV_ENV[@]}" "$MSRV_CARGO_BIN" test --locked -p shmem-pod --no-default-features

if [[ $mode == full ]]; then
  run_gate long "MSRV all-feature workspace tests" \
    "${MSRV_ENV[@]}" "$MSRV_CARGO_BIN" test --locked --workspace --all-features
  for feature in derive fixed-allocator linux-futex; do
    run_gate normal "MSRV isolated feature: $feature" \
      "${MSRV_ENV[@]}" "$MSRV_CARGO_BIN" check --locked -p shmem-pod --all-targets \
      --no-default-features --features "$feature"
  done
fi

run_gate long "clippy" \
  "${CURRENT_ENV[@]}" CLIPPY_DRIVER="$CLIPPY_DRIVER_BIN" \
  "$CARGO_CLIPPY_BIN" clippy --locked --workspace --all-targets --all-features -- -D warnings
run_gate long "current rustdoc" \
  "${CURRENT_ENV[@]}" RUSTDOCFLAGS="-D warnings" \
  "$CARGO_BIN" doc --locked --workspace --all-features --no-deps
if [[ $mode == full ]]; then
  run_gate long "MSRV rustdoc" \
    "${MSRV_ENV[@]}" RUSTDOCFLAGS="-D warnings" \
    "$MSRV_CARGO_BIN" doc --locked --workspace --all-features --no-deps
fi

# Package the proc macro first. The library's dry-run verification then uses a
# local crates.io patch to model the already-published macro version.
run_gate long "package and verify shmem-pod-macros" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" package --locked --allow-dirty -p shmem-pod-macros
run_gate long "package and verify shmem-pod" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" package --locked --allow-dirty -p shmem-pod \
  --config 'patch.crates-io.shmem-pod-macros.path="crates/macros"'

macro_list="$tmpdir/macros-package.txt"
main_list="$tmpdir/main-package.txt"
metadata="$tmpdir/metadata.json"
run_capture normal "list shmem-pod-macros package" "$macro_list" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" package --locked --allow-dirty --list -p shmem-pod-macros
run_capture normal "list shmem-pod package" "$main_list" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" package --locked --allow-dirty --list -p shmem-pod \
  --config 'patch.crates-io.shmem-pod-macros.path="crates/macros"'
run_capture normal "inspect workspace publication policy" "$metadata" \
  "${CURRENT_ENV[@]}" "$CARGO_BIN" metadata --locked --format-version 1 --no-deps

if [[ $dry_run == 0 ]]; then
  for required in Cargo.toml README.md LICENSE-APACHE LICENSE-MIT src/lib.rs src/pod.rs; do
    "$GREP_BIN" -Fxq "$required" "$macro_list" || {
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
    tests/miri_pure.rs \
    tests/reloc_allocator.rs \
    tests/shared_collections.rs; do
    "$GREP_BIN" -Fxq "$required" "$main_list" || {
      echo "main package is missing $required" >&2
      exit 1
    }
  done
  if "$GREP_BIN" -Eq '(^|/)(poc|demos|fuzz|scripts|target|crates|ai_docs|\.minibeads)/' "$main_list"; then
    echo "main package contains a private harness or generated path:" >&2
    "$GREP_BIN" -E '(^|/)(poc|demos|fuzz|scripts|target|crates|ai_docs|\.minibeads)/' \
      "$main_list" >&2
    exit 1
  fi
  if "$GREP_BIN" -Fxq "docs/benchmarks.md" "$main_list"; then
    echo "main package contains repository-only benchmark documentation" >&2
    exit 1
  fi
  if ! "$JQ_BIN" -e '
    [.packages[] | select(.publish != []) | .name] | sort
      == ["shmem-pod", "shmem-pod-macros"]
  ' "$metadata" >/dev/null; then
    echo "workspace publication policy must expose exactly shmem-pod and shmem-pod-macros" >&2
    "$JQ_BIN" -r '.packages[] | "\(.name): publish=\(.publish)"' "$metadata" >&2
    exit 1
  fi
  echo "VERIFIED package contents: private POC, demo, build, and project paths excluded"
fi

if [[ $skip_process == 1 ]]; then
  echo
  echo "SKIP process evidence: RELEASE_CHECK_SKIP_PROCESS=1"
  echo "This run is compile/package evidence only and is not release-green."
else
  if [[ $("$UNAME_BIN" -s) != Linux || $("$UNAME_BIN" -m) != x86_64 ]]; then
    echo "process evidence currently requires Linux x86-64" >&2
    echo "Set RELEASE_CHECK_SKIP_PROCESS=1 for a non-release compile/package run." >&2
    exit 1
  fi

  run_gate adversarial "bounded adversarial validation" \
    "${NESTED_ENV[@]}" "$invocation_root/scripts/adversarial-check.sh" "$mode"

  run_gate long "typed mapping lifecycle process smoke" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example typed_mapping
  run_gate long "low-level layout handshake process smoke" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example layout_handshake
  run_gate long "coarse/fine/atomic counter process example" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example shared_counters
  run_gate long "process-shared futex example" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --features linux-futex --example futex_mutex
  run_gate long "SNZI process example" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example snzi
  run_gate long "closeable SNZI admission process example" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example closeable_snzi
  run_gate long "C-SNZI admission process example" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example csnzi
  run_gate long "SNZI topology comparison" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --release --example csnzi_comparison -- 2 2000
  run_gate long "relocatable allocator and collections process example" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example relocatable_collections
  run_gate long "schema migration and reclamation process example" \
    "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example schema_migration
  run_gate long "reproducible benchmark matrix smoke" \
    "${NESTED_ENV[@]}" "$root/scripts/run-benchmarks.sh" --smoke --output "$tmpdir/benchmark-smoke"

  if [[ $mode == quick ]]; then
    run_gate long "executable pod process smoke" \
      "${NESTED_ENV[@]}" POD_WORKERS=2 POD_THREADS=2 POD_ITERATIONS=100 \
      "$invocation_root/scripts/run-poc.sh"
    run_gate long "LD_PRELOAD unaware-guest process smoke" \
      "${NESTED_ENV[@]}" POD_DEPTH=1 POD_FANOUT=1 POD_THREADS=1 POD_CALLS=20 \
      "$invocation_root/scripts/run-preload-demo.sh"
  else
    run_gate long "relative-offset remapping example" \
      "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --example relative_offsets
    run_gate long "fixed allocator fork example" \
      "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --features fixed-allocator --example fixed_allocator_fork
    run_gate long "fixed allocator exec example" \
      "${CURRENT_ENV[@]}" "$CARGO_BIN" run --locked --features fixed-allocator --example fixed_allocator_exec
    run_gate long "executable pod process suite" \
      "${NESTED_ENV[@]}" POD_WORKERS=4 POD_THREADS=4 POD_ITERATIONS=2000 \
      "$invocation_root/scripts/run-poc.sh"
    run_gate long "LD_PRELOAD unaware-guest process suite" \
      "${NESTED_ENV[@]}" POD_DEPTH=2 POD_FANOUT=2 POD_THREADS=2 POD_CALLS=100 \
      "$invocation_root/scripts/run-preload-demo.sh"
    run_gate long "ptrace bootstrap and detach process suite" \
      "${NESTED_ENV[@]}" "$invocation_root/scripts/run-ptrace-demo.sh"
    run_gate long "connector fail-closed negative suite" \
      "${NESTED_ENV[@]}" "$invocation_root/scripts/test-connector-failures.sh"
  fi
fi

elapsed=$((SECONDS - started))
echo
if ! revalidate_attestation; then
  echo "FAIL: release-check tool attestation changed during execution" >&2
  exit 1
fi
if [[ $dry_run == 1 ]]; then
  echo "PLAN shmem-pod release check mode=$mode gates=$gate_number"
elif [[ $skip_process == 1 ]]; then
  echo "INCOMPLETE shmem-pod release check: required process evidence was skipped"
  exit 2
else
  echo "PASS shmem-pod release check mode=$mode gates=$gate_number elapsed=${elapsed}s"
fi
