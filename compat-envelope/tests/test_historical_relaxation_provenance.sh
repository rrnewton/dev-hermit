#!/usr/bin/env bash
# Exact historical provenance: known rows recover exact sets; unknown stays unknown.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
envelope="$(dirname "$here")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

for name in scorecard.csv fullcorpus-scorecard.csv e9patch-scorecard.csv reverie-scorecard.csv; do
  mkdir -p "$tmp/$name.d"
  cp "$envelope/$name" "$tmp/$name.d/$name"
  cp "$envelope/$name" "$tmp/$name.d/$name.before"
done

python3 "$envelope/backfill-scorecard-relaxations.py" \
  "$tmp/scorecard.csv.d/scorecard.csv" \
  "$tmp/fullcorpus-scorecard.csv.d/fullcorpus-scorecard.csv" \
  "$tmp/e9patch-scorecard.csv.d/e9patch-scorecard.csv" \
  "$tmp/reverie-scorecard.csv.d/reverie-scorecard.csv" --apply

python3 - "$tmp" <<'PY'
import collections, csv, json, sys
from pathlib import Path

root = Path(sys.argv[1])
expected = {
    "scorecard.csv": {
        (): 3,
        ("no-virtualize-cpuid", "max-timeslice=disabled"): 443,
        ("no-virtualize-cpuid", "max-timeslice=disabled", "tmp=/tmp"): 65,
        ("max-timeslice=disabled", "tmp=/tmp"): 107,
    },
    "fullcorpus-scorecard.csv": {
        (): 6,
        ("no-virtualize-cpuid", "max-timeslice=disabled"): 1194,
    },
    "e9patch-scorecard.csv": {("tmp=/tmp",): 454},
    "reverie-scorecard.csv": {(): 12},
}
remaining_positive = {
    "scorecard.csv": 2,
    "fullcorpus-scorecard.csv": 5,
    "e9patch-scorecard.csv": 0,
    "reverie-scorecard.csv": 12,
}
dequalified = {
    "scorecard.csv": 0,
    "fullcorpus-scorecard.csv": 0,
    "e9patch-scorecard.csv": 0,
    "reverie-scorecard.csv": 0,
}
for name, wanted in expected.items():
    path = root / f"{name}.d" / name
    before = list(csv.DictReader((root / f"{name}.d" / f"{name}.before").open()))
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == len(before), (name, len(before), len(rows))
    assert rows and "relaxation_set" in rows[0], name
    got = collections.Counter(tuple(json.loads(row["relaxation_set"])) for row in rows)
    assert got == wanted, (name, got, wanted)
    assert all(row["relaxation_set"] for row in rows), name
    assert got[("UNKNOWN-RELAXATION",)] == 0, name
    positives = sum(row.get("deterministic") == "1" for row in rows)
    assert positives == remaining_positive[name], (name, positives)
    for line, (old, new) in enumerate(zip(before, rows), start=2):
        for column, value in old.items():
            if column == "deterministic" and value == "1" and new[column] == "":
                dequalified[name] += 1
                continue
            assert new[column] == value, (name, line, column, value, new[column])
print("PASS exact historical sets: known=2284/2284 unknown=0/2284")
assert dequalified == {
    "scorecard.csv": 0,
    "fullcorpus-scorecard.csv": 0,
    "e9patch-scorecard.csv": 0,
    "reverie-scorecard.csv": 0,
}, dequalified
print("PASS existing migrated rows are idempotent: preserved=2284/2284")
PY

# Plant a well-shaped row whose run identity is not in the immutable provenance
# allowlist. It must remain visibly unknown and must lose a positive verdict.
python3 - "$tmp/scorecard.csv.d/scorecard.csv" "$tmp/unknown.d/scorecard.csv" <<'PY'
import csv, sys
from pathlib import Path
source, output = map(Path, sys.argv[1:])
output.parent.mkdir()
header = next(csv.reader(source.open()))
row = {column: "" for column in header}
row.update(run_id="unrecorded-run", hermit_sha="f" * 40, run_mode="regression",
           lane="portable", test_id="fixture/unknown", test_mode="verify",
           backend="ptrace", outcome="pass", deterministic="1")
row.pop("relaxation_set", None)
header = [column for column in header if column != "relaxation_set"]
with output.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=header)
    writer.writeheader(); writer.writerow(row)
PY
python3 "$envelope/backfill-scorecard-relaxations.py" "$tmp/unknown.d/scorecard.csv" --apply
python3 - "$tmp/unknown.d/scorecard.csv" <<'PY'
import csv, json, sys
row = next(csv.DictReader(open(sys.argv[1])))
assert json.loads(row["relaxation_set"]) == ["UNKNOWN-RELAXATION"]
assert row["deterministic"] == ""
print("PASS unknown provenance stays UNKNOWN: 1/1; positive dequalified: 1/1")
PY

# Idempotence: a second application must be byte-identical.
before="$(sha256sum "$tmp/scorecard.csv.d/scorecard.csv" | cut -d' ' -f1)"
python3 "$envelope/backfill-scorecard-relaxations.py" \
  "$tmp/scorecard.csv.d/scorecard.csv" --apply >/dev/null
after="$(sha256sum "$tmp/scorecard.csv.d/scorecard.csv" | cut -d' ' -f1)"
test "$before" = "$after"
echo "PASS idempotent: 1/1"
