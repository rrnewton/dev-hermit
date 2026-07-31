#!/bin/bash
# Consolidated authoritative reproducibility matrix -> CSV.
# Each row: mode, ghc_flags, run, aggregate_sha256 ; plus a verdict line to stderr.
# Aggregate = sha256 over (all .o sorted by name) + final binary, hashed OUTSIDE
# the hermit boundary from the persistent /work bind mount.
set -uo pipefail
SRC=/work/pkg/src; OUT=/work/out
CSV=/work/results.csv
echo "mode,ghc_rts_flags,jN,run,aggregate_sha256" > "$CSV"
row() { # mode label(rtsflags...) ; uses global JN=8
  local mode="$1"; shift
  local flagdesc="$1"; shift    # human label for CSV (e.g. default / -C0 / -N8)
  local -a extra=("$@")         # actual +RTS ... -RTS args
  local -a aggs
  for r in 1 2 3; do
    rm -rf "$OUT"; mkdir -p "$OUT/build"; cd "$SRC" || exit 2
    if [ "$mode" = hermit ]; then P=(/work/hermit.sh run --strict --); else P=(); fi
    "${P[@]}" ghc --make -j8 -O0 "${extra[@]}" -outputdir "$OUT/build" -o "$OUT/main" Main.hs \
        >"$OUT/ghc.log" 2>&1 || { echo "$mode/$flagdesc run$r FAILED" >&2; tail -5 "$OUT/ghc.log" >&2; return; }
    local a
    a=$( { find "$OUT/build" -name '*.o' | sort | while read -r f; do sha256sum <"$f"; done; sha256sum <"$OUT/main"; } | sha256sum | cut -d' ' -f1)
    aggs+=("$a")
    echo "$mode,$flagdesc,8,$r,$a" >> "$CSV"
  done
  local uniq; uniq=$(printf '%s\n' "${aggs[@]}" | sort -u | wc -l)
  local verdict; [ "$uniq" = 1 ] && verdict=REPRODUCIBLE || verdict=NON-REPRODUCIBLE
  printf '%-8s %-10s x3 -> %-16s (%d distinct)\n' "$mode" "$flagdesc" "$verdict" "$uniq" >&2
}
row native default
row native -C0     +RTS -C0 -RTS
row native -N8     +RTS -N8 -RTS
row hermit default
row hermit -C0     +RTS -C0 -RTS
row hermit -N8     +RTS -N8 -RTS
echo "CSV written: $CSV" >&2
