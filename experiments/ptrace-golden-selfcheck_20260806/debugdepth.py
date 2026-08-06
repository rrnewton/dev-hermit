#!/usr/bin/env python3
"""Is the ptrace reference self-identical at DEBUG depth once HOST-ENVIRONMENT
facts are canonicalized?

The INFO-depth answer is already "yes" (see README.md). The next deepening of the
ratchet is full-trace/DEBUG, and a naive DEBUG comparison reports the reference as
DIVERGENT against itself. This script establishes *why*, and whether the residue is
guest nondeterminism (which would disqualify DEBUG as a comparison depth) or merely
host metadata the log happens to print (which would not).

Four canonicalizations, each targeting a HOST-environment fact, not guest behaviour:

  1. wall clock   -- "Nondeterministic realtime elapsed: 21.072463ms". Hermit
                     self-labels this nondeterministic.
  2. host core    -- x2apic_id / core_id inside CpuId{..}: which physical core the
                     host scheduler happened to pick.
  3. hex addrs    -- host-side vdso patch targets (@7f12f5346fe0). Host ASLR.
  4. decimal addrs-- inject SyscallArgs { arg1: 94011568533984, .. }. The SAME host
                     ASLR, rendered in decimal. A hex-only canonicalizer misses these.

Addresses are replaced by ORDINAL OF FIRST APPEARANCE, not by a constant, so the
comparison still fails if the two runs use a *different pattern* of addresses (e.g.
one run reuses an address where the other does not). Blanket-masking would hide real
divergence; ordinal-mapping preserves structure.

Usage: debugdepth.py <binary> <pairs> [-- extra hermit run args]
Exit 0 iff every pair is byte-identical after canonicalization.
"""
import re, subprocess, sys, os, tempfile, collections

TS = re.compile(r'^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*')
WALL = re.compile(r'Nondeterministic realtime elapsed:')
# host-CPU identity: which core/CCX the host scheduler picked. max_cores_for_cache
# is cache topology and varies with the AMD chiplet the process lands on.
CORE = re.compile(r'\b(\w*apic_id|core_id|max_cores_for_cache):\s*\d+')
HEX = re.compile(r'\b(?:0x)?[0-9a-f]{8,16}\b')
DEC = re.compile(r'\b\d{10,20}\b')


def canon(path):
    """Return canonicalized lines; address ordinals are assigned per-file in order."""
    ordinals = {}

    def ordinal(tok):
        if tok not in ordinals:
            ordinals[tok] = f'<A{len(ordinals)}>'
        return ordinals[tok]

    pre = []
    for line in open(path, errors='replace'):
        line = TS.sub('', line.rstrip('\n'))
        if WALL.search(line):
            continue                                  # (1) wall clock
        line = CORE.sub(lambda m: f'{m.group(1)}: <CORE>', line)   # (2) host core
        pre.append(line)

    # (5) MUST come before ordinal assignment: the vdso permutation changes the
    # order in which addresses are first seen, so ordinals assigned first would
    # themselves be permuted and the sort could never converge.
    pre = sort_vdso_blocks(pre)

    out = []
    for line in pre:
        line = HEX.sub(lambda m: ordinal(m.group(0)), line)        # (3) hex ASLR
        line = DEC.sub(lambda m: ordinal(m.group(0)), line)        # (4) decimal ASLR
        out.append(line)
    return out


VDSO = re.compile(r'reverie_ptrace::vdso: \d+ patched ')


def sort_vdso_blocks(lines):
    """(5) vdso patch ORDER varies run-to-run.

    The same symbols (__vdso_time, __vdso_getcpu, __vdso_gettimeofday, ...) are
    patched in a different sequence each run -- the signature of iteration over a
    randomly-seeded Rust HashMap/HashSet. This is NOT an address-rendering problem
    and no address canonicalizer can remove it, so a deepening that only fixes the
    decimal-address issue will still see the reference diverge from itself.

    Sorting each contiguous run of vdso patch lines cancels the permutation while
    still failing if the two runs patch a DIFFERENT SET of symbols. The sort key is
    the SYMBOL NAME, not the whole line: the line still carries a run-varying
    address, so a whole-line sort would not converge either.
    """
    key = lambda l: l.split(' patched ', 1)[1].split('@')[0]
    out, block = [], []
    for line in lines:
        if VDSO.search(line):
            block.append(line)
            continue
        if block:
            out.extend(sorted(block, key=key)); block = []
        out.append(line)
    if block:
        out.extend(sorted(block, key=key))
    return out


def main():
    argv = sys.argv[1:]
    extra = []
    if '--' in argv:
        i = argv.index('--')
        extra = argv[i + 1:]
        argv = argv[:i]
    binary, pairs = argv[0], int(argv[1])

    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = env.get('LD_LIBRARY_PATH', '') + \
        ':/home/newton/.local/hermit-deps/lu/usr/lib64'

    d = tempfile.mkdtemp(dir='/home/newton/work/dev-hermit/ignored/w2-selfcheck-deepen')
    raw_bad = canon_bad = 0
    classes = collections.Counter()

    for i in range(pairs):
        logs = []
        for side in ('a', 'b'):
            p = os.path.join(d, f'{i}-{side}.log')
            subprocess.run([binary, '--log', 'debug', '--log-file', p, 'run',
                            *extra, '--strict', '--', '/bin/true'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=env, timeout=180)
            logs.append(p)

        # raw (timestamp-stripped only) -- what a naive deepening would compare
        ra = [TS.sub('', l.rstrip('\n')) for l in open(logs[0], errors='replace')]
        rb = [TS.sub('', l.rstrip('\n')) for l in open(logs[1], errors='replace')]
        if ra != rb:
            raw_bad += 1
            for x, y in zip(ra, rb):
                if x == y:
                    continue
                if WALL.search(x):            classes['wall-clock'] += 1
                elif 'precise_ip' in x:       classes['host-core-id'] += 1
                elif 'vdso' in x:             classes['hex-addr-vdso'] += 1
                elif 'SyscallArgs' in x:      classes['decimal-addr-inject'] += 1
                else:                         classes['OTHER:' + x[:70]] += 1

        ca, cb = canon(logs[0]), canon(logs[1])
        if ca != cb:
            canon_bad += 1
            for x, y in zip(ca, cb):
                if x != y:
                    classes['RESIDUE:' + x[:90]] += 1
                    break
        for p in logs:
            os.unlink(p)

    print(f'pairs={pairs}')
    print(f'  DIVERGENT raw (timestamp-strip only) : {raw_bad}/{pairs}')
    print(f'  DIVERGENT after canonicalization     : {canon_bad}/{pairs}')
    print('  differing-line classes (raw):')
    for k, v in classes.most_common():
        print(f'    {v:6d}  {k}')
    os.rmdir(d)
    return 0 if canon_bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
