#!/usr/bin/env python3
"""Quantify demo05 INFO-log divergence at four normalization levels.

The demo's own comparator (demo_common.hermit_log_diff) reports only the FIRST
divergence and treats the whole comparison as non-fatal. That is not enough to fix
the capture: to know whether the golden is stable you need the COUNT and the CLASS
of differing lines, and you need to know which normalizations are actually load
bearing versus which are masking a real input that should just be pinned.

Levels (each strictly weaker than the last):
  L0 raw
  L1 wallclock prefix stripped            <- legitimate: no deterministic content
  L2 L1 + FileContents(<host inode>)      <- RELAXATION: host-physical id
  L3 L2 + 0x7f... guest addresses masked  <- RELAXATION: justified in-code by unpinned launcher env
"""
import re
import sys
import collections

WALL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z[ \t]+")
INODE = re.compile(r"FileContents\(\d+\)")
ADDR = re.compile(r"0x7f[0-9a-f]{6,}")


def lvl(line, level):
    if level >= 1:
        line = WALL.sub("", line)
    if level >= 2:
        line = INODE.sub("FileContents(<inode>)", line)
    if level >= 3:
        line = ADDR.sub("0x<addr>", line)
    return line


def main():
    a_path, b_path = sys.argv[1], sys.argv[2]
    counts = collections.Counter()
    first = {}
    classes = collections.Counter()
    n = 0
    with open(a_path, errors="replace") as fa, open(b_path, errors="replace") as fb:
        for n, (la, lb) in enumerate(zip(fa, fb), 1):
            la = la.rstrip("\n")
            lb = lb.rstrip("\n")
            for level in (0, 1, 2, 3):
                na, nb = lvl(la, level), lvl(lb, level)
                if na != nb:
                    counts[level] += 1
                    first.setdefault(level, (n, na, nb))
                    if level == 3:
                        # classify the residue that survives every normalization
                        key = "OTHER"
                        for probe in ("FileContents", "mmap", "brk", "openat", "read(",
                                      "stat", "COMMIT turn", "SleepUntil", "rip=",
                                      "futex", "clock_", "getrandom", "write("):
                            if probe in na:
                                key = probe
                                break
                        classes[key] += 1
    print(f"lines compared: {n}")
    for level in (0, 1, 2, 3):
        c = counts[level]
        pct = 100.0 * c / n if n else 0.0
        print(f"  L{level}: {c:>9,} differing lines ({pct:.4f}%)")
    for level in (1, 2, 3):
        if level in first:
            ln, na, nb = first[level]
            print(f"\nfirst L{level} divergence, line {ln}:\n  - {na[:200]}\n  + {nb[:200]}")
    if classes:
        print("\nresidue classes surviving L3:")
        for k, v in classes.most_common(12):
            print(f"  {v:>8,}  {k}")


if __name__ == "__main__":
    main()
