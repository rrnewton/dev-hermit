#!/bin/bash
# Faithful reproduction of reverie-e9patch build.rs::build_e9patch_tools:
# fresh clean copy of the vendored tree, then `make --jobs=N release`,
# then assert e9tool AND e9patch were produced (build.rs asserts both).
set -u
SRC="$1"; JOBS="$2"; RUNDIR="$3"; LOG="$4"
rm -rf "$RUNDIR"
cp -a "$SRC" "$RUNDIR"          # BpfJailer blocks reflink; plain copy
( cd "$RUNDIR" && make --jobs="$JOBS" release ) > "$LOG" 2>&1
rc=$?
if [ $rc -eq 0 ] && [ -f "$RUNDIR/e9tool" ] && [ -f "$RUNDIR/e9patch" ]; then
  echo PASS
else
  echo "FAIL(rc=$rc tool=$([ -f "$RUNDIR/e9tool" ]&&echo y||echo n) patch=$([ -f "$RUNDIR/e9patch" ]&&echo y||echo n))"
fi
