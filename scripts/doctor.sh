#!/usr/bin/env bash
# Non-mutating dependency and host-capability doctor for dev-hermit profiles.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMIT_REPO="${HERMIT_REPO:-$ROOT/hermit}"
PROFILE="${1:-all}"
failures=0
warnings=0

case "$PROFILE" in
  core|full|qemu|all) ;;
  *)
    echo "usage: $0 [core|full|qemu|all]" >&2
    exit 2
    ;;
esac

pass() {
  printf 'PASS  %-24s %s\n' "$1" "$2"
}

fail_check() {
  printf 'FAIL  %-24s %s\n' "$1" "$2"
  failures=$((failures + 1))
}

warn() {
  printf 'WARN  %-24s %s\n' "$1" "$2"
  warnings=$((warnings + 1))
}

find_command() {
  command -v "$1" 2>/dev/null || true
}

resolve_executable() {
  [ -n "$1" ] || return 0
  case "$1" in
    */*) [ -x "$1" ] && printf '%s\n' "$1" ;;
    *) find_command "$1" ;;
  esac
}

require_command() {
  local name="$1"
  local hint="$2"
  local path
  path="$(find_command "$name")"
  if [ -n "$path" ]; then
    pass "$name" "$path"
  else
    fail_check "$name" "$hint"
  fi
}

check_core() {
  local active channel component installed_components pkg_config rust_toolchain

  if [ "$(uname -s 2>/dev/null || true)" = Linux ]; then
    pass "operating system" "Linux"
  else
    fail_check "operating system" "x86-64 Linux is required"
  fi
  case "$(uname -m 2>/dev/null || true)" in
    x86_64|amd64) pass "architecture" "x86_64" ;;
    *) fail_check "architecture" "x86_64 is required" ;;
  esac

  require_command git "install Git"
  require_command make "install make/build-essential"
  require_command gcc "install GCC/build-essential"
  require_command g++ "install G++/build-essential"
  require_command curl "install curl (needed for rustup and public assets)"
  require_command python3 "install Python 3"

  pkg_config="$(find_command pkg-config)"
  [ -n "$pkg_config" ] || pkg_config="$(find_command pkgconf)"
  if [ -n "$pkg_config" ]; then
    pass "pkg-config" "$pkg_config"
    for component in libunwind-ptrace liblzma; do
      if "$pkg_config" --exists "$component"; then
        pass "pkg:$component" "available"
      else
        fail_check "pkg:$component" "run make install-deps-core"
      fi
    done
  else
    fail_check "pkg-config" "install pkg-config (pkgconf on RPM systems)"
  fi

  require_command cargo "install rustup, then the repository toolchain"
  require_command rustc "install rustup, then the repository toolchain"
  if [ -z "$(find_command rustup)" ]; then
    fail_check "rustup" "install from https://rustup.rs, then rerun the profile installer"
  elif [ ! -r "$HERMIT_REPO/rust-toolchain.toml" ]; then
    fail_check "Rust toolchain file" "initialize the Hermit submodule"
  else
    pass "rustup" "$(find_command rustup)"
    channel="$(sed -n 's/^[[:space:]]*channel[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$HERMIT_REPO/rust-toolchain.toml" | head -n 1)"
    if [ -z "$channel" ]; then
      fail_check "Rust channel" "cannot parse hermit/rust-toolchain.toml"
    elif active="$(cd "$HERMIT_REPO" && rustup show active-toolchain 2>/dev/null)"; then
      rust_toolchain="${active%% *}"
      case "$rust_toolchain" in
        "$channel"|"$channel"-*) pass "Rust channel" "$rust_toolchain" ;;
        *) fail_check "Rust channel" "expected $channel, got $rust_toolchain" ;;
      esac
      installed_components="$(rustup component list --installed --toolchain "$rust_toolchain" 2>/dev/null || true)"
      for component in cargo rustc rustfmt clippy; do
        if printf '%s\n' "$installed_components" | grep -q "^$component-"; then
          pass "Rust component:$component" "installed for $rust_toolchain"
        else
          fail_check "Rust component:$component" "rustup component add $component --toolchain $rust_toolchain"
        fi
      done
    else
      fail_check "Rust channel" "toolchain $channel is not installed; rerun make install-deps-core"
    fi
  fi

  if [ -n "$(find_command gdb)" ]; then
    pass "GDB" "$(find_command gdb)"
  else
    warn "GDB" "missing; interactive record/replay debugging will be unavailable"
  fi

  if [ -n "$(find_command unshare)" ] \
     && unshare --user --map-root-user --pid --fork true >/dev/null 2>&1; then
    pass "user/PID namespaces" "unprivileged probe succeeded"
  else
    fail_check "user/PID namespaces" "unshare probe failed; check kernel/user.max_user_namespaces policy"
  fi

  if [ -n "$(find_command python3)" ] \
     && python3 -c 'import ctypes,os; p=os.fork(); (os._exit(0 if ctypes.CDLL(None).ptrace(0,0,None,None)==0 else 1) if p==0 else None); _,s=os.waitpid(p,0); raise SystemExit(0 if os.WIFEXITED(s) and os.WEXITSTATUS(s)==0 else 1)' >/dev/null 2>&1; then
    pass "parent-child ptrace" "PTRACE_TRACEME probe succeeded"
  else
    fail_check "parent-child ptrace" "ptrace probe failed; check container/LSM policy"
  fi

  if [ -n "$(find_command python3)" ] \
     && python3 -c 'import ctypes; raise SystemExit(0 if ctypes.CDLL(None).prctl(21,0,0,0,0)>=0 else 1)' >/dev/null 2>&1; then
    pass "seccomp" "PR_GET_SECCOMP probe succeeded"
  else
    fail_check "seccomp" "kernel or container policy does not expose seccomp"
  fi

  if [ -n "$(find_command perf)" ] \
     && perf stat -e branches -- true >/dev/null 2>&1; then
    pass "PMU branches" "accessible (precise preemption/verify supported)"
  else
    warn "PMU branches" "unavailable; builds work, but PMU-dependent verification may not"
  fi
}

check_full() {
  require_command cmake "install CMake with make install-deps-full"
  require_command perl "install Perl with make install-deps-full"
  if [ -n "$(find_command ninja)" ]; then
    pass "Ninja" "$(find_command ninja)"
  else
    warn "Ninja" "missing; CMake can fall back to Makefiles"
  fi
}

check_qemu() {
  local busybox qemu qemu_img
  qemu="$(resolve_executable "${QEMU_BIN:-qemu-system-x86_64}")"
  if [ -n "$qemu" ] && "$qemu" --version >/dev/null 2>&1; then
    pass "QEMU x86_64" "$qemu"
  else
    fail_check "QEMU x86_64" "missing or not runnable; install the QEMU profile or set QEMU_BIN"
  fi
  qemu_img="$(find_command qemu-img)"
  if [ -n "$qemu_img" ] && "$qemu_img" --version >/dev/null 2>&1; then
    pass "qemu-img" "$qemu_img"
  else
    fail_check "qemu-img" "missing or not runnable; install qemu-utils/qemu-img"
  fi
  require_command cpio "install cpio"
  require_command gzip "install gzip"
  require_command file "install file"
  require_command sha256sum "install coreutils"

  busybox="$(resolve_executable "${BUSYBOX:-busybox}")"
  if [ -z "$busybox" ]; then
    fail_check "static BusyBox" "install busybox-static/EPEL busybox or set BUSYBOX=/path"
  elif file "$busybox" 2>/dev/null | grep -q 'statically linked'; then
    pass "static BusyBox" "$busybox"
  else
    fail_check "static BusyBox" "$busybox is dynamically linked"
  fi

  if [ -e /dev/kvm ]; then
    pass "KVM device" "/dev/kvm present (optional)"
  else
    pass "KVM device" "absent; QEMU demos use TCG and do not require it"
  fi
}

printf 'dev-hermit doctor: profile=%s (checks only; no installation/build)\n' "$PROFILE"
check_core
case "$PROFILE" in
  full|all) check_full ;;
esac
case "$PROFILE" in
  qemu|all) check_qemu ;;
esac

if [ "$failures" -ne 0 ]; then
  printf 'Doctor failed: %d required check(s) failed, %d warning(s).\n' \
    "$failures" "$warnings" >&2
  case "$PROFILE" in
    core) echo 'Run: make install-deps-core' >&2 ;;
    full) echo 'Run: make install-deps-full' >&2 ;;
    qemu) echo 'Run: make install-deps-qemu' >&2 ;;
    all) echo 'Run the applicable install-deps-core/full/qemu target(s).' >&2 ;;
  esac
  exit 1
fi

printf 'Doctor passed: profile=%s (%d warning(s)).\n' "$PROFILE" "$warnings"
