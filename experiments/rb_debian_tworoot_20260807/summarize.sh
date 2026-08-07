#!/usr/bin/env bash
# Derive the two-root verdict table from results.csv, so the numbers quoted in
# README.md are computed from the manifest rather than transcribed by hand.
#
# Three arms are reported, each against its OWN completed denominator so a
# package still in flight in one arm is never silently credited to another:
#
#   native                 -- the per-package control: does this build actually
#                             depend on the root at all?
#   hermit                 -- `hermit run --strict`. CONFOUNDED on a host whose
#                             PMU fails validation, because Hermit derives
#                             virtual time from retired-conditional-branch
#                             counts; see README.md.
#   hermit --no-rcb-time   -- the corrected arm, required on this host.
set -euo pipefail
CSV="${1:-$(dirname "${BASH_SOURCE[0]}")/results.csv}"
exec python3 - "$CSV" <<'ENDPY'
import csv, sys, collections, signal
signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # exit quietly under `| head`

rows = list(csv.DictReader(open(sys.argv[1])))
by = collections.defaultdict(dict)
ver = {}
for r in rows:
    by[r['package']][r['root']] = r['artifact_sha256']
    ver[r['package']] = r['source_version']

NAT = ('native-n1', 'native-n2')
HER = ('hermit-a', 'hermit-b')
NOR = ('hermit-norcb-a', 'hermit-norcb-b')


def verdict(h, keys):
    if not all(k in h for k in keys):
        return None
    return 'IDENTICAL' if h[keys[0]] == h[keys[1]] else 'DIVERGES'


print(f"{'package':<14}{'version':<16}{'native':<11}{'hermit':<11}{'hermit+--no-rcb-time':<22}")
n_tot = n_div = h_tot = h_id = r_tot = r_id = 0
for p in sorted(by):
    h = by[p]
    n, e, r = verdict(h, NAT), verdict(h, HER), verdict(h, NOR)
    if n is not None:
        n_tot += 1
        n_div += n == 'DIVERGES'
    if e is not None:
        h_tot += 1
        h_id += e == 'IDENTICAL'
    if r is not None:
        r_tot += 1
        r_id += r == 'IDENTICAL'
    print(f"{p:<14}{ver[p]:<16}{(n or 'pending'):<11}{(e or 'pending'):<11}{(r or 'pending'):<22}")


def ratio(a, b):
    return f"{a}/{b}" if b else "0/0"


print()
print(f"native control measured:              {n_tot} packages")
print(f"  native two-root DIVERGES:           {ratio(n_div, n_tot)}")
print(f"default hermit arm measured:          {h_tot} packages  [CONFOUNDED on a failed-PMU host]")
print(f"  hermit two-root IDENTICAL:          {ratio(h_id, h_tot)}")
print(f"corrected arm (--no-rcb-time):        {r_tot} packages")
print(f"  hermit+--no-rcb-time IDENTICAL:     {ratio(r_id, r_tot)}")

# The headline: only packages where the control fired AND the corrected arm ran.
cw = sum(1 for p in by
         if verdict(by[p], NAT) == 'DIVERGES' and verdict(by[p], NOR) == 'IDENTICAL')
cwd = sum(1 for p in by
          if verdict(by[p], NAT) is not None and verdict(by[p], NOR) is not None)
print(f"CONTROLLED WIN (native DIVERGES and hermit+--no-rcb-time IDENTICAL): {ratio(cw, cwd)}")
ENDPY
