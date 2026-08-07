#!/usr/bin/env bash
set -euo pipefail

: "${DRB_SNAPSHOT_BASE:?set DRB_SNAPSHOT_BASE}"
apt_snapshot_base=${DRB_SNAPSHOT_BASE/https:/http:}

cp -p /usr/bin/ischroot /usr/bin/ischroot.drb-original
cat >/usr/bin/ischroot <<'EOF'
#!/bin/sh
exit 0
EOF
chmod 755 /usr/bin/ischroot
# Idempotent: `debootstrap --second-stage` unpacks debianutils, which owns
# /usr/bin/ischroot and reinstates the real binary itself, consuming the backup.
# The unguarded `mv` then failed ("cannot stat ischroot.drb-original"), and under
# `set -e` that aborted the script *after* the base system had installed
# successfully -- so the rootfs never got its sources.list, policy-rc.d, or the
# .drb-bootstrap-complete marker, and every later stage refused. Restore only
# when a backup is actually present; the EXIT trap can then also fire harmlessly.
restore_ischroot() {
  if [ -e /usr/bin/ischroot.drb-original ]; then
    mv -f /usr/bin/ischroot.drb-original /usr/bin/ischroot
  fi
}
trap restore_ischroot EXIT

# debootstrap deletes /debootstrap once the second stage succeeds, so its
# absence means the base system is already installed. Skipping it here makes
# this script resumable after a post-second-stage failure; re-running it would
# otherwise be impossible and the rootfs would have to be rebuilt from scratch.
if [ -x /debootstrap/debootstrap ]; then
  /debootstrap/debootstrap --second-stage
else
  echo "second stage already complete; configuring only"
fi
restore_ischroot
trap - EXIT

cat >/usr/sbin/policy-rc.d <<'EOF'
#!/bin/sh
exit 101
EOF
chmod 755 /usr/sbin/policy-rc.d

cat >/etc/apt/sources.list <<EOF
deb $apt_snapshot_base wheezy main
deb-src $apt_snapshot_base wheezy main
EOF

cat >/etc/apt/apt.conf.d/99drb-snapshot <<'EOF'
Acquire::Check-Valid-Until "false";
Acquire::Retries "3";
APT::Get::AllowUnauthenticated "true";
EOF

cat >/etc/drb-reconstruction <<EOF
classification=wheezy-reconstruction
snapshot=$DRB_SNAPSHOT_BASE
EOF

touch /.drb-bootstrap-complete
echo "DRB bootstrap complete: $(cat /etc/debian_version)"
