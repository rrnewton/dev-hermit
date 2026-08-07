#!/usr/bin/env bash
# Rebuild the six guests from the sources in mutants/. Sources are derived from
# experiments/strict-certification-mutation-sweep_20260806/mutants/*.c with the
# shared /tmp state path rewritten per guest (see README "Parallel-safety").
set -e
cd "$(dirname "$0")"
for g in clean_ctrl mut_stdout mut_exit mut_detlog_only mut_addr mut_path; do
  gcc -O0 -static -o "mutants/$g" "mutants/$g.c"
done
