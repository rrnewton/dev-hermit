#!/bin/bash
# Build the fixture directory trees. Same NAME SET, different host enumeration
# order, on two different filesystems.
#
#   usage: make_fixtures.sh <root>
#
# Creates under <root>:
#   asc200/   200 entries n0000..n0199, created in ascending name order
#   desc200/  the SAME 200 names, created in descending name order
#   asc10/    10 entries, ascending  (fits in one getdents64 buffer)
#   desc10/   the same 10 names, descending
#
# On btrfs and tmpfs, readdir order tracks directory-index insertion order, so
# asc* and desc* have identical name sets but opposite host enumeration order.
# That is the "same directory content, different host order" axis -- exactly
# what a host-to-host or filesystem-to-filesystem move looks like to a guest.
set -euo pipefail

root="${1:?usage: make_fixtures.sh <root>}"
rm -rf "$root"
mkdir -p "$root"

mk() { # mk <dir> <count> <asc|desc>
  local dir="$1" n="$2" ord="$3" i
  mkdir -p "$dir"
  if [[ "$ord" == asc ]]; then
    for ((i = 0; i < n; i++)); do : >"$dir/$(printf 'n%04d' "$i")"; done
  else
    for ((i = n - 1; i >= 0; i--)); do : >"$dir/$(printf 'n%04d' "$i")"; done
  fi
}

mk "$root/asc10" 10 asc
mk "$root/desc10" 10 desc
mk "$root/asc200" 200 asc
mk "$root/desc200" 200 desc
# 2000 entries x 32-byte records = 64 KiB > glibc's 32 KiB readdir buffer, so
# even the plain opendir/readdir path spans several getdents64 calls.
mk "$root/asc2000" 2000 asc
mk "$root/desc2000" 2000 desc

echo "fixtures under $root:"
for d in asc10 desc10 asc200 desc200 asc2000 desc2000; do
  printf '  %-8s %s entries\n' "$d" "$(ls -U "$root/$d" | wc -l)"
done
