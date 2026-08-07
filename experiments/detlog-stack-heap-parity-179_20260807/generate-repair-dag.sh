#!/usr/bin/env bash
set -u

ROOT=/home/newton/work/dev-hermit
DENOM=/tmp/detlog-canonical-179.txt
OUT=$ROOT/ignored/detlog-parity/current-0041130/corpus
DAG=$OUT/repair-dag.json
labels=(ptrace ptrace-control dbi kvm sabre liteinst e9patch)
first=1
count=0

printf '%s\n' '{"resource_caps":{"hermit_guest":4},"default_step_timeout":150,"steps":[' > "$DAG"
while read -r id; do
  key=${id//\//__}
  for label in "${labels[@]}"; do
    [ -f "$OUT/runs/$key/$label/rc" ] && continue
    job=${label}__${key}
    job=${job//[^A-Za-z0-9_-]/__}
    if [ "$first" -eq 0 ]; then printf ',\n' >> "$DAG"; fi
    first=0
    count=$((count + 1))
    printf '{"group":"repair","job":"%s","desc":"%s %s","cmd":"bash /tmp/cross-backend-detlog-run-one.sh %s %s","timeout":150,"cpu_timeout":120,"hint":{"resources":{"hermit_guest":1},"est_duration_s":2,"rss_baseline_bytes":2147483648,"hard_mem_max_bytes":8589934592,"classification":"latency-bound"}}' \
      "$job" "$label" "$id" "$label" "$id" >> "$DAG"
  done
done < "$DENOM"
printf '%s\n' ']}' >> "$DAG"
printf 'dag=%s steps=%s\n' "$DAG" "$count"
