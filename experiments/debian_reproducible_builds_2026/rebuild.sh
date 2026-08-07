#!/usr/bin/env bash
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
workspace=$(cd -- "$here/../.." && pwd)
detached="$workspace/scripts/detached-verify.rs"
cache=${DRB_CACHE_DIR:-$here/ignored/cache}
runs=${DRB_RUNS_DIR:-$here/ignored/runs}
runtime=${DRB_PODMAN_RUNTIME_DIR:-$here/ignored/podman-run}
container_proxy=${DRB_CONTAINER_PROXY:-}

snapshot=20190301T000000Z
snapshot_base="https://snapshot.debian.org/archive/debian/$snapshot/"
release_url="${snapshot_base}dists/wheezy/Release"
sources_url="${snapshot_base}dists/wheezy/main/source/Sources.gz"
release_sha256=bf3b1df44c6b8a752c270c22b0057af6fcb301b814f9c594e249f86ccaec6a63
sources_sha256=33ebf8db73b8859173b1c6a5d5d5d466e5f23a4f241fe7b0fd440ca8fa3c008b
builder_image='docker.io/library/debian@sha256:41a613df4beca480a97c22b1f6837f7502cb95206e2cc2daf1ea3cb28f8755ab'

usage() {
  cat <<'EOF'
Usage: rebuild.sh COMMAND [PACKAGE]

Commands:
  fetch-metadata       Fetch and verify the immutable Wheezy indices.
  bootstrap            Create the base Wheezy reconstruction rootfs.
  finish-bootstrap     Resume an unpacked rootfs at its second stage.
  prepare PACKAGE      Install build dependencies and unpack one target.
  resume-prepare PKG   Resume a preserved, partially prepared target.
  hermit PACKAGE       Strictly build twice with Hermit --verify, offline.
  native PACKAGE       Build once in a network-isolated Podman container.
  status [PACKAGE]     Print bounded reconstruction state.

Networked commands must be invoked through with-proxy. Heavy work is routed
through scripts/detached-verify.rs automatically. Set HERMIT_BIN for `hermit`.
EOF
}

die() {
  echo "rebuild.sh: $*" >&2
  exit 2
}

require_file() {
  [[ -f "$1" ]] || die "missing $1"
}

verify_hash() {
  local expected=$1
  local path=$2
  local actual
  actual=$(sha256sum "$path")
  actual=${actual%% *}
  [[ "$actual" == "$expected" ]] || die "SHA-256 mismatch for $path: $actual"
}

validate_package() {
  local package=$1
  case "$package" in
    *[!a-z0-9+.-]*|'') die "invalid Debian source package name: $package" ;;
  esac
  grep -Fxq "$package" "$here/asplos20_dettrace_reproduced_target.txt" ||
    die "$package is not in the 8,688-name Dettrace-reproduced target"
}

ensure_runtime() {
  install -d -m 700 "$runtime"
  mkdir -p "$cache" "$runs"
}

podman_proxy_args=()
if [[ -n "$container_proxy" ]]; then
  podman_proxy_args+=(
    --env "http_proxy=$container_proxy"
    --env "https_proxy=$container_proxy"
    --env "HTTP_PROXY=$container_proxy"
    --env "HTTPS_PROXY=$container_proxy"
  )
fi

fetch_metadata() {
  ensure_runtime
  if [[ ! -f "$cache/Release" ]]; then
    "$detached" run --name drb-wheezy-release --tail 4 --no-grep -- \
      curl --fail --location --silent --show-error --retry 2 \
      --output "$cache/Release" "$release_url"
  fi
  if [[ ! -f "$cache/Sources.gz" ]]; then
    "$detached" run --name drb-wheezy-sources --tail 4 --no-grep -- \
      curl --fail --location --silent --show-error --retry 2 \
      --output "$cache/Sources.gz" "$sources_url"
  fi
  verify_hash "$release_sha256" "$cache/Release"
  verify_hash "$sources_sha256" "$cache/Sources.gz"
  grep -Fq "$sources_sha256  7532572 main/source/Sources.gz" "$cache/Release" ||
    die "Release does not authenticate the selected Sources.gz"
  local source_count missing_count
  source_count=$(gzip -cd "$cache/Sources.gz" | grep -c '^Package: ')
  # The checked-in manifests are C-collation sorted (the recovery recipe in
  # README.md uses a plain `sort -u`). `comm` and `sort` honour the ambient
  # locale, and en_US.UTF-8 orders '-', '+' and '.' differently from C, so an
  # interactive shell made this refuse with "file 1 is not in sorted order".
  # Pin both sides to C so the comparison matches how the files were written.
  missing_count=$(
    LC_ALL=C comm -23 "$here/asplos20_dettrace_reproduced_target.txt" \
      <(gzip -cd "$cache/Sources.gz" | awk '/^Package: / {print $2}' | LC_ALL=C sort -u) |
      wc -l
  )
  [[ "$source_count" == 17175 ]] ||
    die "unexpected Wheezy source stanza count: $source_count"
  [[ "$missing_count" == 0 ]] ||
    die "$missing_count target package names are absent from the selected snapshot"
  echo "metadata verified: snapshot=$snapshot release=$release_sha256 sources=$sources_sha256 targets=8688/8688"
}

