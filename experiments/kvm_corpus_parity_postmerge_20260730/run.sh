#!/bin/bash
# Re-measure frozen 5-example corpus: KVM stdout parity vs ptrace, single --strict runs.
set -u
H=/home/newton/work/dev-hermit/worktrees/kvm/hermit
BIN=$H/target/debug/hermit
OUT=/tmp/kvm-corpus-postmerge
cd "$H"
EXAMPLES="date.sh devrand.sh race.sh rand.py timed-progress-bar.py"
printf "%-24s %-8s %-8s %-14s %-14s %s\n" EXAMPLE PT_EXIT KVM_EXIT PT_SHA KVM_SHA PARITY
match=0; total=0
for ex in $EXAMPLES; do
  total=$((total+1))
  timeout 60 "$BIN" run --strict -- "examples/$ex" >"$OUT/$ex.ptrace.out" 2>"$OUT/$ex.ptrace.err"; pex=$?
  timeout 60 "$BIN" run --backend kvm --strict -- "examples/$ex" >"$OUT/$ex.kvm.out" 2>"$OUT/$ex.kvm.err"; kex=$?
  psha=$(sha256sum "$OUT/$ex.ptrace.out" | cut -c1-12)
  ksha=$(sha256sum "$OUT/$ex.kvm.out" | cut -c1-12)
  if [ "$psha" = "$ksha" ] && [ "$pex" = "0" ] && [ "$kex" = "0" ]; then par=MATCH; match=$((match+1)); else par=DIFF; fi
  printf "%-24s %-8s %-8s %-14s %-14s %s\n" "$ex" "$pex" "$kex" "$psha" "$ksha" "$par"
done
echo "---"
echo "KVM stdout parity vs ptrace: $match/$total"
