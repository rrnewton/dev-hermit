#!/bin/bash
# Run the host-built hermit binary inside the container using host glibc.
exec /work/hostlibs/ld-linux-x86-64.so.2 --library-path /work/hostlibs \
  "${HERMIT_BIN:-/hostrepo/worktrees/makedet/hermit/target/release/hermit}" "$@"