bootstrap() {
  ensure_runtime
  require_file "$cache/Release"
  require_file "$cache/Sources.gz"
  verify_hash "$release_sha256" "$cache/Release"
  verify_hash "$sources_sha256" "$cache/Sources.gz"
  [[ ! -e "$cache/wheezy-rootfs" ]] ||
    die "$cache/wheezy-rootfs already exists; preserving it"
  [[ ! -e "$cache/wheezy-rootfs.tar" ]] ||
    die "$cache/wheezy-rootfs.tar already exists; preserving it"
  "$detached" run --name drb-wheezy-bootstrap --tail 10 \
    --grep 'DRB foreign bootstrap complete' --grep error --grep failed -- \
    env XDG_RUNTIME_DIR="$runtime" podman run --rm --privileged --network=host \
    "${podman_proxy_args[@]}" \
    --volume "$here/container/bootstrap-wheezy.sh:/bootstrap-wheezy.sh:ro" \
    --volume "$cache:/cache" \
    --env DRB_SNAPSHOT_BASE="$snapshot_base" \
    "$builder_image" /bin/bash /bootstrap-wheezy.sh
  mkdir "$cache/wheezy-rootfs"
  "$detached" run --name drb-wheezy-extract --tail 4 --no-grep -- \
    tar -xf "$cache/wheezy-rootfs.tar" -C "$cache/wheezy-rootfs"
  finish_bootstrap
}

finish_bootstrap() {
  ensure_runtime
  if [[ -f "$cache/wheezy-rootfs/.drb-bootstrap-complete" ]]; then
    echo 'DRB bootstrap already complete; preserving it'
    return
  fi
  # Either the second stage still has to run, or it already ran and debootstrap
  # removed itself. Both are resumable; only a rootfs with neither is broken.
  if [[ ! -x "$cache/wheezy-rootfs/debootstrap/debootstrap" ]]; then
    require_file "$cache/wheezy-rootfs/var/lib/dpkg/status"
  fi
  if [[ -L "$cache/wheezy-rootfs/proc" ]]; then
    unlink "$cache/wheezy-rootfs/proc"
    mkdir "$cache/wheezy-rootfs/proc"
  fi
  "$detached" run --name drb-wheezy-second-stage --tail 10 \
    --grep 'DRB bootstrap complete' --grep error --grep failed -- \
    env XDG_RUNTIME_DIR="$runtime" podman run --rm --privileged --network=host \
    "${podman_proxy_args[@]}" \
    --volume "$here/container/finish-wheezy.sh:/finish-wheezy.sh:ro" \
    --env DRB_SNAPSHOT_BASE="$snapshot_base" \
    --env FAKECHROOT=true \
    --rootfs "$cache/wheezy-rootfs" \
    /bin/bash /finish-wheezy.sh
  require_file "$cache/wheezy-rootfs/.drb-bootstrap-complete"
}

prepare() {
  local package=$1
  validate_package "$package"
  ensure_runtime
  require_file "$cache/wheezy-rootfs/.drb-bootstrap-complete"
  local root="$cache/prepared/$package/rootfs"
  [[ ! -e "$root" ]] || die "$root already exists; preserving it"
  mkdir -p "$(dirname "$root")"
  "$detached" run --name "drb-$package-root-copy" --tail 4 --no-grep -- \
    cp -a --reflink=auto "$cache/wheezy-rootfs" "$root"
  configure_package "$package" "$root"
}

resume_prepare() {
  local package=$1
  validate_package "$package"
  ensure_runtime
  local root="$cache/prepared/$package/rootfs"
  [[ -d "$root" ]] || die "missing preserved preparation root: $root"
  [[ ! -f "$root/.drb-package-prepared" ]] ||
    die "$package is already prepared; preserving it"
  configure_package "$package" "$root"
}

