#!/bin/bash
# Generate the ptrace PARITY reference (agent hermit-235).
# The original sweep.sh wrote $cell/ptv.out with `hermit run --strict --verify`,
# but --verify performs an internal double-run and does NOT emit the guest's
# stdout to the parent -> every ptv.out is 0 bytes, so backend parity (compare
# backend stdout to that empty file) is spuriously ~0. The backend .out files
# (sweep-backend.sh) are produced with PLAIN `--strict` (real guest stdout), so
# the reference must match: plain ptrace `--strict` -> $cell/ptref235.out.
# Same lane flags, same guests, same argv as the backend sweep (apples-to-apples).
# Writes ONLY ptref235.out (never touches ptv.out or others). Research/data only.
set -u
EXP=/home/newton/work/dev-hermit/experiments/ptrace_fullcorpus_scorecard_20260801
KVMEXP=/home/newton/work/dev-hermit/experiments/kvm_fullcorpus_scorecard_20260801
HROOT=/home/newton/work/dev-hermit/hermit
BIN=${HERMIT_BIN:?set HERMIT_BIN to the release hermit (featured or plain, ptrace path is identical)}
BUILD=$HROOT/target/kvm-fullcorpus
TMO_RUN=${TMO_RUN:-90}
PAR=${PAR:-16}
CORPUS_C=${CORPUS_C:-$KVMEXP/corpus.tsv}
CORPUS_NONC=${CORPUS_NONC:-$KVMEXP/corpus-nonc.tsv}

genref() { # $1=cell dir  $2=lane ; rest=guest argv
  local cell="$1" lane="$2"; shift 2
  local -a gcmd=("$@")
  local flags=""
  [ "$lane" = portable ] && flags="--no-virtualize-cpuid --max-timeslice=disabled"
  export LC_ALL=C TZ=UTC
  timeout "$TMO_RUN" "$BIN" run --strict $flags -- "${gcmd[@]}" >"$cell/ptref235.out" 2>"$cell/ptref235.err"
  local re=$?
  # Mark a failed reference empty+sentinel so parity treats it as unmeasured, not a match.
  if [ "$re" != 0 ]; then : >"$cell/ptref235.out"; echo "exit=$re" >"$cell/ptref235.fail"; else rm -f "$cell/ptref235.fail"; fi
}
export -f genref
export HROOT BIN BUILD TMO_RUN

echo "=== C cells ptrace plain --strict -> ptref235.out, PAR=$PAR ==="
xargs -a "$CORPUS_C" -d '\n' -P "$PAR" -I{} bash -c '
  IFS="|" read -r id prog cflags extra lane cstate <<<"$1"
  key="${id//\//_}"; cell="'"$BUILD"'/$key"
  [ -x "$cell/guest" ] || exit 0
  genref "$cell" "$lane" "$cell/guest"
' _ {}

echo "=== non-C cells ptrace plain --strict -> ptref235.out, PAR=$PAR ==="
xargs -a "$CORPUS_NONC" -d '\n' -P "$PAR" -I{} bash -c '
  line="$1"; case "$line" in \#*) exit 0;; esac
  id="${line%%|*}"; rest="${line#*|}"; lane="${rest%%|*}"; cmd="${rest#*|}"
  cmd="${cmd//HERMITROOT/'"$HROOT"'}"
  key="${id//\//_}"; cell="'"$BUILD"'/nonc_$key"; mkdir -p "$cell"
  # shellcheck disable=SC2086
  genref "$cell" "$lane" $cmd
' _ {}

echo "=== REF GEN DONE ==="
echo "ptref235.out nonempty: $(find "$BUILD" -name ptref235.out -size +0c | wc -l)"
echo "ptref235.out empty:    $(find "$BUILD" -name ptref235.out -size 0 | wc -l)"
echo "ref failures:          $(find "$BUILD" -name ptref235.fail | wc -l)"
