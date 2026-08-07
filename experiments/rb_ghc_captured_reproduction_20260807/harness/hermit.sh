#!/bin/bash
# Run the host-built hermit binary inside the container against host glibc.
#
# The container (Debian 11, glibc 2.31) is older than the build host, so the
# host binary cannot use the container's loader. run.sh stages the host loader
# and hermit's shared-library closure into /work/hostlibs; this invokes the
# staged loader explicitly and points it at that directory.
exec /work/hostlibs/ld-linux-x86-64.so.2 --library-path /work/hostlibs \
  "${HERMIT_BIN:-/work/hermit-bin}" "$@"
