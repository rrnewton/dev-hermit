#!/bin/bash
# Recompute the parity column of scorecard-{dbi,sabre,e9patch}.csv against the
# CORRECTED ptrace reference ptref235.out (plain --strict, real guest stdout),
# replacing the spurious parity computed against the empty ptv.out. (agent hermit-235)
#
#   parity=1  ref valid (no .fail) AND sha256(backend.out) == sha256(ptref235.out)
#             [non-empty match is unambiguous; empty==empty guarded to clean rows]
#   parity=0  ref valid AND hashes differ (incl. failed backend run -> different/empty out)
#   parity="" ref invalid (ptref235.fail) -> unmeasured denominator cell
#
# Reads cell files directly; maps test_id->cell via the corpus manifests.
set -u
EXP=/home/newton/work/dev-hermit/experiments/ptrace_fullcorpus_scorecard_20260801
KVMEXP=/home/newton/work/dev-hermit/experiments/kvm_fullcorpus_scorecard_20260801
BUILD=/home/newton/work/dev-hermit/hermit/target/kvm-fullcorpus
CORPUS_C=$KVMEXP/corpus.tsv
CORPUS_NONC=$KVMEXP/corpus-nonc.tsv
EMPTY=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
MAP=$(mktemp)   # test_id \t cell_dir

# Build test_id -> cell map from the two manifests (same keys the sweeps use).
while IFS='|' read -r id prog cflags extra lane cstate; do
  [ -z "$id" ] && continue
  key="${id//\//_}"; printf '%s\t%s\n' "$id" "$BUILD/$key" >>"$MAP"
done <"$CORPUS_C"
while IFS= read -r line; do
  case "$line" in \#*|'') continue;; esac
  id="${line%%|*}"; key="${id//\//_}"; printf '%s\t%s\n' "$id" "$BUILD/nonc_$key" >>"$MAP"
done <"$CORPUS_NONC"

refhash() { # $1=cell -> prints hash, or UNMEASURED if ref failed/missing
  local cell="$1"
  [ -f "$cell/ptref235.fail" ] && { echo UNMEASURED; return; }
  [ -f "$cell/ptref235.out" ] || { echo UNMEASURED; return; }
  sha256sum "$cell/ptref235.out" | cut -c1-64
}
outhash() { # $1=cell $2=backend -> hash of backend .out (or MISSING)
  local f="$1/$2.out"; [ -f "$f" ] || { echo MISSING; return; }
  sha256sum "$f" | cut -c1-64
}

for BACKEND in dbi sabre e9patch; do
  CSV="$EXP/scorecard-$BACKEND.csv"
  [ -f "$CSV" ] || { echo "skip $BACKEND (no CSV)"; continue; }
  TMP=$(mktemp)
  head -1 "$CSV" >"$TMP"
  measured=0 par=0 unmeas=0
  tail -n +2 "$CSV" | while IFS= read -r row; do
    IFS=, read -r -a f <<<"$row"
    id="${f[8]}"                                   # test_id column (0-idx 8)
    cell=$(awk -F'\t' -v k="$id" '$1==k{print $2; exit}' "$MAP")
    newp=""
    if [ -n "$cell" ]; then
      rh=$(refhash "$cell"); oh=$(outhash "$cell" "$BACKEND")
      if [ "$rh" = UNMEASURED ]; then newp=""
      elif [ "$oh" = MISSING ]; then newp=0
      elif [ "$rh" = "$EMPTY" ]; then
        # both empty -> only credit clean rows (empty reason) to avoid failed-run false match
        if [ "$oh" = "$EMPTY" ] && [ -z "${f[18]:-}" ]; then newp=1; else newp=0; fi
      elif [ "$rh" = "$oh" ]; then newp=1
      else newp=0; fi
    fi
    f[14]="$newp"                                  # parity column (0-idx 14)
    while [ "${#f[@]}" -lt 19 ]; do f+=(""); done  # restore trailing empties dropped by read -a
    ( IFS=,; echo "${f[*]}" ) >>"$TMP"
  done
  mv "$TMP" "$CSV"
  awk -F, 'NR>1{t++; if($15=="1")p++; if($15=="")u++; if($14=="1")d++}
    END{printf "%-8s cells=%d det=%d parity=%d unmeasured=%d measured=%d\n",B,t,d,p,u,t-u}' B=$BACKEND "$CSV"
done
rm -f "$MAP"
