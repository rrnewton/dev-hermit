#!/bin/bash
# Reproduce experiments/cmake_mtime_vs_content_20260806. Needs only GNU make + c++.
# Creates a scratch project, runs the mutation matrix, prints one line per row of results.csv.
set -u
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
W=${1:-$(mktemp -d)}/proj
mkdir -p "$W/obj" && cd "$W" || exit 2
cp "$HERE"/{cc-atomic,verify-objects,slowcc} . && chmod +x cc-atomic verify-objects slowcc

cat > tmpl.h <<'EOF'
#pragma once
template <typename T> struct sched_t { void set_cur_input(int a, int b, bool c); int v_ = 0; };
EOF
cat > scheduler_impl.cpp <<'EOF'
#include "tmpl.h"
template <typename T> void sched_t<T>::set_cur_input(int a, int b, bool c) { v_ = a + b + (c ? 1 : 0); }
template struct sched_t<int>;
EOF
printf '#include "tmpl.h"\nint option_count() { return 7; }\n' > options.cpp
printf 'int raw2trace_rows() { return 42; }\n' > raw2trace.cpp
cat > main.cpp <<'EOF'
#include <cstdio>
#include "tmpl.h"
int option_count(); int raw2trace_rows();
int main() { sched_t<int> s; s.set_cur_input(1,2,true); printf("ok %d %d\n", option_count(), raw2trace_rows()); return 0; }
EOF
printf 'int victim(){return 1;}\n' > victim.cpp
touch flags.make compiler_depend.ts

# build.make: faithful replica of the CMake "Unix Makefiles" generated rule shape --
# three mtime prerequisites (flags.make, the source, compiler_depend.ts) and
# .DELETE_ON_ERROR at the top, exactly as cmake 3.31 emits.
python3 - <<'PY'
srcs=["scheduler_impl","options","raw2trace","main"]
L=["# CMAKE generated file: DO NOT EDIT!  (replica)","","# Delete rule output on recipe failure.",
   ".DELETE_ON_ERROR:","","CXX = /usr/bin/c++","OBJS = "+" ".join(f"obj/{s}.cpp.o" for s in srcs),"",
   "app: $(OBJS)","\t@echo \"Linking CXX executable app\"","\t$(CXX) $(OBJS) -o app",""]
for s in srcs:
    o=f"obj/{s}.cpp.o"
    L += [f"{o}: flags.make", f"{o}: {s}.cpp", f"{o}: compiler_depend.ts",
          f"\t@echo \"Building CXX object {o}\"",
          f"\t$(CXX) -O2 -std=c++17 -MD -MT {o} -MF {o}.d -o {o} -c {s}.cpp",""]
open("build.make","w").write("\n".join(L)+"\n")
PY

say() { printf '%-5s %s\n' "$1" "$2"; }
make -f build.make app >/dev/null 2>&1 && say E0 "baseline build OK: $(./app)"

n=$(make -f build.make app 2>&1 | grep -c '^Building CXX object'); say E1 "no-change rebuild: $n of 4 rebuilt => SKIPPED $((4-n)) of 4"

truncate -s 0 obj/scheduler_impl.cpp.o
make -f build.make -q obj/scheduler_impl.cpp.o; say E2 "truncated object: gmake -q rc=$? (0 = make says UP TO DATE)"
make -f build.make app >/tmp/e2.log 2>&1; say E2 "build after truncation: $(grep -c 'undefined reference' /tmp/e2.log) undefined-reference error(s)"

touch scheduler_impl.cpp
n=$(make -f build.make app 2>&1 | grep -c '^Building CXX object'); say E3 "touch source: $n of 4 rebuilt; app: $(./app)"

cat > del.make <<'EOF'
.DELETE_ON_ERROR:
obj/t1.o: victim.cpp
	: > $@ ; exit 1
obj/t2.o: victim.cpp
	: > $@ ; kill -9 $$$$
obj/t3.o: victim.cpp
	: > $@
EOF
for t in t1 t2 t3; do
  rm -f obj/$t.o; make -f del.make obj/$t.o >/tmp/del.log 2>&1
  [ -e obj/$t.o ] && r="SURVIVES $(stat -c%s obj/$t.o)B" || r="DELETED"
  say "E4$t" "$r  $(grep -io 'deleting file[^ ]*' /tmp/del.log | head -1)"
done

cat > slow.make <<'EOF'
.DELETE_ON_ERROR:
obj/naive.o: victim.cpp
	./slowcc -c victim.cpp -o obj/naive.o
obj/atomic.o: victim.cpp
	./cc-atomic ./slowcc -c victim.cpp -o obj/atomic.o
EOF
for t in naive atomic; do
  rm -f "obj/$t.o" obj/$t.o.tmp.*
  setsid --wait make -f slow.make "obj/$t.o" >/dev/null 2>&1 & mk=$!; sleep 3
  kill -9 -- -"$(ps -o pgid= -p $mk | tr -d ' ')" 2>/dev/null; wait $mk 2>/dev/null
  [ -e "obj/$t.o" ] && r="target SURVIVES $(stat -c%s obj/$t.o)B" || r="target ABSENT"
  make -f slow.make -q "obj/$t.o"; say "F3-$t" "$r; gmake -q rc=$? (0 = poisoned, 1 = will rebuild)"
  rm -f obj/$t.o.tmp.*
done

make -f build.make app >/dev/null 2>&1
truncate -s 0 obj/options.cpp.o
./verify-objects obj/*.cpp.o | tail -1 | sed 's/^/F1    /'
n=$(make -f build.make app 2>&1 | grep -c '^Building CXX object'); say F1 "after validator: $n of 4 rebuilt; app: $(./app)"
./verify-objects obj/*.cpp.o | tail -1 | sed 's/^/F2    /'
n=$(make -f build.make app 2>&1 | grep -c '^Building CXX object'); say F2 "no-change with validator: $n of 4 rebuilt => SKIPPED $((4-n)) of 4"
echo "workspace: $W"
