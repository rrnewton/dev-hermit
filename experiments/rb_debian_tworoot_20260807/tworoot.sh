#!/usr/bin/env bash
# Two-root controlled reproducibility test for Debian Wheezy source packages.
#
#   prepare once, then build FOUR times from the same prepared source:
#       native  in root N1        hermit  in root A
#       native  in root N2        hermit  in root B
#   and compare N1-vs-N2 and A-vs-B.
#
# The roots differ in path, so anything the build records about where it ran
# shows up as a difference. The native pair is the experiment's own internal
# control: it establishes that the package really is root-sensitive, so a
# matching hermit pair is evidence about Hermit rather than about a package that
# was trivially reproducible anyway. That makes the claim
#
#     "N of N packages: native two-root build DIVERGES, hermit two-root build IDENTICAL"
#
# a controlled statement, not a bare reproducibility percentage.
#
# Depends on the reconstruction rootfs and `prepare` step from
# experiments/debian_reproducible_builds_2026/rebuild.sh.
#
#   ./tworoot.sh --hermit-bin /path/to/hermit PACKAGE [PACKAGE...]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRB="${DRB:-$HERE/../debian_reproducible_builds_2026}"
CACHE="${DRB_CACHE_DIR:-$DRB/ignored/cache}"
RUNS="${DRB_RUNS_DIR:-$HERE/ignored/runs}"
RUNTIME="${DRB_PODMAN_RUNTIME_DIR:-$DRB/ignored/podman-run}"
MANIFEST="${MANIFEST:-$HERE/results.csv}"
HERMIT_BIN="${HERMIT_BIN:-}"
CONTAINER_PROXY="${DRB_CONTAINER_PROXY:-}"
BUILD_TIMEOUT="${BUILD_TIMEOUT:-1800}"

while [ $# -gt 0 ]; do
  case "$1" in
    --hermit-bin) HERMIT_BIN="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; break ;;
    -*) echo "tworoot.sh: unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || { echo "tworoot.sh: name at least one package" >&2; exit 2; }
[ -n "$HERMIT_BIN" ] && [ -x "$HERMIT_BIN" ] || { echo "tworoot.sh: --hermit-bin must be an executable hermit" >&2; exit 2; }

HERMIT_VERSION="$("$HERMIT_BIN" --version 2>/dev/null || echo unknown)"
HERMIT_SHA256="$(sha256sum "$HERMIT_BIN" | cut -c1-64)"
mkdir -p "$RUNS" "$RUNTIME"
export XDG_RUNTIME_DIR="$RUNTIME"

# Append-only manifest keyed by (source tuple, binary sha, root).
if [ ! -f "$MANIFEST" ]; then
  echo "package,source_version,root,executor,artifact_sha256,hermit_sha256,utc" > "$MANIFEST"
fi

# Hash the built .debs: sorted by basename, content only, so the hash depends on
# the artifacts and not on the root path we deliberately varied.
hash_artifacts() { # $1 = root
  local root="$1" f
  find "$root/work" -maxdepth 1 -type f -name '*.deb' -printf '%f\n' 2>/dev/null | sort | while read -r f; do
    printf '%s ' "$f"; sha256sum < "$root/work/$f" | cut -d' ' -f1
  done | sha256sum | cut -d' ' -f1
}

build_native() { # $1 root  $2 srcdir
  timeout "$BUILD_TIMEOUT" podman run --rm --network none \
    --workdir "$2" --rootfs "$1" \
    /usr/bin/dpkg-buildpackage -uc -us -b >"$1.log" 2>&1
}

build_hermit() { # $1 root  $2 srcdir  $3 extra hermit flags (may be empty)
  # Same shape as rebuild.sh's proven invocation: the root is bind-mounted at
  # /tmp/drb-root so build output lands in the real host root rather than in
  # Hermit's private /tmp tmpfs, and /proc + /dev are bound for dpkg.
  #
  # $3 carries --no-rcb-time for the corrected arm. That flag is REQUIRED on a
  # host whose PMU fails validation: Hermit derives virtual time from retired
  # conditional branch counts, so unreliable counters make Hermit's own clock a
  # nondeterminism source. Both arms are measured so the confound is auditable.
  # shellcheck disable=SC2086
  timeout "$BUILD_TIMEOUT" "$HERMIT_BIN" run --strict $3 --base-env=minimal --network=local \
    --bind="$1:/tmp/drb-root" \
    --mount=type=bind,source=/proc,target=/tmp/drb-root/proc \
    --mount=type=bind,source=/dev,target=/tmp/drb-root/dev \
    -- /usr/sbin/chroot /tmp/drb-root /bin/bash -c \
    "cd $2 && exec /usr/bin/perl /usr/bin/dpkg-buildpackage -uc -us -b" >"$1.log" 2>&1
}

overall=0
for package in "$@"; do
  prepared="$CACHE/prepared/$package/rootfs"
  if [ ! -f "$prepared/.drb-package-prepared" ]; then
    echo "== preparing $package =="
    ( cd "$DRB" && env DRB_CONTAINER_PROXY="$CONTAINER_PROXY" with-proxy ./rebuild.sh prepare "$package" ) \
      >"$RUNS/$package.prepare.log" 2>&1
    if [ ! -f "$prepared/.drb-package-prepared" ]; then
      echo "$package: PREPARE-FAILED (see $RUNS/$package.prepare.log)"; overall=1; continue
    fi
  fi
  version="$(sed -n 2p "$prepared/etc/drb-source-tuple" 2>/dev/null || echo unknown)"
  srcdir="$(cat "$prepared/etc/drb-source-dir" 2>/dev/null || echo /work/build)"

  declare -A H=()
  ok=1
  # Four roots at four DIFFERENT paths: that path difference is the variable.
  for spec in native:n1 native:n2 hermit:a hermit:b hermit-norcb:a hermit-norcb:b; do
    executor="${spec%%:*}"; tag="${spec##*:}"
    root="$RUNS/$package/${executor}-${tag}/rootfs"
    rm -rf "$(dirname "$root")"; mkdir -p "$(dirname "$root")"
    cp -a --reflink=auto "$prepared" "$root" || { ok=0; break; }
    case "$executor" in
      native)       build_native "$root" "$srcdir" ;;
      hermit)       build_hermit "$root" "$srcdir" "" ;;
      hermit-norcb) build_hermit "$root" "$srcdir" "--no-rcb-time --max-timeslice=disabled" ;;
    esac
    h="$(hash_artifacts "$root")"
    # An empty artifact set hashes to a constant; treat it as a failed build
    # rather than silently "reproducible".
    if [ -z "$(find "$root/work" -maxdepth 1 -name '*.deb' -print -quit 2>/dev/null)" ]; then
      echo "$package: ${executor}-${tag} BUILD-FAILED (no .deb; see $root.log)"; ok=0; break
    fi
    H["$executor-$tag"]="$h"
    echo "$package,$version,${executor}-${tag},$executor,$h,$HERMIT_SHA256,$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$MANIFEST"
  done
  if [ "$ok" != 1 ]; then overall=1; continue; fi

  nat=DIVERGES; [ "${H[native-n1]}" = "${H[native-n2]}" ] && nat=IDENTICAL
  her=DIVERGES; [ "${H[hermit-a]}" = "${H[hermit-b]}" ] && her=IDENTICAL
  nor=DIVERGES; [ "${H[hermit-norcb-a]}" = "${H[hermit-norcb-b]}" ] && nor=IDENTICAL
  printf '%-24s native:%-9s hermit:%-9s hermit+norcb:%-9s\n' "$package" "$nat" "$her" "$nor"
done
exit "$overall"
