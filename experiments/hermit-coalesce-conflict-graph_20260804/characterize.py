import json
from collections import Counter
R=json.load(open('/tmp/result.json'))
idx=R['idx']; comps=R['components']; edges=set(tuple(e) for e in R['real_edges'])
def is_glue(path):
    if path.endswith('Cargo.lock') or path.endswith('Cargo.toml'): return True
    if path in ('tests/backend-parity/matrix.tsv','tests/backend-parity/README.md','ci/expected-e2e-plan.json','.gitignore'): return True
    if path.startswith('tests/e2e/manifests/'): return True
    if path.startswith('docs/') and path.endswith('COMPATIBILITY.md'): return True
    if path.startswith('.github/workflows/'): return True
    return False
def patch_paths(paths):
    # patching-backend source
    pk=[]
    for p in paths:
        if any(k in p for k in ['sabre','e9patch','dynamorio','/dbi','-dbi','liteinst','dbt']):
            pk.append(p)
    return pk

multi=[c for c in comps if len(c)>1]
for ci,c in enumerate(multi):
    # binding real files across the component
    fc=Counter()
    for n in c:
        for f in idx[str(n)]['real']:
            fc[f]+=1
    shared=[f for f,k in fc.items() if k>=2]
    print(f"=== COMPONENT {'A' if ci==0 else 'B'} (size {len(c)}) ===")
    print(f"  members: {c}")
    print(f"  top shared real-source files:")
    for f,k in fc.most_common(12):
        if k>=2: print(f"    {k:3d}  {f}{'  [PATCHING]' if patch_paths([f]) else ''}")

# patching membership across ALL prs
print("\n=== PATCHING-BACKEND SOURCE membership (all PRs) ===")
patch_prs={}
for n,v in idx.items():
    pk=patch_paths(v['real'])
    if pk: patch_prs[int(n)]=pk
print(f"  {len(patch_prs)} PRs touch patching-backend source:")
for n in sorted(patch_prs):
    cls=idx[str(n)]['base_class']
    print(f"    #{n} [{cls}] {idx[str(n)]['title'][:60]}")
    print(f"        {patch_prs[n]}")

# cross-ref codex-43
codex43=set([1221,1227,1229,1233,1235,1242,1243,1244,1245,1246,1247,1250,1252,1254,1275,1296,1303,1306,1308,1314,1316,1317,1318,1320,1323,1380,1393,1422,1464,1472,1473,1477,1491,1498,1515,1532,1543,1547,1551,1552,1555,1578,1579])
compB=set(multi[1]); compA=set(multi[0])
print(f"\n=== codex-43 vs verified components ===")
print(f"  codex-43 ∩ CompA(23): {sorted(codex43&compA)}")
print(f"  codex-43 ∩ CompB(15): {sorted(codex43&compB)}")
print(f"  codex-43 that are real-source SINGLETONS: {sorted(codex43&set(R['singletons']))}")
