#!/bin/bash
# Double-run self-determinism of the PTRACE reference at INFO depth, using the
# golden's own definition: capture `--log info --log-file`, strip the leading
# wall-clock timestamp, compare byte-for-byte. On a difference, report the first
# differing line and the index of the last COMMIT that still matched
# (DIVERGENT-AT-COMMIT-N).
set -u
BIN="${BIN:?set BIN}"
N="${N:-5}"          # number of independent PAIRS per rung
TMO="${TMO:-180}"
EXTRA="${EXTRA:-}"
TAG="${TAG:-info}"
D=/home/newton/work/dev-hermit/ignored/regress-kvm-livelock
OUT=$D/inforung-$TAG.tsv
W=$D/inforung-$TAG.d; rm -rf "$W"; mkdir -p "$W"
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}:/home/newton/.local/hermit-deps/lu/usr/lib64
printf 'rung\targv\tZ_commits\tpairs_clean\tpairs\tverdict\tdetail\n' > "$OUT"

norm() { sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z[[:space:]]*//' "$1"; }

run_rung() {
  local name="$1"; shift
  local clean=0 verdict=IDENTICAL detail="" Z=0
  for i in $(seq 1 "$N"); do
    local a="$W/$name-$i-a.log" b="$W/$name-$i-b.log"
    timeout "$TMO" "$BIN" --log info --log-file "$a" run --strict $EXTRA -- "$@" >/dev/null 2>&1 || true
    timeout "$TMO" "$BIN" --log info --log-file "$b" run --strict $EXTRA -- "$@" >/dev/null 2>&1 || true
    if [ ! -s "$a" ] || [ ! -s "$b" ]; then verdict=ERROR; detail="empty log (pair $i)"; break; fi
    norm "$a" > "$a.n"; norm "$b" > "$b.n"
    Z=$(grep -c "COMMIT turn" "$a.n" 2>/dev/null || echo 0)
    if cmp -s "$a.n" "$b.n"; then
      clean=$((clean+1))
    else
      verdict=DIVERGENT
      local firstline; firstline=$(diff "$a.n" "$b.n" | grep -m1 '^<' | cut -c3- | cut -c1-100)
      # how many COMMITs matched before the first differing line?
      local ln; ln=$(cmp "$a.n" "$b.n" 2>/dev/null | sed -E 's/.*line ([0-9]+).*/\1/')
      local ncommit; ncommit=$(head -n "${ln:-1}" "$a.n" | grep -c "COMMIT turn" || echo 0)
      detail="DIVERGENT-AT-COMMIT-$ncommit (line ${ln:-?}): ${firstline}"
      break
    fi
  done
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$*" "$Z" "$clean" "$N" "$verdict" "$detail" >> "$OUT"
  printf '%-14s Z=%-4s %s/%s  %-10s %s\n' "$name" "$Z" "$clean" "$N" "$verdict" "$detail"
}

if [ "${RUNGSET:-ratchet}" = "ratchet" ]; then
  run_rung true          /bin/true
  run_rung echo          /bin/echo hello
  run_rung wc-hostname   /usr/bin/wc -c /etc/hostname
  run_rung fork-pipeline /bin/sh -c '/bin/echo a | /usr/bin/wc -c'
else
  run_rung true          /bin/true
  run_rung echo          /bin/echo hermit-golden
  run_rung cat-hostname  /bin/cat /etc/hostname
  run_rung wc-passwd     /usr/bin/wc -l /etc/passwd
  run_rung head-passwd   /usr/bin/head -1 /etc/passwd
  run_rung sh-pipeline   /bin/sh -c '/bin/echo a | /usr/bin/wc -c'
  run_rung sh-loop-exec  /bin/sh -c 'for i in 1 2 3; do /bin/echo $i; done'
fi
echo "--- $OUT"
