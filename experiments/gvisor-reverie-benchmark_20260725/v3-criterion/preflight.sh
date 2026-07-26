#!/usr/bin/env bash
set -euo pipefail

: "${RUNSC_BIN:?set RUNSC_BIN}"
: "${COUNTER2:?set COUNTER2}"
: "${DRRUN:?set DRRUN}"
: "${DBI_CLIENT:?set DBI_CLIENT}"
: "${KVM_COUNTER:?set KVM_COUNTER}"
: "${SABRE_RUNNER:?set SABRE_RUNNER}"
: "${SABRE_PLUGIN:?set SABRE_PLUGIN}"
: "${SABRE:?set SABRE}"
: "${SYSCALL_BENCH_HELPER_OVERRIDE:?set SYSCALL_BENCH_HELPER_OVERRIDE}"

output=${1:?usage: preflight.sh OUTPUT.tsv}
workspace=${DEV_HERMIT_ROOT:-$(cd -- "$(dirname -- "$0")/../../.." && pwd)}
temporary=$(mktemp -d /tmp/benchmark-v3-preflight-XXXXXX)
trap 'rm -rf "$temporary"' EXIT
mkdir -p "$(dirname -- "$output")"

parse_count() {
  awk '
    $1 == "counter2-local" && $2 ~ /^thread=/ && $3 ~ /^syscalls=/ {
      split($3, field, "="); count = field[2]
    }
    END { if (count == "") exit 1; print count }
  ' "$@"
}

run_counted() {
  local backend=$1
  local iterations=$2
  local diagnostics="$temporary/${backend}-${iterations}.stderr"
  local root
  case "$backend" in
    gvisor-systrap|gvisor-kvm)
      local platform="counter2-${backend#gvisor-}"
      root="$temporary/root-${backend}-${iterations}"
      mkdir -p "$root"
      timeout 180 "$RUNSC_BIN" \
        --root="$root" \
        --platform="$platform" \
        --network=none \
        --ignore-cgroups=true \
        --debug-log="$temporary/${backend}-${iterations}.%COMMAND%.log" \
        do --quiet \
        --uid-map="0 $(id -u) 1" \
        --gid-map="0 $(id -g) 1" \
        --cwd="$workspace" \
        "$SYSCALL_BENCH_HELPER_OVERRIDE" --run getpid "$iterations" \
        >"$temporary/${backend}-${iterations}.stdout" 2>"$diagnostics"
      parse_count "$temporary/${backend}-${iterations}."*.log
      ;;
    reverie-ptrace)
      timeout 180 env LC_ALL=C LANG=C RUST_LOG=off \
        "$COUNTER2" -- "$SYSCALL_BENCH_HELPER_OVERRIDE" --run getpid "$iterations" \
        >"$temporary/${backend}-${iterations}.stdout" 2>"$diagnostics"
      parse_count "$diagnostics"
      ;;
    reverie-dbi)
      timeout 180 env LC_ALL=C LANG=C RUST_LOG=off HERMIT_DBI_COUNTER2_EXACT=1 \
        "$DRRUN" -quiet -disable_rseq -stack_size 2M -c "$DBI_CLIENT" -summary -- \
        "$SYSCALL_BENCH_HELPER_OVERRIDE" --run getpid "$iterations" \
        >"$temporary/${backend}-${iterations}.stdout" 2>"$diagnostics"
      parse_count "$diagnostics"
      ;;
    reverie-kvm)
      timeout 180 env LC_ALL=C LANG=C RUST_LOG=off \
        "$KVM_COUNTER" "$SYSCALL_BENCH_HELPER_OVERRIDE" --run getpid "$iterations" \
        >"$temporary/${backend}-${iterations}.stdout" 2>"$diagnostics"
      parse_count "$diagnostics"
      ;;
    reverie-sabre)
      timeout 180 env LC_ALL=C LANG=C RUST_LOG=off \
        "$SABRE_RUNNER" --sabre "$SABRE" --plugin "$SABRE_PLUGIN" \
        --tool counter2-exact -- \
        "$SYSCALL_BENCH_HELPER_OVERRIDE" --run getpid "$iterations" \
        >"$temporary/${backend}-${iterations}.stdout" 2>"$diagnostics"
      parse_count "$diagnostics"
      ;;
    *)
      echo "unknown backend: $backend" >&2
      return 2
      ;;
  esac
}

printf 'backend\ttool\tn0_count\tn16_count\tdelta\tstatus\n' >"$output"
timeout 30 "$SYSCALL_BENCH_HELPER_OVERRIDE" --run getpid 0
timeout 30 "$SYSCALL_BENCH_HELPER_OVERRIDE" --run getpid 16
printf 'native\ttool-free-subtraction-baseline\tNA\tNA\tNA\tpass\n' >>"$output"

for backend in \
  gvisor-systrap gvisor-kvm reverie-ptrace reverie-dbi reverie-kvm reverie-sabre
do
  n0=$(run_counted "$backend" 0)
  n16=$(run_counted "$backend" 16)
  delta=$((n16 - n0))
  status=pass
  if [[ $delta -ne 16 ]]; then
    status=FAIL
  fi
  printf '%s\tCounterLocal\t%s\t%s\t%s\t%s\n' \
    "$backend" "$n0" "$n16" "$delta" "$status" >>"$output"
  if [[ $status != pass ]]; then
    echo "$backend CounterLocal delta was $delta, expected 16" >&2
    exit 1
  fi
done

cat "$output"
