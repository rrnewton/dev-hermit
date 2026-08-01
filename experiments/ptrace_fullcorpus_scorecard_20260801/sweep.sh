#!/bin/bash
# Ptrace full-corpus L2 scorecard sweep (agent hermit-235).
# Produces the TRUE full-manifest ptrace-verify denominator: for every one of the
# 200 verify-mode manifest cells (184 compiled C + 16 shell/interpreter), run
#   hermit run --strict --verify   (ptrace backend, L2 DETLOG-bitwise self-verify)
# and record pass/fail. Reuses the guests already compiled by the companion
# kvm_fullcorpus sweep under $BUILD, and uses the SAME lane flags that sweep used
# for its ptrace reference so this denominator is apples-to-apples with the KVM
# parity column. Bypasses manifest enabled/disabled gating (measures what ptrace
# CAN verify over the whole corpus). Research/data-collection only.
set -u
EXP=/home/newton/work/dev-hermit/experiments/ptrace_fullcorpus_scorecard_20260801
KVMEXP=/home/newton/work/dev-hermit/experiments/kvm_fullcorpus_scorecard_20260801
HROOT=/home/newton/work/dev-hermit/hermit
BIN=${HERMIT_BIN:-$HROOT/target/release/hermit}     # RELEASE binary (debug is 5-10x slower)
BUILD=$HROOT/target/kvm-fullcorpus                  # gitignored guest build tree (shared, read-reuse)
ROWS=${ROWS:-$EXP/rows}
OUTCSV=${OUTCSV:-$EXP/scorecard-ptrace.csv}
RUN_ID=${RUN_ID:-ptrace-fullcorpus-scorecard}
RUN_UTC="@$(date +%s)"
HSHA=$(git -C "$HROOT" rev-parse HEAD)
RSHA=$(git -C /home/newton/work/dev-hermit/reverie rev-parse HEAD)
TMO_VERIFY=${TMO_VERIFY:-120}
PAR=${PAR:-24}
CORPUS_C=${CORPUS_C:-$KVMEXP/corpus.tsv}
CORPUS_NONC=${CORPUS_NONC:-$KVMEXP/corpus-nonc.tsv}
mkdir -p "$BUILD" "$ROWS"
rm -f "$ROWS"/*.row

# --- one C cell: reuse (or rebuild) compiled guest, run ptrace --strict --verify
onec() {
  local id="$1" prog="$2" cflags="$3" extra="$4" lane="$5" cstate="$6"
  local key="${id//\//_}"
  local cell="$BUILD/$key"; mkdir -p "$cell"
  local guest="$cell/guest"
  local flags=""
  [ "$lane" = portable ] && flags="--no-virtualize-cpuid --max-timeslice=disabled"
  [ "${NOFLAGS:-0}" = 1 ] && flags=""     # true-capability run: hermit default flags (preemption on)
  # rebuild guest only if the shared build tree lacks it
  if [ ! -x "$guest" ]; then
    local extra_abs=""
    for e in $extra; do extra_abs="$extra_abs $HROOT/$e"; done
    if ! cc -std=c11 -O2 -g -Wall -Wextra -Werror $cflags "$HROOT/$prog" $extra_abs -o "$guest" 2>"$cell/cc.err"; then
      row "$RUN_ID" "$RUN_UTC" "$HSHA" "$RSHA" false expansion "$lane" "${id%%/*}" "$id" verify ptrace "$cstate" skip "" "" "" 0 "" build-fail
      return
    fi
  fi
  export LC_ALL=C TZ=UTC
  local t0 t1 dur ve ohash
  t0=$(date +%s%3N)
  timeout "$TMO_VERIFY" "$BIN" run --strict --verify $flags -- "$guest" >"$cell/ptv.out" 2>"$cell/ptv.err"; ve=$?
  t1=$(date +%s%3N); dur=$((t1-t0))
  ohash=$(sha256sum "$cell/ptv.out" | cut -c1-64)
  local outcome det reason
  if [ "$ve" = 0 ]; then
    det=1; outcome=pass; reason=""
  elif [ "$ve" = 124 ]; then
    det=0; outcome=timeout; reason="ptrace-verify-timeout-${TMO_VERIFY}s"
  else
    det=0; outcome=diverge; reason="ptrace-verify-fail-exit$ve"
  fi
  # parity of ptrace-vs-ptrace is trivially 1 when it verifies; leave blank (denominator column)
  row "$RUN_ID" "$RUN_UTC" "$HSHA" "$RSHA" false expansion "$lane" "${id%%/*}" "$id" verify ptrace "$cstate" "$outcome" "$det" "" "$ohash" "$dur" "" "$reason"
}

# --- one non-C cell: run the shell/interpreter guest argv under ptrace --strict --verify
onenonc() {
  local id="$1" lane="$2"; shift 2
  local -a gcmd=("$@")
  local key="${id//\//_}"
  local cell="$BUILD/nonc_$key"; mkdir -p "$cell"
  local flags=""
  [ "$lane" = portable ] && flags="--no-virtualize-cpuid --max-timeslice=disabled"
  [ "${NOFLAGS:-0}" = 1 ] && flags=""
  export LC_ALL=C TZ=UTC
  local t0 t1 dur ve ohash
  t0=$(date +%s%3N)
  timeout "$TMO_VERIFY" "$BIN" run --strict --verify $flags -- "${gcmd[@]}" >"$cell/ptv.out" 2>"$cell/ptv.err"; ve=$?
  t1=$(date +%s%3N); dur=$((t1-t0))
  ohash=$(sha256sum "$cell/ptv.out" | cut -c1-64)
  local outcome det reason
  if [ "$ve" = 0 ]; then
    det=1; outcome=pass; reason=""
  elif [ "$ve" = 124 ]; then
    det=0; outcome=timeout; reason="ptrace-verify-timeout-${TMO_VERIFY}s"
  else
    det=0; outcome=diverge; reason="ptrace-verify-fail-exit$ve"
  fi
  row "$RUN_ID" "$RUN_UTC" "$HSHA" "$RSHA" false expansion "$lane" "${id%%/*}" "$id" verify ptrace expansion "$outcome" "$det" "" "$ohash" "$dur" "" "$reason"
}

row() {
  local IFS=,
  echo "$*" > "$ROWS/${9//\//_}.row"   # $9 = test_id
}
export -f onec onenonc row
export EXP KVMEXP HROOT BIN BUILD ROWS RUN_ID RUN_UTC HSHA RSHA TMO_VERIFY

echo "=== C cells ($(wc -l <"$CORPUS_C")) ptrace --strict --verify, PAR=$PAR ==="
xargs -a "$CORPUS_C" -d '\n' -P "$PAR" -I{} bash -c '
  IFS="|" read -r id prog cflags extra lane cstate <<<"$1"
  onec "$id" "$prog" "$cflags" "$extra" "$lane" "$cstate"
' _ {}

echo "=== non-C cells ($(grep -vc "^#" "$CORPUS_NONC")) ptrace --strict --verify, PAR=$PAR ==="
xargs -a "$CORPUS_NONC" -d '\n' -P "$PAR" -I{} bash -c '
  line="$1"
  case "$line" in \#*) exit 0;; esac
  id="${line%%|*}"; rest="${line#*|}"
  lane="${rest%%|*}"; cmd="${rest#*|}"
  cmd="${cmd//HERMITROOT/'"$HROOT"'}"
  # shellcheck disable=SC2086
  onenonc "$id" "$lane" $cmd
' _ {}

HDR="run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason"
{ echo "$HDR"; cat "$ROWS"/*.row; } > "$OUTCSV"

echo "=== PTRACE SWEEP DONE ==="
awk -F, 'NR>1{o[$13]++; if($14=="1")d++; t++}
  END{printf "cells=%d\n",t; for(k in o)printf "  outcome %s=%d\n",k,o[k];
      printf "ptrace L2 pass (deterministic=1): %d/%d (%.1f%%)\n",d,t,100*d/t}' "$EXP/scorecard-ptrace.csv"
