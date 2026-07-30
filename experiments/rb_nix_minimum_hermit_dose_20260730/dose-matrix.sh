#!/usr/bin/env bash
# dose-matrix.sh — run each candidate hermit dose N times on the probe nix build
# and report whether the output hash is IDENTICAL across all runs (robust
# reproducibility) plus median wall-clock. Writes results.csv.
set -uo pipefail
cd "$(dirname "$0")"
N="${N:-3}"
OUT="results.csv"
echo "dose,run_hashes,reproducible,times_s" > "$OUT"

# Heaviest -> lightest. All carry --no-namespace (required for rootless podman).
declare -a DOSES=(
  "native"
  "--no-namespace --strict --sequentialize-threads"
  "--no-namespace --strict"
  "--no-namespace"
  "--no-namespace --no-sequentialize-threads"
  "--no-namespace --no-sequentialize-threads --no-rcb-time --no-deterministic-io"
)

for dose in "${DOSES[@]}"; do
  hashes=(); times=()
  for i in $(seq 1 "$N"); do
    line=$(HERMIT_ARGS="$dose" ./dose-run.sh)
    h=$(echo "$line" | awk '{print $1}'); t=$(echo "$line" | awk '{print $2}')
    hashes+=("${h:0:12}"); times+=("$t")
  done
  # reproducible if all hashes equal and none is BUILD_FAIL
  first="${hashes[0]}"; repro="YES"
  for h in "${hashes[@]}"; do [ "$h" = "$first" ] || repro="NO"; [ "$h" = "BUILD_FAIL" ] && repro="FAIL"; done
  echo "\"$dose\",\"${hashes[*]}\",$repro,\"${times[*]}\"" >> "$OUT"
  printf '%-70s repro=%s  hashes=%s  times=%s\n' "$dose" "$repro" "${hashes[*]}" "${times[*]}"
done
echo "--- results.csv written ---"
