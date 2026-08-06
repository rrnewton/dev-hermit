#!/usr/bin/env bash
# prefix_depth.sh — PREFIX PARITY DEPTH: how many DETCORE COMMIT records into the
# INFO log a backend stays identical to the ptrace golden log.
#
# WHY THIS SHAPE. "does demo05 boot" is a BOOLEAN. It reads 0 for months and
# cannot show progress, so it cannot be ratcheted. Prefix depth is MONOTONIC:
# every divergence fixed moves the number up, so partial progress is visible.
#
# Y/Z where Z = COMMIT records in the golden ptrace log, Y = length of the
# identical leading run. Y=0 with a known Z is a REAL DATUM; no measurement is not.
#
# This is INFO-log depth. Today's published parity is STDOUT-ONLY, so this is
# strictly stronger and WILL read worse. That is correct, not a regression.
set -uo pipefail
LU=/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64
export LD_LIBRARY_PATH="$LU"
H=${HERMIT:-/home/newton/work/dev-hermit/ignored/prefix-build/target/release/hermit}
OUT=${OUT:-/home/newton/work/dev-hermit/scratch/prefix}
mkdir -p "$OUT"

commits() {  # $1=logfile+errfile prefix -> ordered COMMIT records, normalised
  cat "$1".log "$1".err 2>/dev/null \
    | grep -o 'COMMIT turn .*' \
    | sed -E 's/0x[0-9a-f]+/HEX/g'
}

run() { # $1=backend $2=tag $3.. = guest argv
  local be="$1" tag="$2"; shift 2
  timeout 240 "$H" --log=info --log-file="$OUT/$tag.$be.log" \
      run --backend "$be" -- "$@" >/dev/null 2>"$OUT/$tag.$be.err"
  echo $?
}

depth() { # $1=golden-prefix $2=cand-prefix -> Y
  commits "$1" > "$OUT/.g"; commits "$2" > "$OUT/.c"
  awk 'NR==FNR{g[FNR]=$0;n=FNR;next}{if(FNR>n||$0!=g[FNR]){print FNR-1;found=1;exit}}
       END{if(!found)print (FNR<n?FNR:n)}' "$OUT/.g" "$OUT/.c"
}

GOLDEN_SHA=$(git -C /home/newton/work/dev-hermit/hermit rev-parse HEAD)
DATE=$(date -u +%Y-%m-%d)
printf '%-22s %-9s %6s %6s  %-8s %s\n' GUEST BACKEND Y Z RC NOTE
for spec in "$@"; do
  tag="${spec%%=*}"; cmd="${spec#*=}"
  # shellcheck disable=SC2086
  grc=$(run ptrace "$tag" $cmd)
  Z=$(commits "$OUT/$tag.ptrace" | wc -l)
  printf '%-22s %-9s %6s %6s  rc=%-5s %s\n' "$tag" "ptrace(golden)" "$Z" "$Z" "$grc" "self-reference"
  for be in dbi sabre e9patch; do
    # shellcheck disable=SC2086
    rc=$(run "$be" "$tag" $cmd)
    if [ "$(commits "$OUT/$tag.$be" | wc -l)" -eq 0 ] && [ "$rc" != 0 ]; then
      printf '%-22s %-9s %6s %6s  rc=%-5s %s\n' "$tag" "$be" "NO-RUN" "$Z" "$rc" "backend did not produce a log"
      continue
    fi
    Y=$(depth "$OUT/$tag.ptrace" "$OUT/$tag.$be")
    printf '%-22s %-9s %6s %6s  rc=%-5s %s\n' "$tag" "$be" "$Y" "$Z" "$rc" ""
  done
done
echo
echo "golden_sha=$GOLDEN_SHA  flags='--log=info --backend <be>'  date=$DATE  metric=COMMIT-record prefix depth (INFO log)"
