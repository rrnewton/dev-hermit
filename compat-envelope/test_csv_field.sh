#!/usr/bin/env bash
# Both-direction test for collect-fullcorpus.sh's csv_field quoting.
# NEGATIVE: a comma/quote-bearing reason must be quoted so the row stays 19 fields.
# POSITIVE: today's comma-free reasons must stay BYTE-IDENTICAL (no new quotes).
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
eval "$(sed -n '/^csv_field() {/,/^}/p' "$here/collect-fullcorpus.sh")"
fail=0
chk() { # $1=input $2=expected
  local got; got=$(csv_field "$1")
  if [ "$got" = "$2" ]; then echo "  ok   [$1] -> [$got]"
  else echo "  FAIL [$1] -> [$got] expected [$2]"; fail=1; fi
}
echo "POSITIVE — current reasons unchanged:"
chk ""                          ""
chk "ptrace-verify-fail-exit1"  "ptrace-verify-fail-exit1"
chk "kvm-verify-timeout-120s"   "kvm-verify-timeout-120s"
echo "NEGATIVE — pathological reasons are quoted:"
chk 'broke, and also broke'     '"broke, and also broke"'
chk 'said "no"'                 '"said ""no"""'
chk 'a,b"c'                     '"a,b""c"'
echo "field-count check (19-column row survives a comma-bearing reason):"
row="fullcorpus,@1,h,r,false,expansion,portable,c,t,verify,ptrace,expansion,diverge,0,,hash,10,,$(csv_field 'broke, and also broke')"
n=$(python3 -c "import csv,sys;print(len(next(csv.reader([sys.argv[1]]))))" "$row")
[ "$n" = 19 ] && echo "  ok   19 fields" || { echo "  FAIL $n fields"; fail=1; }
exit $fail
