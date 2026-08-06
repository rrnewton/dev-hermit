#!/usr/bin/env bash
# Full residue matrix. For each (mode, backend):
#
#   VERIFY pass  -- VER runs of `--strict --verify`. This is hermit's OWN double-run
#                   check (Stripped comparator). Recorded as its verdict, not as proof:
#                   Stripped tolerates changed numeric literals, which is precisely how
#                   the parent sweep's FileContents(inode) defect slipped past it.
#   DETLOG pass  -- REP runs of `--strict --detlog-stack --detlog-heap`, compared with
#                   normparity.py in CONTENT mode (--keep-hash-values) for run-to-run
#                   self-determinism, and in STRUCTURAL mode across backends (which
#                   ordinalizes addresses so a pure relocation is tolerated).
#
# The known confound is handled explicitly rather than normalized away: the residual
# column re-runs the content comparison with FileContents(<inode>) masked, so a cell can
# be reported as "diverges ONLY on the already-filed raw-inode defect" versus "diverges
# for some further reason". Masking is used to ATTRIBUTE, never to declare a pass.
set -u
ROOT=/home/newton/work/dev-hermit
BASE=$ROOT/ignored/fileio-residue
export HERMIT_BIN="${HERMIT_BIN:-$BASE/bin/hermit}"
OUT="${OUT:-$BASE/data/sweep}"
REP="${REP:-5}"          # detlog runs per (mode,backend)
VER="${VER:-3}"          # --verify runs per (mode,backend)
JOBS="${JOBS:-6}"
BACKENDS="${BACKENDS:-ptrace e9patch}"
MODES="${MODES:?set MODES}"

mkdir -p "$OUT"

run_all() {   # mode backend -> all runs for one cell
  local m="$1" b="$2"
  # NOTE: d= must be its own statement. `local m="$1" d="$OUT/$m"` expands $m BEFORE
  # the builtin assigns it, so d silently became "$OUT//" and every cell overwrote
  # the same files.
  local d="$OUT/$m/$b"
  rm -rf "$d"; mkdir -p "$d"
  local i
  for i in $(seq 1 "$VER"); do
    CELL_TIMEOUT=45 "$BASE/run-cell.sh" "$b" "$BASE/guests/shortvec" "$d" "v$i" "$m" --verify
  done
  for i in $(seq 1 "$REP"); do
    CELL_TIMEOUT=45 "$BASE/run-cell.sh" "$b" "$BASE/guests/shortvec" "$d" "r$i" "$m" \
      --detlog-stack --detlog-heap
  done
}
export -f run_all
export BASE OUT REP VER HERMIT_BIN

for m in $MODES; do for b in $BACKENDS; do echo "$m $b"; done; done \
  | xargs -P "$JOBS" -n2 bash -c 'run_all "$0" "$1"'
echo "runs complete"
