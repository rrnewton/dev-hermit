#!/usr/bin/env bash
# Bracket Demo 8 crash-seed calibration against the CURRENT producer.
#
# WHAT THIS PROVES, and why each bracket exists:
#   1. The producer distinguishes "the guest never ran" from "the guest ran and
#      found no UAF". Those are the two failures the pre-6c7c099 message
#      conflated, and reporting the second as the first is what kept #1877
#      undiagnosed for five hours. Each message must EXCLUDE the other's text,
#      not merely contain its own -- a zero that could be either is the defect.
#   2. A planted report is actually detected, so a clean sweep is a measurement
#      rather than an inert pass.
#   3. A cached crash seed is bound to the fixture identity it was calibrated
#      against, and one that is not is refused rather than reused (#1877).
#
# HISTORY, so the next reader does not repeat it. This file used to drive a
# `DEMO08_TEST_MODE` / `DEMO08_CALIBRATION_RUNNER` interface and grep for
# "path engagement N/M", "NO-RESULT", per-seed `calibration.tsv` rows and
# retained `calibration-cold-seed-*.out` files. Producer commit 3814141
# introduced that vocabulary at 00:55Z; this test landed against it at 01:29Z;
# producer commit 6c7c099 REIMPLEMENTED the same guarantee at 05:06Z as the
# `executed`/`attempted` counters and changed every string. The consumer was
# never updated, so it asserted a contract with zero implementation -- and the
# gate that would have caught that (nightly-demo-sweep.yml) had zero terminal
# outcomes in its last 40 runs, so it sat green-by-absence for ~18 hours.
# The assertions below are written against strings the producer actually emits.
#
# WHAT WAS DROPPED AND NOT REPLACED, stated rather than smuggled: 6c7c099 kept
# the VERDICT and dropped the PER-SEED EVIDENCE (a `calibration.tsv` classifying
# every seed reached/did-not-reach, and retained per-seed output). That is a real
# capability regression -- the 2026-08-07 demo08 investigation wanted exactly
# that data and rebuilt it by hand in
# experiments/demo08-crash-seed-calibration_20260807/. Re-adding it is a
# deliberate decision about the producer, filed separately; it is NOT something
# to slip back in under a test fix.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREP="$ROOT/scripts/prepare-demo08-assets.sh"
TMP="$(mktemp -d -t demo08-calibration-test.XXXXXX)"
trap 'rm -rf -- "$TMP"' EXIT

# PRECONDITION, LOUD RATHER THAN SKIPPED. The producer's preflight demands this
# whole set before it reaches any logic below, so a missing one aborts every
# bracket. Refuse by name: a check that quietly does not run is the exact defect
# this suite exists to refuse, and "skipped" and "passed" must never look alike.
missing=()
for command in autoconf automake file git make mkfs.ext4 patch pkg-config \
  sha256sum truncate; do
  command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done
if [ "${#missing[@]}" -ne 0 ]; then
  echo "REFUSED: prepare-demo08-assets.sh requires tools absent here: ${missing[*]}" >&2
  echo "  Every bracket below would abort in the producer's preflight, so this" >&2
  echo "  run can prove nothing. Install them or run this on the demo-sweep runner." >&2
  exit 1
fi

make_assets() {
  local assets="$1"
  mkdir -p "$assets/buggy" "$assets/fixed"
  printf '#!/usr/bin/env bash\nexit 0\n' >"$assets/buggy/btrfs-convert"
  printf '#!/usr/bin/env bash\nexit 0\n' >"$assets/fixed/btrfs-convert"
  chmod +x "$assets/buggy/btrfs-convert" "$assets/fixed/btrfs-convert"
  : >"$assets/pop-tiny.img"
  # Matching the stamp is what selects the producer's CACHED branch, so these
  # brackets exercise calibration without a btrfs-progs clone and build.
  printf '%s\n' \
    'prep=1 btrfs=4ab0e80be9e3bb1db2e6038e6d4316d35fb7ba8b' \
    >"$assets/.nightly-prep-version"
}

fixture_sha() { sha256sum "$1/buggy/btrfs-convert" | cut -d' ' -f1; }

# THE SEAM. The producer invokes `$HERMIT_RELEASE ... -- <fixture> <image>` once
# per seed and classifies the seed on (exit status, output emptiness), so a stub
# here drives every calibration outcome with no Hermit build, no ASAN toolchain
# and no btrfs fixture. This is the producer's real env hook, not a test-only
# branch compiled into it -- there is no `if TEST_MODE` path to diverge from
# production behaviour.
make_hermit() {
  local path="$1" body="$2"
  printf '#!/usr/bin/env bash\n%s\n' "$body" >"$path"
  chmod +x "$path"
}
# Exits 0 with NO output: a wrapper/toolchain failure around the guest. The
# producer's seed_executed() requires a non-empty output file, so this is a seed
# that never ran.
make_hermit "$TMP/hermit-never-ran" 'exit 0'
# Runs and completes cleanly: the guest executed, and there is no UAF.
make_hermit "$TMP/hermit-clean" 'echo "Conversion complete"; exit 0'
# Runs and reports a use-after-free. The producer detects on the REPORT TEXT,
# not the exit status, because ASAN can report on a thread whose process exits
# 0 -- so emitting the text is what exercises the detector.
make_hermit "$TMP/hermit-uaf" \
  'echo "==1==ERROR: AddressSanitizer: heap-use-after-free on address 0x1"; exit 134'
# Records that it was invoked at all. Used to prove the cached path SHORT-CIRCUITS
# rather than inferring it from an exit code.
make_hermit "$TMP/hermit-tripwire" "touch '$TMP/tripwire-fired'; exit 0"

