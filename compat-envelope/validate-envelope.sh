#!/usr/bin/env bash
# validate-envelope.sh — the compat-envelope REGRESSION gate.
#
# Runs the known-green cells for both the hermit Detcore envelope and the
# reverie B1.5 Guest/Tool boundary, asserts they stayed green, and (as a side
# effect) rewrites the scorecard CSVs recording exactly what was green this run.
#
# This is what the outer-repo `validate` entry point and CI call. It is
# deliberately fast + safe (regression mode only, no expansion sweep). The
# expansion sweep (full superset, bounded by safe-ci-dag-runner) is separate;
# see expansion-dag.rs.
#
# Exit non-zero if ANY enabled cell stopped passing (green-stays-green).
#
# Usage:
#   validate-envelope.sh [--lane LANE] [--repo HERMIT_CHECKOUT] [--no-reverie]
#                        [--backends b1,b2,...]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lane="portable"
repo="${here}/../hermit"
backends="ptrace,dbi,sabre"
do_reverie=1

while [ $# -gt 0 ]; do
  case "$1" in
    --lane) lane="$2"; shift 2 ;;
    --repo) repo="$2"; shift 2 ;;
    --backends) backends="$2"; shift 2 ;;
    --no-reverie) do_reverie=0; shift ;;
    -h|--help)
      grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "validate-envelope: unknown arg $1" >&2; exit 2 ;;
  esac
done

fail=0

echo "== compat-envelope regression: hermit (lane=${lane}, backends=${backends}) =="
if ! "${here}/collect-envelope.rs" --mode regression --lane "${lane}" \
      --repo "${repo}" --backends "${backends}" --with-parity \
      --csv "${here}/scorecard.csv" --assert-green; then
  echo "validate-envelope: HERMIT envelope regression detected" >&2
  fail=1
fi

if [ "${do_reverie}" -eq 1 ]; then
  echo "== compat-envelope regression: reverie B1.5 (ptrace vs kvm) =="
  # KVM parity is measurable only where /dev/kvm exists; on a portable lane it
  # records not-runnable cells honestly and still asserts ptrace determinism.
  if ! "${here}/collect-reverie-compat.rs" --repo "${repo}" \
        --csv "${here}/reverie-scorecard.csv" --assert-green; then
    echo "validate-envelope: REVERIE envelope regression detected" >&2
    fail=1
  fi
fi

# EVERY determinism claim must be EARNED, and must say what earned it.
#
# This gate had no production call site: it existed, it worked, and nothing ran
# it, so a row could claim `deterministic=1` (or `tier=bitwise` on a stripped
# comparison, or with 0|0 counts) and no pipeline would object. An unwired
# verifier is a comment. It runs on the CSVs the collectors just wrote, so it
# gates the freshly produced evidence rather than a stale snapshot.
echo "== compat-envelope: determinism claims must be earned =="
if ! "${here}/check-determinism-earned.sh" "${here}/scorecard.csv"; then
  echo "validate-envelope: UNEARNED or OVER-TIERED determinism claims in scorecard.csv" >&2
  fail=1
fi

# A parity boolean is accepted only when the row carries both hashed operands,
# exact code state, comparison/profile identity, and counted run coverage.  This
# is the same semantic verifier the renderer calls; labels and cached booleans
# are not a second authority.
echo "== compat-envelope: parity claims must carry full provenance =="
if ! "${here}/check-scorecard-provenance.py" "${here}/scorecard.csv"; then
  echo "validate-envelope: UNBOUND parity claims in scorecard.csv" >&2
  fail=1
fi
if [ "${do_reverie}" -eq 1 ] && [ -f "${here}/reverie-scorecard.csv" ]; then
  if ! "${here}/check-scorecard-provenance.py" "${here}/reverie-scorecard.csv" \
        --observable tool-count; then
    echo "validate-envelope: UNBOUND parity claims in reverie-scorecard.csv" >&2
    fail=1
  fi
fi
if [ "${do_reverie}" -eq 1 ] && [ -f "${here}/reverie-scorecard.csv" ]; then
  if ! "${here}/check-determinism-earned.sh" "${here}/reverie-scorecard.csv"; then
    echo "validate-envelope: UNEARNED or OVER-TIERED determinism claims in reverie-scorecard.csv" >&2
    fail=1
  fi
fi

if [ "${fail}" -ne 0 ]; then
  echo "validate-envelope: FAILED — see regressions above" >&2
  exit 1
fi

echo "== compat-envelope: ALL enabled cells green; CSVs refreshed =="
echo "   ${here}/scorecard.csv"
[ "${do_reverie}" -eq 1 ] && echo "   ${here}/reverie-scorecard.csv"
"${here}/render-scorecard.rs" --csv "${here}/scorecard.csv" --latest || true
