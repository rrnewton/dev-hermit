#!/bin/bash
# DEEPENED double-run self-determinism of the PTRACE reference at INFO depth.
#
# Same identity definition as inforung.sh (capture `--log info --log-file`, strip
# the leading wall-clock timestamp, compare byte-for-byte) but two changes that
# matter for the verdict:
#
#   1. LARGE N. inforung.sh ran N=5 pairs, which only bounds a per-pair flake
#      rate at ~45% (one-sided 95%). This runs N=300 by default -> <1%.
#   2. IT DOES NOT STOP AT THE FIRST DIVERGENCE. inforung.sh `break`s on the
#      first bad pair, so it can only ever report "diverged at least once".
#      This one runs all N pairs and counts them, so a rung that flakes yields
#      a RATE (k/N) instead of a bare existence claim. That distinction is the
#      whole point of deepening: a golden that fails 1-in-300 is a very
#      different object from one that fails every time.
#
# Logs are deleted after each clean comparison (300 pairs x ~66KB x 2 would be
# ~40MB/rung); DIVERGENT pairs are RETAINED for inspection.
set -u
BIN="${BIN:?set BIN}"
N="${N:-300}"
TMO="${TMO:-180}"
EXTRA="${EXTRA:-}"
TAG="${TAG:-deep}"
RUNGSET="${RUNGSET:-goldens}"
D="${D:-/home/newton/work/dev-hermit/ignored/w2-selfcheck-deepen}"
OUT=$D/deepen-$TAG.tsv
W=$D/$TAG.d
mkdir -p "$W"
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}:/home/newton/.local/hermit-deps/lu/usr/lib64

norm() { sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z[[:space:]]*//' "$1"; }

run_rung() {
  local name="$1"; shift
  local clean=0 bad=0 err=0 Z=0 detail=""
  for i in $(seq 1 "$N"); do
    local a="$W/$name-a.log" b="$W/$name-b.log"
    timeout "$TMO" "$BIN" --log info --log-file "$a" run --strict $EXTRA -- "$@" >/dev/null 2>&1 || true
    timeout "$TMO" "$BIN" --log info --log-file "$b" run --strict $EXTRA -- "$@" >/dev/null 2>&1 || true
    if [ ! -s "$a" ] || [ ! -s "$b" ]; then
      err=$((err+1)); rm -f "$a" "$b"; continue
    fi
    norm "$a" > "$a.n"; norm "$b" > "$b.n"
    [ "$Z" = 0 ] && Z=$(grep -c "COMMIT turn" "$a.n" 2>/dev/null || echo 0)
    if cmp -s "$a.n" "$b.n"; then
      clean=$((clean+1))
      rm -f "$a" "$b" "$a.n" "$b.n"
    else
      bad=$((bad+1))
      # retain the divergent pair, indexed by pair number
      local ln ncommit firstline
      ln=$(cmp "$a.n" "$b.n" 2>/dev/null | sed -E 's/.*line ([0-9]+).*/\1/')
      ncommit=$(head -n "${ln:-1}" "$a.n" | grep -c "COMMIT turn" || echo 0)
      firstline=$(diff "$a.n" "$b.n" | grep -m1 '^<' | cut -c3- | cut -c1-160)
      [ -z "$detail" ] && detail="DIVERGENT-AT-COMMIT-$ncommit (pair $i, line ${ln:-?}): ${firstline}"
      mv "$a.n" "$W/DIVERGENT-$name-pair$i-a.n"; mv "$b.n" "$W/DIVERGENT-$name-pair$i-b.n"
      rm -f "$a" "$b"
    fi
  done
  local verdict=IDENTICAL
  [ "$bad" -gt 0 ] && verdict=DIVERGENT
  [ "$clean" -eq 0 ] && [ "$err" -gt 0 ] && verdict=ERROR
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$name" "$*" "$Z" "$clean" "$bad" "$err" "$verdict" "$detail" >> "$OUT"
  printf '%-14s Z=%-4s clean=%-4s bad=%-3s err=%-3s %-10s %s\n' \
    "$name" "$Z" "$clean" "$bad" "$err" "$verdict" "$detail"
}

# one rung per invocation when RUNG is set (enables per-rung parallelism)
if [ -n "${RUNG:-}" ]; then
  printf 'rung\targv\tZ_commits\tpairs_clean\tpairs_divergent\tpairs_error\tverdict\tdetail\n' > "$OUT"
  case "$RUNG" in
    true)          run_rung true          /bin/true ;;
    echo-hello)    run_rung echo-hello    /bin/echo hello ;;
    wc-hostname)   run_rung wc-hostname   /usr/bin/wc -c /etc/hostname ;;
    fork-pipeline) run_rung fork-pipeline /bin/sh -c '/bin/echo a | /usr/bin/wc -c' ;;
    echo-golden)   run_rung echo-golden   /bin/echo hermit-golden ;;
    cat-hostname)  run_rung cat-hostname  /bin/cat /etc/hostname ;;
    wc-passwd)     run_rung wc-passwd     /usr/bin/wc -l /etc/passwd ;;
    head-passwd)   run_rung head-passwd   /usr/bin/head -1 /etc/passwd ;;
    sh-pipeline)   run_rung sh-pipeline   /bin/sh -c '/bin/echo a | /usr/bin/wc -c' ;;
    sh-loop-exec)  run_rung sh-loop-exec  /bin/sh -c 'for i in 1 2 3; do /bin/echo $i; done' ;;
    *) echo "unknown RUNG=$RUNG" >&2; exit 2 ;;
  esac
  exit 0
fi

echo "set RUNG=<name> to run a single rung" >&2; exit 2