run_prepare() {
  local assets="$1" artifacts="$2" seeds="$3" hermit="$4"
  env \
    DEMO08_DIR="$assets" \
    DEMO08_BUILD_ROOT="$TMP/build-unused" \
    DEMO08_ARTIFACTS="$artifacts" \
    DEMO08_CALIBRATION_SEEDS="$seeds" \
    DEMO08_CALIBRATION_TIMEOUT=5 \
    HERMIT_RELEASE="$hermit" \
    "$PREP"
}

# Capture rc and output without `set -e` aborting mid-assignment.
capture() {
  local __out=$1 __rc=$2; shift 2
  local o r
  set +e
  o="$("$@" 2>&1)"; r=$?
  set -e
  printf -v "$__out" '%s' "$o"
  printf -v "$__rc" '%s' "$r"
}

# --- Pure-probe bracket -----------------------------------------------------
# `--help` must return before the heavy work and leave nothing behind.
capture help_out help_rc "$PREP" --help
[ "$help_rc" -eq 0 ]
grep -q 'prepare-demo08-assets.sh' <<<"$help_out"

# --- Argument validation ----------------------------------------------------
for bad in DEMO08_CALIBRATION_SEEDS=0 DEMO08_CALIBRATION_SEEDS=x \
  DEMO08_CALIBRATION_TIMEOUT=0 DEMO08_BUILD_JOBS=-1; do
  capture bad_out bad_rc env "$bad" "$PREP"
  [ "$bad_rc" -ne 0 ]
  grep -q "must be a positive integer" <<<"$bad_out"
done

# --- NOT-MEASURED bracket ---------------------------------------------------
# No seed produced a guest exit status with output. This is a statement about
# the MACHINE and must never be reported as an absence of the UAF.
assets="$TMP/assets-never-ran"
make_assets "$assets"
capture never_out never_rc run_prepare "$assets" "$TMP/art-never-ran" 2 "$TMP/hermit-never-ran"
[ "$never_rc" -ne 0 ]
grep -q 'never executed the guest: 0 of 2 seeds' <<<"$never_out"
grep -q 'NOT an absence of the UAF' <<<"$never_out"
# Mutual exclusion: it must NOT also claim the fixture was searched.
! grep -q 'no ASAN UAF found' <<<"$never_out"
[ ! -r "$assets/.crash-seed" ]

# --- NOT-TAKEN bracket ------------------------------------------------------
# Every seed ran; none crashed. A statement about the FIXTURE, and it must carry
# its executed count so the reader can see the search was real.
assets="$TMP/assets-clean"
make_assets "$assets"
capture clean_out clean_rc run_prepare "$assets" "$TMP/art-clean" 2 "$TMP/hermit-clean"
[ "$clean_rc" -ne 0 ]
grep -q 'no ASAN UAF found in seeds 0-1' <<<"$clean_out"
grep -q '2 of 2 seeds executed' <<<"$clean_out"
# Mutual exclusion in the other direction: an executed search is not a no-result.
! grep -q 'never executed the guest' <<<"$clean_out"
[ ! -r "$assets/.crash-seed" ]

# --- Falsifiability bracket -------------------------------------------------
# A planted report must be found, selected, and recorded WITH the fixture
# identity it was calibrated against.
assets="$TMP/assets-uaf"
make_assets "$assets"
capture uaf_out uaf_rc run_prepare "$assets" "$TMP/art-uaf" 3 "$TMP/hermit-uaf"
[ "$uaf_rc" -eq 0 ]
grep -q 'Demo 8 crash seed calibrated: 0' <<<"$uaf_out"
[ "$(cut -d' ' -f1 <"$assets/.crash-seed")" = 0 ]
[ "$(cut -s -d' ' -f2 <"$assets/.crash-seed")" = "$(fixture_sha "$assets")" ]

# --- Fixture-identity brackets (#1877) --------------------------------------
# A cached seed recorded against THIS fixture is reused without recalibrating.
# The tripwire proves the short-circuit directly instead of inferring it from rc.
assets="$TMP/assets-cached-match"
make_assets "$assets"
printf '%s %s\n' 5 "$(fixture_sha "$assets")" >"$assets/.crash-seed"
rm -f -- "$TMP/tripwire-fired"
capture cached_out cached_rc run_prepare "$assets" "$TMP/art-cached" 3 "$TMP/hermit-tripwire"
[ "$cached_rc" -eq 0 ]
grep -q 'cached seed 5 for fixture' <<<"$cached_out"
[ ! -e "$TMP/tripwire-fired" ]

# A bare seed carrying no fixture identity is not evidence about this fixture.
assets="$TMP/assets-cached-bare"
make_assets "$assets"
printf '5\n' >"$assets/.crash-seed"
capture bare_out bare_rc run_prepare "$assets" "$TMP/art-bare" 1 "$TMP/hermit-never-ran"
[ "$bare_rc" -ne 0 ]
grep -q 'carries no fixture identity; recalibrating' <<<"$bare_out"

# A seed calibrated against a DIFFERENT fixture is discarded, naming both.
assets="$TMP/assets-cached-stale"
make_assets "$assets"
printf '5 %s\n' "$(printf 'de%.0s' $(seq 32))" >"$assets/.crash-seed"
capture stale_out stale_rc run_prepare "$assets" "$TMP/art-stale" 1 "$TMP/hermit-never-ran"
[ "$stale_rc" -ne 0 ]
grep -q 'was calibrated for fixture' <<<"$stale_out"
grep -q "but this fixture is $(fixture_sha "$assets" | cut -c1-12)" <<<"$stale_out"

echo 'PASS: Demo 8 calibration separates never-ran from no-UAF, detects a planted'
echo '      report, and refuses a crash seed bound to another fixture'
