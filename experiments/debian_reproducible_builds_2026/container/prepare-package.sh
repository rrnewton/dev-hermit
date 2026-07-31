#!/usr/bin/env bash
set -euo pipefail

: "${DRB_PACKAGE:?set DRB_PACKAGE}"

case "$DRB_PACKAGE" in
  *[!a-z0-9+.-]*|'')
    echo "invalid Debian source package name: $DRB_PACKAGE" >&2
    exit 2
    ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  build-essential devscripts fakeroot faketime
apt-get build-dep -y "$DRB_PACKAGE"

install -d /work
cd /work
apt-get source --download-only "$DRB_PACKAGE"

dsc=$(find /work -maxdepth 1 -type f -name '*.dsc' -print -quit)
source_dir=/work/build
if [[ -z "$dsc" ]]; then
  echo "source download did not produce a .dsc" >&2
  exit 1
fi
if [[ ! -d "$source_dir" ]]; then
  faketime '1984-01-01' dpkg-source -x "$dsc" "$source_dir"
fi

dpkg-parsechangelog -l"$source_dir/debian/changelog" |
  sed -n -e 's/^Source: //p' -e 's/^Version: //p' > /etc/drb-source-tuple
printf '%s\n' "$source_dir" > /etc/drb-source-dir
touch /.drb-package-prepared
echo "DRB package prepared: $(tr '\n' ' ' </etc/drb-source-tuple)"
