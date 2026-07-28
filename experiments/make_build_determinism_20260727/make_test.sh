#!/bin/sh
# Self-contained make-driven build, entirely inside the private /tmp so each
# hermit run starts pristine. Compiler-free (coreutils recipes) => isolates the
# make *driver's* multi-process determinism (fork+exec of recipe shells) from
# compiler vfork nondeterminism.
set -e
B=/tmp/mk-build
rm -rf "$B"; mkdir -p "$B"; cd "$B"
cat > Makefile <<'MK'
.PHONY: all
all: report.txt
gen_%.dat:
	seq 1 100 | awk -v s=$* '{print ($$1*s)%97}' > $@
combined.dat: gen_3.dat gen_7.dat gen_11.dat
	cat $^ | sort -n | uniq -c | sort -rn > $@
report.txt: combined.dat
	{ echo "== build report =="; wc -l < combined.dat | awk '{print "lines:",$$1}'; \
	  md5sum combined.dat; head -5 combined.dat; } > $@
MK
make -s
echo "=== ARTIFACT ==="
cat report.txt
