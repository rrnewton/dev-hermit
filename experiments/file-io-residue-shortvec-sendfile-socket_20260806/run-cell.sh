#!/usr/bin/env bash
# One residue cell: ONE guest mode, ONE backend, ONE run, PINNED environment.
#
# The env -i block is inherited verbatim from the parent sweep's run-cell.sh and is
# load-bearing, not hygiene: the kernel writes envp into the guest's INITIAL STACK, so any
# host variable that differs between two runs changes --detlog-stack and manufactures a
# false divergence. Measured there: unpinned = 3/3 distinct stack hashes, pinned = 2/2
# identical. Every backend gets the SAME variables (including HERMIT_E9TOOL, exported even
# for backends that ignore it) so envp cannot vary by backend either.
set -u

BACKEND="$1"; GUEST="$2"; OUTDIR="$3"; TAG="$4"; MODE="$5"; shift 5
EXTRA=("$@")           # e.g. --verify, or --detlog-stack --detlog-heap

ROOT=/home/newton/work/dev-hermit
HERMIT="${HERMIT_BIN:?HERMIT_BIN must be set}"
LU=$ROOT/ignored/lu-parity/usr/lib64
E9DIR=$ROOT/reverie/third-party/e9patch

mkdir -p "$OUTDIR"
OUTDIR="$(realpath "$OUTDIR")"   # we cd below, so every output path must be absolute
# Each run gets its own CWD: the guests create + unlink temp files, and two concurrent
# runs sharing a directory would collide on those names.
WORK="$OUTDIR/cwd-$TAG"; rm -rf "$WORK"; mkdir -p "$WORK"
GUEST="$(realpath -m "$GUEST")"   # e9patch rejects any path containing a '..' segment

cd "$WORK" || exit 1
timeout "${CELL_TIMEOUT:-300}" env -i \
  PATH=/usr/bin:/bin \
  HOME=$ROOT/ignored/fileio-residue/fakehome \
  LC_ALL=C LANG=C TZ=UTC TERM=dumb SHELL=/bin/sh USER=newton LOGNAME=newton \
  LD_LIBRARY_PATH="$LU" \
  HERMIT_E9TOOL="$E9DIR/e9tool" \
  "$HERMIT" \
    --log info \
    --log-file "$OUTDIR/$TAG.log" \
    run \
      --backend "$BACKEND" \
      --strict \
      "${EXTRA[@]}" \
      -- "$GUEST" "$MODE" \
  > "$OUTDIR/$TAG.out" 2> "$OUTDIR/$TAG.err"
rc=$?
echo "$rc" > "$OUTDIR/$TAG.rc"
exit 0
