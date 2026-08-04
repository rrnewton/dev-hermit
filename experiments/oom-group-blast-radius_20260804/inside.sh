#!/bin/bash
set -u
ALLOC=/home/newton/work/dev-hermit/scratch/oom-group-verify/alloc.py
P=/sys/fs/cgroup$(cat /proc/self/cgroup | sed 's/^0:://')
echo "PARENT scope: $P ; controllers=$(cat $P/cgroup.controllers)"
w(){ printf '%s' "$2" > "$1"; }           # w <file> <value>  (avoids echo N>file fd-redirect footgun)
mkdir -p "$P/mgr"; w "$P/mgr/cgroup.procs" "$$"
w "$P/cgroup.subtree_control" "+memory" || { echo "FATAL no +memory subtree"; exit 9; }
echo "subtree_control=$(cat $P/cgroup.subtree_control)"

mkstep(){ local d="$P/step-$1"; mkdir -p "$d"; w "$d/memory.swap.max" 0; w "$d/memory.high" max; w "$d/memory.oom.group" "$3"; w "$d/memory.max" "$(($2*1024*1024))"; echo "$d"; }
launch(){ local d="$1"; shift; "$@" >/dev/null 2>&1 & local pid=$!; w "$d/cgroup.procs" "$pid"; echo "$pid"; }
alive(){ kill -0 "$1" 2>/dev/null && echo ALIVE || echo DEAD; }
ogk(){ awk '/^oom_group_kill /{print $2}' "$1/memory.events"; }
ok(){ awk '/^oom_kill /{print $2}' "$1/memory.events"; }
verify(){ local d=$1; echo "     (cfg: max=$(cat $d/memory.max) oom.group=$(cat $d/memory.oom.group) swap.max=$(cat $d/memory.swap.max) nprocs=$(wc -l <$d/cgroup.procs))"; }

echo; echo "== CASE1: over-cap step WITH oom.group=1 (FIX): whole step dies as a unit =="
d=$(mkstep withog 64 1); s=$(launch "$d" sleep 300); a=$(launch "$d" python3 "$ALLOC" 200 withog); verify "$d"; sleep 5
echo "  sentinel=$(alive $s) allocator=$(alive $a) oom_kill=$(ok $d) oom_group_kill=$(ogk $d)"
kill -9 $s $a 2>/dev/null

echo; echo "== CONTROL: SAME over-cap step WITHOUT oom.group (TODAY's defect): sentinel survives half-dead =="
d=$(mkstep noog 64 0); s=$(launch "$d" sleep 300); a=$(launch "$d" python3 "$ALLOC" 200 noog); verify "$d"; sleep 5
echo "  sentinel=$(alive $s) allocator=$(alive $a) oom_kill=$(ok $d) oom_group_kill=$(ogk $d)"
kill -9 $s $a 2>/dev/null

echo; echo "== CASE2: offender breaches ITS cap, NEIGHBOUR under cap SURVIVES =="
doff=$(mkstep off 64 1); dnbr=$(mkstep nbr 64 1)
n=$(launch "$dnbr" python3 "$ALLOC" 32 nbr); o=$(launch "$doff" python3 "$ALLOC" 200 off); sleep 5
echo "  OFFENDER=$(alive $o) (oom_group_kill=$(ogk $doff)) | NEIGHBOUR=$(alive $n) (oom_kill=$(ok $dnbr))"
kill -9 $o $n 2>/dev/null

echo; echo "== CASE3: N=10 legitimate steps (32MiB under 64MiB cap), expect ZERO kills =="
declare -a ps ds
for i in $(seq 0 9); do d=$(mkstep legit$i 64 1); ds[$i]=$d; ps[$i]=$(launch "$d" python3 "$ALLOC" 32 legit$i); done
sleep 6
av=0; ki=0; for i in $(seq 0 9); do [ "$(alive ${ps[$i]})" = ALIVE ] && av=$((av+1)); ki=$((ki+$(ok ${ds[$i]}))); done
echo "  N=10 legitimate steps: alive=$av/10 total_oom_kill=$ki"
for i in $(seq 0 9); do kill -9 ${ps[$i]} 2>/dev/null; done

echo; echo "== CASE4: plant cleanup — drain + rmdir every child cgroup =="
sleep 1; rm=0; fa=0
for d in "$P"/step-*; do for pid in $(cat "$d/cgroup.procs" 2>/dev/null); do kill -9 $pid 2>/dev/null; done; sleep 0.2; if rmdir "$d" 2>/dev/null; then rm=$((rm+1)); else fa=$((fa+1)); fi; done
echo "  child cgroups removed=$rm failed=$fa"
echo "ALL-DONE"
