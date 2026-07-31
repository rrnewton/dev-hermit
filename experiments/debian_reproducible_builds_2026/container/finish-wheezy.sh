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
restore_ischroot() {
  mv -f /usr/bin/ischroot.drb-original /usr/bin/ischroot
}
trap restore_ischroot EXIT

/debootstrap/debootstrap --second-stage
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
