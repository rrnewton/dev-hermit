#!/bin/bash
# Usage: ./run.sh <path-to-hermit-binary>
H=${1:?need hermit binary}
G='set -euo pipefail; printf "alpha\nalpha\nbeta\nbeta\ngamma\n" | uniq -d | diff -u <(printf "alpha\nbeta\n") -; printf "uniq-ok\n"'
for be in ptrace kvm; do
  s=$(date +%s.%N)
  setsid --wait timeout -s KILL 30 "$H" run --backend $be --strict --tmp=/tmp -- /bin/true >/dev/null 2>&1
  printf "%-7s /bin/true      rc=%-4s wall=%.2fs\n" "$be" "$?" "$(echo "$(date +%s.%N)-$s"|bc)"
  setsid --wait timeout -s KILL 30 "$H" run --backend $be --strict --tmp=/tmp -- /bin/bash -c "$G" >/tmp/o 2>/tmp/e
  printf "%-7s pipeline-uniq  rc=%-4s err='%s'\n" "$be" "$?" "$(tr '\n' '|' </tmp/e|head -c 80)"
  setsid --wait timeout -s KILL 30 "$H" run --backend $be --strict --tmp=/tmp -- /bin/bash -c 'printf "a\nb\n" | /tmp/w2nb' 2>&1 | grep PROBE | sed "s/^/$be  /"
done
