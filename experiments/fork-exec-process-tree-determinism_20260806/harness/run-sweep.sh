#!/usr/bin/env bash
set -u
export HERMIT_BIN=/home/newton/work/dev-hermit/ignored/fork-exec-parity/bin/hermit-f89c6976-rel-e9
export BACKENDS="ptrace e9patch"
export OUT=/home/newton/work/dev-hermit/ignored/fork-exec-parity/sweep
export CELL_TIMEOUT=300
exec /home/newton/work/dev-hermit/ignored/fork-exec-parity/sweep.sh \
 "forkwait_ordered=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/forkwait_ordered 4" \
 "forkwait_any=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/forkwait_any 5" \
 "forkwait_any_wide=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/forkwait_any 12" \
 "zombie_delay=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/zombie_delay 4" \
 "orphan_reparent=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/orphan_reparent" \
 "exec_chain=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/exec_chain 3" \
 "vfork_exec=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/vfork_exec 2" \
 "spawn_wait=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/spawn_wait 3" \
 "fork_pipe=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/fork_pipe 4" \
 "sh_pipeline=/bin/sh /home/newton/work/dev-hermit/ignored/fork-exec-parity/scripts/pipeline.sh" \
 "sh_seqtree=/bin/sh /home/newton/work/dev-hermit/ignored/fork-exec-parity/scripts/seqtree.sh" \
 "sh_nested=/bin/sh /home/newton/work/dev-hermit/ignored/fork-exec-parity/scripts/nestedsh.sh" \
 "sh_bgjobs=/bin/sh /home/newton/work/dev-hermit/ignored/fork-exec-parity/scripts/bgjobs.sh" \
 "make_j1=/usr/bin/make -s -j1 -C /home/newton/work/dev-hermit/ignored/fork-exec-parity/mk"
