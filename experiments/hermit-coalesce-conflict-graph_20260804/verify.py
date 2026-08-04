import json,subprocess
REPO='/home/newton/work/dev-hermit/hermit'
MAIN='b384187efd725c504d69281f043d442325d4fcb2'
idx=json.load(open('/tmp/pr_index.json'))
def is_glue(path):
    if path.endswith('Cargo.lock') or path.endswith('Cargo.toml'): return True
    if path in ('tests/backend-parity/matrix.tsv','tests/backend-parity/README.md',
                'ci/expected-e2e-plan.json','.gitignore'): return True
    if path.startswith('tests/e2e/manifests/'): return True
    if path.startswith('docs/') and path.endswith('COMPATIBILITY.md'): return True
    if path.startswith('.github/workflows/'): return True
    return False
def mt(a,b):
    r=subprocess.run(['git','merge-tree','--write-tree','--name-only',a,b],cwd=REPO,capture_output=True,text=True)
    if r.returncode==0: return []  # clean
    lines=r.stdout.split('\n')
    paths=[]
    for ln in lines[1:]:
        if ln.strip()=='': break
        paths.append(ln.strip())
    return paths

# 1) base-mergeability vs main
base={}
for n,v in idx.items():
    c=mt(MAIN,v['head'])
    real_c=[p for p in c if not is_glue(p)]
    glue_c=[p for p in c if is_glue(p)]
    if not c: base[n]='CLEAN'
    elif not real_c: base[n]='GLUE-ONLY'
    else: base[n]='SOURCE-CONFLICT'
    idx[n]['base_conflicts_real']=real_c
    idx[n]['base_class']=base[n]
from collections import Counter
print('=== BASE-MERGEABILITY vs main (glue-filtered) ===')
print(Counter(base.values()))

# 2) verify pairwise real-source edges
real_edges=set()
for line in open('/tmp/real_edges.txt'):
    a,b,ha,hb,rest=line.split(' ',4)
    c=mt(ha,hb)
    real_c=[p for p in c if not is_glue(p)]
    if real_c:
        real_edges.add((int(a),int(b)))
print(f'\n=== PAIRWISE: {len(real_edges)} REAL-conflict edges (of 218 file-overlap edges) ===')

# recompute components over verified real edges
nodes=[int(n) for n in idx]
parent={n:n for n in nodes}
def find(x):
    while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
    return x
for a,b in real_edges:
    ra,rb=find(a),find(b)
    if ra!=rb: parent[ra]=rb
comps={}
for n in nodes: comps.setdefault(find(n),[]).append(n)
comps=sorted([sorted(v) for v in comps.values()],key=len,reverse=True)
print('=== VERIFIED REAL-CONFLICT COMPONENTS ===')
multi=[c for c in comps if len(c)>1]
sing=[c[0] for c in comps if len(c)==1]
for c in multi:
    print(f'  size {len(c):3d}: {c}')
print(f'  singletons (conflict with NOTHING on real source): {len(sing)}')
print(f'  -> {sorted(sing)}')
json.dump({'base':base,'real_edges':[list(e) for e in sorted(real_edges)],
           'components':comps,'singletons':sing,'idx':idx},open('/tmp/result.json','w'))
