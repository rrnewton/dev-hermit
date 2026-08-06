#!/usr/bin/env bash
# One fork/exec process-tree cell: ONE guest under ONE backend, PINNED environment.
#
# WHY env -i WITH A FIXED SET: the kernel writes envp into the guest's INITIAL
# STACK, so any host variable that differs between two runs changes --detlog-stack
# and manufactures a false gap. Every variable below is identical for every
# backend -- including HERMIT_E9TOOL, exported even for backends that ignore it,
# precisely so envp does not vary by backend. (Same discipline as
# ignored/detlog-parity/run-cell.sh, whose positive control validated it.)
set -u

BACKEND="$1"; OUTDIR="$2"; TAG="$3"; shift 3
CMD=("$@")

HERMIT="${HERMIT_BIN:?HERMIT_BIN must be set}"
LU=/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64
E9DIR=/home/newton/work/dev-hermit/reverie/third-party/e9patch

mkdir -p "$OUTDIR"

# e9patch refuses any guest path containing a '..' segment; canonicalize the
# argv[0] for ALL backends so the guest argv (and thus the stack) is identical
# across the row.
CMD[0]="$(realpath -m "${CMD[0]}")"

timeout "${CELL_TIMEOUT:-240}" env -i \
  PATH=/usr/bin:/bin \
  HOME=/home/newton/work/dev-hermit/ignored/fork-exec-parity/fakehome \
  LC_ALL=C LANG=C TZ=UTC TERM=dumb SHELL=/bin/sh USER=newton LOGNAME=newton \
  LD_LIBRARY_PATH="$LU" \
  HERMIT_E9TOOL="$E9DIR/e9tool" \
  "$HERMIT" \
    --log info \
    --log-file "$OUTDIR/$TAG.log" \
    run \
      --backend "$BACKEND" \
      --strict \
      --detlog-stack \
      --detlog-heap \
      ${EXTRA_RUN_FLAGS:-} \
      -- "${CMD[@]}" \
  > "$OUTDIR/$TAG.out" 2> "$OUTDIR/$TAG.err"
rc=$?
echo "$rc" > "$OUTDIR/$TAG.rc"
exit 0
