#!/bin/sh
# make driving a REAL gcc compile+link of a multi-file C program, in private /tmp.
set -e
B=/tmp/mkgcc; rm -rf "$B"; mkdir -p "$B"; cd "$B"
cat > util.c <<'C'
int sq(int x){return x*x;}
C
cat > util.h <<'C'
int sq(int);
C
cat > main.c <<'C'
#include <stdio.h>
#include "util.h"
int main(void){int s=0;for(int i=1;i<=10;i++)s+=sq(i);printf("sum=%d\n",s);return 0;}
C
cat > Makefile <<'MK'
CFLAGS=-O2
prog: main.o util.o
	$(CC) $(CFLAGS) -o $@ main.o util.o
main.o: main.c util.h
	$(CC) $(CFLAGS) -c -o $@ main.c
util.o: util.c util.h
	$(CC) $(CFLAGS) -c -o $@ util.c
MK
make -s CC=gcc
echo "=== RUN ARTIFACT ==="
./prog
md5sum prog | awk '{print "prog_md5:",$1}'
