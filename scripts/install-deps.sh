#!/usr/bin/env bash
# Install system dependencies for one dev-hermit build/demo profile.

set -euo pipefail

PROFILE="${1:-core}"
MODE="${2:-install}"
OS_RELEASE="${OS_RELEASE:-/etc/os-release}"

case "$PROFILE" in
  core|full|qemu) ;;
  *) echo "usage: $0 [core|full|qemu] [install|--print-packages]" >&2; exit 2 ;;
esac
case "$MODE" in
  install|--print-packages) ;;
  *) echo "usage: $0 [core|full|qemu] [install|--print-packages]" >&2; exit 2 ;;
esac

if [ ! -r "$OS_RELEASE" ]; then
  echo "ERROR: cannot detect the operating system ($OS_RELEASE is missing)." >&2
  exit 1
fi
# OS_RELEASE is intentionally overridable so package mappings can be tested.
# shellcheck disable=SC1090
. "$OS_RELEASE"

distro="${ID:-} ${ID_LIKE:-}"
manager=""
packages=()
case "$distro" in
  *debian*|*ubuntu*)
    manager=apt-get
    packages=(build-essential clang git curl lua5.4 python3 ruby gdb pkg-config libunwind-dev liblzma-dev)
    if [ "$PROFILE" = full ]; then
      packages+=(cmake ninja-build perl)
    elif [ "$PROFILE" = qemu ]; then
      packages+=(qemu-system-x86 qemu-utils busybox-static cpio gzip file)
    fi
    ;;
  *rhel*|*fedora*|*centos*)
    manager=dnf
    packages=(gcc gcc-c++ clang git curl lua make python3 ruby gdb libunwind-devel xz-devel pkgconf)
    if [ "$PROFILE" = full ]; then
      packages+=(cmake ninja-build perl)
    elif [ "$PROFILE" = qemu ]; then
      packages+=(qemu-img busybox cpio gzip file)
      if [ "${ID:-}" = fedora ]; then
        packages+=(qemu-system-x86-core)
      else
        packages+=(qemu-kvm-core)
      fi
    fi
    ;;
  *)
    echo "ERROR: unsupported distribution: ${PRETTY_NAME:-unknown}." >&2
    exit 1
    ;;
esac

if [ "$MODE" = --print-packages ]; then
  printf '%s install -y' "$manager"
  printf ' %q' "${packages[@]}"
  printf '\n'
  exit 0
fi

echo "WARNING: install-deps-$PROFILE installs system packages and may invoke sudo."
if [ "$(id -u)" -eq 0 ]; then
  sudo_cmd=()
elif command -v sudo >/dev/null 2>&1; then
  sudo_cmd=(sudo)
else
  echo "ERROR: sudo is required when not running as root." >&2
  exit 1
fi

if ! command -v "$manager" >/dev/null 2>&1; then
  echo "ERROR: $manager is required for ${PRETTY_NAME:-this distribution}." >&2
  exit 1
fi
"${sudo_cmd[@]}" "$manager" install -y "${packages[@]}"

if ! command -v rustup >/dev/null 2>&1; then
  echo "ERROR: rustup is required but is not installed by system packages." >&2
  echo "Install it from https://rustup.rs, reload your shell, and rerun this target." >&2
  exit 1
fi

toolchain_file="${HERMIT_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/hermit}/rust-toolchain.toml"
if [ ! -r "$toolchain_file" ]; then
  echo "ERROR: missing $toolchain_file; initialize the Hermit submodule." >&2
  exit 1
fi
channel="$(sed -n 's/^[[:space:]]*channel[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$toolchain_file" | head -n 1)"
if [ -z "$channel" ]; then
  echo "ERROR: cannot parse Rust channel from $toolchain_file." >&2
  exit 1
fi
rustup toolchain install "$channel" --profile minimal --component rustfmt --component clippy
