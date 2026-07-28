# Compiler-free build: generate -> transform -> aggregate -> checksum.
# Proves the make *driver* (fork+exec of recipe shells) determinizes.
.PHONY: all clean
all: report.txt

gen_%.dat:
	seq 1 100 | awk -v s=$* '{print ($$1*s)%97}' > $@

combined.dat: gen_3.dat gen_7.dat gen_11.dat
	cat $^ | sort -n | uniq -c | sort -rn > $@

report.txt: combined.dat
	{ echo "== build report =="; wc -l < combined.dat | awk '{print "lines:",$$1}'; \
	  md5sum combined.dat; head -5 combined.dat; } > $@

clean:
	rm -f gen_*.dat combined.dat report.txt
