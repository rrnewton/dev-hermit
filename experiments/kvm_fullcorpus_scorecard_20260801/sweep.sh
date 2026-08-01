#!/bin/bash
# KVM full-corpus scorecard sweep: per verify-mode C cell, measure
#   parity  = kvm stdout == ptrace stdout (both exit 0)   [--strict]
#   det     = kvm --strict --verify exits 0 (bitwise repeat) [L2]
# Emits scorecard.csv-schema rows to rows/<id>.row, assembled to scorecard-kvm.csv.
# Bypasses manifest enabled/disabled gating (measures what KVM CAN do), the same
# way experiments/kvm_b3_corpus_sweep_20260730 did. Research/data-collection only.
set -u
EXP=/home/newton/work/dev-hermit/experiments/kvm_fullcorpus_scorecard_20260801
HROOT=/home/newton/work/dev-hermit/hermit
BIN=${HERMIT_BIN:-$HROOT/target/debug/hermit}
BUILD=$HROOT/target/kvm-fullcorpus          # gitignored guest build tree
ROWS=$EXP/rows
LOGS=$HROOT/target/kvm-fullcorpus/logs      # gitignored raw logs
CORPUS=${CORPUS:-$EXP/corpus.tsv}
RUN_ID=kvm-fullcorpus-scorecard
RUN_UTC="@$(date +%s)"
HSHA=$(git -C "$HROOT" rev-parse HEAD)
RSHA=$(git -C /home/newton/work/dev-hermit/reverie rev-parse HEAD)
TMO_RUN=30
TMO_VERIFY=60
PAR=${PAR:-16}
mkdir -p "$BUILD" "$ROWS" "$LOGS"
rm -f "$ROWS"/*.row

one() {
  local id="$1" prog="$2" cflags="$3" extra="$4" lane="$5" cstate="$6"
  local key="${id//\//_}"
  local cell="$BUILD/$key"; mkdir -p "$cell"
  local guest="$cell/guest"
  local flags=""
  [ "$lane" = portable ] && flags="--no-virtualize-cpuid --max-timeslice=disabled"
  # resolve extra_sources relative to hermit repo root
  local extra_abs=""
  for e in $extra; do extra_abs="$extra_abs $HROOT/$e"; done
  # compile
  if ! cc -std=c11 -O2 -g -Wall -Wextra -Werror $cflags "$HROOT/$prog" $extra_abs -o "$guest" 2>"$cell/cc.err"; then
    row "$RUN_ID" "$RUN_UTC" "$HSHA" "$RSHA" false expansion "$lane" "${id%%/*}" "$id" verify kvm "$cstate" skip "" "" "" 0 "" build-fail
    return
  fi
  export LC_ALL=C TZ=UTC
  # ptrace reference (single run)
  timeout "$TMO_RUN" "$BIN" run --strict $flags -- "$guest" >"$cell/pt.out" 2>"$cell/pt.err"; local pe=$?
  # kvm single run
  timeout "$TMO_RUN" "$BIN" run --backend kvm --strict $flags -- "$guest" >"$cell/kvm.out" 2>"$cell/kvm.err"; local ke=$?
  local phash khash
  phash=$(sha256sum "$cell/pt.out" | cut -c1-64)
  khash=$(sha256sum "$cell/kvm.out" | cut -c1-64)
  # kvm L2 verify (determinism), timed
  local t0 t1 dur
  t0=$(date +%s%3N)
  timeout "$TMO_VERIFY" "$BIN" run --backend kvm --strict --verify $flags -- "$guest" >"$cell/kvmv.out" 2>"$cell/kvmv.err"; local ve=$?
  t1=$(date +%s%3N); dur=$((t1-t0))

  # determinism: kvm --strict --verify exit 0
  local det outcome reason parity ohash
  ohash="$khash"
  if [ "$ke" != 0 ]; then
    det=0; outcome=fail; reason="kvm-run-fail-exit$ke"
  elif [ "$ve" = 0 ]; then
    det=1; outcome=pass; reason=""
  else
    det=0; outcome=diverge; reason="kvm-verify-fail-exit$ve"
  fi
  # parity vs ptrace (only meaningful if ptrace ran)
  if [ "$pe" != 0 ]; then
    parity=""; [ -n "$reason" ] && reason="$reason;ptrace-side-fail-exit$pe" || reason="ptrace-side-fail-exit$pe"
  elif [ "$ke" = 0 ] && [ "$phash" = "$khash" ]; then
    parity=1
  else
    parity=0
  fi
  row "$RUN_ID" "$RUN_UTC" "$HSHA" "$RSHA" false expansion "$lane" "${id%%/*}" "$id" verify kvm "$cstate" "$outcome" "$det" "$parity" "$ohash" "$dur" "" "$reason"
}

row() {
  local IFS=,
  echo "$*" > "$ROWS/${9//\//_}.row"   # $9 = test_id (bucket is $8); use id for filename
}
export -f one row
export EXP HROOT BIN BUILD ROWS LOGS RUN_ID RUN_UTC HSHA RSHA TMO_RUN TMO_VERIFY

# drive corpus in parallel
xargs -a "$CORPUS" -d '\n' -P "$PAR" -I{} bash -c '
  IFS="|" read -r id prog cflags extra lane cstate <<<"$1"
  one "$id" "$prog" "$cflags" "$extra" "$lane" "$cstate"
' _ {}

# assemble
HDR="run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason"
{ echo "$HDR"; cat "$ROWS"/*.row; } > "$EXP/scorecard-kvm.csv"

echo "=== SWEEP DONE ==="
awk -F, 'NR>1{o[$13]++; if($14=="1")d++; if($15=="1")p++; if($15=="")pu++; t++}
  END{printf "cells=%d\n",t; for(k in o)printf "  outcome %s=%d\n",k,o[k];
      printf "deterministic=1: %d/%d (%.1f%%)\n",d,t,100*d/t;
      printf "parity=1: %d (parity-unmeasured/ptrace-side-fail: %d)\n",p,pu;
      m=t-pu; if(m>0)printf "parity%% of measurable: %.1f%% (%d/%d)\n",100*p/m,p,m}' "$EXP/scorecard-kvm.csv"
