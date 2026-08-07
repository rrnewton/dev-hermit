#!/usr/bin/env bash
# Falsifiable check for the two-mechanism model of root-varying divergence:
#
#   mechanism 1 "path-embedded"  -- the build root lands in the output bytes
#                                   (DW_AT_comp_dir, __FILE__, rpath, ...).
#                                   Hermit should NOT fix this.
#   mechanism 2 "path-triggered" -- the root change perturbs timing, entropy or
#                                   iteration order, which leaks out.
#                                   Hermit SHOULD fix this.
#
# Prediction under the model: because every package in this set went IDENTICAL
# under Hermit, none of them can embed the differing build-root path.
#
# For each package this unpacks both NATIVE .debs and greps the payload for the
# root paths and for the one differing path component.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNS="$HERE/ignored/runs"
printf '%-12s %-10s %-10s %s\n' package n1_hit n2_hit guest_visible_build_dir
for pkg in $(ls "$RUNS" 2>/dev/null | grep -v '\.prepare\.log$'); do
  r1="$RUNS/$pkg/native-n1/rootfs"; r2="$RUNS/$pkg/native-n2/rootfs"
  [ -d "$r1/work" ] && [ -d "$r2/work" ] || continue
  ls "$r1"/work/*.deb >/dev/null 2>&1 || continue
  h1=0; h2=0
  tmp=$(mktemp -d)
  for tag in n1 n2; do
    root="$RUNS/$pkg/native-$tag/rootfs"; mkdir -p "$tmp/$tag"
    for d in "$root"/work/*.deb; do
      ( cd "$tmp/$tag" && ar x "$d" 2>/dev/null && for t in data.tar.*; do [ -e "$t" ] && tar xf "$t" 2>/dev/null; done )
    done
  done
  # The full host root path, and the single differing component.
  grep -rlaF -- "$r1" "$tmp/n1" >/dev/null 2>&1 && h1=1
  grep -rlaF -- "$r2" "$tmp/n2" >/dev/null 2>&1 && h2=1
  grep -rlaF -- "native-n1" "$tmp/n1" >/dev/null 2>&1 && h1=1
  grep -rlaF -- "native-n2" "$tmp/n2" >/dev/null 2>&1 && h2=1
  # What build directory IS recorded in the artifacts (guest's own view)?
  gv=$(grep -rhoaE '/work/build[A-Za-z0-9_/.+-]*' "$tmp/n1" 2>/dev/null | head -1)
  [ -n "$gv" ] || gv="(none found)"
  printf '%-12s %-10s %-10s %s\n' "$pkg" "$([ $h1 = 1 ] && echo EMBEDS || echo no)" "$([ $h2 = 1 ] && echo EMBEDS || echo no)" "$gv"
  rm -rf "$tmp"
done
