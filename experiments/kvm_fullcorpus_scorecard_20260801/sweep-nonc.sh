#!/bin/bash
# KVM full-corpus scorecard sweep, NON-C (interpreter/shell) cells.
# The 16 verify-mode manifest cells whose guest is a .sh wrapper or a `direct`
# interpreter command rather than a compiled .c program. Same methodology as
# sweep.sh: measure kvm parity vs ptrace and kvm L2 self-determinism, bypassing
# manifest enabled/disabled gating. KVM is expected to fail most of these
# (dynamically-linked PIE interpreters); the point is honest MEASURED reds so
# the KVM column covers 235's full 200-cell denominator, not blanks.
set -u
EXP=/home/newton/work/dev-hermit/experiments/kvm_fullcorpus_scorecard_20260801
HROOT=/home/newton/work/dev-hermit/hermit
BIN=${HERMIT_BIN:-$HROOT/target/debug/hermit}
BUILD=$HROOT/target/kvm-fullcorpus            # gitignored scratch tree
ROWS=$EXP/rows-nonc
RUN_ID=kvm-fullcorpus-scorecard
RUN_UTC="@$(date +%s)"
HSHA=$(git -C "$HROOT" rev-parse HEAD)
RSHA=$(git -C /home/newton/work/dev-hermit/reverie rev-parse HEAD)
TMO_RUN=30
TMO_VERIFY=60
PAR=${PAR:-8}
CORPUS=${CORPUS:-$EXP/corpus-nonc.tsv}
mkdir -p "$BUILD" "$ROWS"
rm -f "$ROWS"/*.row

one() {
  local id="$1" lane="$2"; shift 2
  local -a gcmd=("$@")                        # already-expanded guest argv
  local key="${id//\//_}"
  local cell="$BUILD/nonc_$key"; mkdir -p "$cell"
  local flags=""
  [ "$lane" = portable ] && flags="--no-virtualize-cpuid --max-timeslice=disabled"
  export LC_ALL=C TZ=UTC
  # ptrace reference (single run)
  timeout "$TMO_RUN" "$BIN" run --strict $flags -- "${gcmd[@]}" >"$cell/pt.out" 2>"$cell/pt.err"; local pe=$?
  # kvm single run
  timeout "$TMO_RUN" "$BIN" run --backend kvm --strict $flags -- "${gcmd[@]}" >"$cell/kvm.out" 2>"$cell/kvm.err"; local ke=$?
  local phash khash
  phash=$(sha256sum "$cell/pt.out" | cut -c1-64)
  khash=$(sha256sum "$cell/kvm.out" | cut -c1-64)
  # kvm L2 verify (determinism), timed
  local t0 t1 dur
  t0=$(date +%s%3N)
  timeout "$TMO_VERIFY" "$BIN" run --backend kvm --strict --verify $flags -- "${gcmd[@]}" >"$cell/kvmv.out" 2>"$cell/kvmv.err"; local ve=$?
  t1=$(date +%s%3N); dur=$((t1-t0))

  local det outcome reason parity ohash
  ohash="$khash"
  if [ "$ke" != 0 ]; then
    det=0; outcome=fail; reason="kvm-run-fail-exit$ke"
  elif [ "$ve" = 0 ]; then
    det=1; outcome=pass; reason=""
  else
    det=0; outcome=diverge; reason="kvm-verify-fail-exit$ve"
  fi
  if [ "$pe" != 0 ]; then
    parity=""; [ -n "$reason" ] && reason="$reason;ptrace-side-fail-exit$pe" || reason="ptrace-side-fail-exit$pe"
  elif [ "$ke" = 0 ] && [ "$phash" = "$khash" ]; then
    parity=1
  else
    parity=0
  fi
  row "$RUN_ID" "$RUN_UTC" "$HSHA" "$RSHA" false expansion "$lane" "${id%%/*}" "$id" verify kvm expansion "$outcome" "$det" "$parity" "$ohash" "$dur" "" "$reason"
}

row() {
  local IFS=,
  echo "$*" > "$ROWS/${9//\//_}.row"
}
export -f one row
export EXP HROOT BIN BUILD ROWS RUN_ID RUN_UTC HSHA RSHA TMO_RUN TMO_VERIFY

# corpus-nonc.tsv rows are: id|lane|guestcmd   where guestcmd uses HERMITROOT/ prefix
xargs -a "$CORPUS" -d '\n' -P "$PAR" -I{} bash -c '
  line="$1"
  case "$line" in \#*) exit 0;; esac
  id="${line%%|*}"; rest="${line#*|}"
  lane="${rest%%|*}"; cmd="${rest#*|}"
  cmd="${cmd//HERMITROOT/'"$HROOT"'}"
  # shellcheck disable=SC2086
  one "$id" "$lane" $cmd
' _ {}

HDR="run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason"
{ echo "$HDR"; cat "$ROWS"/*.row; } > "$EXP/scorecard-kvm-nonc.csv"

echo "=== NON-C SWEEP DONE ==="
awk -F, 'NR>1{o[$13]++; if($14=="1")d++; if($15=="1")p++; if($15=="")pu++; t++}
  END{printf "cells=%d\n",t; for(k in o)printf "  outcome %s=%d\n",k,o[k];
      printf "deterministic=1: %d/%d\n",d,t;
      printf "parity=1: %d (parity-unmeasured: %d)\n",p,pu}' "$EXP/scorecard-kvm-nonc.csv"
