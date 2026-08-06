#!/usr/bin/env python3
"""Reduce the raw per-region records to results.csv.

Every row states what it measured, not just a verdict. The three questions kept
separate on purpose, because they have different answers:

  determinism  -- does an arm agree with ITSELF across runs?
  content      -- does the guest's own allocated data match the ptrace reference?
  address      -- does it match AT THE SAME ADDRESS? (the other half of the
                  owner's prediction; a digest-only comparison cannot see this)
"""
import re
import sys
from pathlib import Path

REGION = re.compile(r"REGION (0x\w+)-(0x\w+) (\S+) size=(\d+) digest=(\w+)")
BACKENDS = ("ptrace", "sabre", "dbi")


def load(path):
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        m = REGION.match(line)
        if m:
            out[(m.group(1), int(m.group(4)))] = m.group(5)
    return out


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    arms = {b: (load(out / f"{b}_r1.txt"), load(out / f"{b}_r2.txt"), load(out / f"{b}_mut.txt"))
            for b in BACKENDS}

    ref = arms["ptrace"][0]
    if not ref:
        print("ERROR: no ptrace reference records -- NO RESULT, not a pass", file=sys.stderr)
        return 2
    # The guest's own allocations, identified from the reference arm by the two
    # workload shapes the probe creates (brk-served smalls, large mmaps).
    guest_digests = {d for (_, size), d in ref.items() if size in (270336, 33587200)}

    w = print
    w("backend,regions,total_bytes,unstable_regions,unstable_guest_regions,"
      "guest_regions,content_twins_of_ptrace,exact_addr_and_content_matches,"
      "ref_regions,mutation_detected,mutation_digest_matches_ptrace")

    ref_tuples = set(ref.items())
    ref_digests = set(ref.values())
    pt_mut = arms["ptrace"][2]
    pt_mut_guest = {d for (_, size), d in pt_mut.items() if size == 33587200}

    for b in BACKENDS:
        r1, r2, mut = arms[b]
        if not r1:
            w(f"{b},NO-RESULT,,,,,,,,,")
            continue
        keys = set(r1) | set(r2)
        unstable = [k for k in keys if r1.get(k) != r2.get(k)]
        unstable_guest = [k for k in unstable
                          if r1.get(k) in guest_digests or r2.get(k) in guest_digests]
        guest_regions = [k for k in r1 if r1[k] in guest_digests]
        twins = len(ref_digests & set(r1.values()))
        exact = len(ref_tuples & set(r1.items()))
        moved = [k for k in r1 if mut.get(k) != r1[k]]
        moved_guest = [k for k in moved if r1[k] in guest_digests]
        mut_dig = {mut[k] for k in moved_guest} if moved_guest else set()
        w(f"{b},{len(r1)},{sum(s for _, s in r1)},{len(unstable)},{len(unstable_guest)},"
          f"{len(guest_regions)},{twins},{exact},{len(ref)},"
          f"{'yes' if moved_guest else 'NO-VACUOUS'},"
          f"{'yes' if mut_dig and mut_dig == pt_mut_guest else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
