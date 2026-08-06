#!/usr/bin/env bash
# One --verify cell: hermit's OWN internal double-run comparison.
#
# DEFAULT IS PLAIN --verify (Stripped comparator). --verify-strict is added only
# when VERIFY_STRICT is set, because on this host at hermit f89c69766 it is
# INERT: it returns bitwise_parity:false for /bin/true and /bin/echo as well as
# for every fork guest, so it cannot discriminate. Two host-state leaks sit
# inside its compared envelope -- a DEBUG reverie_ptrace::timer CpuId dump whose
# initial_local_apic_id names the host core, and
# `DEBUG detcore::tool_global: Nondeterministic realtime elapsed: <N>ms`
# (detcore/src/tool_global.rs:541), a host wall clock. Both are DEBUG lines,
# though AGENTS.md defines BitwiseInfoV1 over INFO events; verify.rs:557 forces
# LevelFilter::DEBUG for verification paths. Bracketed on /bin/true => an
# instrument defect, not a fork/exec result.
#
# This is a DIFFERENT instrument from the external double-run in sweep.sh:
#   external double-run  = two independent hermit PROCESSES, compared by me
#   --verify             = one hermit process running the guest twice, compared
#                          by hermit under its BitwiseInfoV1 policy
# Both are required by the task's "strict + --verify + DOUBLE-RUN" standard, and
# they can disagree (e.g. --verify aborts if run 1 exits via a signal).
set -u

BACKEND="$1"; OUTDIR="$2"; TAG="$3"; shift 3
CMD=("$@")

HERMIT="${HERMIT_BIN:?HERMIT_BIN must be set}"
LU=/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64
E9DIR=/home/newton/work/dev-hermit/reverie/third-party/e9patch

mkdir -p "$OUTDIR"
CMD[0]="$(realpath -m "${CMD[0]}")"

timeout "${CELL_TIMEOUT:-360}" env -i \
  PATH=/usr/bin:/bin \
  HOME=/home/newton/work/dev-hermit/ignored/fork-exec-parity/fakehome \
  LC_ALL=C LANG=C TZ=UTC TERM=dumb SHELL=/bin/sh USER=newton LOGNAME=newton \
  LD_LIBRARY_PATH="$LU" \
  HERMIT_E9TOOL="$E9DIR/e9tool" \
  "$HERMIT" run \
      --backend "$BACKEND" \
      --strict \
      --verify \
      ${VERIFY_STRICT:+--verify-strict} \
      --verify-json "$OUTDIR/$TAG.verify.json" \
      --detlog-stack \
      --detlog-heap \
      -- "${CMD[@]}" \
  > "$OUTDIR/$TAG.vout" 2> "$OUTDIR/$TAG.verr"
rc=$?
echo "$rc" > "$OUTDIR/$TAG.vrc"
exit 0
