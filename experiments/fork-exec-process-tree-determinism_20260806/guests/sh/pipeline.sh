#!/bin/sh
# Shell pipeline: sh forks one process per stage; the STAGE PROCESSES ARE
# CONCURRENT, so their creation/exit/reap interleaving is the ordering probe.
echo "abcabcabc" | tr a X | tr b Y | sort | uniq | wc -c
echo "second" | cat | cat | cat
