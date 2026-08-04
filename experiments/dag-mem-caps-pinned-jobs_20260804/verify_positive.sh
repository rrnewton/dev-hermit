#!/bin/bash
# POSITIVE LEG: run each GENUINE node workload boxed AT its new hard_mem_max_bytes
# (cgroup v2 memory.max + memory.swap.max=0, the runner's exact mechanism) and
# confirm it completes with NO OOM-kill. Uses the surviving warm shared target so
# each node runs DAG-representative (build.workspace already warmed it), matching
# how results.csv peaks were taken.
set -u
WT=/home/newton/work/dev-hermit/worktrees/lander/hermit
OUT=/home/newton/work/dev-hermit/experiments/dag-mem-caps-pinned-jobs_20260804
TDIR=/tmp/mc-target-j4hi         # warm shared target from the sweep
POS_CSV=$OUT/verify-positive.csv
J=8

declare -A CMD
CMD[build.workspace]="cargo build --workspace --features third-party-backends"
CMD[build.dbi_release]="cargo build --release --locked -p hermit --features third-party-backends -p detcore-dbi -p hermit-install"
CMD[build.sabre_release]="cargo build --release --locked -p detcore-sabre && cargo build --release --locked -p hermit-install"
CMD[doc.doctests]="cargo test --workspace --features third-party-backends --doc"
CMD[test.hermit_unit]="cargo test -p hermit --features third-party-backends --lib --bins -- --test-threads=1"
CMD[lint.clippy]="cargo clippy --workspace --all-targets -- -D warnings"
CMD[doc.rustdoc]="cargo doc --workspace --no-deps"
CMD[test.regular_crates]="cargo nextest run --workspace --exclude detcore --exclude hermit --exclude hermetic_infra_hermit_flaky-tests"
CMD[build.flaky_harnesses]="cargo test -p hermetic_infra_hermit_flaky-tests --no-run"
CMD[test.detcore_unit]="cargo test -p detcore --lib --bins"

# order: cheap first, expensive last
ORDER=(build.flaky_harnesses doc.doctests doc.rustdoc test.detcore_unit test.regular_crates build.sabre_release test.hermit_unit lint.clippy build.workspace build.dbi_release)

echo "node,cap_GiB,rc,oom_kill,peak,verdict" > "$POS_CSV"

for name in "${ORDER[@]}"; do
  cap=$(python3 - "$name" <<'EOF'
import json,sys
d=json.load(open('/home/newton/work/dev-hermit/worktrees/lander/hermit/ci/dag/portable.json'))
def walk(o):
    if isinstance(o,dict):
        if 'group' in o and 'job' in o and 'cmd' in o: yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)
t=sys.argv[1]
for n in walk(d):
    if f"{n['group']}.{n['job']}"==t: print(n['hint']['hard_mem_max_bytes']); break
EOF
)
  cmd="${CMD[$name]}"
  unit="mcpos-${name//./-}"
  nlog="$OUT/pos-${name//./-}.log"
  systemctl --user reset-failed "$unit" 2>/dev/null
  echo "=== $(date -u +%H:%M:%S) POS $name cap=$cap ($(echo "scale=2;$cap/1073741824"|bc)GiB) ==="
  systemd-run --user --unit="$unit" -p MemoryAccounting=1 \
    -p MemoryMax="$cap" -p MemorySwapMax=0 \
    --working-directory="$WT" \
    --setenv=HOME="$HOME" --setenv=PATH="$PATH" \
    --setenv=CARGO_BUILD_JOBS=$J --setenv=THIRD_PARTY_BUILD_JOBS=$J \
    --setenv=CARGO_TARGET_DIR="$TDIR" \
    --setenv=HERMIT_INSTALL_FORCE_RESTAGE="verify-$name" \
    --wait /bin/bash -c "exec with-proxy $cmd" > "$nlog" 2>&1
  rc=$?
  # read oom_kill + peak from the transient unit's cgroup before reset
  oom=$(systemctl --user show "$unit" -p Result --value 2>/dev/null)
  peak=$(grep -oiE 'Memory peak: [0-9.]+[KMGT]?' "$nlog" | tail -1 | sed 's/Memory peak: //I')
  if [ "$rc" -eq 0 ]; then verdict="PASS-NO-KILL"
  elif [ "$rc" -eq 137 ] || [ "$oom" = "oom-kill" ]; then verdict="FAIL-OOM-KILLED"
  else verdict="FAIL-rc$rc($oom)"; fi
  echo "$name,$(echo "scale=2;$cap/1073741824"|bc),$rc,${oom:-NA},${peak:-NA},$verdict" | tee -a "$POS_CSV"
  systemctl --user reset-failed "$unit" 2>/dev/null
done
echo "=== POSITIVE LEG COMPLETE -> $POS_CSV ==="
