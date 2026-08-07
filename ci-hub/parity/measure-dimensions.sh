#!/usr/bin/env bash
# measure-dimensions.sh — run each parity dimension on ITS reference guest and
# emit cells through the refusal gate.
#
# Every cell this prints names the guest that produced it. A dimension can only
# be measured on the guest pinned for it in reference-guests.json; anything else
# is refused by reference_guest.emit() before a number is ever printed.
#
# GUESTS MUST NOT LIVE UNDER /tmp. Hermit replaces the guest /tmp with an
# isolated directory and refuses with an explicit error, which reads like a
# broken build and is not one. Built under $OUT instead.
set -uo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HERE/../.." && pwd)
H=${HERMIT:-$ROOT/ignored/prefix-build/target/release/hermit}
OUT=${OUT:-$ROOT/scratch/refguest-dimensions}
BACKEND=${BACKEND:-ptrace}
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-$ROOT/ignored/lu-parity/usr/lib64}"
mkdir -p "$OUT/bin"

if [ ! -x "$H" ]; then echo "no hermit at $H (set HERMIT=)" >&2; exit 2; fi

# EMIT THE PROVENANCE OF WHAT WE READ, before any number is printed.
#
# On 2026-08-07 a hermit binary 23 commits behind main produced three FALSE
# nondeterminism verdicts against the golden reference and a 60x-inflated cost.
# The binary was internally consistent and gave no signal it was stale -- a
# surprising result from a stale artifact looks identical to a discovery. So the
# binary states its own distance from main here, beside the cells it produces.
python3 - "$H" "$ROOT/hermit" <<'PROV' >&2
import sys
sys.path.insert(0, "/home/newton/work/dev-hermit/ci-hub")
sys.path.insert(0, __import__("os").environ.get("CI_HUB_DIR", ""))
try:
    from provenance import binary_provenance
except ImportError:
    print("provenance: UNAVAILABLE (ci-hub/provenance.py not importable)"); raise SystemExit(0)
print(binary_provenance(sys.argv[1], sys.argv[2]).render())
PROV

# Build every pinned guest from the manifest, so "checked in" also means
# "re-runnable from source" rather than trusting a stale binary.
mapfile -t DIMS < <(python3 -c "
import json,sys
m=json.load(open('$HERE/reference-guests.json'))
for d,v in m['dimensions'].items(): print(d, v['source'])
")
for entry in "${DIMS[@]}"; do
  dim=${entry%% *}; src=${entry#* }
  gcc -O2 -static -o "$OUT/bin/$dim" "$ROOT/$src" || { echo "build failed: $src" >&2; exit 2; }
done

status=0
for entry in "${DIMS[@]}"; do
  dim=${entry%% *}; src=${entry#* }
  timeout 300 "$H" --log=info --log-file="$OUT/$dim.log" run --backend "$BACKEND" \
      --strict --detlog-heap --detlog-stack --base-env minimal \
      -- "$OUT/bin/$dim" >"$OUT/$dim.out" 2>"$OUT/$dim.err"
  rc=$?
  # The measured value per dimension. stdout/detlog are record counts; heap and
  # stack are their own hash counts -- each read from the run that just happened.
  case "$dim" in
    heap)   val=$(grep -c '\[heap\]->'  "$OUT/$dim.log") ;;
    stack)  val=$(grep -c '\[stack\]->' "$OUT/$dim.log") ;;
    stdout) val=$(wc -l < "$OUT/$dim.out") ;;
    *)      val=$(grep -c 'DETLOG' "$OUT/$dim.log") ;;
  esac
  # Emission is GATED: this refuses rather than printing an unattributed number.
  python3 - "$dim" "$BACKEND" "$val" "$ROOT/$src" "$OUT/$dim.out" "$OUT/$dim.log" "$rc" <<'PY' || status=1
import json, sys
sys.path.insert(0, __import__("os").path.dirname(__file__) or ".")
sys.path.insert(0, "/home/newton/work/dev-hermit/ci-hub/parity")
from reference_guest import emit, RefusedError
dim, backend, val, src, outp, logp, rc = sys.argv[1:8]
try:
    if rc != "0":
        raise RefusedError(f"guest exited rc={rc}; a failed run is not a cell")
    cell = emit(dim, backend, int(val), guest_source=src,
                guest_stdout=open(outp, errors="replace").read(),
                detlog_text=open(logp, errors="replace").read())
except RefusedError as e:
    print(f"REFUSED  {dim:<7} {backend:<8} {e}")
    sys.exit(1)
r = cell.as_row()
print(f"{r['dimension']:<7} {r['backend']:<8} value={r['value']:<6} "
      f"guest={r['guest'].split('/')[-1]:<26} sha={r['guest_sha256'][:12]} "
      f"witness={r['witness']}")
PY
done
exit "$status"
