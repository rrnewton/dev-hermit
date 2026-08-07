#!/usr/bin/env bash
# canonical-nrep.sh — N-repetition canonical-rebuild reproducibility oracle.
#
# WHY NOT `nix --check`: with `sandbox = false` (this host) nix's check-mode
# rebuild goes to a REDIRECTED output path, so any output embedding a
# self-reference to its own $out differs by exactly that store-path hash. That
# is a false positive unrelated to runtime nondeterminism (the nftables-1.1.6
# finding in experiments/nix-hermit-execbuilder-prototype_20260729). This oracle
# instead builds N times into the SAME canonical $out (build -> hash -> delete
# -> rebuild), so self-references are identical in every build and only genuine
# runtime nondeterminism (time, RNG, ordering, scheduling) can differ.
#
# Usage:
#   canonical-nrep.sh <label> <native|hermit> '<nix-expr>' [N]
#
# Emits one CSV row on stdout:
#   label,target_expr,mode,dose,drv,n,distinct,verdict,hashes,wall_s,notes
# and appends per-run rows to $EXP_DIR/runs.csv.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/env.sh"

label="${1:?label}"; mode="${2:?native|hermit}"; expr="${3:?nix-expr}"; N="${4:-3}"
: "${BUILD_TIMEOUT:=3600}"

log() { printf '[%s/%s] %s\n' "$label" "$mode" "$*" >&2; }
csv_note=""

# Render $HERMIT_ARGS (a shell word list, e.g. "run --no-namespace --no-rcb-time")
# as a nix list literal so the dose is part of the derivation identity.
hermit_args_nix() {
  local out="[" w
  for w in $HERMIT_ARGS; do out+=" \"$w\""; done
  echo "$out ]"
}

case "$mode" in
  native) full_expr="$expr"; dose="native" ;;
  hermit) full_expr="(import ${EXP_DIR}/nix/hermit-wrap.nix { hermit = \"${HERMIT}\"; hermitArgs = $(hermit_args_nix); useSetarch = ${HERMIT_USE_SETARCH}; }).hermitize (${expr})"
          dose="$HERMIT_ARGS$([ "$HERMIT_USE_SETARCH" = true ] && echo ' +setarch-R')" ;;
  *) echo "bad mode: $mode" >&2; exit 64 ;;
esac

drv=$(nix-instantiate -E "$full_expr" 2>"$LOG_DIR/$label.$mode.inst.err" | tail -1)
if [ -z "$drv" ] || [ ! -e "$drv" ]; then
  log "instantiate FAILED"; tail -20 "$LOG_DIR/$label.$mode.inst.err" | sed 's/^/    /' >&2
  echo "$label,\"$expr\",$mode,\"$dose\",INSTANTIATE_FAIL,0,,error,,,instantiate-failed"; exit 2
fi
log "drv=$drv"

# --- phase A: make every INPUT available (substitution allowed) --------------
# Only the target itself must be built locally; its dependencies may come from
# cache.nixos.org through fwdproxy. `--query --references` on a .drv yields its
# input .drv files and input sources.
check_disk || { echo "$label,\"$expr\",$mode,\"$dose\",$drv,0,,disk-guard,,,refused-low-disk"; exit 3; }
log "phase A: realising inputs (substitution allowed)"
mapfile -t inputs < <(nix-store --query --references "$drv" | grep '\.drv$' || true)
if [ "${#inputs[@]}" -gt 0 ]; then
  if ! timeout "$BUILD_TIMEOUT" nix-store --realise "${NIX_SERIAL_OPTS[@]}" "${inputs[@]}" \
        >"$LOG_DIR/$label.$mode.inputs.out" 2>"$LOG_DIR/$label.$mode.inputs.err"; then
    log "input realisation FAILED"; tail -20 "$LOG_DIR/$label.$mode.inputs.err" | sed 's/^/    /' >&2
    echo "$label,\"$expr\",$mode,\"$dose\",$drv,0,,error,,,input-realise-failed"; exit 2
  fi
fi

