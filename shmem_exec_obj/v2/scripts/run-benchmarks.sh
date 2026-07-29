#!/bin/bash -p
set -Eeuo pipefail

# Returning from a subshell succeeds only when this file was sourced. Reject it
# before a caller-defined function can interpose on any control operation.
if (return 0 2>/dev/null); then
  return 1
fi

if [[ $- != *p* ]]; then
  echo "run-benchmarks.sh requires a privileged /bin/bash -p interpreter" >&2
  exit 1
fi

required_control_tools=(
  as awk cat chmod cmp cp date diff env find gcc git jq ld ldd ln mkdir mktemp
  mv nproc od readlink rm sed sha256sum sort stat sync tail timeout tr uname wc
)
for control_tool in "${required_control_tools[@]}" cargo rustc; do
  if declare -F "$control_tool" >/dev/null; then
    echo "benchmark control executable is shadowed by a shell function: $control_tool" >&2
    exit 1
  fi
done
launch_home=${HOME:?HOME is required}
launch_rustc=$(type -P rustc) || { echo "run-benchmarks.sh requires rustc" >&2; exit 1; }
launch_cargo=$(type -P cargo) || { echo "run-benchmarks.sh requires cargo" >&2; exit 1; }
export PATH=/usr/bin:/bin
export LC_ALL=C LANG=C TZ=UTC
[[ -x /usr/bin/stat ]] || { echo "run-benchmarks.sh requires /usr/bin/stat" >&2; exit 1; }
for control_tool in "${required_control_tools[@]}"; do
  control_path="/usr/bin/$control_tool"
  control_uid=$(/usr/bin/stat -Lc %u -- "$control_path" 2>/dev/null || printf invalid)
  control_mode=$(/usr/bin/stat -Lc %a -- "$control_path" 2>/dev/null || printf invalid)
  if [[ ! -x $control_path || ! $control_uid =~ ^[0-9]+$ ||
        ! $control_mode =~ ^[0-7]+$ ]] ||
     ((10#$control_uid != 0 || (8#$control_mode & 8#022) != 0)); then
    echo "run-benchmarks.sh requires a root-owned, non-writable control executable: $control_path" >&2
    exit 1
  fi
done
bash_uid=$(/usr/bin/stat -Lc %u -- /bin/bash 2>/dev/null || printf invalid)
bash_mode=$(/usr/bin/stat -Lc %a -- /bin/bash 2>/dev/null || printf invalid)
if [[ ! -x /bin/bash || ! $bash_uid =~ ^[0-9]+$ || ! $bash_mode =~ ^[0-7]+$ ]] ||
   ((10#$bash_uid != 0 || (8#$bash_mode & 8#022) != 0)); then
  echo "run-benchmarks.sh requires root-owned, non-writable /bin/bash" >&2
  exit 1
fi

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
  provenance/source/   Immutable snapshot of every non-ignored v2 input.
  bundle-inventory.tsv Exact mode, size, and digest of each retained payload.

Counts are explicit in environment.json. Results describe only this host/run;
the suite deliberately makes no portable performance claim.
EOF
}

script_directory=${BASH_SOURCE[0]%/*}
[[ $script_directory != "${BASH_SOURCE[0]}" ]] || script_directory=.
root=$(cd "$script_directory/.." && pwd -P)
cd "$root"

mode=standard
output_dir=
output_claimed=0
run_succeeded=0
temporary=
runner_owner_token=
source_snapshot=
source_manifest=
source_snapshot_manifest=
source_paths_file=
warmup=
iterations=
samples=
workers=
run_timeout=

cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n $temporary && -d $temporary ]]; then
    rm -rf -- "$temporary"
  fi
  if ((output_claimed == 1 && run_succeeded == 0)) && [[ -n $output_dir && -d $output_dir ]]; then
    local owner_path="$output_dir/runner-owner"
    local owner_run owner_token
    owner_run=$(sed -n 's/^run_id=//p' "$owner_path" 2>/dev/null || true)
    owner_token=$(sed -n 's/^token=//p' "$owner_path" 2>/dev/null || true)
    if [[ -n $runner_owner_token && -f $owner_path &&
          $owner_run == "$run_id" && $owner_token == "$runner_owner_token" ]]; then
      chmod -R u+w -- "$output_dir" 2>/dev/null || true
      rm -rf -- "$output_dir"
    else
      echo "refusing to clean output directory without this run's owner token: $output_dir" >&2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT

reject_ambient_build_controls() {
  local name
  local -a rejected=()
  while IFS= read -r name; do
    [[ $name == RUSTUP_TOOLCHAIN ]] && continue
    case "$name" in
      CARGO_* | RUSTC | RUSTC_* | RUSTDOC | RUSTDOC_* | RUSTFLAGS | \
        RUSTDOCFLAGS | RUST_BACKTRACE | RUSTUP_HOME | RUSTUP_DIST_SERVER | \
        RUSTUP_UPDATE_ROOT | POD_RUSTC | CC | CC_* | *_CC | CXX | CXX_* | *_CXX | \
        CPP | CPP_* | *_CPP | AR | AR_* | *_AR | AS | AS_* | *_AS | LD | LD_* | \
        *_LD | NM | NM_* | *_NM | RANLIB | RANLIB_* | *_RANLIB | STRIP | STRIP_* | \
        *_STRIP | OBJCOPY | OBJCOPY_* | *_OBJCOPY | OBJDUMP | OBJDUMP_* | \
        *_OBJDUMP | CFLAGS | CFLAGS_* | *_CFLAGS | CXXFLAGS | CXXFLAGS_* | \
        *_CXXFLAGS | CPPFLAGS | CPPFLAGS_* | *_CPPFLAGS | LDFLAGS | LDFLAGS_* | \
        *_LDFLAGS | LIBRARY_PATH | CPATH | C_INCLUDE_PATH | CPLUS_INCLUDE_PATH | \
        OBJC_INCLUDE_PATH | GCC_EXEC_PREFIX | GCC_COMPARE_DEBUG | COMPILER_PATH | \
        COLLECT_GCC | COLLECT_GCC_OPTIONS | COLLECT_LTO_WRAPPER | \
        DEPENDENCIES_OUTPUT | SUNPRO_DEPENDENCIES | PKG_CONFIG* | BINDGEN* | \
        LIBCLANG* | CLANG* | LLVM* | \
        SCCACHE* | CACHEPOT* | DISTCC* | ICECC* | MAKEFLAGS | MFLAGS | NUM_JOBS | \
        HOST | TARGET | OUT_DIR | OPT_LEVEL | DEBUG | PROFILE | DEP_* | \
        SOURCE_DATE_EPOCH)
        rejected+=("$name")
        ;;
    esac
  done < <(compgen -e | LC_ALL=C sort -u)
  if ((${#rejected[@]} != 0)); then
    echo "benchmark builds reject inherited build controls; use RUSTUP_TOOLCHAIN to select a toolchain" >&2
    printf '  rejected: %s\n' "${rejected[@]}" >&2
    return 1
  fi
}

assert_no_cargo_config_ancestors() {
  local directory=$1
  while :; do
    if [[ -e $directory/.cargo/config || -L $directory/.cargo/config ||
          -e $directory/.cargo/config.toml || -L $directory/.cargo/config.toml ]]; then
      echo "Cargo configuration escapes the hermetic benchmark environment: $directory/.cargo" >&2
      return 1
    fi
    [[ $directory == / ]] && break
    directory=${directory%/*}
    [[ -n $directory ]] || directory=/
  done
}

verify_cargo_discovery_roots() {
  local directory uid mode unexpected
  for directory in / /usr /usr/share "$build_cargo_home"; do
    uid=$(stat -Lc %u -- "$directory" 2>/dev/null || printf invalid)
    mode=$(stat -Lc %a -- "$directory" 2>/dev/null || printf invalid)
    if [[ ! -d $directory || -L $directory || ! $uid =~ ^[0-9]+$ ||
          ! $mode =~ ^[0-7]+$ ]] ||
       ((10#$uid != 0 || (8#$mode & 8#022) != 0)); then
      echo "Cargo discovery root is not a root-owned, non-writable directory: $directory" >&2
      return 1
    fi
  done
  unexpected=$(find "$build_cargo_home" -mindepth 1 -print -quit)
  if [[ -n $unexpected ]]; then
    echo "Cargo build home is not empty: $unexpected" >&2
    return 1
  fi
  assert_no_cargo_config_ancestors /
}

resolve_program() {
  local program=$1
  local candidate
  candidate=$(command -v -- "$program") || {
    echo "cannot resolve required executable through PATH: $program" >&2
    return 1
  }
  candidate=$(readlink -f -- "$candidate")
  if [[ ! -f $candidate || ! -x $candidate ]]; then
    echo "resolved executable is not a regular executable file: $program -> $candidate" >&2
    return 1
  fi
  printf '%s' "$candidate"
}

write_host_linker_manifest() {
  local output=$1
  local label path digest
  : >"$output"
  while IFS=$'\t' read -r label path; do
    if [[ ! -f $path || -L $path ]]; then
      echo "host linker closure member is not a regular file: $label -> $path" >&2
      return 1
    fi
    path=$(readlink -f -- "$path")
    digest=$(sha256sum -- "$path" | awk '{print $1}')
    printf '%s\t%s\t%s\n' "$label" "$path" "$digest" >>"$output"
  done <"$host_linker_paths"
}

write_control_tool_manifest() {
  local output=$1
  local label path uid mode digest
  : >"$output"
  while IFS=$'\t' read -r label path; do
    if [[ ! -f $path || -L $path || ! -x $path ]]; then
      echo "benchmark control tool is not a canonical executable: $label -> $path" >&2
      return 1
    fi
    uid=$(stat -Lc %u -- "$path")
    mode=$(stat -Lc %a -- "$path")
    if [[ $uid != 0 || ! $mode =~ ^[0-7]+$ ]] ||
       ((8#$mode & 8#022)); then
      echo "benchmark control tool is not root-owned and non-writable: $label -> $path" >&2
      return 1
    fi
    digest=$(sha256sum -- "$path" | awk '{print $1}')
    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$label" "$path" "$uid" "$mode" "$digest" >>"$output"
  done <"$control_tool_paths"
}

verify_control_tool_manifest() {
  local current="$temporary/control-tools-current.tsv"
  write_control_tool_manifest "$current"
  if ! cmp -s -- "$control_tool_manifest" "$current"; then
    echo "benchmark integrity-control executable set changed during the run" >&2
    diff -u -- "$control_tool_manifest" "$current" >&2 || true
    return 1
  fi
}

append_dynamic_dependency_paths() {
  local label=$1
  local executable=$2
  local destination=$3
  local output="$temporary/ldd-$label.txt"
  local path index=0
  if ! "$env_path" -i LC_ALL=C LANG=C "$ldd_path" "$executable" >"$output" 2>&1; then
    echo "cannot resolve dynamic-library evidence for $label: $executable" >&2
    sed -n '1,40p' "$output" >&2
    return 1
  fi
  if awk '/not found/ { found = 1 } END { exit !found }' "$output"; then
    echo "dynamic-library evidence contains an unresolved dependency for $label" >&2
    sed -n '1,40p' "$output" >&2
    return 1
  fi
  while IFS= read -r path; do
    path=$(readlink -f -- "$path")
    printf 'dynamic.%s.%s\t%s\n' "$label" "$index" "$path" >>"$destination"
    index=$((index + 1))
  done < <(
    awk '
      /=>/ && $3 ~ /^\// { print $3 }
      /^[[:space:]]*\// { print $1 }
    ' "$output" | LC_ALL=C sort -u
  )
}

write_host_linker_config() {
  local output=$1
  {
    printf 'driver=%s\n' "$host_linker_path"
    printf 'specs_probe=%s\n' "$(gcc_control -print-file-name=specs)"
    printf 'dumpmachine=%s\n' "$(gcc_control -dumpmachine)"
    printf 'dumpversion=%s\n' "$(gcc_control -dumpversion)"
    printf '%s\n' '--- search-dirs ---'
    gcc_control -print-search-dirs
    printf '%s\n' '--- built-in-specs ---'
    gcc_control -dumpspecs
  } >"$output"
}

verify_host_linker_manifest() {
  local manifest=$1
  local current="$temporary/host-linker-current.tsv"
  local dynamic_current="$temporary/host-linker-dynamic-current.tsv"
  local dynamic_initial="$temporary/host-linker-dynamic-initial.tsv"
  local label executable
  write_host_linker_manifest "$current"
  if ! cmp -s -- "$manifest" "$current"; then
    echo "host linker executable/input closure changed during the benchmark run" >&2
    diff -u -- "$manifest" "$current" >&2 || true
    return 1
  fi
  : >"$dynamic_current"
  while IFS=$'\t' read -r label executable; do
    append_dynamic_dependency_paths "$label" "$executable" "$dynamic_current"
  done <"$dynamic_executables"
  awk -F '\t' '$1 ~ /^dynamic\./ { print }' "$host_linker_paths" >"$dynamic_initial"
  if ! cmp -s -- "$dynamic_initial" "$dynamic_current"; then
    echo "host executable dynamic-library path closure changed during the benchmark run" >&2
    diff -u -- "$dynamic_initial" "$dynamic_current" >&2 || true
    return 1
  fi
  current="$temporary/host-linker-config-current.txt"
  write_host_linker_config "$current"
  if ! cmp -s -- "$host_linker_config" "$current"; then
    echo "host linker specs/search configuration changed during the benchmark run" >&2
    diff -u -- "$host_linker_config" "$current" >&2 || true
    return 1
  fi
}

verify_vendor_stable() {
  local current="$temporary/vendor-current.tsv"
  write_directory_manifest "$vendor_dir" "$current"
  if ! cmp -s -- "$vendor_manifest" "$current"; then
    echo "retained Cargo vendor input tree changed during the benchmark run" >&2
    diff -u -- "$vendor_manifest" "$current" >&2 || true
    return 1
  fi
}

retain_rust_sysroot() {
  local source=$1
  local destination=$2
  local cargo_source=$3
  local paths before copied after symlink cargo_before cargo_copied cargo_after
  paths="$temporary/rust-sysroot-paths.z"
  before="$temporary/rust-sysroot-source-before.tsv"
  copied="$temporary/rust-sysroot-copy.tsv"
  after="$temporary/rust-sysroot-source-after.tsv"

  if [[ ! -f $source/bin/rustc || -L $source/bin/rustc ]]; then
    echo "selected Rust sysroot lacks a regular bin/rustc: $source" >&2
    return 1
  fi
  if [[ ! -f $cargo_source || -L $cargo_source ]]; then
    echo "selected Cargo is not a regular file: $cargo_source" >&2
    return 1
  fi
  symlink=$(find "$source/lib" -type l -print -quit)
  if [[ -n $symlink ]]; then
    echo "selected Rust sysroot contains a symlink whose target escapes retention: $symlink" >&2
    return 1
  fi
  {
    printf 'bin/rustc\0'
    (cd "$source" && find lib -type f -printf '%P\0' | sed -z 's!^!lib/!' | LC_ALL=C sort -z)
  } >"$paths"
  write_manifest_from_paths "$source" "$paths" "$before"
  cargo_before="$(stat -c '%a:%s' -- "$cargo_source"):$(sha256sum "$cargo_source" | awk '{print $1}')"

  mkdir -p "$destination/bin"
  cp -a --reflink=auto -- "$source/bin/rustc" "$destination/bin/rustc"
  cp -a --reflink=auto -- "$cargo_source" "$destination/bin/cargo"
  cp -a --reflink=auto -- "$source/lib" "$destination/lib"
  write_manifest_from_paths "$destination" "$paths" "$copied"
  write_manifest_from_paths "$source" "$paths" "$after"
  cargo_copied="$(stat -c '%a:%s' -- "$destination/bin/cargo"):$(sha256sum "$destination/bin/cargo" | awk '{print $1}')"
  cargo_after="$(stat -c '%a:%s' -- "$cargo_source"):$(sha256sum "$cargo_source" | awk '{print $1}')"
  if ! cmp -s -- "$before" "$copied" || ! cmp -s -- "$before" "$after" ||
     ! [[ $cargo_before == "$cargo_copied" && $cargo_before == "$cargo_after" ]]; then
    echo "selected Rust sysroot changed or was copied inconsistently" >&2
    diff -u -- "$before" "$copied" >&2 || true
    diff -u -- "$before" "$after" >&2 || true
    return 1
  fi

  chmod -R a-w -- "$destination"
  rust_sysroot_manifest="$provenance_dir/rust-sysroot-manifest.tsv"
  write_directory_manifest "$destination" "$rust_sysroot_manifest"
  rust_sysroot_manifest_sha256=$(sha256sum "$rust_sysroot_manifest" | awk '{print $1}')
  rust_sysroot_file_count=$(wc -l <"$rust_sysroot_manifest")
}

verify_retained_sysroot_stable() {
  local current="$temporary/rust-sysroot-current.tsv"
  write_directory_manifest "$retained_rust_sysroot" "$current"
  if ! cmp -s -- "$rust_sysroot_manifest" "$current"; then
    echo "retained Rust compiler/sysroot changed during the benchmark run" >&2
    diff -u -- "$rust_sysroot_manifest" "$current" >&2 || true
    return 1
  fi
}

runner_owner_contents() {
  printf 'shmem-pod-benchmark-runner-owner-v1\nrun_id=%s\ntoken=%s\n' \
    "$run_id" "$runner_owner_token"
}

git_control() {
  /usr/bin/env -i \
    HOME="$control_home" \
    XDG_CONFIG_HOME="$control_home/.config" \
    PATH=/usr/bin:/bin \
    LC_ALL=C LANG=C TZ=UTC \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null \
    GIT_OPTIONAL_LOCKS=0 \
    /usr/bin/git "$@"
}

gcc_control() {
  /usr/bin/env -i "${hermetic_env[@]}" "$host_linker_path" "$@"
}

status_fingerprint() {
  git_control status --porcelain=v2 --untracked-files=all -- . |
    sha256sum | awk '{print $1}'
}

enumerate_source_paths() {
  git_control ls-files -co --exclude-standard -z -- . | LC_ALL=C sort -z
}

path_hex() {
  od -An -v -tx1 | tr -d ' \n'
}

write_manifest_from_paths() {
  local base=$1
  local paths=$2
  local output=$3
  local path full kind mode size digest encoded
  : >"$output"
  while IFS= read -r -d '' path; do
    full="$base/$path"
    if [[ -L $full ]]; then
      kind='symlink'
      mode=$(stat -c '%a' -- "$full")
      size=$(stat -c '%s' -- "$full")
      digest=$(readlink -z -- "$full" | sha256sum | awk '{print $1}')
    elif [[ -f $full ]]; then
      kind='file'
      mode=$(stat -c '%a' -- "$full")
      size=$(stat -c '%s' -- "$full")
      digest=$(sha256sum -- "$full" | awk '{print $1}')
    else
      echo "source input is missing or not a regular file/symlink: $path" >&2
      return 1
    fi
    encoded=$(printf '%s' "$path" | path_hex)
    printf '%s\t%s\t%s\t%s\t%s\n' \
      "$kind" "$mode" "$size" "$digest" "$encoded" >>"$output"
  done <"$paths"
}

write_live_manifest() {
  local paths=$1
  local output=$2
  enumerate_source_paths >"$paths"
  write_manifest_from_paths "$root" "$paths" "$output"
}

snapshot_source() {
  local path destination snapshot_paths snapshot_manifest live_paths_after live_manifest_after
  while IFS= read -r -d '' path; do
    if [[ -L $root/$path ]]; then
      echo "source snapshots reject symlinks because their target bytes escape the manifest: $path" >&2
      return 1
    fi
  done <"$source_paths_file"

  while IFS= read -r -d '' path; do
    destination="$source_snapshot/$path"
    mkdir -p -- "$(dirname "$destination")"
    cp -a -- "$root/$path" "$destination"
  done <"$source_paths_file"

  snapshot_paths="$temporary/snapshot-paths.z"
  snapshot_manifest="$temporary/snapshot-manifest.tsv"
  (
    cd "$source_snapshot"
    find . -mindepth 1 \( -type f -o -type l \) -printf '%P\0' | LC_ALL=C sort -z
  ) >"$snapshot_paths"
  if ! cmp -s -- "$source_paths_file" "$snapshot_paths"; then
    echo "source snapshot path set differs from the initial live path set" >&2
    return 1
  fi
  write_manifest_from_paths "$source_snapshot" "$snapshot_paths" "$snapshot_manifest"
  if ! cmp -s -- "$source_manifest" "$snapshot_manifest"; then
    echo "source snapshot bytes differ from the initial live manifest" >&2
    return 1
  fi

  live_paths_after="$temporary/live-paths-after-copy.z"
  live_manifest_after="$temporary/live-manifest-after-copy.tsv"
  write_live_manifest "$live_paths_after" "$live_manifest_after"
  if ! cmp -s -- "$source_manifest" "$live_manifest_after"; then
    echo "live source changed while the immutable snapshot was copied" >&2
    return 1
  fi
  chmod -R a-w -- "$source_snapshot"
  source_snapshot_manifest="$temporary/retained-source-manifest.tsv"
  write_manifest_from_paths \
    "$source_snapshot" "$source_paths_file" "$source_snapshot_manifest"
}

verify_snapshot_stable() {
  local paths manifest
  paths="$temporary/snapshot-verify-paths.z"
  manifest="$temporary/snapshot-verify-manifest.tsv"
  (
    cd "$source_snapshot"
    find . -mindepth 1 \( -type f -o -type l \) -printf '%P\0' | LC_ALL=C sort -z
  ) >"$paths"
  if ! cmp -s -- "$source_paths_file" "$paths"; then
    echo "retained source snapshot path set changed" >&2
    return 1
  fi
  write_manifest_from_paths "$source_snapshot" "$paths" "$manifest"
  if ! cmp -s -- "$source_snapshot_manifest" "$manifest"; then
    echo "retained source snapshot bytes changed" >&2
    return 1
  fi
}

source_tree_fingerprint() {
  local paths manifest
  paths="$temporary/live-fingerprint-paths.z"
  manifest="$temporary/live-fingerprint-manifest.tsv"
  write_live_manifest "$paths" "$manifest"
  sha256sum "$manifest" | awk '{print $1}'
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

verify_harness_result_bytes() {
  verify_sha256 "$output_dir/results.jsonl" "$harness_results_jsonl_sha256"
  verify_sha256 "$output_dir/results.csv" "$harness_results_csv_sha256"
}

verify_toolchain_stable() {
  verify_cargo_discovery_roots
  verify_control_tool_manifest
  verify_sha256 "$rustc_launcher_path" "$rustc_launcher_sha256"
  verify_sha256 "$cargo_launcher_path" "$cargo_launcher_sha256"
  verify_sha256 "$rustc_path" "$rustc_sha256"
  verify_sha256 "$cargo_path" "$cargo_sha256"
  verify_sha256 "$env_path" "$env_sha256"
  verify_sha256 "$timeout_path" "$timeout_sha256"
  verify_sha256 "$uname_path" "$uname_sha256"
  verify_sha256 "$ldd_path" "$ldd_sha256"
  verify_sha256 "$host_linker_path" "$host_linker_sha256"
  verify_sha256 "$host_ld_path" "$host_ld_sha256"
  verify_sha256 "$host_as_path" "$host_as_sha256"
  verify_host_linker_manifest "$host_linker_manifest"
  verify_vendor_stable
  verify_retained_sysroot_stable
  if [[ $(cd / && "$env_path" -i "${hermetic_env[@]}" \
            "$rustc_path" --version --verbose) != \
          "$SHMEM_POD_BENCH_RUSTC" ||
        $(cd / && "$env_path" -i "${hermetic_env[@]}" \
            "$cargo_path" --version --verbose) != \
          "$SHMEM_POD_BENCH_CARGO" ]]; then
    echo "Rust toolchain identity changed during the benchmark run" >&2
    return 1
  fi
}

verify_source_stable() {
  local revision status_sha256 tree_sha256
  revision=$(git_control rev-parse HEAD)
  status_sha256=$(status_fingerprint)
  tree_sha256=$(source_tree_fingerprint)
  if [[ $revision != "$source_revision_before" ||
        $status_sha256 != "$source_status_sha256_before" ||
        $tree_sha256 != "$source_manifest_sha256" ]]; then
    echo "live benchmark source changed after its snapshot was captured" >&2
    echo "  revision: $source_revision_before -> $revision" >&2
    echo "  status:   $source_status_sha256_before -> $status_sha256" >&2
    echo "  manifest: $source_manifest_sha256 -> $tree_sha256" >&2
    exit 1
  fi
  verify_snapshot_stable
}

manifest_value() {
  local manifest=$1
  local key=$2
  awk -v key="$key" '
    index($0, key "=") == 1 {
      count += 1
      value = substr($0, length(key) + 2)
    }
    END {
      if (count != 1) exit 1
      print value
    }
  ' "$manifest"
}

verify_compiler_manifest_file() {
  local manifest=$1
  local path_key=$2
  local digest_key=$3
  local expected_path=$4
  local found_path expected_digest
  found_path=$(manifest_value "$manifest" "$path_key")
  if [[ $found_path != "$expected_path" ]]; then
    echo "compiler manifest $path_key path mismatch" >&2
    return 1
  fi
  expected_digest=$(manifest_value "$manifest" "$digest_key")
  if [[ ! $expected_digest =~ ^[0-9a-f]{64}$ ]]; then
    echo "compiler manifest $digest_key is not SHA-256" >&2
    return 1
  fi
  verify_sha256 "$expected_path" "$expected_digest"
}

verify_compiler_dependencies() {
  local manifest=$1
  local prefix=$2
  local index path digest canonical count=0 sha_count
  local -A seen=()
  while IFS=$'\t' read -r index path; do
    if [[ $index != "$count" ]]; then
      echo "compiler manifest $prefix dependency indices are not contiguous" >&2
      return 1
    fi
    if [[ $path != "$source_snapshot/"* || ! -f $path || -L $path ]]; then
      echo "compiler manifest $prefix dependency escapes the retained snapshot: $path" >&2
      return 1
    fi
    canonical=$(readlink -f -- "$path")
    if [[ $canonical != "$path" || -v seen[$path] ]]; then
      echo "compiler manifest $prefix dependency is noncanonical or duplicated: $path" >&2
      return 1
    fi
    seen[$path]=1
    digest=$(manifest_value "$manifest" "$prefix.dependency.$index.sha256")
    if [[ ! $digest =~ ^[0-9a-f]{64}$ ]]; then
      echo "compiler manifest $prefix dependency $index digest is not SHA-256" >&2
      return 1
    fi
    verify_sha256 "$path" "$digest"
    count=$((count + 1))
  done < <(
    awk -v prefix="$prefix" '
      {
        equals = index($0, "=")
        if (equals == 0) next
        key = substr($0, 1, equals - 1)
        value = substr($0, equals + 1)
        expected = "^" prefix "\\.dependency\\.[0-9]+\\.path$"
        if (key ~ expected) {
          index_value = key
          sub("^" prefix "\\.dependency\\.", "", index_value)
          sub("\\.path$", "", index_value)
          print index_value "\t" value
        }
      }
    ' "$manifest"
  )
  sha_count=$(awk -v prefix="$prefix" '
    $0 ~ ("^" prefix "\\.dependency\\.[0-9]+\\.sha256=") { count += 1 }
    END { print count + 0 }
  ' "$manifest")
  if ((count == 0 || count != sha_count)); then
    echo "compiler manifest $prefix dependency path/digest count mismatch" >&2
    return 1
  fi
}

write_directory_manifest() {
  local base=$1
  local output=$2
  local paths
  paths="$temporary/directory-paths.z"
  (
    cd "$base"
    find . -mindepth 1 \( -type f -o -type l \) \
      ! -path './bundle-inventory.tsv' \
      ! -path './environment.json' \
      -printf '%P\0' | LC_ALL=C sort -z
  ) >"$paths"
  write_manifest_from_paths "$base" "$paths" "$output"
}

verify_bundle_inventory() {
  local current
  current="$temporary/bundle-inventory-current.tsv"
  write_directory_manifest "$output_dir" "$current"
  if ! cmp -s -- "$output_dir/bundle-inventory.tsv" "$current"; then
    echo "benchmark bundle inventory changed before completion" >&2
    diff -u "$output_dir/bundle-inventory.tsv" "$current" >&2 || true
    return 1
  fi
}

proc_status_field() {
  local field=$1
  awk -v field="$field" '
    index($0, field) == 1 {
      value = substr($0, length(field) + 1)
      sub(/^[[:space:]]+/, "", value)
      print value
      exit
    }
  ' /proc/self/status
}

cgroup_source_path() {
  local directory=$1
  local relative=${directory#/sys/fs/cgroup}
  printf '%s' "${relative:-/}"
}

collect_cgroup_context() {
  runtime_cgroup_path=$(sed -n 's/^0:://p' /proc/self/cgroup)
  if [[ -z $runtime_cgroup_path ]]; then
    runtime_cgroup_path=unknown
    runtime_cpu_max=unknown
    runtime_cpu_source=unknown
    runtime_memory_max=unknown
    runtime_memory_source=unknown
    runtime_swap_max=unknown
    runtime_swap_source=unknown
    runtime_cpuset=unknown
    runtime_cpuset_source=unknown
    runtime_cpuset_mems=unknown
    runtime_cpuset_mems_source=unknown
    return
  fi

  local root=/sys/fs/cgroup
  local directory="$root/${runtime_cgroup_path#/}"
  local -a ancestors=()
  while :; do
    ancestors+=("$directory")
    [[ $directory == "$root" ]] && break
    directory=${directory%/*}
  done

  runtime_cpu_max=max
  runtime_cpu_source=none
  local best_quota='' best_period='' contents quota period extra
  for directory in "${ancestors[@]}"; do
    [[ -r $directory/cpu.max ]] || continue
    contents=$(<"$directory/cpu.max")
    read -r quota period extra <<<"$contents"
    [[ $quota =~ ^[0-9]+$ && $period =~ ^[1-9][0-9]*$ && -z ${extra:-} ]] || continue
    if [[ -z $best_quota ]] ||
       ((quota * best_period < best_quota * period)); then
      best_quota=$quota
      best_period=$period
      runtime_cpu_max="$quota $period"
      runtime_cpu_source=$(cgroup_source_path "$directory")
    fi
  done

  local name value normalized best
  for name in memory.max memory.swap.max; do
    best=
    local source=none
    for directory in "${ancestors[@]}"; do
      [[ -r $directory/$name ]] || continue
      value=$(<"$directory/$name")
      [[ $value =~ ^[0-9]+$ ]] || continue
      normalized=$(canonical_uint "$value")
      if [[ -z $best ]] ||
         ((${#normalized} < ${#best})) ||
         [[ ${#normalized} -eq ${#best} && $normalized < $best ]]; then
        best=$normalized
        source=$(cgroup_source_path "$directory")
      fi
    done
    if [[ $name == memory.max ]]; then
      runtime_memory_max=${best:-max}
      runtime_memory_source=$source
    else
      runtime_swap_max=${best:-max}
      runtime_swap_source=$source
    fi
  done

  runtime_cpuset=unknown
  runtime_cpuset_source=unknown
  runtime_cpuset_mems=unknown
  runtime_cpuset_mems_source=unknown
  for directory in "${ancestors[@]}"; do
    if [[ $runtime_cpuset == unknown && -r $directory/cpuset.cpus.effective ]]; then
      value=$(<"$directory/cpuset.cpus.effective")
      if [[ -n $value ]]; then
        runtime_cpuset=$value
        runtime_cpuset_source=$(cgroup_source_path "$directory")
      fi
    fi
    if [[ $runtime_cpuset_mems == unknown && -r $directory/cpuset.mems.effective ]]; then
      value=$(<"$directory/cpuset.mems.effective")
      if [[ -n $value ]]; then
        runtime_cpuset_mems=$value
        runtime_cpuset_mems_source=$(cgroup_source_path "$directory")
      fi
    fi
  done
}

collect_runtime_context() {
  local output=$1
  runtime_cpu_affinity=$(proc_status_field 'Cpus_allowed_list:')
  runtime_memory_affinity=$(proc_status_field 'Mems_allowed_list:')
  runtime_numa_online=$(cat /sys/devices/system/node/online 2>/dev/null || printf unknown)
  runtime_numa_possible=$(cat /sys/devices/system/node/possible 2>/dev/null || printf unknown)
  runtime_numa_balancing=$(cat /proc/sys/kernel/numa_balancing 2>/dev/null || printf unknown)
  collect_cgroup_context
  runtime_available_parallelism=$(nproc)
  if [[ $runtime_cpu_max =~ ^([0-9]+)[[:space:]]+([1-9][0-9]*)$ ]]; then
    local quota_parallelism=$((BASH_REMATCH[1] / BASH_REMATCH[2]))
    ((quota_parallelism > 0)) || quota_parallelism=1
    if ((quota_parallelism < runtime_available_parallelism)); then
      runtime_available_parallelism=$quota_parallelism
    fi
  fi
  jq -n \
    --argjson available "$runtime_available_parallelism" \
    --arg os linux \
    --arg arch x86_64 \
    --arg numa_online "$runtime_numa_online" \
    --arg numa_possible "$runtime_numa_possible" \
    --arg numa_balancing "$runtime_numa_balancing" \
    --arg cpu_affinity "$runtime_cpu_affinity" \
    --arg memory_affinity "$runtime_memory_affinity" \
    --arg cgroup_path "$runtime_cgroup_path" \
    --arg cpu_max "$runtime_cpu_max" \
    --arg cpu_source "$runtime_cpu_source" \
    --arg memory_max "$runtime_memory_max" \
    --arg memory_source "$runtime_memory_source" \
    --arg swap_max "$runtime_swap_max" \
    --arg swap_source "$runtime_swap_source" \
    --arg cpuset "$runtime_cpuset" \
    --arg cpuset_source "$runtime_cpuset_source" \
    --arg cpuset_mems "$runtime_cpuset_mems" \
    --arg cpuset_mems_source "$runtime_cpuset_mems_source" \
    '{
      available_parallelism: $available,
      os: $os,
      arch: $arch,
      numa_nodes_online: $numa_online,
      numa_nodes_possible: $numa_possible,
      automatic_numa_balancing: $numa_balancing,
      cpu_affinity_list: $cpu_affinity,
      memory_affinity_list: $memory_affinity,
      cgroup_v2_path: $cgroup_path,
      inherited_cpu_max: $cpu_max,
      inherited_cpu_max_source: $cpu_source,
      inherited_memory_max: $memory_max,
      inherited_memory_max_source: $memory_source,
      inherited_memory_swap_max: $swap_max,
      inherited_memory_swap_max_source: $swap_source,
      effective_cpuset: $cpuset,
      effective_cpuset_source: $cpuset_source,
      effective_cpuset_mems: $cpuset_mems,
      effective_cpuset_mems_source: $cpuset_mems_source
    }' >"$output"
}

write_canonical_environment() {
  local output=$1
  jq -n \
    --slurpfile runtime "$runtime_context" \
    --arg run_id "$run_id" \
    --arg bundle_path "$output_dir" \
    --arg inventory_sha256 "$bundle_inventory_sha256" \
    --argjson inventory_entries "$bundle_inventory_entries" \
    --arg source_revision "$source_revision_before" \
    --argjson source_dirty "$source_dirty" \
    --arg source_status_sha256 "$source_status_sha256_before" \
    --arg source_live_manifest_sha256 "$source_manifest_sha256" \
    --arg source_snapshot_manifest_sha256 "$source_snapshot_manifest_sha256" \
    --argjson source_file_count "$source_file_count" \
    --arg workspace_manifest_sha256 "$workspace_manifest_sha256" \
    --arg workspace_lock_sha256 "$workspace_lock_sha256" \
    --arg runner_sha256 "$runner_sha256" \
    --arg runner_owner_sha256 "$SHMEM_POD_BENCH_RUNNER_OWNER_SHA256" \
    --arg harness_owner_sha256 "$harness_owner_sha256" \
    --arg harness_source_sha256 "$harness_source_sha256" \
    --arg harness_manifest_sha256 "$SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256" \
    --arg harness_lock_sha256 "$SHMEM_POD_BENCH_HARNESS_LOCK_SHA256" \
    --arg harness_binary_sha256 "$SHMEM_POD_BENCH_HARNESS_BINARY_SHA256" \
    --arg harness_report_sha256 "$harness_report_sha256" \
    --arg compiler_binary_sha256 "$compiler_sha256" \
    --arg compiler_manifest_sha256 "$compiler_manifest_sha256" \
    --arg compiler_crosscheck_sha256 "$compiler_crosscheck_sha256" \
    --arg control_tool_manifest_sha256 "$control_tool_manifest_sha256" \
    --arg host_linker_manifest_sha256 "$host_linker_manifest_sha256" \
    --arg vendor_manifest_sha256 "$vendor_manifest_sha256" \
    --arg rust_sysroot_manifest_sha256 "$rust_sysroot_manifest_sha256" \
    --argjson rust_sysroot_file_count "$rust_sysroot_file_count" \
    --arg results_jsonl_sha256 "$results_jsonl_sha256" \
    --arg results_csv_sha256 "$results_csv_sha256" \
    --arg hostname "$SHMEM_POD_BENCH_HOSTNAME" \
    --arg kernel "$SHMEM_POD_BENCH_KERNEL" \
    --arg cpu_model "$SHMEM_POD_BENCH_CPU_MODEL" \
    --arg rustc "$SHMEM_POD_BENCH_RUSTC" \
    --arg rustc_launcher_path "$rustc_launcher_path" \
    --arg rustc_launcher_sha256 "$rustc_launcher_sha256" \
    --arg rustc_path "$rustc_path" \
    --arg rustc_sha256 "$rustc_sha256" \
    --arg selected_rustc_path "$selected_rustc_path" \
    --arg selected_rustc_sha256 "$selected_rustc_sha256" \
    --arg selected_rust_sysroot "$selected_rust_sysroot" \
    --arg retained_rust_sysroot "$retained_rust_sysroot" \
    --arg cargo "$SHMEM_POD_BENCH_CARGO" \
    --arg cargo_launcher_path "$cargo_launcher_path" \
    --arg cargo_launcher_sha256 "$cargo_launcher_sha256" \
    --arg cargo_path "$cargo_path" \
    --arg cargo_sha256 "$cargo_sha256" \
    --arg selected_cargo_path "$selected_cargo_path" \
    --arg selected_cargo_sha256 "$selected_cargo_sha256" \
    --arg pod_rustc "$SHMEM_POD_BENCH_POD_RUSTC_VERSION" \
    --arg rustup_toolchain "$rustup_toolchain" \
    --arg env_path "$env_path" \
    --arg env_sha256 "$env_sha256" \
    --arg timeout_path "$timeout_path" \
    --arg timeout_sha256 "$timeout_sha256" \
    --arg uname_path "$uname_path" \
    --arg uname_sha256 "$uname_sha256" \
    --arg ldd_path "$ldd_path" \
    --arg ldd_sha256 "$ldd_sha256" \
    --arg host_linker_path "$host_linker_path" \
    --arg host_linker_sha256 "$host_linker_sha256" \
    --arg host_ld_path "$host_ld_path" \
    --arg host_ld_sha256 "$host_ld_sha256" \
    --arg host_as_path "$host_as_path" \
    --arg host_as_sha256 "$host_as_sha256" \
    --arg host_linker_config_sha256 "$host_linker_config_sha256" \
    --arg cargo_cache "$ambient_cargo_home" \
    --arg build_home "$build_home" \
    --arg build_cargo_home "$build_cargo_home" \
    --arg vendor_cargo_home "$vendor_cargo_home" \
    --arg build_path "$build_tool_bin" \
    --arg build_tmp "$build_tmp" \
    --arg workspace_profile "$SHMEM_POD_BENCH_WORKSPACE_PROFILE" \
    --arg harness_profile "$SHMEM_POD_BENCH_HARNESS_PROFILE" \
    --arg artifact_sha256 "$artifact_sha256" \
    --arg mode "$mode" \
    --argjson warmup "$warmup" \
    --argjson iterations "$iterations" \
    --argjson samples "$samples" \
    --argjson workers "$workers" \
    --argjson timeout "$run_timeout" \
    --argjson result_rows "$expected_rows" \
    '($runtime[0]) as $r | {
      schema: "shmem-pod-benchmark-environment-v2",
      complete: true,
      run_id: $run_id,
      result_rows: $result_rows,
      bundle: {
        absolute_path: $bundle_path,
        inventory: {
          bundle_path: "bundle-inventory.tsv",
          sha256: $inventory_sha256,
          entries: $inventory_entries,
          excludes: ["bundle-inventory.tsv", "environment.json"]
        }
      },
      source: {
        revision: $source_revision,
        dirty: $source_dirty,
        status_sha256: $source_status_sha256,
        status_bundle_path: "provenance/source-status.txt",
        initial_manifest: {
          bundle_path: "provenance/source-live-manifest.tsv",
          sha256: $source_live_manifest_sha256,
          files: $source_file_count
        },
        manifest: {
          bundle_path: "provenance/source-manifest.tsv",
          sha256: $source_snapshot_manifest_sha256,
          files: $source_file_count
        },
        snapshot_bundle_path: "provenance/source"
      },
      provenance: {
        workspace_manifest: {bundle_path: "provenance/source/Cargo.toml", sha256: $workspace_manifest_sha256},
        workspace_lock: {bundle_path: "provenance/source/Cargo.lock", sha256: $workspace_lock_sha256},
        runner: {bundle_path: "provenance/source/scripts/run-benchmarks.sh", sha256: $runner_sha256},
        runner_owner: {bundle_path: "runner-owner", sha256: $runner_owner_sha256},
        harness_owner: {bundle_path: "harness-owner.json", sha256: $harness_owner_sha256},
        harness_source: {bundle_path: "provenance/source/benchmarks/harness.rs", sha256: $harness_source_sha256},
        harness_manifest: {bundle_path: "provenance/harness-Cargo.toml", sha256: $harness_manifest_sha256},
        harness_lock: {bundle_path: "provenance/harness-Cargo.lock", sha256: $harness_lock_sha256},
        harness_binary: {bundle_path: "bin/shmem-pod-benchmark-harness", sha256: $harness_binary_sha256},
        harness_report: {bundle_path: "harness-report.json", sha256: $harness_report_sha256},
        compiler_binary: {bundle_path: "bin/shmem-pod-image-compiler", sha256: $compiler_binary_sha256},
        compiler_manifest: {bundle_path: "artifacts/pod.manifest", sha256: $compiler_manifest_sha256},
        compiler_crosscheck: {bundle_path: "provenance/compiler-crosscheck.json", sha256: $compiler_crosscheck_sha256},
        control_tools: {bundle_path: "provenance/control-tools.tsv", sha256: $control_tool_manifest_sha256},
        host_linker_manifest: {bundle_path: "provenance/host-linker-manifest.tsv", sha256: $host_linker_manifest_sha256},
        host_linker_config: {bundle_path: "provenance/host-linker-config.txt", sha256: $host_linker_config_sha256},
        vendor_manifest: {bundle_path: "provenance/vendor-manifest.tsv", sha256: $vendor_manifest_sha256},
        rust_sysroot_manifest: {bundle_path: "provenance/rust-sysroot-manifest.tsv", sha256: $rust_sysroot_manifest_sha256},
        results_jsonl: {bundle_path: "results.jsonl", sha256: $results_jsonl_sha256},
        results_csv: {bundle_path: "results.csv", sha256: $results_csv_sha256}
      },
      host: {
        hostname: $hostname,
        kernel: $kernel,
        cpu_model: $cpu_model,
        available_parallelism: $r.available_parallelism,
        os: $r.os,
        arch: $r.arch,
        numa_nodes_online: $r.numa_nodes_online,
        numa_nodes_possible: $r.numa_nodes_possible,
        automatic_numa_balancing: $r.automatic_numa_balancing
      },
      execution_limits: {
        cpu_affinity_list: $r.cpu_affinity_list,
        memory_affinity_list: $r.memory_affinity_list,
        cgroup_v2_path: $r.cgroup_v2_path,
        inherited_cpu_max: $r.inherited_cpu_max,
        inherited_cpu_max_source: $r.inherited_cpu_max_source,
        inherited_memory_max: $r.inherited_memory_max,
        inherited_memory_max_source: $r.inherited_memory_max_source,
        inherited_memory_swap_max: $r.inherited_memory_swap_max,
        inherited_memory_swap_max_source: $r.inherited_memory_swap_max_source,
        effective_cpuset: $r.effective_cpuset,
        effective_cpuset_source: $r.effective_cpuset_source,
        effective_cpuset_mems: $r.effective_cpuset_mems,
        effective_cpuset_mems_source: $r.effective_cpuset_mems_source
      },
      toolchain: {
        rustc: $rustc,
        rustc_launcher_path: $rustc_launcher_path,
        rustc_launcher_sha256: $rustc_launcher_sha256,
        rustc_path: $rustc_path,
        rustc_sha256: $rustc_sha256,
        selected_rustc_path: $selected_rustc_path,
        selected_rustc_sha256: $selected_rustc_sha256,
        selected_rust_sysroot: $selected_rust_sysroot,
        retained_rust_sysroot: $retained_rust_sysroot,
        retained_rust_sysroot_manifest: {
          bundle_path: "provenance/rust-sysroot-manifest.tsv",
          sha256: $rust_sysroot_manifest_sha256,
          files: $rust_sysroot_file_count
        },
        cargo: $cargo,
        cargo_launcher_path: $cargo_launcher_path,
        cargo_launcher_sha256: $cargo_launcher_sha256,
        cargo_path: $cargo_path,
        cargo_sha256: $cargo_sha256,
        selected_cargo_path: $selected_cargo_path,
        selected_cargo_sha256: $selected_cargo_sha256,
        pod_rustc: $pod_rustc
      },
      build_environment: {
        policy: "env-i-v1",
        inherited_policy: "RUSTUP_TOOLCHAIN is the only inherited build selector; known Cargo/rustc/compiler/linker controls are rejected and every other inherited variable is stripped",
        rustup_toolchain: $rustup_toolchain,
        cargo_offline: true,
        cargo_incremental: "0",
        cargo_registry_cache: $cargo_cache,
        cargo_vendor_home: $vendor_cargo_home,
        cargo_home: $build_cargo_home,
        home: $build_home,
        path: $build_path,
        tmpdir: $build_tmp,
        locale: {lc_all: "C", lang: "C", tz: "UTC"},
        source_date_epoch: "0",
        cargo_config_discovery: "root-owned empty /usr/share/empty CARGO_HOME and root-directory working directory, both revalidated before completion",
        target: "x86_64-unknown-linux-gnu",
        rustc: $rustc_path,
        target_linker: $host_linker_path,
        control_executables: {
          env: {path: $env_path, sha256: $env_sha256},
          timeout: {path: $timeout_path, sha256: $timeout_sha256},
          uname: {path: $uname_path, sha256: $uname_sha256},
          ldd: {path: $ldd_path, sha256: $ldd_sha256}
        },
        integrity_control_manifest: {
          bundle_path: "provenance/control-tools.tsv",
          sha256: $control_tool_manifest_sha256,
          path_policy: "fixed root-owned /usr/bin:/bin; shell-function shadowing rejected before external commands"
        },
        host_linker: {
          driver: {path: $host_linker_path, sha256: $host_linker_sha256},
          ld: {path: $host_ld_path, sha256: $host_ld_sha256},
          assembler: {path: $host_as_path, sha256: $host_as_sha256},
          observed_input_manifest_bundle_path: "provenance/host-linker-manifest.tsv",
          observed_input_manifest_sha256: $host_linker_manifest_sha256,
          specs_and_search_config_bundle_path: "provenance/host-linker-config.txt",
          specs_and_search_config_sha256: $host_linker_config_sha256,
          boundary: "hashed and revalidated host evidence; system executables, shared libraries, loader and C startup/sysroot inputs are not copied into a relocatable rebuild environment; the Rust compiler/sysroot is retained separately"
        },
        vendor: {
          bundle_path: "provenance/vendor",
          manifest_bundle_path: "provenance/vendor-manifest.tsv",
          manifest_sha256: $vendor_manifest_sha256,
          policy: "registry cache is used only by cargo vendor; all compilation replaces crates.io with this read-only retained tree"
        },
        workspace_profile: $workspace_profile,
        harness_profile: $harness_profile
      },
      artifact: {
        absolute_path: ($bundle_path + "/artifacts/pod.bin"),
        bundle_path: "artifacts/pod.bin",
        sha256: $artifact_sha256
      },
      configuration: {
        mode: $mode,
        profile: "release",
        warmup_operations_per_worker: $warmup,
        iterations_per_worker: $iterations,
        samples: $samples,
        workers: $workers,
        timeout_seconds: $timeout,
        timer: "std::time::Instant",
        sample_semantics: "ordered repeated intervals",
        warmup_policy: "persistent-state workloads warm once; freshly constructed process and presence states warm once per sample"
      },
      interpretation: "One-host observations only; compare rows within a controlled run and do not treat them as portable performance claims."
    }' >"$output"
}

verify_owner_files() {
  local expected_runner_owner="$temporary/expected-runner-owner"
  runner_owner_contents >"$expected_runner_owner"
  if ! cmp -s -- "$expected_runner_owner" "$output_dir/runner-owner"; then
    echo "benchmark runner ownership record changed" >&2
    return 1
  fi
  validate_json_unique_paths "$output_dir/harness-owner.json"
  if ! jq -e --arg run_id "$run_id" --arg token "$runner_owner_token" \
    '. == {
      schema: "shmem-pod-benchmark-owner-v2",
      run_id: $run_id,
      runner_owner_token: $token
    }' "$output_dir/harness-owner.json" >/dev/null; then
    echo "benchmark harness ownership validation failed" >&2
    return 1
  fi
}

validate_json_unique_paths() {
  local input=$1
  if ! jq --stream -n -e '
    [inputs | select(length == 2) | (.[0] | @json)] as $paths
    | ($paths | length) == ($paths | unique | length)
  ' <"$input" >/dev/null; then
    echo "JSON document contains duplicate object paths: $input" >&2
    return 1
  fi
}

verify_environment_bindings() {
  local environment=$1
  local relative digest full canonical inventory_digest inventory_entries binding_count=0
  local -A seen=()
  inventory_digest=$(jq -er '.bundle.inventory.sha256' "$environment")
  inventory_entries=$(jq -er '.bundle.inventory.entries' "$environment")
  verify_sha256 "$output_dir/bundle-inventory.tsv" "$inventory_digest"
  if [[ $inventory_entries != "$(wc -l <"$output_dir/bundle-inventory.tsv")" ]]; then
    echo "canonical environment has the wrong bundle inventory entry count" >&2
    return 1
  fi

  while IFS=$'\t' read -r relative digest; do
    if [[ -z $relative || $relative == /* || $relative == *$'\n'* ||
          ! $digest =~ ^[0-9a-f]{64}$ || -v seen[$relative] ]]; then
      echo "canonical environment has an invalid provenance binding: $relative" >&2
      return 1
    fi
    seen[$relative]=1
    binding_count=$((binding_count + 1))
    full="$output_dir/$relative"
    if [[ ! -f $full || -L $full ]]; then
      echo "canonical environment provenance is not a regular bundle file: $relative" >&2
      return 1
    fi
    canonical=$(readlink -f -- "$full")
    if [[ $canonical != "$full" ]]; then
      echo "canonical environment provenance path is noncanonical: $relative" >&2
      return 1
    fi
    verify_sha256 "$full" "$digest"
  done < <(
    jq -er '.provenance | to_entries[] | [.value.bundle_path, .value.sha256] | @tsv' \
      "$environment"
  )
  if ((binding_count != 20)); then
    echo "canonical environment has the wrong provenance binding count: $binding_count" >&2
    return 1
  fi

  verify_sha256 "$provenance_dir/source-status.txt" \
    "$(jq -er '.source.status_sha256' "$environment")"
  verify_sha256 "$provenance_dir/source-live-manifest.tsv" \
    "$(jq -er '.source.initial_manifest.sha256' "$environment")"
  verify_sha256 "$provenance_dir/source-manifest.tsv" \
    "$(jq -er '.source.manifest.sha256' "$environment")"
  verify_sha256 "$host_linker_manifest" \
    "$(jq -er '.build_environment.host_linker.observed_input_manifest_sha256' "$environment")"
  verify_sha256 "$host_linker_config" \
    "$(jq -er '.build_environment.host_linker.specs_and_search_config_sha256' "$environment")"
  verify_sha256 "$control_tool_manifest" \
    "$(jq -er '.build_environment.integrity_control_manifest.sha256' "$environment")"
  verify_sha256 "$vendor_manifest" \
    "$(jq -er '.build_environment.vendor.manifest_sha256' "$environment")"
  verify_sha256 "$rust_sysroot_manifest" \
    "$(jq -er '.toolchain.retained_rust_sysroot_manifest.sha256' "$environment")"
  verify_sha256 "$artifact_dir/pod.bin" \
    "$(jq -er '.artifact.sha256' "$environment")"
  if [[ $(jq -er '.source.initial_manifest.files' "$environment") != \
          "$(wc -l <"$provenance_dir/source-live-manifest.tsv")" ||
        $(jq -er '.source.manifest.files' "$environment") != \
          "$(wc -l <"$provenance_dir/source-manifest.tsv")" ||
        $(jq -er '.toolchain.retained_rust_sysroot_manifest.files' "$environment") != \
          "$(wc -l <"$rust_sysroot_manifest")" ]]; then
    echo "canonical environment has the wrong source manifest file count" >&2
    return 1
  fi
  local expected_revision="$temporary/expected-source-revision.txt"
  printf '%s\n' "$source_revision_before" >"$expected_revision"
  if ! cmp -s -- "$expected_revision" "$provenance_dir/source-revision.txt"; then
    echo "retained source revision record changed" >&2
    return 1
  fi
  verify_owner_files
}

verify_compiler_crosscheck() {
  if ! jq -e \
    --arg manifest_sha256 "$compiler_manifest_sha256" \
    --arg artifact_sha256 "$artifact_sha256" \
    --arg object_sha256 "$(sha256sum "$artifact_dir/pod.o" | awk '{print $1}')" \
    --arg elf_sha256 "$(sha256sum "$artifact_dir/pod.elf" | awk '{print $1}')" \
    --arg sdk_rlib_sha256 "$(sha256sum "$artifact_dir/libshmem_pod.rlib" | awk '{print $1}')" \
    --arg sdk_dep_info_sha256 "$(sha256sum "$artifact_dir/libshmem_pod.rlib.d" | awk '{print $1}')" \
    --arg sdk_probe_dep_info_sha256 "$sdk_probe_dep_info_sha256" \
    --arg pod_dep_info_sha256 "$(sha256sum "$artifact_dir/pod.o.d" | awk '{print $1}')" \
    --arg pod_probe_dep_info_sha256 "$pod_probe_dep_info_sha256" \
    '. == {
      schema: "shmem-pod-compiler-crosscheck-v1",
      compiler_manifest: {
        bundle_path: "artifacts/pod.manifest",
        sha256: $manifest_sha256
      },
      pod_bin_sha256: $artifact_sha256,
      pod_object_sha256: $object_sha256,
      pod_elf_sha256: $elf_sha256,
      sdk_rlib_sha256: $sdk_rlib_sha256,
      sdk_dep_info_sha256: $sdk_dep_info_sha256,
      sdk_probe_dep_info_sha256: $sdk_probe_dep_info_sha256,
      pod_dep_info_sha256: $pod_dep_info_sha256,
      pod_probe_dep_info_sha256: $pod_probe_dep_info_sha256
    }' "$provenance_dir/compiler-crosscheck.json" >/dev/null; then
    echo "runner compiler cross-check differs from the verified compiler outputs" >&2
    return 1
  fi
}

validate_result_jsonl_schema() {
  local input=$1
  local expected_count=$2
  local line row=0
  local expected_keys='["schema","run_id","category","benchmark","variant","topology","workers","sample","operations","elapsed_ns","operations_per_second","verified"]'

  # jq's ordinary object representation discards earlier duplicate keys. Its
  # streaming parser exposes each occurrence, so validate each physical JSONL
  # line before any normal parse or field projection can hide one.
  while IFS= read -r line || [[ -n $line ]]; do
    row=$((row + 1))
    if [[ -z $line ]] || ! printf '%s\n' "$line" | jq --stream -n -e \
      --argjson expected "$expected_keys" \
      '
        [inputs
         | select(length == 2)
         | .[0]
         | if (length == 1 and (.[0] | type) == "string")
           then .[0]
           else error("result row is not a flat object")
           end] as $keys
        | ($keys | sort) == ($expected | sort)
          and ($keys | length) == ($expected | length)
          and ($keys | length) == ($keys | unique | length)
      ' >/dev/null; then
      echo "benchmark JSONL row $row has duplicate, missing, extra, or nested keys" >&2
      return 1
    fi
  done <"$input"
  if ((row != expected_count)); then
    echo "benchmark JSONL has $row physical rows, expected $expected_count" >&2
    return 1
  fi
}

self_test_result_jsonl_schema() {
  local valid="$temporary/result-schema-valid.jsonl"
  local invalid="$temporary/result-schema-invalid.jsonl"
  local row='{"schema":"shmem-pod-benchmark-result-v1","run_id":"self-test","category":"latency","benchmark":"call","variant":"self_test","topology":"single_thread","workers":1,"sample":0,"operations":1,"elapsed_ns":1,"operations_per_second":1,"verified":true}'
  printf '%s\n' "$row" >"$valid"
  validate_result_jsonl_schema "$valid" 1

  printf '%s\n' "${row/\{\"schema\"/\{\"schema\":\"shadowed\",\"schema\"}" >"$invalid"
  if validate_result_jsonl_schema "$invalid" 1 >/dev/null 2>&1; then
    echo "result-schema self-test accepted a duplicate JSON key" >&2
    return 1
  fi
  printf '%s\n' "${row/\{\"schema\"/\{\"extra\":0,\"schema\"}" >"$invalid"
  if validate_result_jsonl_schema "$invalid" 1 >/dev/null 2>&1; then
    echo "result-schema self-test accepted an extra JSON key" >&2
    return 1
  fi
  printf '%s\n' "${row/\"verified\":true/\"verified\":\{\"nested\":true\}}" >"$invalid"
  if validate_result_jsonl_schema "$invalid" 1 >/dev/null 2>&1; then
    echo "result-schema self-test accepted a nested JSON member" >&2
    return 1
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

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "the executable-image benchmark currently requires Linux x86-64" >&2
  exit 1
fi
reject_ambient_build_controls

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

run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
temporary=$(mktemp -d)
assert_no_cargo_config_ancestors "$temporary"
self_test_result_jsonl_schema
control_home="$temporary/control-home"
mkdir "$control_home"
export HOME="$control_home"
export XDG_CONFIG_HOME="$control_home/.config"
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_OPTIONAL_LOCKS=0
source_revision_before=$(git_control rev-parse HEAD)
source_status_file="$temporary/live-source-status.txt"
git_control status --porcelain=v2 --untracked-files=all -- . >"$source_status_file"
source_status_sha256_before=$(sha256sum "$source_status_file" | awk '{print $1}')
source_paths_file="$temporary/live-source-paths.z"
source_manifest="$temporary/live-source-manifest.tsv"
write_live_manifest "$source_paths_file" "$source_manifest"
source_manifest_sha256=$(sha256sum "$source_manifest" | awk '{print $1}')
source_file_count=$(wc -l <"$source_manifest")
if [[ -n $(git_control status --porcelain --untracked-files=all -- .) ]]; then
  source_dirty=true
else
  source_dirty=false
fi
if [[ -z $output_dir ]]; then
  output_dir="$root/target/benchmark-results/$run_id"
fi
output_parent=$(dirname "$output_dir")
mkdir -p -- "$output_parent"
if ! mkdir -- "$output_dir"; then
  echo "--output must name a new directory: $output_dir" >&2
  exit 1
fi
output_claimed=1
output_dir=$(cd "$output_dir" && pwd -P)
runner_owner_token=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
if [[ ! $runner_owner_token =~ ^[0-9a-f]{64}$ ]]; then
  echo "failed to generate a runner owner token" >&2
  exit 1
fi
runner_owner_contents >"$output_dir/runner-owner"
artifact_dir="$output_dir/artifacts"
binary_dir="$output_dir/bin"
provenance_dir="$output_dir/provenance"
mkdir "$artifact_dir" "$binary_dir" "$provenance_dir"
control_tool_paths="$temporary/control-tool-paths.tsv"
{
  printf 'bash\t%s\n' "$(readlink -f -- /bin/bash)"
  for control_tool in "${required_control_tools[@]}"; do
    printf '%s\t%s\n' "$control_tool" "$(readlink -f -- "/usr/bin/$control_tool")"
  done
} >"$control_tool_paths"
control_tool_manifest="$provenance_dir/control-tools.tsv"
write_control_tool_manifest "$control_tool_manifest"
control_tool_manifest_sha256=$(sha256sum "$control_tool_manifest" | awk '{print $1}')
source_snapshot="$provenance_dir/source"
mkdir "$source_snapshot"
snapshot_source
source_snapshot_manifest_sha256=$(sha256sum "$source_snapshot_manifest" | awk '{print $1}')
cp "$source_manifest" "$provenance_dir/source-live-manifest.tsv"
cp "$source_snapshot_manifest" "$provenance_dir/source-manifest.tsv"
cp "$source_status_file" "$provenance_dir/source-status.txt"
printf '%s\n' "$source_revision_before" >"$provenance_dir/source-revision.txt"

runner_sha256=$(sha256sum "$source_snapshot/scripts/run-benchmarks.sh" | awk '{print $1}')
harness_source_sha256=$(sha256sum "$source_snapshot/benchmarks/harness.rs" | awk '{print $1}')
workspace_manifest_sha256=$(sha256sum "$source_snapshot/Cargo.toml" | awk '{print $1}')
workspace_lock_sha256=$(sha256sum "$source_snapshot/Cargo.lock" | awk '{print $1}')

echo "shmem-pod benchmark suite"
echo "  mode: $mode"
echo "  output: $output_dir"
echo "  warmup/iterations/samples/workers: $warmup/$iterations/$samples/$workers"
echo "  per-command timeout: ${run_timeout}s"

# Resolve every executable that can affect compilation before any measured
# binary is built. RUSTUP_TOOLCHAIN is the only inherited build selector; all
# build children receive an env -i environment and direct executable paths.
rustup_toolchain=${RUSTUP_TOOLCHAIN:-<default>}
rustc_launcher_path=$(readlink -f -- "$launch_rustc")
cargo_launcher_path=$(readlink -f -- "$launch_cargo")
if [[ ${rustc_launcher_path##*/} == rustup ]]; then
  rustc_path=$(cd / && HOME="$launch_home" "$rustc_launcher_path" which rustc)
else
  rustc_path=$rustc_launcher_path
fi
if [[ ${cargo_launcher_path##*/} == rustup ]]; then
  cargo_path=$(cd / && HOME="$launch_home" "$cargo_launcher_path" which cargo)
else
  cargo_path=$cargo_launcher_path
fi
rustc_path=$(readlink -f -- "$rustc_path")
cargo_path=$(readlink -f -- "$cargo_path")
if [[ ! -x $rustc_path || ! -f $rustc_path || ! -x $cargo_path || ! -f $cargo_path ]]; then
  echo "resolved Rust toolchain paths are not regular executables" >&2
  exit 1
fi
env_path=$(resolve_program env)
timeout_path=$(resolve_program timeout)
uname_path=$(resolve_program uname)
ldd_path=$(resolve_program ldd)
host_linker_path=$(resolve_program gcc)
host_ld_path=$(resolve_program ld)
host_as_path=$(resolve_program as)

rustc_launcher_sha256=$(sha256sum "$rustc_launcher_path" | awk '{print $1}')
cargo_launcher_sha256=$(sha256sum "$cargo_launcher_path" | awk '{print $1}')
selected_rustc_path=$rustc_path
selected_cargo_path=$cargo_path
selected_rustc_sha256=$(sha256sum "$selected_rustc_path" | awk '{print $1}')
selected_cargo_sha256=$(sha256sum "$selected_cargo_path" | awk '{print $1}')
env_sha256=$(sha256sum "$env_path" | awk '{print $1}')
timeout_sha256=$(sha256sum "$timeout_path" | awk '{print $1}')
uname_sha256=$(sha256sum "$uname_path" | awk '{print $1}')
ldd_sha256=$(sha256sum "$ldd_path" | awk '{print $1}')
host_linker_sha256=$(sha256sum "$host_linker_path" | awk '{print $1}')
host_ld_sha256=$(sha256sum "$host_ld_path" | awk '{print $1}')
host_as_sha256=$(sha256sum "$host_as_path" | awk '{print $1}')

build_home="$temporary/build-home"
build_tmp="$temporary/build-tmp"
build_tool_bin="$temporary/build-tool-bin"
build_cargo_home=/usr/share/empty
vendor_cargo_home="$temporary/vendor-cargo-home"
mkdir "$build_home" "$build_tmp" "$build_tool_bin" "$vendor_cargo_home"
ln -s -- "$host_ld_path" "$build_tool_bin/ld"
ln -s -- "$host_as_path" "$build_tool_bin/as"
ln -s -- "$uname_path" "$build_tool_bin/uname"

selection_env=(
  "HOME=$build_home"
  "PATH=$build_tool_bin"
  "TMPDIR=$build_tmp"
  "LC_ALL=C"
  "LANG=C"
  "TZ=UTC"
)
selected_rust_sysroot=$(cd / && "$env_path" -i "${selection_env[@]}" \
  "$selected_rustc_path" --print sysroot)
selected_rust_sysroot=$(readlink -f -- "$selected_rust_sysroot")
rust_host_target=$(cd / && "$env_path" -i "${selection_env[@]}" \
  "$selected_rustc_path" --version --verbose | sed -n 's/^host: //p')
if [[ -z $rust_host_target || ! -d $selected_rust_sysroot/lib/rustlib/$rust_host_target/lib ]]; then
  echo "selected Rust compiler returned an invalid sysroot or host target" >&2
  exit 1
fi
echo "retaining exact Rust compiler and sysroot inputs"
retained_rust_sysroot="$provenance_dir/rust-sysroot"
mkdir "$retained_rust_sysroot"
retain_rust_sysroot "$selected_rust_sysroot" "$retained_rust_sysroot" "$selected_cargo_path"
rustc_path="$retained_rust_sysroot/bin/rustc"
cargo_path="$retained_rust_sysroot/bin/cargo"
rustc_sha256=$(sha256sum "$rustc_path" | awk '{print $1}')
cargo_sha256=$(sha256sum "$cargo_path" | awk '{print $1}')

hermetic_env=(
  "HOME=$build_home"
  "CARGO_HOME=$build_cargo_home"
  "CARGO_NET_OFFLINE=true"
  "CARGO_INCREMENTAL=0"
  "RUSTC=$rustc_path"
  "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=$host_linker_path"
  "PATH=$build_tool_bin"
  "TMPDIR=$build_tmp"
  "LC_ALL=C"
  "LANG=C"
  "TZ=UTC"
  "SOURCE_DATE_EPOCH=0"
)
vendor_env=("${hermetic_env[@]}")
vendor_env[1]="CARGO_HOME=$vendor_cargo_home"

verify_cargo_discovery_roots
rust_sysroot=$(cd / && "$env_path" -i "${hermetic_env[@]}" "$rustc_path" --print sysroot)
if [[ $rust_sysroot != "$retained_rust_sysroot" ]]; then
  echo "retained rustc did not resolve its retained sysroot: $rust_sysroot" >&2
  exit 1
fi
rust_lld_path=$(readlink -f -- \
  "$rust_sysroot/lib/rustlib/$rust_host_target/bin/rust-lld")
if [[ ! -f $rust_lld_path || ! -x $rust_lld_path ]]; then
  echo "selected Rust toolchain does not contain rust-lld: $rust_lld_path" >&2
  exit 1
fi

# Cargo needs its content-addressed offline registry cache but must not load
# user config, credentials, aliases, wrappers, or network policy from it.
ambient_cargo_home=$(readlink -f -- "$launch_home/.cargo")
if [[ ! -d $ambient_cargo_home/registry ]]; then
  echo "the hermetic benchmark requires a populated offline Cargo registry cache" >&2
  exit 1
fi
ln -s -- "$ambient_cargo_home/registry" "$vendor_cargo_home/registry"
if [[ -d $ambient_cargo_home/git ]]; then
  ln -s -- "$ambient_cargo_home/git" "$vendor_cargo_home/git"
fi

host_linker_paths="$temporary/host-linker-paths.tsv"
dynamic_executables="$temporary/host-dynamic-executables.tsv"
collect2_path=$(gcc_control -print-prog-name=collect2)
lto_wrapper_path=$(gcc_control -print-prog-name=lto-wrapper)
liblto_plugin_path=$(gcc_control -print-prog-name=liblto_plugin.so)
lto1_path=$(gcc_control -print-prog-name=lto1)
gcc_specs_path=$(gcc_control -print-file-name=specs)
if [[ $collect2_path != /* || ! -f $collect2_path ||
      $lto_wrapper_path != /* || ! -f $lto_wrapper_path ||
      $liblto_plugin_path != /* || ! -f $liblto_plugin_path ||
      $lto1_path != /* || ! -f $lto1_path ]]; then
  echo "GCC cannot resolve absolute collect2/LTO component paths" >&2
  exit 1
fi
{
  printf 'driver\t%s\n' "$host_linker_path"
  printf 'collect2\t%s\n' "$(readlink -f -- "$collect2_path")"
  printf 'ld\t%s\n' "$host_ld_path"
  printf 'as\t%s\n' "$host_as_path"
  printf 'lto-wrapper\t%s\n' "$(readlink -f -- "$lto_wrapper_path")"
  printf 'liblto-plugin\t%s\n' "$(readlink -f -- "$liblto_plugin_path")"
  printf 'lto1\t%s\n' "$(readlink -f -- "$lto1_path")"
  if [[ $gcc_specs_path != specs ]]; then
    [[ -f $gcc_specs_path ]] || {
      echo "GCC specs probe returned a missing file: $gcc_specs_path" >&2
      exit 1
    }
    printf 'gcc-specs\t%s\n' "$(readlink -f -- "$gcc_specs_path")"
  fi
  for link_input in \
    Scrt1.o crti.o crtbeginS.o crtendS.o crtn.o \
    libgcc_s.so libgcc_s.so.1 libgcc.a \
    libc.so libc.so.6 libm.so libm.so.6 libdl.a libpthread.a librt.a libutil.a; do
    input_path=$(gcc_control -print-file-name="$link_input")
    if [[ $input_path == "$link_input" || ! -f $input_path ]]; then
      echo "host linker cannot resolve required input: $link_input" >&2
      exit 1
    fi
    printf '%s\t%s\n' "$link_input" "$(readlink -f -- "$input_path")"
  done
} >"$host_linker_paths"
{
  printf 'rustc\t%s\n' "$rustc_path"
  printf 'cargo\t%s\n' "$cargo_path"
  printf 'rust-lld\t%s\n' "$rust_lld_path"
  printf 'env\t%s\n' "$env_path"
  printf 'timeout\t%s\n' "$timeout_path"
  printf 'uname\t%s\n' "$uname_path"
  printf 'gcc\t%s\n' "$host_linker_path"
  printf 'collect2\t%s\n' "$(readlink -f -- "$collect2_path")"
  printf 'ld\t%s\n' "$host_ld_path"
  printf 'as\t%s\n' "$host_as_path"
  printf 'lto-wrapper\t%s\n' "$(readlink -f -- "$lto_wrapper_path")"
  printf 'liblto-plugin\t%s\n' "$(readlink -f -- "$liblto_plugin_path")"
  printf 'lto1\t%s\n' "$(readlink -f -- "$lto1_path")"
} >"$dynamic_executables"
while IFS=$'\t' read -r dynamic_label dynamic_executable; do
  append_dynamic_dependency_paths \
    "$dynamic_label" "$dynamic_executable" "$host_linker_paths"
done <"$dynamic_executables"
host_linker_manifest="$provenance_dir/host-linker-manifest.tsv"
write_host_linker_manifest "$host_linker_manifest"
host_linker_manifest_sha256=$(sha256sum "$host_linker_manifest" | awk '{print $1}')
host_linker_config="$provenance_dir/host-linker-config.txt"
write_host_linker_config "$host_linker_config"
host_linker_config_sha256=$(sha256sum "$host_linker_config" | awk '{print $1}')

SHMEM_POD_BENCH_RUSTC=$(cd / && "$env_path" -i "${hermetic_env[@]}" "$rustc_path" --version --verbose)
SHMEM_POD_BENCH_CARGO=$(cd / && "$env_path" -i "${hermetic_env[@]}" "$cargo_path" --version --verbose)
SHMEM_POD_BENCH_POD_RUSTC_VERSION=$SHMEM_POD_BENCH_RUSTC
SHMEM_POD_BENCH_WORKSPACE_PROFILE='release: opt-level=3, debug=false, strip=none, debug-assertions=false, overflow-checks=false, lto=thin, panic=abort, incremental=false, codegen-units=1, rpath=false'
SHMEM_POD_BENCH_HARNESS_PROFILE=$SHMEM_POD_BENCH_WORKSPACE_PROFILE

echo "retaining exact Cargo registry inputs"
vendor_dir="$provenance_dir/vendor"
(
  cd /
  "$timeout_path" --signal=TERM --kill-after=15s "${run_timeout}s" \
    "$env_path" -i "${vendor_env[@]}" "$cargo_path" vendor \
      --locked --offline --versioned-dirs \
      --manifest-path "$source_snapshot/Cargo.toml" "$vendor_dir" >/dev/null
)
vendor_symlink=$(find "$vendor_dir" -type l -print -quit)
if [[ -n $vendor_symlink ]]; then
  echo "Cargo vendor input contains a symlink whose target escapes byte retention: $vendor_symlink" >&2
  exit 1
fi
chmod -R a-w -- "$vendor_dir"
vendor_manifest="$provenance_dir/vendor-manifest.tsv"
write_directory_manifest "$vendor_dir" "$vendor_manifest"
vendor_manifest_sha256=$(sha256sum "$vendor_manifest" | awk '{print $1}')
verify_vendor_stable
cargo_source_config=(
  --config 'source.crates-io.replace-with="vendored-sources"'
  --config "source.vendored-sources.directory=\"$vendor_dir\""
)

echo "building executable-pod compiler and runtime"
compiler_target="$temporary/compiler-target"
(
  cd /
  "$timeout_path" --signal=TERM --kill-after=15s "${run_timeout}s" \
    "$env_path" -i "${hermetic_env[@]}" CARGO_TARGET_DIR="$compiler_target" \
    "$cargo_path" "${cargo_source_config[@]}" build \
    --locked --offline --release --manifest-path "$source_snapshot/Cargo.toml" \
    -p shmem-pod-image-compiler \
    -p shmem-pod-runtime
)
compiler_build="$compiler_target/release/shmem-pod-image-compiler"
[[ -x $compiler_build ]] || { echo "compiler executable is missing: $compiler_build" >&2; exit 1; }
compiler="$binary_dir/shmem-pod-image-compiler"
cp "$compiler_build" "$compiler"
compiler_sha256=$(sha256sum "$compiler" | awk '{print $1}')

compiler_args=(
  --source "$source_snapshot/poc/code/src/lib.rs"
  --sdk-manifest "$source_snapshot/Cargo.toml"
  --sdk-source "$source_snapshot/src/lib.rs"
  --sdk-rlib "$artifact_dir/libshmem_pod.rlib"
  --linker-script "$source_snapshot/poc/code/pod.ld"
  --output "$artifact_dir/pod.bin"
  --object "$artifact_dir/pod.o"
  --elf "$artifact_dir/pod.elf"
  --manifest "$artifact_dir/pod.manifest"
)
compiler_args+=(--rustc "$rustc_path")
compiler_work="$temporary/compiler-work"
mkdir "$compiler_work"
(
  cd "$compiler_work"
  "$timeout_path" --signal=TERM --kill-after=15s "${run_timeout}s" \
    "$env_path" -i "${hermetic_env[@]}" "$compiler" "${compiler_args[@]}"
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
compiler_manifest="$artifact_dir/pod.manifest"
if [[ $(manifest_value "$compiler_manifest" format) != shmem-pod-image-v2 ||
      $(manifest_value "$compiler_manifest" provenance.scope) != rustc-dep-info-plus-explicit-link-inputs ||
      $(manifest_value "$compiler_manifest" sdk.default_features) != false ||
      $(manifest_value "$compiler_manifest" sdk.features) != linux-futex ]]; then
  echo "compiler manifest has an unexpected format or SDK feature contract" >&2
  exit 1
fi
verify_compiler_manifest_file "$compiler_manifest" source source_sha256 \
  "$source_snapshot/poc/code/src/lib.rs"
verify_compiler_manifest_file "$compiler_manifest" object object_sha256 \
  "$artifact_dir/pod.o"
verify_compiler_manifest_file "$compiler_manifest" elf elf_sha256 \
  "$artifact_dir/pod.elf"
verify_compiler_manifest_file "$compiler_manifest" image artifact_sha256 \
  "$artifact_dir/pod.bin"
verify_compiler_manifest_file "$compiler_manifest" linker_script linker_script_sha256 \
  "$source_snapshot/poc/code/pod.ld"
verify_compiler_manifest_file "$compiler_manifest" sdk.manifest sdk.manifest_sha256 \
  "$source_snapshot/Cargo.toml"
verify_compiler_manifest_file "$compiler_manifest" sdk.crate_root sdk.crate_root_sha256 \
  "$source_snapshot/src/lib.rs"
verify_compiler_manifest_file "$compiler_manifest" sdk.rlib sdk.rlib_sha256 \
  "$artifact_dir/libshmem_pod.rlib"
verify_compiler_manifest_file "$compiler_manifest" sdk.dep_info sdk.dep_info_sha256 \
  "$artifact_dir/libshmem_pod.rlib.d"
verify_compiler_manifest_file "$compiler_manifest" pod.dep_info pod.dep_info_sha256 \
  "$artifact_dir/pod.o.d"
verify_compiler_manifest_file "$compiler_manifest" rustc_launcher rustc_launcher_sha256 \
  "$rustc_path"
verify_compiler_manifest_file "$compiler_manifest" rustc_binary rustc_binary_sha256 \
  "$rustc_path"
verify_compiler_manifest_file "$compiler_manifest" rust_lld rust_lld_sha256 \
  "$rust_lld_path"
verify_compiler_dependencies "$compiler_manifest" sdk
verify_compiler_dependencies "$compiler_manifest" pod
if [[ $(manifest_value "$compiler_manifest" sdk.root) != "$source_snapshot" ||
      $(manifest_value "$compiler_manifest" link.inputs) != \
        "$artifact_dir/pod.o,$artifact_dir/libshmem_pod.rlib,$source_snapshot/poc/code/pod.ld" ||
      $(manifest_value "$compiler_manifest" image_len) != \
        "$(stat -c '%s' "$artifact_dir/pod.bin")" ]]; then
  echo "compiler manifest geometry or path binding is inconsistent" >&2
  exit 1
fi
for required in \
  "$artifact_dir/libshmem_pod.rlib.probe.d" \
  "$artifact_dir/libshmem_pod.rlib.d" \
  "$artifact_dir/pod.o.probe.d" \
  "$artifact_dir/pod.o.d"; do
  [[ -f $required ]] || { echo "compiler evidence is missing: $required" >&2; exit 1; }
done
sdk_probe_dep_info_sha256=$(sha256sum "$artifact_dir/libshmem_pod.rlib.probe.d" | awk '{print $1}')
pod_probe_dep_info_sha256=$(sha256sum "$artifact_dir/pod.o.probe.d" | awk '{print $1}')
compiler_manifest_sha256=$(sha256sum "$compiler_manifest" | awk '{print $1}')
jq -n \
  --arg manifest_path artifacts/pod.manifest \
  --arg manifest_sha256 "$compiler_manifest_sha256" \
  --arg artifact_sha256 "$artifact_sha256" \
  --arg object_sha256 "$(sha256sum "$artifact_dir/pod.o" | awk '{print $1}')" \
  --arg elf_sha256 "$(sha256sum "$artifact_dir/pod.elf" | awk '{print $1}')" \
  --arg sdk_rlib_sha256 "$(sha256sum "$artifact_dir/libshmem_pod.rlib" | awk '{print $1}')" \
  --arg sdk_dep_info_sha256 "$(sha256sum "$artifact_dir/libshmem_pod.rlib.d" | awk '{print $1}')" \
  --arg sdk_probe_dep_info_sha256 "$sdk_probe_dep_info_sha256" \
  --arg pod_dep_info_sha256 "$(sha256sum "$artifact_dir/pod.o.d" | awk '{print $1}')" \
  --arg pod_probe_dep_info_sha256 "$pod_probe_dep_info_sha256" \
  '{
    schema: "shmem-pod-compiler-crosscheck-v1",
    compiler_manifest: {bundle_path: $manifest_path, sha256: $manifest_sha256},
    pod_bin_sha256: $artifact_sha256,
    pod_object_sha256: $object_sha256,
    pod_elf_sha256: $elf_sha256,
    sdk_rlib_sha256: $sdk_rlib_sha256,
    sdk_dep_info_sha256: $sdk_dep_info_sha256,
    sdk_probe_dep_info_sha256: $sdk_probe_dep_info_sha256,
    pod_dep_info_sha256: $pod_dep_info_sha256,
    pod_probe_dep_info_sha256: $pod_probe_dep_info_sha256
  }' >"$provenance_dir/compiler-crosscheck.json"
verify_compiler_crosscheck

mkdir -p "$temporary/src"
cp "$source_snapshot/benchmarks/harness.rs" "$temporary/src/main.rs"
cp "$source_snapshot/Cargo.lock" "$temporary/Cargo.lock"
chmod u+w "$temporary/Cargo.lock"
cat >"$temporary/Cargo.toml" <<EOF
[package]
name = "shmem-pod-benchmark-harness"
version = "0.0.0"
edition = "2024"
publish = false

[dependencies]
shmem-pod = { path = "$source_snapshot", default-features = false, features = ["linux-futex"] }
shmem-pod-runtime = { path = "$source_snapshot/poc/runtime" }
sha2 = "=0.10.9"

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

SHMEM_POD_BENCH_HOSTNAME=$(uname -n)
SHMEM_POD_BENCH_KERNEL=$(uname -srvmo)
cpu_model=$(awk -F: '/^model name/{sub(/^[[:space:]]+/, "", $2); print $2; exit}' /proc/cpuinfo)
SHMEM_POD_BENCH_CPU_MODEL=${cpu_model:-unknown}

# Cargo prunes workspace-only packages from the copied lockfile. Normalize that
# temporary lock without network access, then make the measured build immutable.
harness_target="$temporary/harness-target"
(
  cd /
  "$timeout_path" --signal=TERM --kill-after=15s "${run_timeout}s" \
    "$env_path" -i "${hermetic_env[@]}" CARGO_TARGET_DIR="$harness_target" \
    "$cargo_path" "${cargo_source_config[@]}" metadata \
    --offline --manifest-path "$temporary/Cargo.toml" \
      --format-version 1 >/dev/null
)
SHMEM_POD_BENCH_HARNESS_LOCK_SHA256=$(sha256sum "$temporary/Cargo.lock" | awk '{print $1}')
cp "$temporary/Cargo.toml" "$provenance_dir/harness-Cargo.toml"
cp "$temporary/Cargo.lock" "$provenance_dir/harness-Cargo.lock"
SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256=$(sha256sum "$provenance_dir/harness-Cargo.toml" | awk '{print $1}')

echo "building exact benchmark harness"
(
  cd /
  "$timeout_path" --signal=TERM --kill-after=15s "${run_timeout}s" \
    "$env_path" -i "${hermetic_env[@]}" CARGO_TARGET_DIR="$harness_target" \
    "$cargo_path" "${cargo_source_config[@]}" build \
    --locked --offline --release --manifest-path "$temporary/Cargo.toml"
)
harness_build="$harness_target/release/shmem-pod-benchmark-harness"
[[ -x $harness_build ]] || { echo "benchmark harness executable is missing: $harness_build" >&2; exit 1; }
harness="$binary_dir/shmem-pod-benchmark-harness"
cp "$harness_build" "$harness"
SHMEM_POD_BENCH_HARNESS_BINARY_SHA256=$(sha256sum "$harness" | awk '{print $1}')
SHMEM_POD_BENCH_RUNNER_OWNER_SHA256=$(sha256sum "$output_dir/runner-owner" | awk '{print $1}')
runtime_context="$temporary/runtime-context.json"
collect_runtime_context "$runtime_context"

echo "running verified benchmark harness"
set +e
harness_stdout=$("$timeout_path" --signal=TERM --kill-after=15s "${run_timeout}s" \
  "$env_path" -i "${hermetic_env[@]}" "$harness" \
    --run-id "$run_id" \
    --runner-owner-token "$runner_owner_token" \
    --artifact "$artifact_dir/pod.bin" \
    --sha256 "$artifact_sha256" \
    --output-dir "$output_dir" \
    --warmup "$warmup" \
    --iterations "$iterations" \
    --samples "$samples" \
    --workers "$workers" \
    --mode "$mode" \
    --timeout-seconds "$run_timeout" \
    --defer-completion 1)
status=$?
set -e
printf '%s\n' "$harness_stdout"
if ((status != 0)); then
  if ((status == 124 || status == 137)); then
    echo "benchmark harness exceeded its ${run_timeout}s deadline" >&2
  fi
  exit "$status"
fi
mapfile -t harness_auth_lines < <(
  printf '%s\n' "$harness_stdout" | sed -n '/^benchmark-result-auth-v1 /p'
)
if ((${#harness_auth_lines[@]} != 1)) ||
   [[ ! ${harness_auth_lines[0]} =~ ^benchmark-result-auth-v1\ results_jsonl_sha256=([0-9a-f]{64})\ results_csv_sha256=([0-9a-f]{64})$ ]]; then
  echo "benchmark harness did not emit exactly one valid result authentication record" >&2
  exit 1
fi
harness_results_jsonl_sha256=${BASH_REMATCH[1]}
harness_results_csv_sha256=${BASH_REMATCH[2]}
verify_harness_result_bytes

verify_source_stable
verify_sha256 "$artifact_dir/pod.bin" "$artifact_sha256"
verify_sha256 "$artifact_dir/pod.manifest" "$compiler_manifest_sha256"
verify_sha256 "$binary_dir/shmem-pod-image-compiler" "$compiler_sha256"
verify_sha256 "$binary_dir/shmem-pod-benchmark-harness" "$SHMEM_POD_BENCH_HARNESS_BINARY_SHA256"
verify_sha256 "$source_snapshot/scripts/run-benchmarks.sh" "$runner_sha256"
verify_sha256 "$source_snapshot/benchmarks/harness.rs" "$harness_source_sha256"
verify_sha256 "$source_snapshot/Cargo.toml" "$workspace_manifest_sha256"
verify_sha256 "$source_snapshot/Cargo.lock" "$workspace_lock_sha256"
verify_sha256 "$provenance_dir/source-live-manifest.tsv" "$source_manifest_sha256"
verify_sha256 "$provenance_dir/source-manifest.tsv" "$source_snapshot_manifest_sha256"
verify_sha256 "$provenance_dir/harness-Cargo.toml" "$SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256"
verify_sha256 "$provenance_dir/harness-Cargo.lock" "$SHMEM_POD_BENCH_HARNESS_LOCK_SHA256"
validate_json_unique_paths "$output_dir/harness-owner.json"
if ! jq -e --arg run_id "$run_id" --arg token "$runner_owner_token" \
  '. == {
    schema: "shmem-pod-benchmark-owner-v2",
    run_id: $run_id,
    runner_owner_token: $token
  }' \
  "$output_dir/harness-owner.json" >/dev/null; then
  echo "benchmark harness ownership validation failed" >&2
  exit 1
fi

expected_rows=$((22 * samples))
if [[ -e $output_dir/environment.json ||
      -e $output_dir/environment.json.pending ]]; then
  echo "harness attempted to create runner-owned completion metadata" >&2
  exit 1
fi
expected_harness_report="$temporary/expected-harness-report.json"
validate_json_unique_paths "$output_dir/harness-report.json"
jq -n \
  --slurpfile runtime "$runtime_context" \
  --arg run_id "$run_id" \
  --arg mode "$mode" \
  --argjson warmup "$warmup" \
  --argjson iterations "$iterations" \
  --argjson samples "$samples" \
  --argjson workers "$workers" \
  --argjson timeout "$run_timeout" \
  --arg jsonl_sha256 "$harness_results_jsonl_sha256" \
  --arg csv_sha256 "$harness_results_csv_sha256" \
  '{
    schema: "shmem-pod-benchmark-harness-report-v1",
    run_id: $run_id,
    result_rows: ($samples * 22),
    results: {jsonl_sha256: $jsonl_sha256, csv_sha256: $csv_sha256},
    runtime_context: $runtime[0],
    configuration: {
      mode: $mode,
      profile: "release",
      warmup_operations_per_worker: $warmup,
      iterations_per_worker: $iterations,
      samples: $samples,
      workers: $workers,
      timeout_seconds: $timeout,
      timer: "std::time::Instant",
      sample_semantics: "ordered repeated intervals",
      warmup_policy: "persistent-state workloads warm once; freshly constructed process and presence states warm once per sample"
    },
    runner_completion_deferred: true
  }' >"$expected_harness_report"
if ! diff -u \
  <(jq -S . "$expected_harness_report") \
  <(jq -S . "$output_dir/harness-report.json"); then
  echo "benchmark harness report differs from the runner's independent observations" >&2
  exit 1
fi
validate_result_jsonl_schema "$output_dir/results.jsonl" "$expected_rows"
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
verify_toolchain_stable
verify_sha256 "$artifact_dir/pod.manifest" "$compiler_manifest_sha256"
verify_compiler_manifest_file "$compiler_manifest" object object_sha256 \
  "$artifact_dir/pod.o"
verify_compiler_manifest_file "$compiler_manifest" elf elf_sha256 \
  "$artifact_dir/pod.elf"
verify_compiler_manifest_file "$compiler_manifest" image artifact_sha256 \
  "$artifact_dir/pod.bin"
verify_compiler_manifest_file "$compiler_manifest" sdk.rlib sdk.rlib_sha256 \
  "$artifact_dir/libshmem_pod.rlib"
verify_compiler_manifest_file "$compiler_manifest" sdk.dep_info sdk.dep_info_sha256 \
  "$artifact_dir/libshmem_pod.rlib.d"
verify_compiler_manifest_file "$compiler_manifest" pod.dep_info pod.dep_info_sha256 \
  "$artifact_dir/pod.o.d"
verify_sha256 "$artifact_dir/libshmem_pod.rlib.probe.d" "$sdk_probe_dep_info_sha256"
verify_sha256 "$artifact_dir/pod.o.probe.d" "$pod_probe_dep_info_sha256"

verify_compiler_crosscheck
compiler_crosscheck_sha256=$(sha256sum "$provenance_dir/compiler-crosscheck.json" | awk '{print $1}')
verify_owner_files
harness_owner_sha256=$(sha256sum "$output_dir/harness-owner.json" | awk '{print $1}')
harness_report_sha256=$(sha256sum "$output_dir/harness-report.json" | awk '{print $1}')
results_jsonl_sha256=$(sha256sum "$output_dir/results.jsonl" | awk '{print $1}')
results_csv_sha256=$(sha256sum "$output_dir/results.csv" | awk '{print $1}')
if [[ $results_jsonl_sha256 != "$harness_results_jsonl_sha256" ||
      $results_csv_sha256 != "$harness_results_csv_sha256" ]]; then
  echo "benchmark results changed after harness-authenticated emission" >&2
  exit 1
fi

# Freeze every payload file before inventorying it. The inventory and canonical
# completion marker are the only self-describing control files excluded.
find "$output_dir" -type f -exec chmod a-w -- {} +
verify_harness_result_bytes
inventory_staging="$temporary/bundle-inventory.tsv"
write_directory_manifest "$output_dir" "$inventory_staging"
mv -- "$inventory_staging" "$output_dir/bundle-inventory.tsv"
chmod a-w "$output_dir/bundle-inventory.tsv"
bundle_inventory_sha256=$(sha256sum "$output_dir/bundle-inventory.tsv" | awk '{print $1}')
bundle_inventory_entries=$(wc -l <"$output_dir/bundle-inventory.tsv")
verify_bundle_inventory

environment_staging="$temporary/environment.json"
environment_check="$temporary/environment-check.json"
write_canonical_environment "$environment_staging"
write_canonical_environment "$environment_check"
if ! cmp -s "$environment_staging" "$environment_check"; then
  echo "canonical benchmark environment generation was not deterministic" >&2
  exit 1
fi
if ! jq -e \
  --arg run_id "$run_id" \
  --arg bundle_path "$output_dir" \
  --arg inventory_sha256 "$bundle_inventory_sha256" \
  --arg source_manifest_sha256 "$source_snapshot_manifest_sha256" \
  --arg artifact_sha256 "$artifact_sha256" \
  --argjson rows "$expected_rows" \
  '.schema == "shmem-pod-benchmark-environment-v2"
   and .complete == true
   and .run_id == $run_id
   and .result_rows == $rows
   and .bundle.absolute_path == $bundle_path
   and .bundle.inventory.sha256 == $inventory_sha256
   and .source.manifest.sha256 == $source_manifest_sha256
   and .artifact.sha256 == $artifact_sha256
   and .artifact.absolute_path == ($bundle_path + "/artifacts/pod.bin")' \
  "$environment_staging" >/dev/null; then
  echo "runner-generated canonical environment failed exact binding checks" >&2
  exit 1
fi
verify_environment_bindings "$environment_staging"
sync -f "$environment_staging"

# These are deliberately the final observations before the runner alone
# publishes the completion marker.
verify_source_stable
verify_toolchain_stable
verify_harness_result_bytes
verify_bundle_inventory
verify_environment_bindings "$environment_staging"
if [[ -e $output_dir/environment.json || -L $output_dir/environment.json ]]; then
  echo "refusing to replace an existing benchmark completion marker" >&2
  exit 1
fi
mv -- "$environment_staging" "$output_dir/environment.json"
sync -f "$output_dir/environment.json"
chmod a-w "$output_dir/environment.json"
run_succeeded=1
output_claimed=0

echo "PASS benchmark artifacts validated in $output_dir"
