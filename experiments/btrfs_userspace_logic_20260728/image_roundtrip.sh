#!/usr/bin/env bash
set -euo pipefail

if (($# != 3)); then
  echo "usage: $0 BTRFS_IMAGE INPUT OUTPUT" >&2
  exit 64
fi

tool=$1
input=$2
output=$3
rm -f -- "$output"
"$tool" -t 4 "$input" "$output"
sha256sum "$output"
rm -f -- "$output"