# --- phase B: N local builds into the same canonical $out --------------------
hashes=(); walls=(); distinct=0
for i in $(seq 1 "$N"); do
  check_disk || { csv_note="stopped-low-disk-after-$((i-1))"; break; }

  # Ensure a clean slate: the canonical outputs must not exist, or nix would
  # short-circuit. Deleting is also what makes the rebuild land on the SAME
  # paths. A derivation may have SEVERAL outputs (`out`, `dev`, `doc`, ...) that
  # reference each other, so they must all be deleted in ONE call or nix refuses
  # on account of a live referrer.
  mapfile -t curs < <(nix-store --query --outputs "$drv")
  existing=(); for o in "${curs[@]}"; do [ -e "$o" ] && existing+=("$o"); done
  if [ "${#existing[@]}" -gt 0 ]; then
    if ! nix-store --delete "${existing[@]}" >>"$LOG_DIR/$label.$mode.del.log" 2>&1; then
      # An external referrer (another package already built against this one)
      # makes the canonical rebuild impossible; that is a harness limitation for
      # this target, not a verdict about the package.
      ext=$(nix-store --query --referrers "${existing[0]}" | grep -vxF -f <(printf '%s\n' "${existing[@]}") | head -3 | tr '\n' ';')
      log "pre-delete FAILED; external referrers: ${ext:-none/gc-root}"
      echo "$label,\"$expr\",$mode,\"$dose\",$drv,${#hashes[@]},,error,$(IFS=' '; echo "${hashes[*]}"),,delete-failed:${ext:-gcroot}"; exit 2
    fi
  fi

  t0=$(date +%s)
  mapfile -t outs < <(timeout "$BUILD_TIMEOUT" nix-store --realise "${NIX_SERIAL_OPTS[@]}" "${NIX_NOSUB_OPTS[@]}" "$drv" \
          2>"$LOG_DIR/$label.$mode.build$i.err")
  rc=$?
  t1=$(date +%s); wall=$((t1 - t0))
  if [ $rc -eq 124 ]; then
    log "build#$i TIMEOUT after ${BUILD_TIMEOUT}s"
    echo "$label,\"$expr\",$mode,\"$dose\",$drv,${#hashes[@]},,timeout,$(IFS=' '; echo "${hashes[*]}"),$(IFS=' '; echo "${walls[*]}"),timeout-${BUILD_TIMEOUT}s"; exit 4
  fi
  if [ "${#outs[@]}" -eq 0 ] || [ ! -e "${outs[0]}" ]; then
    log "build#$i FAILED (rc=$rc)"; tail -25 "$LOG_DIR/$label.$mode.build$i.err" | sed 's/^/    /' >&2
    echo "$label,\"$expr\",$mode,\"$dose\",$drv,${#hashes[@]},,build-fail,$(IFS=' '; echo "${hashes[*]}"),$(IFS=' '; echo "${walls[*]}"),build$i-failed"; exit 2
  fi
  # Combined witness over EVERY output of the derivation, joined with '+'.
  h=""
  for o in "${outs[@]}"; do h+="${h:++}$(nix-hash --type sha256 --base32 "$o")"; done
  hashes+=("$h"); walls+=("$wall")
  log "build#$i outs=${outs[*]} nar=$h wall=${wall}s"
  printf '%s,%s,"%s",%s,%s,%d,%s,%s,%d\n' "$label" "$mode" "$dose" "$drv" "${outs[0]}" "$i" "$h" "ok" "$wall" >> "$EXP_DIR/runs.csv"
done

distinct=$(printf '%s\n' "${hashes[@]}" | sort -u | wc -l)
if [ "${#hashes[@]}" -lt 2 ]; then verdict="INCONCLUSIVE"
elif [ "$distinct" -eq 1 ]; then verdict="reproducible"
else verdict="NONDETERMINISTIC"; fi
log "VERDICT: $verdict (n=${#hashes[@]} distinct=$distinct)"

echo "$label,\"$expr\",$mode,\"$dose\",$drv,${#hashes[@]},$distinct,$verdict,$(IFS=' '; echo "${hashes[*]}"),$(IFS=' '; echo "${walls[*]}"),$csv_note"
