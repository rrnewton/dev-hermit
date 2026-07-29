#!/usr/bin/env bash
set -euo pipefail

readonly WORK_DIR=${1:-/tmp/hermit-compat-make-parallel}
readonly SRC_DIR=$WORK_DIR/src
readonly BUILD_DIR=$WORK_DIR/build

rm -rf "$WORK_DIR"
mkdir -p "$SRC_DIR" "$BUILD_DIR"
trap 'rm -rf "$WORK_DIR"' EXIT

for number in 1 2 3 4; do
    cat >"$SRC_DIR/unit${number}.c" <<EOF
int unit${number}(void) { return ${number}; }
EOF
done

cat >"$SRC_DIR/main.c" <<'EOF'
#include <stdio.h>
int unit1(void);
int unit2(void);
int unit3(void);
int unit4(void);
int main(void) {
    int total = unit1() + unit2() + unit3() + unit4();
    printf("parallel-build=%d\n", total);
    return total != 10;
}
EOF

cat >"$WORK_DIR/Makefile" <<'EOF'
CC := /usr/bin/gcc
CFLAGS := -std=c11 -O1 -Wall -Werror -fno-ident -fno-stack-protector -fno-pie
OBJECTS := build/unit1.o build/unit2.o build/unit3.o build/unit4.o

.PHONY: all
all: build/program

build/%.o: src/%.c
	@printf 'CC %s\n' '$<'
	@$(CC) $(CFLAGS) -frandom-seed=$* -c $< -o $@

build/main.o: src/main.c
	@printf 'CC %s\n' '$<'
	@$(CC) $(CFLAGS) -frandom-seed=main -c $< -o $@

build/program: $(OBJECTS) build/main.o
	@printf 'LD %s\n' '$@'
	@$(CC) -no-pie -Wl,--build-id=none $^ -o $@
EOF

cd "$WORK_DIR"
/usr/bin/make --no-print-directory -j4
./build/program
sha256sum build/program | cut -d' ' -f1
