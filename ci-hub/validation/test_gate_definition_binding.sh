#!/usr/bin/env bash
# INERT both-sided bracket of the merge gate's DEFINITION BINDING.
#
# The other half of the #231 fake-green pair: a PR carrying its OWN older,
# weaker `.github/workflows/merge-gate.yml` used to satisfy the CURRENT gate
# with its own check definition, so the gate weakened monotonically with PR age.
# The fix (hermit PR #1579) is the "Require the registered v4 gate definition"
# step in the authoritative merge-gate-v4 job: the blob of merge-gate.yml AT THE
# RUN'S OWN SHA must equal the registered `vars.MERGE_GATE_V4_BLOB`.
#
# That step is inline workflow YAML, so this bracket EXTRACTS its script and
# runs it against a STUBBED `gh` that reports a chosen blob. Nothing here dials
# GitHub, dispatches a workflow, or touches a PR: a gate-satisfying artifact is
# itself an authorization, so it is never planted on live state (#243).
#
#   NEGATIVE  a branch-owned (divergent) gate blob is REFUSED, and an
#             unconfigured/blank registration is REFUSED rather than waved
#             through -- an unbound gate must fail closed, not open.
#   POSITIVE  the registered blob is ACCEPTED, so the check is not refuse-all.
#
# Optional live corroboration (needs network, skipped by default): --live reads
# the registered variable and the blob of merge-gate.yml on origin/main and
# reports whether the deployed configuration is self-consistent. Reads only.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$script_dir/../.." && pwd)
gate_yaml=${GATE_YAML:-$root/hermit/.github/workflows/merge-gate.yml}
step_name='Require the registered v4 gate definition'
live=0
[ "${1:-}" = --live ] && live=1

if [ ! -r "$gate_yaml" ]; then
  echo "FAIL: cannot read the gate definition: $gate_yaml" >&2
  echo "      (set GATE_YAML, or initialise the hermit checkout)" >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

# Extract the step's `run:` block verbatim. Parsing the real workflow -- rather
# than restating the check here -- is the point: a restated copy would keep
# passing after the real step was weakened or deleted.
python3 - "$gate_yaml" "$step_name" >"$tmp/step.sh" <<'PY'
import sys
path, want = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()
start = None
for i, line in enumerate(lines):
    if line.strip() in (f"- name: {want}", f'- name: "{want}"'):
        start = i
        break
if start is None:
    sys.exit(f"step not found: {want}")
# Find `run: |` inside this step, then take its indented block.
run_at = None
for i in range(start + 1, len(lines)):
    s = lines[i].strip()
    if s.startswith("- name:"):
        break
    if s in ("run: |", "run: |-"):
        run_at = i
        break
if run_at is None:
    sys.exit(f"step has no run block: {want}")
indent = len(lines[run_at]) - len(lines[run_at].lstrip())
body = []
for line in lines[run_at + 1:]:
    if line.strip() and (len(line) - len(line.lstrip())) <= indent:
        break
    body.append(line[indent + 2:] if len(line) > indent + 2 else "")
print("\n".join(body))
PY

if [ ! -s "$tmp/step.sh" ]; then
  echo "FAIL: extracted an empty step body for '$step_name'" >&2
  exit 1
fi
# The extracted script must actually be the guard, not merely present.
for needle in EXPECTED_GATE_BLOB actual_blob 'exit 1'; do
  grep -Fq "$needle" "$tmp/step.sh" || {
    echo "FAIL: extracted step does not look like the definition guard (missing: $needle)" >&2
    exit 1
  }
done

REGISTERED=579f5e7816c7e2844eadfd7018d95ee37c8d8640   # a stand-in registration
BRANCH_OWNED=d4c276acaca8000000000000000000000000beef # a divergent branch blob

