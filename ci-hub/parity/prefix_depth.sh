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
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
readonly ROOT_DIR
LU=${LU:-$ROOT_DIR/ignored/lu-parity/usr/lib64}
export LD_LIBRARY_PATH="$LU"
H=${HERMIT:-$ROOT_DIR/ignored/prefix-build/target/release/hermit}
OUT=${OUT:-$ROOT_DIR/scratch/prefix}
mkdir -p "$OUT"

commits() {  # $1=logfile+errfile prefix -> ordered COMMIT records, normalised
  # A5 (audit item, owned by the ratchet task -- documented here, not changed):
  # this concatenates .log THEN .err, so if a backend emits COMMIT records on
  # BOTH streams the .err records are appended after all .log records rather
  # than interleaved in emission order, and the "prefix" is then a prefix of a
  # sequence that never occurred. The grep does filter out the KVM/LiteInst-only
  # ':: Backend:' banner (it does not match 'COMMIT turn'), so the COUNT is not
  # inflated by it -- the exposure is ORDER, not magnitude. Do not "fix" by
  # dropping .err without first checking which backends log only to stderr.
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

GOLDEN_SHA=$(git -C "$ROOT_DIR/hermit" rev-parse HEAD)
DATE=$(date -u +%Y-%m-%d)
# EMIT is the candidate's own comparable-record count. It is printed as its own
# column because Y alone cannot distinguish "compared and diverged immediately"
# (Y=0, EMIT>0) from "produced nothing to compare" (NO-RUN, EMIT=0). Z is the
# denominator for both: a count is self-describing only next to the size of the
# thing it counted.
printf '%-22s %-9s %6s %6s %6s  %-8s %s\n' GUEST BACKEND Y Z EMIT RC NOTE
status=0
for spec in "$@"; do
  tag="${spec%%=*}"; cmd="${spec#*=}"
  # shellcheck disable=SC2086
  grc=$(run ptrace "$tag" $cmd)
  Z=$(commits "$OUT/$tag.ptrace" | wc -l)
  # A4: Z is the denominator for every row of this guest. If the golden run
  # produced no comparable records there is nothing to be a prefix OF, and the
  # self-reference row would otherwise print Y=0 Z=0 -- which reads as the golden
  # matching itself perfectly, the most reassuring line on the page -- and every
  # backend row would be Y/0. A missing golden is fatal for the guest, not a zero.
  if [ "$Z" -eq 0 ]; then
    printf '%-22s %-9s %6s %6s %6s  rc=%-5s %s\n' \
      "$tag" "ptrace(golden)" "NO-GOLDEN" "0" "0" "$grc" \
      "golden emitted 0 COMMIT records; no denominator, so no rows for this guest"
    status=2
    continue
  fi
  printf '%-22s %-9s %6s %6s %6s  rc=%-5s %s\n' "$tag" "ptrace(golden)" "$Z" "$Z" "$Z" "$grc" "self-reference"
  for be in dbi sabre e9patch; do
    # shellcheck disable=SC2086
    rc=$(run "$be" "$tag" $cmd)
    emitted=$(commits "$OUT/$tag.$be" | wc -l)
    # A3: NO-RUN is decided by the RECORDS, not by the exit code. The old guard
    # was AND-gated (records==0 && rc!=0), so a backend that exits 0 while
    # emitting nothing fell through to depth(), whose awk prints 0 for an empty
    # candidate file, and Y=0 was published as "diverges at record 0". That is
    # sabre's documented failure mode exactly: patched_sites=0, silent ptrace
    # fallback, rc=0. Zero comparable records is NOT-EXERCISED whatever the rc.
    if [ "$emitted" -eq 0 ]; then
      printf '%-22s %-9s %6s %6s %6s  rc=%-5s %s\n' \
        "$tag" "$be" "NO-RUN" "$Z" "0" "$rc" "emitted 0 comparable records (not a depth of 0)"
      continue
    fi
    Y=$(depth "$OUT/$tag.ptrace" "$OUT/$tag.$be")
    printf '%-22s %-9s %6s %6s %6s  rc=%-5s %s\n' "$tag" "$be" "$Y" "$Z" "$emitted" "$rc" ""
  done
done
echo
echo "golden_sha=$GOLDEN_SHA  flags='--log=info --backend <be>'  date=$DATE  metric=COMMIT-record prefix depth (INFO log)"
# Distinct exit status so a consumer can tell "measured" from "could not measure":
# 0 = every guest had a golden denominator, 2 = at least one guest was NO-GOLDEN.
exit "$status"
