#!/usr/bin/env bash
set -u

ROOT=/home/newton/work/dev-hermit
HROOT=$ROOT/worktrees/w21/hermit
H=$HROOT/target/install_pkg/hermit
BOX=$ROOT/scripts/hermit-box-run
OUT=$ROOT/ignored/detlog-parity/current-0041130/probe
RSRCS=$HROOT/target/install_pkg/rsrcs
LU=$ROOT/ignored/lu-parity/usr/lib64

mkdir -p "$OUT" "$OUT/fakehome"

for backend in ptrace dbi kvm sabre liteinst e9patch; do
  if [ "$backend" = kvm ] && { [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; }; then
    printf '%s\n' UNAVAILABLE_KVM_DEVICE > "$OUT/$backend.rc"
    continue
  fi

  "$BOX" --passthrough --cpu-budget 60 --wall 120 --mem 8G --cores 2 \
    --diag-log "$OUT/$backend.box.log" -- \
    env -i \
      PATH="$RSRCS:/usr/bin:/bin" \
      HOME="$OUT/fakehome" \
      LC_ALL=C LANG=C TZ=UTC TERM=dumb SHELL=/bin/sh USER=newton LOGNAME=newton \
      LD_LIBRARY_PATH="$LU:$RSRCS" \
      HERMIT_INSTALL_DIR="$HROOT/target/install_pkg" \
      HERMIT_E9TOOL="$RSRCS/e9tool" \
      HERMIT_SABRE_BINARY="$RSRCS/sabre" \
      "$H" --log info --log-file "$OUT/$backend.log" run \
        --backend "$backend" --strict --no-virtualize-cpuid --max-timeslice=disabled \
        --detlog-stack --detlog-heap -- /bin/true \
        > "$OUT/$backend.out" 2> "$OUT/$backend.err"
  printf '%s\n' "$?" > "$OUT/$backend.rc"
done

for backend in ptrace dbi kvm sabre liteinst e9patch; do
  rc=$(cat "$OUT/$backend.rc")
  info=$(grep -c ' INFO ' "$OUT/$backend.log" 2>/dev/null || true)
  detlog=$(grep -c 'DETLOG' "$OUT/$backend.log" 2>/dev/null || true)
  stack=$(grep -c 'DETLOG.*\[memory\].*\[stack\]' "$OUT/$backend.log" 2>/dev/null || true)
  heap=$(grep -c 'DETLOG.*\[memory\].*\[heap\]' "$OUT/$backend.log" 2>/dev/null || true)
  err_detlog=$(grep -c 'DETLOG' "$OUT/$backend.err" 2>/dev/null || true)
  printf '%s rc=%s logfile_info=%s logfile_detlog=%s stack=%s heap=%s stderr_detlog=%s\n' \
    "$backend" "$rc" "$info" "$detlog" "$stack" "$heap" "$err_detlog"
done
