#!/usr/bin/env bash
set -u
export HERMIT_BIN=/home/newton/work/dev-hermit/ignored/fork-exec-parity/bin/hermit-f89c6976-rel-e9
export CELL_TIMEOUT=180
GUESTS=( "forkwait_any=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/forkwait_any 5" "zombie_delay=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/zombie_delay 4" \
  "orphan_reparent=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/orphan_reparent" "vfork_exec=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/vfork_exec 2" \
  "exec_chain=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/exec_chain 3" "fork_pipe=/home/newton/work/dev-hermit/ignored/fork-exec-parity/guests/fork_pipe 4" \
  "sh_pipeline=/bin/sh /home/newton/work/dev-hermit/ignored/fork-exec-parity/scripts/pipeline.sh" "sh_bgjobs=/bin/sh /home/newton/work/dev-hermit/ignored/fork-exec-parity/scripts/bgjobs.sh" )
echo "##### SERIAL N=20 CONC=1 #####"
N=20 CONC=1 OUT=/home/newton/work/dev-hermit/ignored/fork-exec-parity/stress-serial /home/newton/work/dev-hermit/ignored/fork-exec-parity/stress.sh "${GUESTS[@]}"
echo "##### CONCURRENT N=32 CONC=16 (vfork ESRCH death-race condition) #####"
N=32 CONC=16 OUT=/home/newton/work/dev-hermit/ignored/fork-exec-parity/stress-conc /home/newton/work/dev-hermit/ignored/fork-exec-parity/stress.sh "${GUESTS[@]}"
