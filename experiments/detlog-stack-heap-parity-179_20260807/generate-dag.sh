#!/usr/bin/env bash
set -u

DENOM=/tmp/detlog-canonical-179.txt
OUT=/home/newton/work/dev-hermit/ignored/detlog-parity/current-0041130/corpus
DAG=$OUT/sweep-dag.json
mkdir -p "$OUT"

awk '
BEGIN {
  labels[1]="ptrace";
  labels[2]="ptrace-control";
  labels[3]="dbi";
  labels[4]="kvm";
  labels[5]="sabre";
  labels[6]="liteinst";
  labels[7]="e9patch";
  print "{\"resource_caps\":{\"hermit_guest\":16},\"default_step_timeout\":180,\"steps\":[";
  first=1;
}
{
  id=$0;
  for (i=1; i<=7; i++) {
    label=labels[i];
    job=label "__" id;
    gsub(/[^A-Za-z0-9_-]/, "__", job);
    if (!first) printf ",\n";
    first=0;
    printf "{\"group\":\"measure\",\"job\":\"%s\",\"desc\":\"%s %s\",", job, label, id;
    printf "\"cmd\":\"bash /tmp/cross-backend-detlog-run-one.sh %s %s\",", label, id;
    printf "\"timeout\":150,\"cpu_timeout\":120,";
    printf "\"hint\":{\"resources\":{\"hermit_guest\":1},\"est_duration_s\":2,\"rss_baseline_bytes\":536870912,\"hard_mem_max_bytes\":4294967296,\"classification\":\"latency-bound\"}}";
  }
}
END { print "\n]}"; }
' "$DENOM" > "$DAG"

printf 'dag=%s steps=%s\n' "$DAG" "$(jq '.steps | length' "$DAG")"
