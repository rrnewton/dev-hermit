#!/usr/bin/env python3
"""Score one retained INFO log pair under BOTH selections.

The shipped comparator selects INFO from `all_a` (detcore/src/logdiff.rs:790 on
hermit main 0041130c). Hermit PR #1692 -- OPEN and UNMERGED -- would select from
`detcore_a` instead. Both selections are replicated here from the source
predicates, so the difference between them IS the artefact, computable offline
without waiting for the PR to land.
"""
import re, sys
IS_DETCORE = re.compile(r'^(ERROR|WARN|INFO|DEBUG|TRACE).* detcore:')  # logdiff.rs:381

def strip_prefix(line):
    """Emulate extract_log_messages: drop only the real wall-clock prefix."""
    return re.sub(r'^\s*\S*\d{2}:\d{2}:\d{2}\S*\s+', '', line.rstrip('\n'))

def load(path):
    return [strip_prefix(l) for l in open(path, errors='replace')]

shipped = lambda v: [l for l in v if l.startswith('INFO ')]
fixed   = lambda v: [l for l in v if IS_DETCORE.match(l) and l.startswith('INFO ')]

def divergent(A, B):
    return sum(1 for x, y in zip(A, B) if x != y) + abs(len(A) - len(B))

a, b = load(sys.argv[1]), load(sys.argv[2])
for name, sel in (('SHIPPED  filter_infos(&all_a)', shipped),
                  ('PR#1692  filter_infos(&detcore_a)', fixed)):
    A, B = sel(a), sel(b)
    print(f'{name:36} left={len(A):4} right={len(B):4} divergent={divergent(A,B)}')
A, F = shipped(a), set(fixed(a))
extra = [l for l in A if l not in F]
print(f'\nnon-guest INFO lines admitted by the shipped selection: {len(extra)} of {len(A)}')
for l in dict.fromkeys(extra):
    print('   ', l)
