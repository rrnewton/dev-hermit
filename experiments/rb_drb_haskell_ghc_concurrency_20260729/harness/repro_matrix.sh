#!/bin/bash
# Reproducibility matrix for GHC parallel-build determinism under hermit.
# For a given MODE (native|hermit) and -jN, build the generated package NRUNS
# times to a FIXED canonical path (/work/out) and hash every .hi/ABI/.o/binary
# into a per-run manifest. Hashing runs OUTSIDE the hermit boundary (plain
# container shell reading the persistent /work bind mount), so the fingerprints
# are independent of hermit. Prints each run's aggregate SHA and a verdict.
#
# Usage: repro_matrix.sh <native|hermit> <jN> <nruns>
# Env:   HERMIT_BIN must point at the hermit binary when MODE=hermit.
set -euo pipefail
MODE="$1"; JN="$2"; NRUNS="${3:-3}"
SRC=/work/pkg/src            # canonical, identical every run
OUT=/work/out               # canonical output path (kills GHC flag-hash confound)
MANDIR=/work/manifests
mkdir -p "$MANDIR"
declare -a AGGS

for run in $(seq 1 "$NRUNS"); do
  rm -rf "$OUT"; mkdir -p "$OUT/build"
  cd "$SRC"
  if [ "$MODE" = hermit ]; then
    /work/hermit.sh run ${HERMIT_FLAGS:-} -- ghc --make -j"$JN" -O0 \
      -outputdir "$OUT/build" -o "$OUT/main" Main.hs > "$OUT/ghc.log" 2>&1 || {
        echo "run $run BUILD-FAILED"; tail -20 "$OUT/ghc.log"; exit 1; }
  elif [ "$MODE" = native ]; then
    ghc --make -j"$JN" -O0 \
      -outputdir "$OUT/build" -o "$OUT/main" Main.hs > "$OUT/ghc.log" 2>&1 || {
        echo "run $run BUILD-FAILED"; tail -20 "$OUT/ghc.log"; exit 1; }
  else
    echo "bad MODE: $MODE"; exit 2
  fi
  # Hash outputs OUTSIDE hermit (this shell), reading the persistent bind mount.
  MAN="$MANDIR/${MODE}-j${JN}-run${run}.manifest"
  {
    echo "== .hi interface files =="
    find "$OUT/build" -name '*.hi' | sort | while read -r f; do
      printf "%s  %s\n" "$(sha256sum <"$f" | cut -d' ' -f1)" "$(basename "$f")"
    done
    echo "== ABI hashes (ghc --show-iface) =="
    find "$OUT/build" -name '*.hi' | sort | while read -r f; do
      abi=$(ghc --show-iface "$f" 2>/dev/null | awk -F': ' '/ABI hash:/{print $2; exit}')
      printf "%s  %s\n" "$abi" "$(basename "$f")"
    done
    echo "== .o object files =="
    find "$OUT/build" -name '*.o' | sort | while read -r f; do
      printf "%s  %s\n" "$(sha256sum <"$f" | cut -d' ' -f1)" "$(basename "$f")"
    done
    echo "== final binary =="
    printf "%s  main\n" "$(sha256sum <"$OUT/main" | cut -d' ' -f1)"
  } > "$MAN"
  AGG=$(sha256sum <"$MAN" | cut -d' ' -f1)
  AGGS+=("$AGG")
  echo "run $run  AGG=$AGG  ($MAN)"
done

# Verdict
first="${AGGS[0]}"; allsame=1
for a in "${AGGS[@]}"; do [ "$a" = "$first" ] || allsame=0; done
if [ "$allsame" = 1 ]; then
  echo "VERDICT[$MODE -j$JN x$NRUNS]: REPRODUCIBLE (all $NRUNS aggregate hashes identical)"
else
  echo "VERDICT[$MODE -j$JN x$NRUNS]: NON-REPRODUCIBLE (aggregate hashes differ)"
fi
