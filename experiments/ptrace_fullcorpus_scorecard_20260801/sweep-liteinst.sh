#!/bin/bash
# LiteInst full-corpus scorecard sweep (agent hermit-235).
# LiteInst is a hybrid backend (reverie-liteinst patch runtime + ptrace Detcore
# Tool) available in the default hermit binary. For each of the 200 verify-mode
# cells reuse the shared compiled guest / shell argv and measure:
#   parity = liteinst --strict stdout == ptrace --strict --verify stdout ($cell/ptv.out
#            from the companion ptrace sweep; that is the ptrace reference output)
#   det    = liteinst --strict --verify exits 0 (L2)
# Uses the SAME lane flags as the ptrace/kvm sweeps for apples-to-apples columns.
set -u
EXP=/home/newton/work/dev-hermit/experiments/ptrace_fullcorpus_scorecard_20260801
KVMEXP=/home/newton/work/dev-hermit/experiments/kvm_fullcorpus_scorecard_20260801
HROOT=/home/newton/work/dev-hermit/hermit
BIN=${HERMIT_BIN:-$HROOT/target/release/hermit}
BUILD=$HROOT/target/kvm-fullcorpus
ROWS=$EXP/rows-liteinst
RUN_ID=liteinst-fullcorpus-scorecard
RUN_UTC="@$(date +%s)"
HSHA=$(git -C "$HROOT" rev-parse HEAD)
RSHA=$(git -C /home/newton/work/dev-hermit/reverie rev-parse HEAD)
TMO_RUN=${TMO_RUN:-90}
TMO_VERIFY=${TMO_VERIFY:-120}
PAR=${PAR:-24}
CORPUS_C=${CORPUS_C:-$KVMEXP/corpus.tsv}
CORPUS_NONC=${CORPUS_NONC:-$KVMEXP/corpus-nonc.tsv}
mkdir -p "$BUILD" "$ROWS"
rm -f "$ROWS"/*.row

runcell() { # $1=cell dir (holds ptv.out ref) $2=lane ; rest=guest argv
  local cell="$1" lane="$2"; shift 2
  local -a gcmd=("$@")
  local flags=""
  [ "$lane" = portable ] && flags="--no-virtualize-cpuid --max-timeslice=disabled"
  export LC_ALL=C TZ=UTC
  timeout "$TMO_RUN" "$BIN" run --backend liteinst --strict $flags -- "${gcmd[@]}" >"$cell/li.out" 2>"$cell/li.err"; local re=$?
  local t0 t1 dur ve
  t0=$(date +%s%3N)
  timeout "$TMO_VERIFY" "$BIN" run --backend liteinst --strict --verify $flags -- "${gcmd[@]}" >"$cell/liv.out" 2>"$cell/liv.err"; ve=$?
  t1=$(date +%s%3N); dur=$((t1-t0))
  local lhash phash det outcome reason parity ohash
  lhash=$(sha256sum "$cell/li.out" | cut -c1-64); ohash="$lhash"
  if [ "$ve" = 0 ]; then det=1; outcome=pass; reason="";
  elif [ "$ve" = 124 ]; then det=0; outcome=timeout; reason="liteinst-verify-timeout-${TMO_VERIFY}s";
  else det=0; outcome=diverge; reason="liteinst-verify-fail-exit$ve"; fi
  # parity vs ptrace reference output (only if ptrace ref exists and liteinst ran)
  if [ ! -s "$cell/ptv.out" ] && [ ! -f "$cell/ptv.out" ]; then parity="";
  elif [ "$re" != 0 ]; then parity=0; [ -z "$reason" ] && reason="liteinst-run-fail-exit$re";
  else
    phash=$(sha256sum "$cell/ptv.out" | cut -c1-64)
    if [ "$lhash" = "$phash" ]; then parity=1; else parity=0; fi
  fi
  echo "$RUN_ID,$RUN_UTC,$HSHA,$RSHA,false,expansion,$lane,$BUCKET,$ID,verify,liteinst,expansion,$outcome,$det,$parity,$ohash,$dur,,$reason" > "$ROWS/${ID//\//_}.row"
}
export -f runcell
export EXP KVMEXP HROOT BIN BUILD ROWS RUN_ID RUN_UTC HSHA RSHA TMO_RUN TMO_VERIFY

echo "=== C cells liteinst, PAR=$PAR ==="
xargs -a "$CORPUS_C" -d '\n' -P "$PAR" -I{} bash -c '
  IFS="|" read -r id prog cflags extra lane cstate <<<"$1"
  export ID="$id" BUCKET="${id%%/*}"
  key="${id//\//_}"; cell="'"$BUILD"'/$key"
  [ -x "$cell/guest" ] || exit 0
  runcell "$cell" "$lane" "$cell/guest"
' _ {}

echo "=== non-C cells liteinst, PAR=$PAR ==="
xargs -a "$CORPUS_NONC" -d '\n' -P "$PAR" -I{} bash -c '
  line="$1"; case "$line" in \#*) exit 0;; esac
  id="${line%%|*}"; rest="${line#*|}"; lane="${rest%%|*}"; cmd="${rest#*|}"
  cmd="${cmd//HERMITROOT/'"$HROOT"'}"
  export ID="$id" BUCKET="${id%%/*}"
  key="${id//\//_}"; cell="'"$BUILD"'/nonc_$key"; mkdir -p "$cell"
  # shellcheck disable=SC2086
  runcell "$cell" "$lane" $cmd
' _ {}

HDR="run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason"
{ echo "$HDR"; cat "$ROWS"/*.row; } > "$EXP/scorecard-liteinst.csv"
echo "=== LITEINST SWEEP DONE ==="
awk -F, 'NR>1{o[$13]++; if($14=="1")d++; if($15=="1")p++; if($15=="")pu++; t++}
  END{printf "cells=%d  det=%d/%d (%.1f%%)  parity=%d (unmeasured %d)\n",t,d,t,100*d/t,p,pu;
      for(k in o)printf "  outcome %s=%d\n",k,o[k]}' "$EXP/scorecard-liteinst.csv"
