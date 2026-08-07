#!/usr/bin/env bash
# Size candidate rungs: how many detcore records does each guest produce?
# Sizing pass only -- one run each. Self-determinism (n=3) comes after, on the
# candidates that actually land in the gap.
#
# Three-valued as always: a crash, a timeout, or zero records is TOOL-ERROR and
# is never reported as a size.
#
# Guests are passed as an argv ARRAY, not a shell string, so quoting cannot eat
# arguments -- the previous python rung failed because `sh -c` swallowed the
# parens in `print(1)`. Anything needing a shell says so explicitly.
set -uo pipefail
cd /home/newton/work/dev-hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
export PYTHONDONTWRITEBYTECODE=1   # pin the shared bytecode cache (demo05 lesson)
H=${HERMIT_BIN:?set HERMIT_BIN}
D=$HOME/det4-rungs; rm -rf "$D"; mkdir -p "$D"
OUT=ignored/det4-rung-sizes.tsv
: > "$OUT"; printf 'candidate\tverdict\trecords\twall_s\tdetail\n' >> "$OUT"

# a small C file to compile, and a data file to chew on, both outside /tmp
printf 'int main(void){return 0;}\n' > "$D/tiny.c"
cat /usr/include/linux/*.h > "$D/big.txt" 2>/dev/null || true

size() { # label -- argv...
  local label=$1; shift; shift
  local log="$D/$label.log"
  local t0 t1 rc n
  t0=$(date +%s.%N)
  timeout 900 "$H" --log info --log-file "$log" run --backend=ptrace --strict -- "$@" \
    > "$D/$label.out" 2> "$D/$label.err"
  rc=$?
  t1=$(date +%s.%N)
  n=$(grep -cE 'DETLOG|COMMIT turn' "$log" 2>/dev/null || echo 0)
  local w; w=$(printf '%.1f' "$(echo "$t1-$t0" | bc)")
  if [[ $rc -ne 0 || $n -eq 0 ]]; then
    printf '  %-22s TOOL-ERROR (rc=%s records=%s)\n' "$label" "$rc" "$n"
    printf '%s\tTOOL-ERROR\t%s\t%s\t%s\n' "$label" "$n" "$w" \
      "$(head -1 "$D/$label.err" 2>/dev/null | cut -c1-120)" >> "$OUT"
  else
    printf '  %-22s %9d records  %6ss\n' "$label" "$n" "$w"
    printf '%s\tOK\t%s\t%s\t\n' "$label" "$n" "$w" >> "$OUT"
  fi
}

size python-startup      -- /usr/bin/python3 -c 'print(1)'
size python-import-json  -- /usr/bin/python3 -c 'import json,sys; sys.stdout.write(json.dumps({"a":1}))'
size python-loop-10k     -- /usr/bin/python3 -c 'n=0
for i in range(10000): n+=i
print(n)'
size perl-startup        -- /usr/bin/perl -e 'print 1'
size gcc-tiny-c          -- /usr/bin/gcc -O0 -o "$D/tiny.out" "$D/tiny.c"
size sha256-big          -- /usr/bin/sha256sum "$D/big.txt"
size sort-big            -- /bin/sh -c "/usr/bin/sort $D/big.txt | /usr/bin/wc -l"
size grep-recursive      -- /bin/sh -c "/usr/bin/grep -rl include /usr/include | /usr/bin/wc -l"
size find-usr-include    -- /bin/sh -c "/usr/bin/find /usr/include -type f | /usr/bin/wc -l"
size tar-usr-include     -- /bin/sh -c "/usr/bin/tar cf /dev/null /usr/include"
size fork-100            -- /bin/sh -c 'i=0; while [ $i -lt 100 ]; do /bin/true; i=$((i+1)); done'
size fork-500            -- /bin/sh -c 'i=0; while [ $i -lt 500 ]; do /bin/true; i=$((i+1)); done'

echo; column -t -s$'\t' "$OUT"
