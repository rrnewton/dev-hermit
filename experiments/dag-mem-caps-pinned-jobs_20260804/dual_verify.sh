#!/bin/bash
# DUAL-VERIFY the per-node memory caps in ci/dag/portable.json.
#
# The runner (agent-utils safe_ci_dag_runner) enforces hard_mem_max_bytes as a
# cgroup-v2 inner limit: memory.max = <hard_mem_max_bytes> (verbatim, no factor),
# memory.swap.max = 0, memory.high = max  => a step exceeding the cap is
# kernel-OOM-killed (SIGKILL). `systemd-run --user -p MemoryMax=N -p
# MemorySwapMax=0 --scope` writes exactly those two cgroup files on a v2 scope,
# so it exercises the IDENTICAL kernel primitive (this is the mechanism, not a
# proxy for it).
#
# NEGATIVE LEG (per cap): box a memory hog at MemoryMax=cap and have it try to
#   touch cap+512MiB. Expect OOM-kill (the mechanism is not permissive / the cap
#   value actually bites at this size).
# POSITIVE LEG is handled separately (genuine workloads re-boxed at cap); the
#   uncapped cgroup-RECORDED peak < cap (results.csv) is the by-construction
#   argument for every node since peaks were measured with NO inner cap.
set -u
WT=/home/newton/work/dev-hermit/worktrees/lander/hermit
OUT=/home/newton/work/dev-hermit/experiments/dag-mem-caps-pinned-jobs_20260804
HOG=$OUT/memhog.py
NEG_CSV=$OUT/verify-negative.csv

cat > "$HOG" <<'PY'
import sys
# Allocate & TOUCH memory in 64MiB chunks up to target bytes, forcing RSS.
target = int(sys.argv[1])
chunk = 64 * 1024 * 1024
blocks = []
done = 0
while done < target:
    n = min(chunk, target - done)
    b = bytearray(n)
    for i in range(0, n, 4096):   # touch every page
        b[i] = 1
    blocks.append(b)
    done += n
    print(f"touched {done//(1024*1024)}MiB", flush=True)
print(f"REACHED-TARGET {done//(1024*1024)}MiB", flush=True)
PY

echo "node,cap_bytes,cap_GiB,target_GiB,rc,verdict" > "$NEG_CSV"

# node:cap pairs pulled from portable.json
mapfile -t PAIRS < <(python3 - <<'EOF'
import json
d=json.load(open('/home/newton/work/dev-hermit/worktrees/lander/hermit/ci/dag/portable.json'))
def walk(o):
    if isinstance(o,dict):
        if 'group' in o and 'job' in o and 'cmd' in o: yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)
for n in walk(d):
    if 'CARGO_BUILD_JOBS=8' in n['cmd']:
        print(f"{n['group']}.{n['job']} {n['hint']['hard_mem_max_bytes']}")
EOF
)

GiB=$((1024*1024*1024))
for line in "${PAIRS[@]}"; do
  name="${line% *}"; cap="${line#* }"
  target=$((cap + 512*1024*1024))     # exceed cap by 512MiB
  unit="mcneg-${name//./-}"
  systemctl --user reset-failed "$unit" 2>/dev/null
  echo "=== NEG $name cap=$((cap/GiB)).$(( (cap%GiB)*100/GiB ))GiB target=$((target/GiB)).$(( (target%GiB)*100/GiB ))GiB ==="
  systemd-run --user --scope --unit="$unit" \
    -p MemoryMax="$cap" -p MemorySwapMax=0 \
    /usr/bin/python3 "$HOG" "$target" > "$OUT/neg-${name//./-}.log" 2>&1
  rc=$?
  if grep -q REACHED-TARGET "$OUT/neg-${name//./-}.log"; then
    verdict="FAIL-NOT-ENFORCED"
  elif [ "$rc" -eq 137 ] || [ "$rc" -ge 128 ]; then
    verdict="PASS-OOM-KILLED"
  else
    verdict="UNCLEAR-rc$rc"
  fi
  printf '%s,%s,%.2f,%.2f,%s,%s\n' "$name" "$cap" "$(echo "scale=2;$cap/$GiB"|bc)" "$(echo "scale=2;$target/$GiB"|bc)" "$rc" "$verdict" | tee -a "$NEG_CSV"
  systemctl --user reset-failed "$unit" 2>/dev/null
done
echo "=== NEGATIVE LEG COMPLETE -> $NEG_CSV ==="
