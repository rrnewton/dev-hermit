#!/usr/bin/env bash
# Multi-guest COMMIT-turn prefix depth, ptrace golden vs other backends.
# Normalization is copied VERBATIM from ci-hub/parity/prefix_depth.sh so these
# numbers are comparable to the recorded rung baseline. The only change is that
# KVM is in the backend loop -- prefix_depth.sh hardcodes dbi/sabre/e9patch and
# structurally cannot measure the pair reverie#402 targets.
set -uo pipefail
export LD_LIBRARY_PATH=/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64
H=${HERMIT:-/home/newton/work/dev-hermit/scratch/p4/bin/hermit}
OUT=${OUT:-/home/newton/work/dev-hermit/ignored/w15-prefix-sweep/out}
BACKENDS=${BACKENDS:-kvm}
mkdir -p "$OUT"

commits() { cat "$1".log "$1".err 2>/dev/null | grep -o 'COMMIT turn .*' | sed -E 's/0x[0-9a-f]+/HEX/g'; }
run() { local be="$1" tag="$2"; shift 2
  timeout 240 "$H" --log=info --log-file="$OUT/$tag.$be.log" \
    run --backend "$be" --base-env minimal -- "$@" >"$OUT/$tag.$be.out" 2>"$OUT/$tag.$be.err"; echo $?; }
depth() { commits "$1" > "$OUT/.g"; commits "$2" > "$OUT/.c"
  awk 'NR==FNR{g[FNR]=$0;n=FNR;next}{if(FNR>n||$0!=g[FNR]){print FNR-1;found=1;exit}}
       END{if(!found)print (FNR<n?FNR:n)}' "$OUT/.g" "$OUT/.c"; }

printf '%-22s %-8s %6s %6s %7s %5s  %s\n' GUEST BACKEND Y Z EMIT RC NOTE
measured=0
total=0
backend_count=0
for be in $BACKENDS; do backend_count=$((backend_count + 1)); done
for spec in "$@"; do
  tag="${spec%%=*}"; cmd="${spec#*=}"
  total=$((total + backend_count))
  grc=$(run ptrace "$tag" $cmd)
  Z=$(commits "$OUT/$tag.ptrace" | wc -l)
  if [ "$Z" -eq 0 ]; then
    printf '%-22s %-8s %6s %6s %7s %5s  %s\n' "$tag" "ptrace" "NO-GOLDEN" 0 0 "$grc" "no denominator; guest yields no rows"
    continue; fi
  printf '%-22s %-8s %6s %6s %7s %5s  %s\n' "$tag" "ptrace" "$Z" "$Z" "$Z" "$grc" "self-reference"
  for be in $BACKENDS; do
    rc=$(run "$be" "$tag" $cmd); em=$(commits "$OUT/$tag.$be" | wc -l)
    if [ "$grc" -ne 0 ] || [ "$rc" -ne 0 ] || [ "$em" -eq 0 ]; then
      printf '%-22s %-8s %6s %6s %7s %5s  %s\n' "$tag" "$be" "UNMEASURED" "$Z" "$em" "$rc" "setup/run failure; zero qualifying trials (NOT a depth)"
    else
      measured=$((measured + 1))
      printf '%-22s %-8s %6s %6s %7s %5s  %s\n' "$tag" "$be" "$(depth "$OUT/$tag.ptrace" "$OUT/$tag.$be")" "$Z" "$em" "$rc" ""
    fi
  done
done
printf 'COVERAGE measured=%d total=%d unit=%s\n' "$measured" "$total" 'guest-x-backend-pairs'
if [ "$measured" -eq 0 ]; then
  printf 'RATCHET UNMEASURED: 0/%d qualifying pairs; no depth may be published or carried forward\n' "$total" >&2
  exit 2
fi
