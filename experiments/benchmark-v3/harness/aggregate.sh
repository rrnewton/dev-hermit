#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "$0")" && pwd)
RESULTS_ROOT=${1:-$ROOT/../results}
COMBINED_HOME=${CRITERION_HOME:-/tmp/criterion-counter1-v3-combined}
OPERATIONS=(getpid read-devnull write-devnull clock-gettime)

for operation in "${OPERATIONS[@]}"; do
  run_directory="$RESULTS_ROOT/runs/$operation"
  for required in summary.tsv raw-samples.tsv medians.tsv metadata.tsv     artifact-sha256.tsv preflight.tsv idle-gates.tsv backend-order.tsv capabilities.tsv
  do
    [[ -f "$run_directory/$required" ]] || {
      echo "missing $run_directory/$required" >&2
      exit 2
    }
  done
  [[ -d "$run_directory/criterion-raw/marginal_$operation" ]] || {
    echo "missing Criterion tree for $operation" >&2
    exit 2
  }
done

mkdir -p "$COMBINED_HOME" "$RESULTS_ROOT/criterion-raw"
for operation in "${OPERATIONS[@]}"; do
  source_tree="$RESULTS_ROOT/runs/$operation/criterion-raw/marginal_$operation"
  [[ ! -e "$COMBINED_HOME/marginal_$operation" ]] || {
    echo "combined Criterion tree already contains $operation" >&2
    exit 2
  }
  cp -a "$source_tree" "$COMBINED_HOME/marginal_$operation"
  cp -a "$source_tree" "$RESULTS_ROOT/criterion-raw/marginal_$operation"
done

with-proxy cargo run --locked --release --manifest-path "$ROOT/Cargo.toml"   --bin summarize -- "$COMBINED_HOME" "$RESULTS_ROOT"

combine_tsv() {
  local filename=$1
  local output=$2
  local first=true
  : >"$output"
  for operation in "${OPERATIONS[@]}"; do
    if $first; then
      awk -v operation="$operation" 'NR == 1 {print "run_syscall\t" $0; next} {print operation "\t" $0}'         "$RESULTS_ROOT/runs/$operation/$filename" >>"$output"
      first=false
    else
      awk -v operation="$operation" 'NR == 1 {next} {print operation "\t" $0}'         "$RESULTS_ROOT/runs/$operation/$filename" >>"$output"
    fi
  done
}

combine_tsv metadata.tsv "$RESULTS_ROOT/run-metadata.tsv"
combine_tsv preflight.tsv "$RESULTS_ROOT/preflight.tsv"
combine_tsv idle-gates.tsv "$RESULTS_ROOT/idle-gates.tsv"
combine_tsv backend-order.tsv "$RESULTS_ROOT/backend-order.tsv"
combine_tsv capabilities.tsv "$RESULTS_ROOT/capabilities.tsv"
cp "$RESULTS_ROOT/runs/getpid/fixed-counts.tsv" "$RESULTS_ROOT/fixed-counts.tsv"
cp "$RESULTS_ROOT/runs/getpid/artifact-sha256.tsv" "$RESULTS_ROOT/artifact-sha256.tsv"

for operation in "${OPERATIONS[@]:1}"; do
  cmp <(cut -f2 "$RESULTS_ROOT/runs/getpid/artifact-sha256.tsv") \
    <(cut -f2 "$RESULTS_ROOT/runs/$operation/artifact-sha256.tsv")
done

printf 'Aggregated summary: %s\n' "$RESULTS_ROOT/SUMMARY.md"
printf 'Combined raw samples: %s\n' "$RESULTS_ROOT/raw-samples.tsv"