# Stub `gh` so the step's one API read returns a chosen blob. No network.
mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
# Only the contents?ref=<sha> --jq .sha read is exercised by this step.
printf '%s\n' "${STUB_BLOB:-}"
STUB
chmod +x "$tmp/bin/gh"

neg_refused=0; neg_total=0; pos_accepted=0; pos_total=0; fail=0

check() { # check <side> <expect_rc> <name> <registered> <actual>
  local side=$1 expect=$2 name=$3 registered=$4 actual=$5 rc=0 out status=OK
  out=$(PATH="$tmp/bin:$PATH" STUB_BLOB="$actual" \
        EXPECTED_GATE_BLOB="$registered" \
        GATE_PATH=.github/workflows/merge-gate.yml \
        GH_TOKEN=stub REPO=rrnewton/hermit SHA=$(printf 'a%.0s' {1..40}) \
        bash "$tmp/step.sh" 2>&1) || rc=$?
  [ "$rc" -eq "$expect" ] || status=BAD
  if [ "$side" = NEG ] && [ "$rc" -eq 0 ]; then status=BAD; fi
  if [ "$side" = POS ] && [ "$rc" -ne 0 ]; then status=BAD; fi
  if [ "$side" = NEG ]; then
    neg_total=$((neg_total + 1)); [ "$status" = OK ] && neg_refused=$((neg_refused + 1))
  else
    pos_total=$((pos_total + 1)); [ "$status" = OK ] && pos_accepted=$((pos_accepted + 1))
  fi
  printf '%-4s %-4s rc=%-2s %s\n' "$status" "$side" "$rc" "$name"
  [ "$status" = BAD ] && { fail=1; printf '     output: %s\n' "$out" >&2; }
  return 0
}

echo "== NEGATIVE leg: a branch-owned or unbound gate definition cannot authorize =="
check NEG 1 "STALE YAML: PR carries its own divergent merge-gate.yml" \
  "$REGISTERED" "$BRANCH_OWNED"
check NEG 1 "STALE YAML: an older registration than the run's blob" \
  "$BRANCH_OWNED" "$REGISTERED"
check NEG 1 "UNBOUND: MERGE_GATE_V4_BLOB unset (empty)" \
  "" "$REGISTERED"
check NEG 1 "UNBOUND: registration blank AND blob blank" "" ""

echo
echo "== POSITIVE leg: the registered definition is still accepted =="
check POS 0 "run's gate blob equals the registered v4 blob" \
  "$REGISTERED" "$REGISTERED"

echo
if [ "$live" -eq 1 ]; then
  echo "== LIVE corroboration (read-only) =="
  reg=$(with-proxy gh variable get MERGE_GATE_V4_BLOB --repo rrnewton/hermit 2>/dev/null || true)
  cur=$(with-proxy gh api \
    'repos/rrnewton/hermit/contents/.github/workflows/merge-gate.yml?ref=main' \
    --jq .sha 2>/dev/null || true)
  printf 'registered vars.MERGE_GATE_V4_BLOB = %s\n' "${reg:-<unreadable>}"
  printf 'blob of merge-gate.yml on main     = %s\n' "${cur:-<unreadable>}"
  if [ -n "$reg" ] && [ "$reg" = "$cur" ]; then
    echo "LIVE OK: the deployed registration matches the definition on main."
  else
    echo "LIVE MISMATCH OR UNREADABLE: report to the coordinator; not a bracket failure." >&2
  fi
  echo
fi

printf 'NEGATIVE refusals: %d/%d   POSITIVE acceptances: %d/%d\n' \
  "$neg_refused" "$neg_total" "$pos_accepted" "$pos_total"
if [ "$fail" -ne 0 ] || [ "$neg_refused" -ne "$neg_total" ] ||
   [ "$pos_accepted" -ne "$pos_total" ]; then
  echo "FAIL: gate-definition binding bracket" >&2
  exit 1
fi
echo "PASS: a branch-owned or unregistered gate definition is refused; the registered one is accepted"
