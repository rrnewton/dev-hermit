#!/bin/bash
set -euo pipefail

DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export DYNAMORIO_HOME=${DYNAMORIO_HOME:-$HOME/dynamorio/install}
export HERMIT_DRRUN=${HERMIT_DRRUN:-$DYNAMORIO_HOME/bin64/drrun}
export HERMIT_DBI_CLIENT=${HERMIT_DBI_CLIENT:?set HERMIT_DBI_CLIENT to libreverie_dbi_client.so}
DRRUN=$HERMIT_DRRUN
HDBI=${HDBI:-$HOME/work/dev-hermit/hermit/target/debug/hermit}
HPT=${HPT:-$HDBI}

meas() { # label reps timeout cmd...
  local label="$1" reps="$2" to="$3"; shift 3
  local out=""
  for r in $(seq 1 $reps); do
    : > /tmp/be.$$
    /usr/bin/time -o /tmp/bt.$$ -f "%e" timeout $to "$@" >/dev/null 2>/tmp/be.$$
    local rc=$? t; t=$(cat /tmp/bt.$$ 2>/dev/null)
    if [ $rc -eq 124 ]; then t=">${to}TO"
    elif grep -qiE "panic|SIGSEGV|Error:" /tmp/be.$$; then t="CRASH"; fi
    out="$out $t"
  done
  printf '%-28s %s\n' "$label:" "$out"
}

echo "### START load=$(cat /proc/loadavg)"
for w in loop3s loop10s mixed; do
  echo "== $w =="
  meas "  native"        3 60  $DIR/$w
  meas "  bareDR(cnt=off)" 2 300 $DRRUN -disable_rseq -- $DIR/$w
  meas "  dbi(cnt=on)"   2 400 with-proxy $HDBI run --backend dbi -- $DIR/$w
  meas "  ptrace"        1 400 with-proxy $HPT run -- $DIR/$w
done
echo "### DONE load=$(cat /proc/loadavg)"
