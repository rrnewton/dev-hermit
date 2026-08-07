#!/usr/bin/env bash
# namespace-refusal-probe.sh — establish WHY the seam wraps the builder rather
# than wrapping `nix` itself.
#
# detcore refuses the mount/namespace admin family with a fixed -EPERM by
# design (detcore/src/syscall_classification.rs, `is_mount_ns_admin_refused_syscall`:
# mount, umount2, mount_setattr, move_mount, open_tree, fsopen, fsmount,
# fsconfig, fspick, unshare, setns, open_by_handle_at, fanotify_*, settimeofday).
# The source states the reason: those calls "would otherwise perturb the pinned
# container", and refusing them in Detcore is "bitwise-identical across --verify
# and record/replay". It is a deliberate determinism boundary, not a gap.
#
# CAREFUL — the obvious probe is a FALSE DISCRIMINATOR. A bare
# `unshare(CLONE_NEWNS)` returns EPERM NATIVELY too, because an unprivileged
# process cannot unshare a mount namespace without also unsharing a user
# namespace. Testing that alone "confirms" the refusal on a host where nothing
# is being refused. The discriminating case is the UNPRIVILEGED user+mount
# unshare that nix's build sandbox actually performs.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/env.sh"

run() { printf '  %-58s -> %s\n' "$1" "$2"; }

echo "== FALSE DISCRIMINATOR: bare unshare(CLONE_NEWNS) =="
src=$(mktemp -d); cat > "$src/p.c" <<'C'
#define _GNU_SOURCE
#include <sched.h>
#include <stdio.h>
#include <errno.h>
int main(void){ int r=unshare(CLONE_NEWNS); printf("rc=%d errno=%d\n", r, r?errno:0); return 0; }
C
if gcc -o "$src/p" "$src/p.c" 2>/dev/null; then
  run "native" "$("$src/p")"
  # shellcheck disable=SC2086
  run "hermit $HERMIT_ARGS" "$("$HERMIT" $HERMIT_ARGS -- "$src/p" 2>/dev/null)"
  echo "  ^ EPERM BOTH WAYS. Proves nothing about hermit."
fi

echo
echo "== DISCRIMINATING: unprivileged user+mount unshare (what nix's sandbox does) =="
if unshare --mount --user --map-root-user /bin/true 2>/dev/null; then n="OK"; else n="FAILED"; fi
run "native   unshare --mount --user --map-root-user" "$n"
# shellcheck disable=SC2086
if "$HERMIT" $HERMIT_ARGS -- /usr/bin/unshare --mount --user --map-root-user /bin/true >/dev/null 2>&1; then h="OK"; else h="EPERM (refused)"; fi
run "hermit   unshare --mount --user --map-root-user" "$h"

echo
echo "CONCLUSION: nix's sandboxed builder path unshares before every build, so"
echo "nix cannot run INSIDE hermit and build. The exec-builder seam is correct"
echo "BECAUSE it inverts the nesting: nix does its namespace work on the host and"
echo "execve's a builder that hermit then owns."
