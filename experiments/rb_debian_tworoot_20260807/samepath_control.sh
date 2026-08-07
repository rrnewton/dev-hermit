#!/usr/bin/env bash
# Control for guestpath_arm.sh: build TWICE at the SAME long guest path.
# If these are IDENTICAL, then the short-vs-long difference is caused by the
# path, not by residual run-to-run nondeterminism.
set -uo pipefail
HERE=/home/newton/work/dev-hermit/experiments/rb_debian_tworoot_20260807
CACHE=$HERE/../debian_reproducible_builds_2026/ignored/cache
H=/home/newton/work/dev-hermit/worktrees/dbi-clock176/hermit/target/release/hermit
GP=/work/build-with-a-substantially-longer-directory-name
pkg="${1:?package}"; prepared="$CACHE/prepared/$pkg/rootfs"
declare -A R=()
for rep in r1 r2; do
  root=$HERE/ignored/samepath/$pkg/$rep/rootfs; rm -rf "$(dirname "$root")"; mkdir -p "$(dirname "$root")"
  cp -a --reflink=auto "$prepared" "$root"; mv "$root/work/build" "$root$GP"
  timeout 1800 "$H" run --strict --no-rcb-time --max-timeslice=disabled --base-env=minimal --network=local \
    --bind="$root:/tmp/drb-root" --mount=type=bind,source=/proc,target=/tmp/drb-root/proc \
    --mount=type=bind,source=/dev,target=/tmp/drb-root/dev \
    -- /usr/sbin/chroot /tmp/drb-root /bin/bash -c "cd $GP && exec /usr/bin/perl /usr/bin/dpkg-buildpackage -uc -us -b" >"$root.log" 2>&1
  R[$rep]=$(find "$root/work" -maxdepth 1 -type f -name '*.deb' -printf '%f\n' | sort | while read -r f; do printf '%s ' "$f"; sha256sum < "$root/work/$f" | cut -d' ' -f1; done | sha256sum | cut -d' ' -f1)
done
v=DIFFERS; [ "${R[r1]}" = "${R[r2]}" ] && v=IDENTICAL
echo "$pkg  same-long-path x2 under hermit: $v  (${R[r1]:0:12} / ${R[r2]:0:12})"
