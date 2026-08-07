#!/usr/bin/env bash
# Mechanism-1 probe: vary the GUEST-VISIBLE build path, not just the host root.
#
# tworoot.sh varies the host root while the build always runs at the same
# guest path (/work/build), because the build happens inside the rootfs
# (podman --rootfs, or hermit + chroot). That design isolates path-TRIGGERED
# divergence and structurally excludes path-EMBEDDED divergence.
#
# This probe closes that gap: it builds the same source at two guest paths of
# different length and compares. Expected under the two-mechanism model:
#   native  DIFFERS   (the path is in the output bytes)
#   hermit  DIFFERS   (Hermit should NOT fix this -- the guest asked where it
#                      was and got a truthful, different answer)
# A hermit IDENTICAL here would mean Hermit is doing something to paths nobody
# has accounted for.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="${DRB_CACHE_DIR:-$HERE/../debian_reproducible_builds_2026/ignored/cache}"
RUNS="$HERE/ignored/guestpath"
H="${HERMIT_BIN:?set HERMIT_BIN}"
SHORT=/work/build
LONG=/work/build-with-a-substantially-longer-directory-name
pkg="${1:?package}"
prepared="$CACHE/prepared/$pkg/rootfs"
[ -f "$prepared/.drb-package-prepared" ] || { echo "$pkg: not prepared"; exit 1; }

hashit() { find "$1/work" -maxdepth 1 -type f -name '*.deb' -printf '%f\n' | sort | while read -r f; do printf '%s ' "$f"; sha256sum < "$1/work/$f" | cut -d' ' -f1; done | sha256sum | cut -d' ' -f1; }

declare -A R=()
for mode in native hermit; do
  for variant in short long; do
    [ "$variant" = short ] && gp="$SHORT" || gp="$LONG"
    root="$RUNS/$pkg/$mode-$variant/rootfs"; rm -rf "$(dirname "$root")"; mkdir -p "$(dirname "$root")"
    cp -a --reflink=auto "$prepared" "$root"
    # Relocate the source tree to the target guest path inside this root.
    if [ "$gp" != "$SHORT" ]; then mv "$root$SHORT" "$root$gp"; fi
    if [ "$mode" = native ]; then
      timeout 1800 podman run --rm --network none --workdir "$gp" --rootfs "$root" \
        /usr/bin/dpkg-buildpackage -uc -us -b >"$root.log" 2>&1
    else
      timeout 1800 "$H" run --strict --no-rcb-time --max-timeslice=disabled \
        --base-env=minimal --network=local \
        --bind="$root:/tmp/drb-root" \
        --mount=type=bind,source=/proc,target=/tmp/drb-root/proc \
        --mount=type=bind,source=/dev,target=/tmp/drb-root/dev \
        -- /usr/sbin/chroot /tmp/drb-root /bin/bash -c \
        "cd $gp && exec /usr/bin/perl /usr/bin/dpkg-buildpackage -uc -us -b" >"$root.log" 2>&1
    fi
    if [ -z "$(find "$root/work" -maxdepth 1 -name '*.deb' -print -quit 2>/dev/null)" ]; then
      echo "$pkg $mode/$variant BUILD-FAILED (see $root.log)"; exit 1; fi
    R["$mode-$variant"]=$(hashit "$root")
  done
done
n=DIFFERS; [ "${R[native-short]}" = "${R[native-long]}" ] && n=IDENTICAL
h=DIFFERS; [ "${R[hermit-short]}" = "${R[hermit-long]}" ] && h=IDENTICAL
echo "$pkg  guest-path-varied  native:$n  hermit:$h"
