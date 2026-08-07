#!/bin/bash
# Discriminating experiment: does Hermit normalize the PATH axis, or only the
# sources a path change can TRIGGER (time / randomness / readdir order)?
# Two build roots differing only in absolute path. One axis varied.
H=/home/newton/work/dev-hermit/worktrees/int3-swallow176/hermit/target/debug/hermit
A=/tmp/pathaxis/rootA/deep
B=/tmp/pathaxis/rootB-longer-name/deep

run_case() {  # $1=label  $2=cmd  $3=native|hermit
  local out=""
  for root in "$A" "$B"; do
    rm -rf "$root"; mkdir -p "$root"; cp /tmp/pathaxis/payload.sh "$root/payload.sh"
    if [ "$3" = hermit ]; then
      r=$(cd "$root" && $H run --tmp=/tmp --no-rcb-time -- /bin/bash ./payload.sh "$2" 2>/dev/null)
    else
      r=$(cd "$root" && /bin/bash ./payload.sh "$2" 2>/dev/null)
    fi
    out="$out|$(printf '%s' "$r" | sha256sum | cut -c1-16)"
  done
  local h1=${out%|*}; h1=${h1#|}; local h2=${out##*|}
  if [ "$h1" = "$h2" ]; then echo "  $1 [$3]: IDENTICAL   ($h1)"; else echo "  $1 [$3]: DIFFERS     ($h1 vs $h2)"; fi
}

cat > /tmp/pathaxis/payload.sh <<'PAY'
case "$1" in
  path)  pwd ;;                                   # output literally CONTAINS the path
  time)  date +%s%N ;;                            # path-independent, time-sourced
  rand)  head -c 8 /dev/urandom | od -An -tx1 ;;  # path-independent, entropy-sourced
  tarmt) touch -d "@$(date +%s)" f; tar -cf - f --format=pax 2>/dev/null | sha256sum ;;
esac
PAY

for c in path time rand tarmt; do
  run_case "$c" "$c" native
  run_case "$c" "$c" hermit
done
