#!/usr/bin/env python3
"""Classify the per-line divergences between a --verify double-run's two retained logs.

Input: two `/tmp/run{1,2}_log_*` files retained by `hermit run --verify` on a
divergence. Strips the real wall-clock prefix (the one thing the Canonical
comparator also strips) and then compares line-for-line, bucketing each
differing line into the divergence classes C1..C6 defined in README.md.

Usage: classify.py <run1.log> <run2.log> [label]
Prints one CSV row plus, with -v, the character-level edit regions.
"""

import difflib
import re
import sys
from collections import Counter

TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")


def load(path):
    with open(path, errors="replace") as fh:
        return [TS.sub("", line.rstrip("\n")) for line in fh]


def classify(line):
    if "reverie_ptrace::timer" in line and "CpuId" in line:
        return "C1"  # tracer reads host CPUID: initial_local_apic_id / x2apic_id
    if "reverie_ptrace::vdso" in line and "patched __vdso_" in line:
        return "C2"  # VDSO_PATCH_INFO HashMap iteration order
    if "beginning inject of syscall" in line:
        return "C3"  # tracer-process heap pointers in the inject trace
    if "DETLOG (pre) registers" in line or "DETLOG (post) registers" in line:
        return "C4"  # guest rbx = CPUID leaf-1 EBX = host APIC id
    if "Nondeterministic realtime elapsed" in line:
        return "C5"  # producer-declared nondeterministic wall clock
    return "C6"  # everything else -- the planted-defect channel


def main():
    a, b = load(sys.argv[1]), load(sys.argv[2])
    label = sys.argv[3] if len(sys.argv) > 3 else sys.argv[1]
    verbose = "-v" in sys.argv

    counts = Counter()
    by_level = Counter()
    info_total = sum(1 for line in a if line.startswith("INFO"))
    info_diff = 0

    for i, (x, y) in enumerate(zip(a, b)):
        if x == y:
            continue
        kind = classify(x)
        counts[kind] += 1
        by_level[x.split()[0] if x.split() else "?"] += 1
        if x.startswith("INFO"):
            info_diff += 1
        if verbose:
            matcher = difflib.SequenceMatcher(None, x, y, autojunk=False)
            edits = [op for op in matcher.get_opcodes() if op[0] != "equal"]
            print(f"  line {i} [{kind}] {x[:100]}")
            for tag, i1, i2, j1, j2 in edits[:4]:
                print(f"      [{tag}] L={x[i1:i2]!r} R={y[j1:j2]!r}")

    total = sum(counts.values())
    verdict = "EQUAL" if info_diff == 0 else "UNEQUAL"
    print(
        f"{label},{len(a)},{len(b)},{total},"
        + ",".join(str(counts[k]) for k in ("C1", "C2", "C3", "C4", "C5", "C6"))
        + f",{info_total},{info_diff},{verdict}"
    )
    if verbose:
        print(f"# differing lines by log level: {dict(by_level)}", file=sys.stderr)


if __name__ == "__main__":
    main()
