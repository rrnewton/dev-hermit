#!/bin/bash
# Mutation test for the register-file hash and its guest-logical-control boundary.
#
# Four legs. Each prints its own evidence so a reader can re-derive the verdict:
#   1 PLANT      a real register divergence AT a control point -> comparison must FAIL
#   2 CLEAN      an otherwise identical guest              -> comparison must PASS
#   3 NON-VACUITY both legs must have EMITTED register lines and had them COMPARED
#   4 BOUNDARY   handler-interior register traffic          -> must NOT be reported
set -u
H=${HERMIT:?set HERMIT}
D=$(cd "$(dirname "$0")" && pwd)
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64
CAD=${CADENCE:-1}
TIER=$([ "$CAD" = 1 ] && echo full || echo "spot-1/$CAD")
fail=0

verify() { # -> prints VERDICT<TAB>compared<TAB>reglines
  local guest=$1 log; log=$(mktemp)
  timeout 180 "$H" --log info run --strict --detlog-regs --detlog-regs-cadence="$CAD" \
      --verify --verify-strict -- "$guest" >"$log" 2>&1
  local v cmp reg
  if grep -q "Success: deterministic" "$log"; then v=PASS
  elif grep -q "Failure: nondeterministic" "$log"; then v=FAIL
  else v=NO-RESULT; fi
  cmp=$(grep -oE "\([0-9]+ \| [0-9]+ (INFO|DETLOG)" "$log" | grep -oE "^\([0-9]+" | tr -d '(' | tail -1)
  reg=$(timeout 180 "$H" --log info run --strict --detlog-regs --detlog-regs-cadence="$CAD" -- "$guest" 2>&1 | grep -c "\[registers\]")
  printf '%s\t%s\t%s' "$v" "${cmp:-0}" "${reg:-0}"
  rm -f "$log"
}

echo "== cost tier: $TIER (cadence=$CAD) =="

IFS=$'\t' read -r v1 c1 r1 <<<"$(verify "$D/regmut_plant")"
echo "LEG 1 PLANT  (register diverges at a control point): verdict=$v1 compared=$c1 reglines=$r1"
[ "$v1" = FAIL ] || { echo "   !! expected FAIL"; fail=1; }

IFS=$'\t' read -r v2 c2 r2 <<<"$(verify "$D/regmut_clean")"
echo "LEG 2 CLEAN  (same shape, constant register)       : verdict=$v2 compared=$c2 reglines=$r2"
[ "$v2" = PASS ] || { echo "   !! expected PASS"; fail=1; }

echo "LEG 3 NON-VACUITY: plant reglines=$r1 clean reglines=$r2 (both must be > 0)"
{ [ "$r1" -gt 0 ] && [ "$r2" -gt 0 ]; } || { echo "   !! a leg went green emitting NO register output"; fail=1; }

# Boundary: heavy tool-handler traffic, no guest-logical divergence.
IFS=$'\t' read -r v4 c4 r4 <<<"$(verify "$D/handler_traffic")"
sy=$(timeout 180 "$H" --log info run --strict --detlog-regs --detlog-regs-cadence="$CAD" -- "$D/handler_traffic" 2>&1 | grep -c "finish syscall")
echo "LEG 4 BOUNDARY (200 CPUID + 200 RDTSC handler entries): verdict=$v4 reglines=$r4 control_points=$sy"
[ "$v4" = PASS ] || { echo "   !! handler-interior traffic was reported as a divergence"; fail=1; }
if [ "$CAD" = 1 ]; then
  [ "$r4" -gt 0 ] && [ "$r4" = "$sy" ] || { echo "   !! sampling leaked outside control points ($r4 samples for $sy points)"; fail=1; }
fi

echo "== regmut: $([ $fail = 0 ] && echo ALL LEGS AS EXPECTED || echo SOME LEG UNEXPECTED) =="
exit $fail
