#!/usr/bin/env python3
"""Search a /proc/self/maps dump for the hole structure that produces d23=-8.

The ptrace/native spacing is caused by ONE specific arrangement: a 5-9 page free
gap sitting directly above a 2-4 page anonymous block (glibc's loader leaves a
7-page gap above a 3-page block). 1+2+3 pages fill the gap top-down leaving a
single free page, so the 4-page request cannot fit and skips below the anon
block -- landing 8 pages under ANON[2].

This makes the "could relocating a DBI mapping reproduce -8?" question decidable
by measurement instead of argument: if no such structure exists anywhere in the
backend's address space, then no relocation can produce it, because relocating a
mapping only chooses among the gaps that already exist.
"""
import sys


def vmas(path):
    out = []
    for line in open(path):
        parts = line.split()
        if not parts or "-" not in parts[0]:
            continue
        lo, hi = parts[0].split("-")
        out.append((int(lo, 16), int(hi, 16), parts[1],
                    parts[5] if len(parts) > 5 else "[anon]"))
    return sorted(out)


def report(path):
    vs = vmas(path)
    gaps = [((vs[i + 1][0] - vs[i][1]) // 4096, vs[i], vs[i + 1])
            for i in range(len(vs) - 1) if vs[i + 1][0] > vs[i][1]]
    sig = [(g, lo) for g, lo, _ in gaps
           if 5 <= g <= 9 and lo[3] == "[anon]" and 2 <= (lo[1] - lo[0]) // 4096 <= 4]
    print(f"{path}: {len(vs)} VMAs, {len(gaps)} gaps, "
          f"{len(sig)} glibc-signature holes")
    for g, lo in sig:
        print(f"   {g}-page gap directly above a {(lo[1]-lo[0])//4096}-page anon "
              f"block at {lo[0]:012x}")


for p in sys.argv[1:]:
    report(p)