configure_package() {
  local package=$1
  local root=$2
  "$detached" run --name "drb-$package-prepare" --tail 12 \
    --grep 'DRB package prepared' --grep error --grep failed -- \
    env XDG_RUNTIME_DIR="$runtime" podman run --rm --privileged --network=host \
    "${podman_proxy_args[@]}" \
    --volume "$here/container/prepare-package.sh:/prepare-package.sh:ro" \
    --env DRB_PACKAGE="$package" \
    --rootfs "$root" \
    /bin/bash /prepare-package.sh
}

copy_run_root() {
  local package=$1
  local executor=$2
  local prepared="$cache/prepared/$package/rootfs"
  local root="$runs/$package/$executor/rootfs"
  require_file "$prepared/.drb-package-prepared"
  [[ ! -e "$root" ]] || die "$root already exists; preserving it"
  mkdir -p "$(dirname "$root")"
  "$detached" run --name "drb-$package-$executor-copy" --tail 4 --no-grep -- \
    cp -a --reflink=auto "$prepared" "$root"
}

native_build() {
  local package=$1
  validate_package "$package"
  ensure_runtime
  copy_run_root "$package" native
  local root="$runs/$package/native/rootfs"
  local source_dir
  source_dir=$(<"$root/etc/drb-source-dir")
  "$detached" run --name "drb-$package-native" --tail 12 \
    --grep 'dpkg-buildpackage' --grep error --grep failed -- \
    env XDG_RUNTIME_DIR="$runtime" podman run --rm --network none \
    --workdir "$source_dir" --rootfs "$root" \
    /usr/bin/dpkg-buildpackage -uc -us -b
  find "$root/work" -maxdepth 1 -type f -name '*.deb' -print0 |
    sort -z | xargs -0r sha256sum
}

hermit_build() {
  local package=$1
  validate_package "$package"
  ensure_runtime
  : "${HERMIT_BIN:?set HERMIT_BIN to the PR #1160-based Hermit binary}"
  require_file "$HERMIT_BIN"
  copy_run_root "$package" hermit
  local root="$runs/$package/hermit/rootfs"

  # Box the bare hermit run under the reaper. detached-verify only captures/greps the guest
  # output (it does not cgroup-box), so the strict --verify subtree can still leak a burned
  # core; --passthrough streams the guest output byte-identically into detached-verify's
  # --grep/--tail capture while cgroup.kill tears the whole subtree down. This is a full
  # dpkg-buildpackage under hermit, so the CPU budget is set high (2h CPU) to reap only a
  # genuine runaway, never a legitimately long build.
  "$detached" run --name "drb-$package-hermit" --tail 16 \
    --grep 'determin' --grep 'verification' --grep error --grep failed -- \
    "$workspace/scripts/hermit-box-run" --passthrough --label "drb-$package" \
    --cpu-budget 7200 -- \
    "$HERMIT_BIN" run --strict --verify --base-env=minimal --network=local \
    --bind="$root:/tmp/drb-root" \
    --mount=type=bind,source=/proc,target=/tmp/drb-root/proc \
    --mount=type=bind,source=/dev,target=/tmp/drb-root/dev \
    -- /usr/sbin/chroot /tmp/drb-root /bin/bash -c \
    'cd /work/build && exec /usr/bin/perl /usr/bin/dpkg-buildpackage -uc -us -b'
  find "$root/work" -maxdepth 1 -type f -name '*.deb' -print0 |
    sort -z | xargs -0r sha256sum
}

status() {
  local package=${1:-hello}
  echo "classification=wheezy-reconstruction"
  echo "snapshot=$snapshot"
  echo "release_sha256=$release_sha256"
  echo "sources_sha256=$sources_sha256"
  echo "builder_image=$builder_image"
  echo "package=$package"
  [[ -f "$cache/wheezy-rootfs/.drb-bootstrap-complete" ]] && echo 'bootstrap=ready' || echo 'bootstrap=missing'
  [[ -f "$cache/prepared/$package/rootfs/.drb-package-prepared" ]] && echo 'package=ready' || echo 'package=missing'
}

command=${1:-}
case "$command" in
  fetch-metadata) fetch_metadata ;;
  bootstrap) bootstrap ;;
  finish-bootstrap) finish_bootstrap ;;
  prepare) [[ $# -eq 2 ]] || die 'prepare requires PACKAGE'; prepare "$2" ;;
  resume-prepare) [[ $# -eq 2 ]] || die 'resume-prepare requires PACKAGE'; resume_prepare "$2" ;;
  hermit) [[ $# -eq 2 ]] || die 'hermit requires PACKAGE'; hermit_build "$2" ;;
  native) [[ $# -eq 2 ]] || die 'native requires PACKAGE'; native_build "$2" ;;
  status) status "${2:-hello}" ;;
  -h|--help|help|'') usage ;;
  *) usage >&2; die "unknown command: $command" ;;
esac
