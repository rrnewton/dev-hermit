#!/usr/bin/env bash
# run-detached.sh — self-contained env + harness runner for detached execution.
#
# Sources the single-user nix profile, sets the fwdproxy env, then runs
# rebuild-compare.sh for one (label, expr) and appends the CSV row to results.
#
# Usage (typically under `nohup setsid ... &`):
#   run-detached.sh <label> '<nix-expr>'
set -uo pipefail
cd "$(dirname "$0")/.."

. /home/newton/.nix-profile/etc/profile.d/nix.sh
export http_proxy="http://fwdproxy:8080" https_proxy="http://fwdproxy:8080"
export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$https_proxy"
export no_proxy=".facebook.com,.internalfb.com,.tfbnw.net,.fbcdn.net,localhost,127.0.0.1,::1"
export NO_PROXY="$no_proxy"

label="${1:?label}"; expr="${2:?expr}"
harness="${3:-rebuild-compare.sh}"   # or rebuild-canonical.sh
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "=== $(ts) START $label ($harness) ==="
row=$(bash "harness/$harness" "$label" "$expr")
rc=$?
echo "=== $(ts) END $label rc=$rc ==="
echo "ROW: $row"
# Append to results.csv (create header once).
[ -f results.csv ] || echo "label,drv,out_narhash,check_narhash,check_exit,verdict" > results.csv
echo "$row" >> results.csv
