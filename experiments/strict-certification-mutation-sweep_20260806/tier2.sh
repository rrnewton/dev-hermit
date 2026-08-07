#!/bin/bash
# Tier discrimination via two SEPARATE hermit invocations (avoids the --verify
# internal-double-run slow-drain). Each guest bumps /tmp/w7mutstate, so
# invocation A and invocation B are the planted "run 1" and "run 2".
H=/home/newton/work/dev-hermit/hermit/target/debug/hermit
D="$(cd "$(dirname "$0")" && pwd)"; M="$D/${MUTDIR:-mutants}"
OUT="$D/tier2-results-${BK}${TAG:-}.csv"
BK="${BK:-ptrace}"
echo "backend,guest,tier,extra_flags,rc_a,rc_b,stdout_same,exit_same,lines_a,lines_b,divergent_lines,caught" > "$OUT"
norm(){ sed 's/\x1b\[[0-9;]*m//g' "$1" | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z[[:space:]]*//'; }
run(){ # $1=logfile $2=guest $3...=extra
  local lf="$1" g="$2"; shift 2
  local pre=(run --strict --base-env=minimal --max-timeslice=disabled --tmp=/tmp)
  local bflag=(); [ "$BK" != ptrace ] && bflag=(--backend "$BK")
  [ "$BK" = ptrace ] && pre+=(--no-virtualize-cpuid)
  timeout 300 "$H" --log=info --log-file="$lf" "${bflag[@]}" "${pre[@]}" "$@" -- "$M/$g" 2>/tmp/w7e.$$ 
}
for g in clean_ctrl mut_stdout mut_exit mut_detlog_only mut_addr mut_path; do
 for tier in INFO INFO_DETLOG DEBUG HEAP STACK; do
  extra=(); lvl=info; filt=cat
  case $tier in
    INFO)        lvl=info; filt="grep -a -E '^INFO '";;
    INFO_DETLOG) lvl=info; filt="grep -a DETLOG";;
    DEBUG)       lvl=debug; filt="grep -a -E '^DEBUG '";;
    HEAP)        lvl=info; extra=(--detlog-heap); filt="grep -a DETLOG";;
    STACK)       lvl=info; extra=(--detlog-stack); filt="grep -a DETLOG";;
  esac
  rm -f /tmp/w7mutstate /tmp/w7mutpath_* /tmp/w7A.log /tmp/w7B.log
  oa=$(HERMIT_LOG=$lvl timeout 300 "$H" --log=$lvl --log-file=/tmp/w7A.log \
        $([ "$BK" != ptrace ] && echo --backend $BK) run --strict \
        $([ "$BK" = ptrace ] && echo --no-virtualize-cpuid) \
        --base-env=minimal --max-timeslice=disabled --tmp=/tmp "${extra[@]}" -- "$M/$g" 2>/dev/null); ra=$?
  ob=$(HERMIT_LOG=$lvl timeout 300 "$H" --log=$lvl --log-file=/tmp/w7B.log \
        $([ "$BK" != ptrace ] && echo --backend $BK) run --strict \
        $([ "$BK" = ptrace ] && echo --no-virtualize-cpuid) \
        --base-env=minimal --max-timeslice=disabled --tmp=/tmp "${extra[@]}" -- "$M/$g" 2>/dev/null); rb=$?
  if [ ! -s /tmp/w7A.log ] || [ ! -s /tmp/w7B.log ]; then
    echo "$BK,$g,$tier,${extra[*]:--},$ra,$rb,NA,NA,0,0,NOLOG,NOLOG" | tee -a "$OUT"; continue
  fi
  [ "$oa" = "$ob" ] && ss=same || ss=DIFF
  [ "$ra" = "$rb" ] && es=same || es=DIFF
  la=$(norm /tmp/w7A.log | eval $filt | wc -l); lb=$(norm /tmp/w7B.log | eval $filt | wc -l)
  dv=$(diff <(norm /tmp/w7A.log | eval $filt) <(norm /tmp/w7B.log | eval $filt) | grep -c '^<')
  if [ "$ss" = DIFF ] || [ "$es" = DIFF ] || [ "$dv" -gt 0 ]; then c=CAUGHT; else c=missed; fi
  echo "$BK,$g,$tier,${extra[*]:--},$ra,$rb,$ss,$es,$la,$lb,$dv,$c" | tee -a "$OUT"
 done
done
rm -f /tmp/w7e.$$
