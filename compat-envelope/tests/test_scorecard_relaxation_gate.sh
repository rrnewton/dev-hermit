#!/usr/bin/env bash
# Bracket the strict determinism authority against every supported relaxation.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
envelope="$(dirname "$here")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
failures=0

write_fixture() { # output, JSON relaxation set
  python3 - "$envelope/scorecard.csv" "$1" "$2" <<'PY'
import csv, sys
source, output, relaxation_set = sys.argv[1:]
fieldnames = next(csv.reader(open(source)))
if "relaxation_set" not in fieldnames:
    fieldnames.insert(fieldnames.index("profile_flags") + 1, "relaxation_set")
row = {name: "" for name in fieldnames}
row.update(
    run_id="relaxation-gate-fixture",
    test_id="strict-positive-control",
    test_mode="verify",
    backend="ptrace",
    outcome="pass",
    deterministic="1",
    verify_compare="stripped",
    tier="stripped-uncounted",
    relaxation_set=relaxation_set,
)
with open(output, "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerow(row)
PY
}

check() {
  if "$@"; then
    printf '  ok    %s\n' "$label"
  else
    printf '  FAIL  %s\n' "$label"
    failures=$((failures + 1))
  fi
}

write_fixture "$tmp/strict.csv" '[]'
label="strict positive is accepted (gate is not refusing everything)"
check "$envelope/check-determinism-earned.sh" "$tmp/strict.csv"

# Run the exact consumer once for each determinism relaxation accepted by the
# Hermit CLI. Unknown future values are also refused because the authority is
# emptiness of the recorded set, not this test list.
relaxations=(
  no-sequentialize-threads
  no-deterministic-io
  no-namespace
  no-rcb-time
  no-virtualize-time
  no-virtualize-cpuid
  no-virtualize-metadata
  strace-only
  max-timeslice=disabled
)
for relaxation in "${relaxations[@]}"; do
  json="$(python3 -c 'import json,sys; print(json.dumps([sys.argv[1]], separators=(",", ":")))' "$relaxation")"
  file="$tmp/${relaxation//\//_}.csv"
  write_fixture "$file" "$json"
  label="deterministic=1 under $relaxation is REFUSED"
  if "$envelope/check-determinism-earned.sh" "$file" >/dev/null 2>&1; then
    printf '  FAIL  %s\n' "$label"
    failures=$((failures + 1))
  else
    rc=$?
    if [ "$rc" -eq 1 ]; then printf '  ok    %s\n' "$label"; else
      printf '  FAIL  %s (rc=%s, wanted 1)\n' "$label" "$rc"
      failures=$((failures + 1))
    fi
  fi
done

write_fixture "$tmp/blank.csv" ''
label="blank relaxation binding is REFUSED"
if "$envelope/check-determinism-earned.sh" "$tmp/blank.csv" >/dev/null 2>&1; then
  printf '  FAIL  %s\n' "$label"; failures=$((failures + 1))
else
  [ "$?" -eq 1 ] && printf '  ok    %s\n' "$label" || failures=$((failures + 1))
fi

printf '\n'
if [ "$failures" -ne 0 ]; then
  printf 'FAIL (%s assertions)\n' "$failures"
  exit 1
fi
printf 'PASS (strict positive + %s relaxed negatives + missing-binding negative)\n' "${#relaxations[@]}"
