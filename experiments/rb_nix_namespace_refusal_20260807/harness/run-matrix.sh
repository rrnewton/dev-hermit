#!/bin/bash
# usage: HERMIT_BIN=<hermit> ./run-matrix.sh   -> writes ../results.csv
set -u
HERMIT="${HERMIT_BIN:?set HERMIT_BIN}"
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${IMAGE:-docker.io/nixos/nix:2.3.16}"
OUT="${OUT:-$HERE/../results.csv}"
{
  echo "probe,environment,result"
  "$HERE/probe-namespace-syscalls.sh" 2>/dev/null | sed 's/^/PROBE,native,/' \
    | sed 's/PROBE,native,\([a-z_]*\)=/\1,native,/'
  "$HERMIT" run --tmp=/tmp -- /usr/bin/unshare --user /bin/true >/dev/null 2>&1 \
    && echo "unshare_user,hermit-default,OK" || echo "unshare_user,hermit-default,EPERM"
  "$HERMIT" run --image "$IMAGE" --tmp=/tmp -- /bin/sh -c 'unshare --user true' >/dev/null 2>&1 \
    && echo "unshare_user,hermit-image,OK" || echo "unshare_user,hermit-image,EPERM"
} >"$OUT"
cat "$OUT"
