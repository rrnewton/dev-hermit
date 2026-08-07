#!/bin/bash
# LiteInst prefix-parity depth, using ci-hub/parity/prefix_depth.sh's OWN method
# (same commits() normalisation, same depth() awk, same Y/Z/EMIT semantics).
# Written separately rather than editing the shared ratchet, which another task owns.
H="$1"; OUT="$2"; mkdir -p "$OUT"
export LD_LIBRARY_PATH=/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64
G=/home/newton/work/dev-hermit/scratch/w7-liteinst-maps/pg
commits() { cat "$1".log "$1".err 2>/dev/null | grep -o 'COMMIT turn .*' | sed -E 's/0x[0-9a-f]+/HEX/g'; }
run() { local be="$1" tag="$2"; shift 2
  timeout 240 "$H" --log=info --log-file="$OUT/$tag.$be.log" run --backend "$be" -- "$@" >/dev/null 2>"$OUT/$tag.$be.err"; echo $?; }
depth() { commits "$1" > "$OUT/.g"; commits "$2" > "$OUT/.c"
  awk 'NR==FNR{g[FNR]=$0;n=FNR;next}{if(FNR>n||$0!=g[FNR]){print FNR-1;found=1;exit}}
       END{if(!found)print (FNR<n?FNR:n)}' "$OUT/.g" "$OUT/.c"; }
echo "binary: $("$H" --version)"
printf '%-22s %-9s %6s %6s %6s  %-8s\n' GUEST BACKEND Y Z EMIT RC
for g in "$G"/*; do
  [ -x "$g" ] || continue
  tag=$(basename "$g")
  grc=$(run ptrace "$tag" "$g"); Z=$(commits "$OUT/$tag.ptrace" | wc -l)
  if [ "$Z" -eq 0 ]; then printf '%-22s %-9s %6s %6s %6s  rc=%-5s\n' "$tag" ptrace NO-GOLDEN 0 0 "$grc"; continue; fi
  printf '%-22s %-9s %6s %6s %6s  rc=%-5s\n' "$tag" "ptrace(gold)" "$Z" "$Z" "$Z" "$grc"
  rc=$(run liteinst "$tag" "$g"); emitted=$(commits "$OUT/$tag.liteinst" | wc -l)
  if [ "$emitted" -eq 0 ]; then printf '%-22s %-9s %6s %6s %6s  rc=%-5s\n' "$tag" liteinst NO-RUN "$Z" 0 "$rc"; continue; fi
  Y=$(depth "$OUT/$tag.ptrace" "$OUT/$tag.liteinst")
  printf '%-22s %-9s %6s %6s %6s  rc=%-5s\n' "$tag" liteinst "$Y" "$Z" "$emitted" "$rc"
done
