#!/usr/bin/env bash
# Build hermit in THREE profiles from the primary (main) source WITHOUT touching
# any slot's/primary's target dir or Cargo.lock (--locked; per-profile
# CARGO_TARGET_DIR; all profile knobs via ENV, never a Cargo.toml edit), then
# time each COMPILE and run the runtime harness for each profile.
#
# Profiles (the owner's clause: "fastest possible minimal debug compile"):
#   release     shipped: opt-level=3, debug-assertions=off, overflow-checks=off.
#   release-o0  --release + CARGO_PROFILE_RELEASE_OPT_LEVEL=0. SEMANTICS-PRESERVING
#               fast-compile candidate: identical behaviour to release (only
#               opt-level differs, which cannot change well-defined semantics),
#               so it is a VALID CI profile for determinism tests.
#   debug       cargo default dev: opt-level=0 AND debug-assertions=on AND
#               overflow-checks=on -> BEHAVIOUR-CHANGING. Measured for the cost
#               curve but NOT a valid CI candidate for determinism tests.
#
# Decision metric: TOTAL = compile + test(runtime). A profile that halves
# compile but doubles runtime is a loss. compile.csv + results.csv feed analyze.py.
#
# Run AFTER/around the drain; boxing + CPU-seconds keep it robust to a shared box.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT=/home/newton/work/dev-hermit
SRC=$ROOT/hermit                 # primary, main, clean, read-only source
N="${1:-7}"
export CARGO_BUILD_JOBS=8 THIRD_PARTY_BUILD_JOBS=8
SHA="$(git -C "$SRC" rev-parse HEAD)"

CC="$HERE/compile.csv"
echo "profile,phase,wall_s,user_s,sys_s,cpu_s" > "$CC"

# build <label> <target-subdir-name> <cargo-args...> ; extra env via BUILD_ENV
build() {
  local label=$1 sub=$2; shift 2
  local td=$HERE/target-$label
  local tf; tf="$(mktemp)"
  echo "=== building hermit ($label) -> $td @ ${SHA:0:12} ==="
  # from-scratch build into a fresh per-label target dir = full honest compile cost.
  env ${BUILD_ENV:-} CARGO_TARGET_DIR="$td" \
    /usr/bin/time -f '%e %U %S' -o "$tf" -- \
    with-proxy cargo build --locked --manifest-path "$SRC/Cargo.toml" \
    --bin hermit "$@" 2>&1 | tail -3 || { echo "BUILD FAILED ($label)"; cat "$tf"; exit 4; }
  awk -v p="$label" '{printf "%s,compile,%s,%s,%s,%.3f\n", p,$1,$2,$3,$2+$3}' "$tf" >> "$CC"
  rm -f "$tf"
  echo "  compile: $(tail -1 "$CC")"
}

BUILD_ENV=""                              build release    release    --release
BUILD_ENV="CARGO_PROFILE_RELEASE_OPT_LEVEL=0" build release-o0 release-o0 --release
BUILD_ENV=""                              build debug      debug

REL=$HERE/target-release/release/hermit
RO0=$HERE/target-release-o0/release/hermit    # --release profile => 'release/' subdir
DBG=$HERE/target-debug/debug/hermit
for b in "$REL" "$RO0" "$DBG"; do
  [ -x "$b" ] || { echo "MISSING BIN: $b" >&2; exit 5; }
done

# --- SEMANTIC GUARD: capture guest-observable behaviour per (profile,guest) ---
# release vs release-o0 MUST match (behaviour-identical by construction). If
# debug differs, that is the proof the default profile changes behaviour.
SEM="$HERE/semantics.txt"; : > "$SEM"
echo "profile guest exit stdout_sha256" >> "$SEM"
for prof in release release-o0 debug; do
  case $prof in release) B=$REL;; release-o0) B=$RO0;; debug) B=$DBG;; esac
  for g in compute_bound syscall_bound; do
    out="$("$B" run -- "$HERE/guests/bin/$g" 2>/dev/null)"; rc=$?
    sha="$(printf '%s' "$out" | sha256sum | cut -c1-16)"
    echo "$prof $g $rc $sha" >> "$SEM"
  done
done
echo "=== semantics ==="; cat "$SEM"
# assert release == release-o0 (the safe-candidate invariant)
if ! diff <(grep '^release '  "$SEM" | awk '{print $2,$3,$4}') \
          <(grep '^release-o0 ' "$SEM" | awk '{print $2,$3,$4}') >/dev/null; then
  echo "!! WARNING: release vs release-o0 DIVERGED — investigate before trusting numbers" >&2
fi

# --- RUNTIME: native baseline once, then each profile's hermit ---
bash "$HERE/harness.sh" native "-"   "$N"
bash "$HERE/harness.sh" release    "$REL" "$N"
bash "$HERE/harness.sh" release-o0 "$RO0" "$N"
bash "$HERE/harness.sh" debug      "$DBG" "$N"
echo "=== DONE; analyze with: python3 analyze.py ==="
