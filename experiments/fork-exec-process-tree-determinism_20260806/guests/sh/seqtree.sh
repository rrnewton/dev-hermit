#!/bin/sh
# `make -j1`-shaped SERIAL subprocess tree: one child at a time, each exec'ing a
# real binary, reaped before the next is forked. Contrast with pipeline.sh: this
# should be trivially ordered, so a divergence here is a much sharper defect.
i=0
while [ $i -lt 6 ]; do
  /bin/echo "step $i"
  i=$((i+1))
done
/bin/true
/bin/false || echo "false rc=$?"
