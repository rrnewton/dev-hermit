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
  provenance/source/   Immutable snapshot of every non-ignored v2 input.
  bundle-inventory.tsv Exact mode, size, and digest of each retained payload.

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

environment_value() {
  local name=$1
  if [[ -v $name ]]; then
    printf '%s' "${!name}"
  else
    printf '<unset>'
  fi
}

runner_owner_contents() {
  printf 'shmem-pod-benchmark-runner-owner-v1\nrun_id=%s\ntoken=%s\n' \
    "$run_id" "$runner_owner_token"
}

status_fingerprint() {
  git status --porcelain=v2 --untracked-files=all -- . |
    sha256sum | awk '{print $1}'
}

enumerate_source_paths() {
  git ls-files -co --exclude-standard -z -- . | LC_ALL=C sort -z
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

verify_toolchain_stable() {
  verify_sha256 "$rustc_launcher_path" "$rustc_launcher_sha256"
  verify_sha256 "$cargo_launcher_path" "$cargo_launcher_sha256"
  verify_sha256 "$rustc_path" "$rustc_sha256"
  verify_sha256 "$cargo_path" "$cargo_sha256"
  if [[ $(cd "$temporary" && "$rustc_executable" --version --verbose) != \
          "$SHMEM_POD_BENCH_RUSTC" ||
        $(cd "$temporary" && cargo --version --verbose) != \
          "$SHMEM_POD_BENCH_CARGO" ]]; then
    echo "Rust toolchain identity changed during the benchmark run" >&2
    return 1
  fi
  if [[ -n ${POD_RUSTC:-} &&
        $("$POD_RUSTC" --version --verbose) != \
          "$SHMEM_POD_BENCH_POD_RUSTC_VERSION" ]]; then
    echo "pod compiler rustc identity changed during the benchmark run" >&2
    return 1
  fi
}

verify_source_stable() {
  local revision status_sha256 tree_sha256
  revision=$(git rev-parse HEAD)
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
    --arg harness_source_sha256 "$harness_source_sha256" \
    --arg harness_manifest_sha256 "$SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256" \
    --arg harness_lock_sha256 "$SHMEM_POD_BENCH_HARNESS_LOCK_SHA256" \
    --arg harness_binary_sha256 "$SHMEM_POD_BENCH_HARNESS_BINARY_SHA256" \
    --arg harness_report_sha256 "$harness_report_sha256" \
    --arg compiler_binary_sha256 "$compiler_sha256" \
    --arg compiler_manifest_sha256 "$compiler_manifest_sha256" \
    --arg compiler_crosscheck_sha256 "$compiler_crosscheck_sha256" \
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
    --arg cargo "$SHMEM_POD_BENCH_CARGO" \
    --arg cargo_launcher_path "$cargo_launcher_path" \
    --arg cargo_launcher_sha256 "$cargo_launcher_sha256" \
    --arg cargo_path "$cargo_path" \
    --arg cargo_sha256 "$cargo_sha256" \
    --arg pod_rustc "$SHMEM_POD_BENCH_POD_RUSTC_VERSION" \
    --arg rustflags "$SHMEM_POD_BENCH_RUSTFLAGS" \
    --arg encoded_rustflags "$SHMEM_POD_BENCH_CARGO_ENCODED_RUSTFLAGS" \
    --arg rustc_override "$SHMEM_POD_BENCH_RUSTC_OVERRIDE" \
    --arg pod_rustc_override "$SHMEM_POD_BENCH_POD_RUSTC" \
    --arg rustc_wrapper "$SHMEM_POD_BENCH_RUSTC_WRAPPER" \
    --arg workspace_wrapper "$SHMEM_POD_BENCH_RUSTC_WORKSPACE_WRAPPER" \
    --arg cargo_home "$SHMEM_POD_BENCH_CARGO_HOME" \
    --arg cargo_target "$SHMEM_POD_BENCH_CARGO_BUILD_TARGET" \
    --arg workspace_profile "$SHMEM_POD_BENCH_WORKSPACE_PROFILE" \
    --arg harness_profile "$SHMEM_POD_BENCH_HARNESS_PROFILE" \
    --arg profile_opt "$SHMEM_POD_BENCH_PROFILE_OPT_LEVEL" \
    --arg profile_debug "$SHMEM_POD_BENCH_PROFILE_DEBUG" \
    --arg profile_strip "$SHMEM_POD_BENCH_PROFILE_STRIP" \
    --arg profile_assertions "$SHMEM_POD_BENCH_PROFILE_DEBUG_ASSERTIONS" \
    --arg profile_overflow "$SHMEM_POD_BENCH_PROFILE_OVERFLOW_CHECKS" \
    --arg profile_lto "$SHMEM_POD_BENCH_PROFILE_LTO" \
    --arg profile_panic "$SHMEM_POD_BENCH_PROFILE_PANIC" \
    --arg profile_incremental "$SHMEM_POD_BENCH_PROFILE_INCREMENTAL" \
    --arg profile_units "$SHMEM_POD_BENCH_PROFILE_CODEGEN_UNITS" \
    --arg profile_rpath "$SHMEM_POD_BENCH_PROFILE_RPATH" \
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
        harness_source: {bundle_path: "provenance/source/benchmarks/harness.rs", sha256: $harness_source_sha256},
        harness_manifest: {bundle_path: "provenance/harness-Cargo.toml", sha256: $harness_manifest_sha256},
        harness_lock: {bundle_path: "provenance/harness-Cargo.lock", sha256: $harness_lock_sha256},
        harness_binary: {bundle_path: "bin/shmem-pod-benchmark-harness", sha256: $harness_binary_sha256},
        harness_report: {bundle_path: "harness-report.json", sha256: $harness_report_sha256},
        compiler_binary: {bundle_path: "bin/shmem-pod-image-compiler", sha256: $compiler_binary_sha256},
        compiler_manifest: {bundle_path: "artifacts/pod.manifest", sha256: $compiler_manifest_sha256},
        compiler_crosscheck: {bundle_path: "provenance/compiler-crosscheck.json", sha256: $compiler_crosscheck_sha256},
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
        cargo: $cargo,
        cargo_launcher_path: $cargo_launcher_path,
        cargo_launcher_sha256: $cargo_launcher_sha256,
        cargo_path: $cargo_path,
        cargo_sha256: $cargo_sha256,
        pod_rustc: $pod_rustc
      },
      build_environment: {
        rustflags: $rustflags,
        cargo_encoded_rustflags: $encoded_rustflags,
        rustc_override: $rustc_override,
        pod_rustc: $pod_rustc_override,
        rustc_wrapper: $rustc_wrapper,
        rustc_workspace_wrapper: $workspace_wrapper,
        cargo_home: $cargo_home,
        cargo_build_target: $cargo_target,
        workspace_profile: $workspace_profile,
        harness_profile: $harness_profile,
        profile_overrides: {
          opt_level: $profile_opt,
          debug: $profile_debug,
          strip: $profile_strip,
          debug_assertions: $profile_assertions,
          overflow_checks: $profile_overflow,
          lto: $profile_lto,
          panic: $profile_panic,
          incremental: $profile_incremental,
          codegen_units: $profile_units,
          rpath: $profile_rpath
        }
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

for tool in awk cargo cat chmod cmp cp date diff dirname find git jq mkdir mktemp mv nproc od readlink rm rustc sed sha256sum sort stat sync tail timeout tr uname wc; do
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

run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
temporary=$(mktemp -d)
source_revision_before=$(git rev-parse HEAD)
source_status_file="$temporary/live-source-status.txt"
git status --porcelain=v2 --untracked-files=all -- . >"$source_status_file"
source_status_sha256_before=$(sha256sum "$source_status_file" | awk '{print $1}')
source_paths_file="$temporary/live-source-paths.z"
source_manifest="$temporary/live-source-manifest.tsv"
write_live_manifest "$source_paths_file" "$source_manifest"
source_manifest_sha256=$(sha256sum "$source_manifest" | awk '{print $1}')
source_file_count=$(wc -l <"$source_manifest")
if [[ -n $(git status --porcelain --untracked-files=all -- .) ]]; then
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

# Capture the exact launchers, resolved toolchain binaries, versions, and build
# controls before any measured binary is compiled.
rustc_executable=${RUSTC:-rustc}
rustc_launcher_path=$(readlink -f -- "$(command -v "$rustc_executable")")
cargo_launcher_path=$(readlink -f -- "$(command -v cargo)")
if [[ ${rustc_launcher_path##*/} == rustup ]]; then
  rustc_path=$(cd "$temporary" && "$rustc_launcher_path" which rustc)
else
  rustc_path=$rustc_launcher_path
fi
if [[ ${cargo_launcher_path##*/} == rustup ]]; then
  cargo_path=$(cd "$temporary" && "$cargo_launcher_path" which cargo)
else
  cargo_path=$cargo_launcher_path
fi
rustc_path=$(readlink -f -- "$rustc_path")
cargo_path=$(readlink -f -- "$cargo_path")
rustc_launcher_sha256=$(sha256sum "$rustc_launcher_path" | awk '{print $1}')
cargo_launcher_sha256=$(sha256sum "$cargo_launcher_path" | awk '{print $1}')
rustc_sha256=$(sha256sum "$rustc_path" | awk '{print $1}')
cargo_sha256=$(sha256sum "$cargo_path" | awk '{print $1}')
SHMEM_POD_BENCH_RUSTC=$(cd "$temporary" && "$rustc_executable" --version --verbose)
SHMEM_POD_BENCH_CARGO=$(cd "$temporary" && cargo --version --verbose)
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
done

echo "building executable-pod compiler and runtime"
compiler_target="$temporary/compiler-target"
(
  cd "$temporary"
  CARGO_TARGET_DIR="$compiler_target" \
    timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
    cargo build --locked --release --manifest-path "$source_snapshot/Cargo.toml" \
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
verify_sha256 "$artifact_dir/libshmem_pod.rlib.probe.d" "$sdk_probe_dep_info_sha256"
verify_sha256 "$artifact_dir/pod.o.probe.d" "$pod_probe_dep_info_sha256"
verify_compiler_manifest_file "$compiler_manifest" rustc_launcher rustc_launcher_sha256 \
  "$(manifest_value "$compiler_manifest" rustc_launcher)"
verify_compiler_manifest_file "$compiler_manifest" rustc_binary rustc_binary_sha256 \
  "$(manifest_value "$compiler_manifest" rustc_binary)"
verify_compiler_manifest_file "$compiler_manifest" rust_lld rust_lld_sha256 \
  "$(manifest_value "$compiler_manifest" rust_lld)"
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
  cd "$temporary"
  CARGO_TARGET_DIR="$harness_target" \
    timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
    cargo metadata --offline --manifest-path "$temporary/Cargo.toml" \
      --format-version 1 >/dev/null
)
SHMEM_POD_BENCH_HARNESS_LOCK_SHA256=$(sha256sum "$temporary/Cargo.lock" | awk '{print $1}')
cp "$temporary/Cargo.toml" "$provenance_dir/harness-Cargo.toml"
cp "$temporary/Cargo.lock" "$provenance_dir/harness-Cargo.lock"
SHMEM_POD_BENCH_HARNESS_MANIFEST_SHA256=$(sha256sum "$provenance_dir/harness-Cargo.toml" | awk '{print $1}')

echo "building exact benchmark harness"
(
  cd "$temporary"
  CARGO_TARGET_DIR="$harness_target" \
    timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
    cargo build --locked --offline --release --manifest-path "$temporary/Cargo.toml"
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
timeout --signal=TERM --kill-after=15s "${run_timeout}s" \
  "$harness" \
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
jq -n \
  --slurpfile runtime "$runtime_context" \
  --arg run_id "$run_id" \
  --arg mode "$mode" \
  --argjson warmup "$warmup" \
  --argjson iterations "$iterations" \
  --argjson samples "$samples" \
  --argjson workers "$workers" \
  --argjson timeout "$run_timeout" \
  '{
    schema: "shmem-pod-benchmark-harness-report-v1",
    run_id: $run_id,
    result_rows: ($samples * 22),
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

compiler_crosscheck_sha256=$(sha256sum "$provenance_dir/compiler-crosscheck.json" | awk '{print $1}')
harness_report_sha256=$(sha256sum "$output_dir/harness-report.json" | awk '{print $1}')
results_jsonl_sha256=$(sha256sum "$output_dir/results.jsonl" | awk '{print $1}')
results_csv_sha256=$(sha256sum "$output_dir/results.csv" | awk '{print $1}')

# Freeze every payload file before inventorying it. The inventory and canonical
# completion marker are the only self-describing control files excluded.
find "$output_dir" -type f -exec chmod a-w -- {} +
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
sync -f "$environment_staging"

# These are deliberately the final observations before the runner alone
# publishes the completion marker.
verify_source_stable
verify_toolchain_stable
verify_bundle_inventory
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
