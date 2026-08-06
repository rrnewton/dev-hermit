#!/usr/bin/env bash
set -u
export HERMIT_BIN=/home/newton/work/dev-hermit/ignored/fork-exec-parity/bin/hermit-f89c6976-rel-e9
export CELL_TIMEOUT=240
echo "##### LEG: new process-event shapes (clone3 / setsid / setpgid / waitid WNOWAIT) #####"
BACKENDS="ptrace e9patch" OUT=/home/newton/work/dev-hermit/ignored/fork-exec-parity/sweep-shapes /home/newton/work/dev-hermit/ignored/fork-exec-parity/sweep.sh "proc_shapes=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/proc_shapes"
echo
echo "##### LEG: chaos mode, same-seed reproducibility + cross-seed perturbation #####"
SEEDS="1 2 3" OUT=/home/newton/work/dev-hermit/ignored/fork-exec-parity/chaos /home/newton/work/dev-hermit/ignored/fork-exec-parity/chaos.sh \
  "forkwait_any=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/forkwait_any 5" "zombie_delay=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/zombie_delay 4" \
  "orphan_reparent=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/orphan_reparent" "fork_pipe=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/fork_pipe 4" \
  "exec_chain=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/exec_chain 3" "sh_bgjobs=/bin/sh /home/newton/work/dev-hermit/ignored/fork-exec-parity/scripts/bgjobs.sh" \
  "proc_shapes=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/proc_shapes"
