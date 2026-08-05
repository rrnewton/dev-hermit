#!/usr/bin/env bash
# Reproduce the cold verification that a clean checkout + plain `make` builds and
# wires every backend, compiling the third-party native deps (DynamoRIO/e9patch/
# SaBRe) from scratch. Isolated so it never disturbs the shared ~/.cargo on this box.
#
# Usage: ./reproduce.sh [work_dir]   (default: a fresh temp dir)
set -euo pipefail

WORK="${1:-$(mktemp -d)}"
JOBS="${THIRD_PARTY_BUILD_JOBS:-32}"
HERMIT_SRC="${HERMIT_SRC:-https://github.com/rrnewton/hermit.git}"

echo "== cold verify in $WORK (jobs=$JOBS, src=$HERMIT_SRC) =="
mkdir -p "$WORK"; cd "$WORK"

# 1) Fresh clone, submodules left UNINITIALIZED so `make` auto-init is exercised.
with-proxy git clone "$HERMIT_SRC" hermit
cd hermit
echo "HEAD: $(git rev-parse HEAD)"
git submodule status || true   # expect '-' prefixes (uninitialized)

# 2) Fresh isolated CARGO_HOME => cold fetch+build of reverie git dep + its
#    DynamoRIO/e9patch/SaBRe submodules (nothing cached).
export CARGO_HOME="$WORK/cargo-home"; mkdir -p "$CARGO_HOME"
export THIRD_PARTY_BUILD_JOBS="$JOBS"

# 3) Time the default `make`. SANITY-CHECK: a cold build with DynamoRIO compiled
#    from scratch takes minutes; a ~1-minute build means the deps were cached.
/usr/bin/time -v make 2>&1 | tee "$WORK/cold-make.log"

# 4) Prove DynamoRIO actually compiled (library artifacts, not just configure).
find target -iname 'libdynamorio*' | head

# 5) Smoke-test each backend from the resulting binary.
BIN=target/debug/hermit
for b in ptrace kvm liteinst dbi sabre e9patch; do
  if timeout -k 5 --signal=KILL 180 "$BIN" run --backend "$b" \
       --sequentialize-threads --max-timeslice=disabled /bin/true >/dev/null 2>&1; then
    echo "backend $b : PASS"
  else
    echo "backend $b : rc=$? (kvm may stall in sandboxes without usable /dev/kvm ioctls)"
  fi
done
