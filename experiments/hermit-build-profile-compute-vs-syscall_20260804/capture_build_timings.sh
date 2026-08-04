#!/bin/bash
# capture_build_timings.sh — width(t) profile for the cargo build fat-middle.
# Answers owner's "report the WIDTH PROFILE OVER TIME, not an average" for build steps.
#
# READINESS-GATED: refuses to run unless the shared box is clear of other agents'
# validate.sh / test_harness.sh / exp-* work. Safe to poll — it will NOT contend a
# peer's SOLO window. Run manually when the box frees:
#     bash experiments/hermit-build-profile-compute-vs-syscall_20260804/capture_build_timings.sh
#
# Traps this avoids (from this task's history): absolute paths only (relative exec => rc127);
# systemd-run --user NOT nested inside another --user unit (nested bus break); own output dir.
set -u
HERMIT=/home/newton/work/dev-hermit/hermit
OUTDIR=/home/newton/work/dev-hermit/experiments/hermit-build-profile-compute-vs-syscall_20260804/build-timings
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
UNIT="buildtimings-${STAMP}"

# --- readiness predicate: box must be clear of other agents' heavy work ---
BUSY=$(ps -eo args 2>/dev/null | grep -E 'validate\.sh|test_harness\.sh|prebuild-then-validate|harness\.sh (release|debug|native)' | grep -v grep | grep -v capture_build_timings)
if [ -n "$BUSY" ]; then
  echo "BOX NOT CLEAR — deferring (other agents' work in flight):"
  echo "$BUSY" | sed 's/^/  /'
  exit 0
fi
EXPUNITS=$(systemctl --user list-units 'exp-*' --no-legend 2>/dev/null | grep -c running)
if [ "${EXPUNITS:-0}" != "0" ]; then
  echo "BOX NOT CLEAR — $EXPUNITS exp-* units still running; deferring."; exit 0
fi

mkdir -p "$OUTDIR"
echo "Box clear. Capturing cargo --timings for the critical-path build node (build.workspace, DEBUG)."
# From-scratch: remove target so the timing reflects the real cold fat-middle, not incremental ~0.
# Boxed via systemd-run --user --wait (asks systemd for a scope = still boxed, not a bypass).
systemd-run --user --wait --collect --quiet --unit="$UNIT" \
  --property=CPUQuota=100% \
  --working-directory="$HERMIT" \
  --setenv=HOME=/home/newton --setenv=PATH="$PATH" \
  --setenv=CARGO_BUILD_JOBS=64 \
  /bin/bash -c "exec with-proxy cargo build --workspace --features third-party-backends \
     --timings=html,json -Z unstable-options 2>&1 || \
     exec with-proxy cargo build --workspace --features third-party-backends --timings 2>&1" \
  > "$OUTDIR/build-${STAMP}.log" 2>&1
RC=$?
echo "build rc=$RC log=$OUTDIR/build-${STAMP}.log"
# cargo writes target/cargo-timings/cargo-timing-*.{html,json}; copy into our dir
cp -a "$HERMIT"/target/cargo-timings/cargo-timing-*.html "$OUTDIR/" 2>/dev/null
cp -a "$HERMIT"/target/cargo-timings/cargo-timing-*.json "$OUTDIR/" 2>/dev/null
ls -la "$OUTDIR/"
echo "DONE ${STAMP}. Next: extract width(t) buckets from the JSON (concurrent-unit count over time)."
