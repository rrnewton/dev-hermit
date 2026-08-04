import json, re
d=json.load(open('/tmp/hermit_prs_full.json'))
main=[p for p in d if p['baseRefName']=='main']
stacked=[p for p in d if p['baseRefName']!='main']

def is_glue(path):
    if path.endswith('Cargo.lock') or path.endswith('Cargo.toml'): return True
    if path in ('tests/backend-parity/matrix.tsv','tests/backend-parity/README.md',
                'ci/expected-e2e-plan.json','.gitignore'): return True
    if path.startswith('tests/e2e/manifests/'): return True   # *.toml + inventory/*.json
    if path.startswith('docs/') and path.endswith('COMPATIBILITY.md'): return True
    if path.startswith('.github/workflows/'): return True
    return False

prs={p['number']:p for p in main}
files={p['number']:set(f['path'] for f in p['files']) for p in main}
real={n:{f for f in fs if not is_glue(f)} for n,fs in files.items()}

# union-find over a given file->set mapping
def components(fmap):
    parent={n:n for n in fmap}
    def find(x):
        while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
        return x
    def union(a,b):
        ra,rb=find(a),find(b)
        if ra!=rb: parent[ra]=rb
    # build file->prs index
    idx={}
    for n,fs in fmap.items():
        for f in fs: idx.setdefault(f,[]).append(n)
    edges={}
    for f,ns in idx.items():
        if len(ns)>1:
            for i in range(len(ns)):
                for j in range(i+1,len(ns)):
                    a,b=ns[i],ns[j]; union(a,b)
                    key=tuple(sorted((a,b))); edges.setdefault(key,set()).add(f)
    comps={}
    for n in fmap: comps.setdefault(find(n),[]).append(n)
    return sorted([sorted(v) for v in comps.values()], key=len, reverse=True), edges

print("=== ALL-FILES file-overlap (incl. glue) ===")
comps_all,_=components(files)
for c in comps_all:
    if len(c)>1: print(f"  size {len(c):3d}: {c[:8]}{'...' if len(c)>8 else ''}")
sing_all=[c[0] for c in comps_all if len(c)==1]
print(f"  singletons: {len(sing_all)}")

print("\n=== REAL-SOURCE file-overlap (glue excluded) ===")
comps_real,edges_real=components(real)
for c in comps_real:
    if len(c)>1:
        # show which files bind them
        binders=set()
        for k,fs in edges_real.items():
            if k[0] in c and k[1] in c: binders|=fs
        print(f"  size {len(c):3d}: {sorted(c)}")
        print(f"        bound by: {sorted(binders)}")
sing_real=[c[0] for c in comps_real if len(c)==1]
# PRs with NO real-source files at all (pure glue PRs)
pure_glue=[n for n in real if not real[n]]
print(f"  singletons (real-source): {len(sing_real)}")
print(f"  of which PURE-GLUE (no source files at all): {len(pure_glue)} -> {sorted(pure_glue)}")

# codex 43-disjoint list
codex43=[1221,1227,1229,1233,1235,1242,1243,1244,1245,1246,1247,1250,1252,1254,1275,1296,1303,1306,1308,1314,1316,1317,1318,1320,1323,1380,1393,1422,1464,1472,1473,1477,1491,1498,1515,1532,1543,1547,1551,1552,1555,1578,1579]
openset=set(prs)
print(f"\n=== codex-43 reconciliation ===")
print(f"  of 43, still open: {len([x for x in codex43 if x in openset])}; closed since: {sorted(set(codex43)-openset)}")
print(f"\nstacked (non-main base):", [(p['number'],p['baseRefName']) for p in stacked])

# Emit the real-source-sharing edge list (pairs to verify with merge-tree)
import sys
with open('/tmp/real_edges.txt','w') as fh:
    for (a,b),fs in sorted(edges_real.items()):
        fh.write(f"{a} {b} {prs[a]['headRefOid']} {prs[b]['headRefOid']} {';'.join(sorted(fs))}\n")
print(f"\nreal-source-sharing edges to verify: {len(edges_real)}")
# also dump glue-set predicate results per PR for later + head oids
json.dump({str(n):{'head':prs[n]['headRefOid'],'real':sorted(real[n]),'glue':sorted(files[n]-real[n]),
                   'mergeable':prs[n]['mergeable'],'draft':prs[n]['isDraft'],'title':prs[n]['title'],
                   'labels':[l['name'] for l in prs[n]['labels']]} for n in prs},
          open('/tmp/pr_index.json','w'))
