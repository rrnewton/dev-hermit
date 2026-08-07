#!/usr/bin/env bash
# One-command reproduction of the "Hermit determinizes concurrent GHC
# compilation" result (experiments/rb_drb_haskell_ghc_concurrency_20260729).
#
# That experiment recorded the numbers and four harness scripts, but the scripts
# assumed a container someone had already built by hand ("the podman container
# 'ghcbw'") with the hermit binary and a host-glibc closure already staged
# inside it. Nothing captured how that container came to exist, so the result
# was not re-runnable by anyone else. This script is that missing capture: it
# builds the environment, stages everything, runs the matrix, and copies the
# results back out.
#
#   ./run.sh --hermit-bin /path/to/target/release/hermit
#
# Everything else has a default. Nothing outside the workdir is modified, and no
# container image or build tree is written into the repository.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-docker.io/library/haskell:9.8.4}"
WORK="${WORK:-${TMPDIR:-/tmp}/rb-ghc-captured-$USER}"
HERMIT_BIN=""
KEEP=0
RUNS_NOTE=""

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --hermit-bin PATH   Release-built hermit binary (required unless HERMIT_BIN is set).
  --work DIR          Host scratch directory bind-mounted at /work (default: $TMPDIR/rb-ghc-captured-$USER).
  --image REF         Container image (default: docker.io/library/haskell:9.8.4).
  --keep              Leave the workdir in place for inspection.
  -h, --help          This message.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --hermit-bin) HERMIT_BIN="$2"; shift 2 ;;
    --work) WORK="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "run.sh: unknown argument $1" >&2; usage >&2; exit 2 ;;
  esac
done

HERMIT_BIN="${HERMIT_BIN:-${HERMIT_BIN_ENV:-}}"
[ -n "$HERMIT_BIN" ] || HERMIT_BIN="${HERMIT_BIN_DEFAULT:-}"
if [ -z "$HERMIT_BIN" ]; then
  echo "run.sh: --hermit-bin is required (a RELEASE-built hermit; a debug build is too slow for a 46-module GHC build)." >&2
  exit 2
fi
[ -x "$HERMIT_BIN" ] || { echo "run.sh: not executable: $HERMIT_BIN" >&2; exit 2; }
command -v podman >/dev/null || { echo "run.sh: podman not found" >&2; exit 2; }

# --- provenance: bind every result to the exact binary that produced it -------
HERMIT_VERSION="$("$HERMIT_BIN" --version 2>/dev/null || echo unknown)"
HERMIT_SHA256="$(sha256sum "$HERMIT_BIN" | cut -c1-64)"
case "$HERMIT_VERSION" in
  *dirty*) echo "run.sh: WARNING: hermit reports a dirty build ($HERMIT_VERSION); results will not bind to a clean commit." >&2 ;;
esac

echo "== rb_ghc_captured_reproduction =="
echo "   image      : $IMAGE"
echo "   hermit     : $HERMIT_BIN"
echo "   version    : $HERMIT_VERSION"
echo "   sha256     : $HERMIT_SHA256"
echo "   workdir    : $WORK"

# --- environment --------------------------------------------------------------
if ! podman image exists "$IMAGE"; then
  echo "== pulling $IMAGE (via with-proxy if available) =="
  if command -v with-proxy >/dev/null; then with-proxy podman pull "$IMAGE"; else podman pull "$IMAGE"; fi
fi

rm -rf "$WORK"
mkdir -p "$WORK/hostlibs"

# Stage hermit plus its whole shared-library closure AND the host loader. The
# container's glibc (2.31) is older than the build host's, so the host binary
# must be run by the host loader against host libraries.
cp "$HERMIT_BIN" "$WORK/hermit-bin"
chmod +x "$WORK/hermit-bin"
LOADER="$(ldd "$HERMIT_BIN" | awk '/ld-linux-x86-64/ {print $1}' | head -1)"
[ -n "$LOADER" ] && [ -e "$LOADER" ] || { echo "run.sh: could not locate the host loader for $HERMIT_BIN" >&2; exit 2; }
cp -L "$LOADER" "$WORK/hostlibs/ld-linux-x86-64.so.2"
ldd "$HERMIT_BIN" | awk '/=> \// {print $3}' | while read -r lib; do
  [ -e "$lib" ] && cp -L "$lib" "$WORK/hostlibs/"
done
echo "   staged     : $(ls "$WORK/hostlibs" | wc -l) host libraries + loader"

cp "$HERE/harness/hermit.sh" "$WORK/hermit.sh"
mkdir -p "$WORK/harness"
cp "$HERE/harness/gen_pkg.sh" "$HERE/harness/results_csv.sh" "$WORK/harness/"
chmod +x "$WORK/hermit.sh" "$WORK/harness/"*.sh

# --- run ------------------------------------------------------------------------
# --privileged: hermit's ptrace backend needs PTRACE_* and personality control,
# which the default podman seccomp/caps profile denies.
echo "== generating package and running the 6-config x 3-run matrix =="
podman run --rm --privileged \
  -v "$WORK:/work" \
  -e HERMIT_BIN=/work/hermit-bin \
  "$IMAGE" \
  bash -c 'set -e; bash /work/harness/gen_pkg.sh /work/pkg 40; bash /work/harness/results_csv.sh' \
  2>&1 | tee "$WORK/run.log"

if [ -f "$WORK/results.csv" ]; then
  cp "$WORK/results.csv" "$HERE/results.csv"
  echo "== wrote $HERE/results.csv =="
else
  echo "run.sh: the container produced no results.csv; see $WORK/run.log" >&2
  exit 1
fi

# Record exactly what produced these numbers, beside the numbers.
cat > "$HERE/run_provenance.txt" <<EOF
image=$IMAGE
hermit_binary=$HERMIT_BIN
hermit_version=$HERMIT_VERSION
hermit_sha256=$HERMIT_SHA256
host_kernel=$(uname -sr)
host_glibc=$(ldd --version | head -1)
container_ghc=$(podman run --rm "$IMAGE" ghc --numeric-version 2>/dev/null || echo unknown)
generated_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
$RUNS_NOTE
EOF
echo "== wrote $HERE/run_provenance.txt =="

[ "$KEEP" = 1 ] || rm -rf "$WORK"
