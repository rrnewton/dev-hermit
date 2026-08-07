#!/usr/bin/env bash
# cmake-hang-repro.sh — minimal reproducer for "cmake configure hangs under
# `hermit run`", the blocker that stopped the real-package (lensfun) arm.
#
# Observed: the cmake process sits in `do_epoll_wait` with ZERO accumulated CPU
# time (checked via /proc/<pid>/wchan and ps TIME) for tens of minutes. It is a
# HANG, not slowness. Reproduces under both `--tmp=/tmp` and `--no-namespace`.
#
# Usage: cmake-hang-repro.sh [timeout_s]
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/env.sh"
T="${1:-90}"

CMAKE="$(nix-build '<nixpkgs>' -A cmake --no-out-link 2>/dev/null)/bin/cmake"
GCCBIN="$(nix-build '<nixpkgs>' -A gcc --no-out-link 2>/dev/null)/bin"
export PATH="$GCCBIN:$(dirname "$CMAKE"):$PATH"
src=$(mktemp -d /tmp/cmake-hang-repro.XXXXXX)
printf 'cmake_minimum_required(VERSION 3.10)\nproject(trivial C)\nadd_executable(t main.c)\n' > "$src/CMakeLists.txt"
printf 'int main(void){return 0;}\n' > "$src/main.c"

echo "cmake  = $CMAKE"
echo "hermit = $HERMIT"
echo "dose   = $HERMIT_ARGS"

mkdir -p "$src/b-native"
s=$(date +%s); ( cd "$src/b-native" && timeout "$T" "$CMAKE" .. >/dev/null 2>&1 ); rc=$?; e=$(date +%s)
echo "native: rc=$rc wall=$((e-s))s"

mkdir -p "$src/b-hermit"
s=$(date +%s)
# shellcheck disable=SC2086
( cd "$src/b-hermit" && timeout "$T" "$HERMIT" $HERMIT_ARGS -- "$CMAKE" .. >"$src/hermit.log" 2>&1 ); rc=$?; e=$(date +%s)
echo "hermit: rc=$rc wall=$((e-s))s   (rc=124 == TIMED OUT == the hang)"
echo "--- last lines hermit saw ---"
grep -vE "reverie_ptrace|ARCH_GET_CPUID|PMU validation" "$src/hermit.log" | tail -6
echo "--- artifacts in $src ---"
