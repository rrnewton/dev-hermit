#!/bin/bash
# Tier discrimination, generic over backend. Captures BOTH --log-file and stderr and
# uses whichever carries the DETLOG stream (DBI ignores --log-file and logs to stderr).
H="${HERMIT:-/home/newton/work/dev-hermit/hermit/target/debug/hermit}"
D="$(cd "$(dirname "$0")" && pwd)"; M="$D/${MUTDIR:-mutants-dyn}"
BK="${BK:-ptrace}"; OUT="$D/tier3-results-${BK}.csv"
echo "backend,guest,tier,rc_a,rc_b,stdout_same,exit_same,src,lines_a,lines_b,divergent,caught" > "$OUT"
norm(){ sed 's/\x1b\[[0-9;]*m//g' "$1" | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z[[:space:]]*//'; }
one(){ # $1=logpath $2=errpath $3=lvl $4=guest $5..=extra
  local lf="$1" ef="$2" lvl="$3" g="$4"; shift 4
  local bf=(); [ "$BK" != ptrace ] && bf=(--backend "$BK")
  local pf=(); [ "$BK" = ptrace ] && pf=(--no-virtualize-cpuid)
  timeout 300 "$H" --log="$lvl" --log-file="$lf" "${bf[@]}" run --strict "${pf[@]}" \
    --base-env=minimal --max-timeslice=disabled --tmp=/tmp "$@" -- "$M/$g" 2>"$ef"
}
for g in clean_ctrl mut_stdout mut_exit mut_detlog_only mut_addr mut_path; do
 for tier in INFO INFO_DETLOG DEBUG HEAP STACK; do
  extra=(); lvl=info; filt="grep -a DETLOG"
  case $tier in
    INFO)        filt="grep -a -E '^INFO '";;
    INFO_DETLOG) :;;
    DEBUG)       lvl=debug; filt="grep -a -E '^DEBUG '";;
    HEAP)        extra=(--detlog-heap);;
    STACK)       extra=(--detlog-stack);;
  esac
  rm -f /tmp/w7mutstate /tmp/w7mutpath_* /tmp/w7A.log /tmp/w7B.log /tmp/w7A.err /tmp/w7B.err
  oa=$(one /tmp/w7A.log /tmp/w7A.err $lvl $g "${extra[@]}"); ra=$?
  ob=$(one /tmp/w7B.log /tmp/w7B.err $lvl $g "${extra[@]}"); rb=$?
  # pick whichever sink actually carries the stream
  if [ -s /tmp/w7A.log ]; then A=/tmp/w7A.log; B=/tmp/w7B.log; src=logfile
  else A=/tmp/w7A.err; B=/tmp/w7B.err; src=stderr; fi
  if [ ! -s "$A" ] || [ ! -s "$B" ]; then
    echo "$BK,$g,$tier,$ra,$rb,NA,NA,none,0,0,NOLOG,NOLOG" | tee -a "$OUT"; continue
  fi
  [ "$oa" = "$ob" ] && ss=same || ss=DIFF
  [ "$ra" = "$rb" ] && es=same || es=DIFF
  la=$(norm $A|eval $filt|wc -l); lb=$(norm $B|eval $filt|wc -l)
  dv=$(diff <(norm $A|eval $filt) <(norm $B|eval $filt)|grep -c '^<')
  if [ "$la" -eq 0 ] || [ "$lb" -eq 0 ]; then c=VACUOUS
  elif [ "$ss" = DIFF ] || [ "$es" = DIFF ] || [ "$dv" -gt 0 ]; then c=CAUGHT; else c=missed; fi
  echo "$BK,$g,$tier,$ra,$rb,$ss,$es,$src,$la,$lb,$dv,$c" | tee -a "$OUT"
 done
done
