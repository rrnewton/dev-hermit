import sys, heapq, os

HERE = os.path.dirname(os.path.abspath(__file__))

def load(path):
    nodes={}
    for line in open(path):
        line=line.rstrip("\n")
        if not line: continue
        tag,est,cls,deps,res=line.split("|")
        deps=[d for d in deps.split(",") if d]
        r={}
        for kv in res.split(","):
            if kv:
                k,v=kv.split(":"); r[k]=int(v)
        nodes[tag]={"est":float(est),"cls":cls,"deps":deps,"res":r}
    return nodes

def critical_path(nodes):
    # longest path by est (finish time), returns (length, path)
    memo={}
    def f(t):
        if t in memo: return memo[t]
        best=(0,[])
        for d in nodes[t]["deps"]:
            l,p=f(d)
            if l>best[0]: best=(l,p)
        res=(best[0]+nodes[t]["est"], best[1]+[t])
        memo[t]=res
        return res
    overall=(0,[])
    for t in nodes:
        l,p=f(t)
        if l>overall[0]: overall=(l,p)
    return overall

def simulate(nodes, J, caps, respect_res=True):
    # list scheduling: greedy, honor deps + resource caps + J workers
    indeg={t:set(nodes[t]["deps"]) for t in nodes}
    done=set(); running=[]  # heap of (finish_time, tag)
    t=0.0; used_res={k:0 for k in caps}; free=J
    order=[]
    def ready():
        return [x for x in nodes if x not in done and x not in [r[1] for r in running] and indeg[x]<=done]
    remaining=set(nodes)
    while remaining:
        progressed=True
        while progressed:
            progressed=False
            rl=sorted(ready(), key=lambda x:-nodes[x]["est"])
            for x in rl:
                if free<=0: break
                r=nodes[x]["res"]
                ok=True
                if respect_res:
                    for k,v in r.items():
                        if used_res.get(k,0)+v>caps.get(k,10**9): ok=False;break
                if not ok: continue
                free-=1
                if respect_res:
                    for k,v in r.items(): used_res[k]=used_res.get(k,0)+v
                heapq.heappush(running,(t+nodes[x]["est"],x))
                remaining.discard(x)
                progressed=True
        if not running:
            # deadlock/nothing ready but remaining -> resource wait; advance impossible
            break
        ft,x=heapq.heappop(running)
        t=ft; done.add(x); free+=1
        r=nodes[x]["res"]
        if respect_res:
            for k,v in r.items(): used_res[k]-=v
        order.append((round(ft,1),x))
    return t

for name in ["portable","privileged"]:
    nodes=load(os.path.join(HERE, f"{name}_nodes.txt"))
    total=sum(n["est"] for n in nodes.values())
    cl,cp=critical_path(nodes)
    caps={"hermit_guest":1,"manifest_guest":4}
    print(f"\n===== {name}  ({len(nodes)} nodes) =====")
    print(f"total work (sum est_s)     : {total:.0f}s")
    print(f"critical path length       : {cl:.0f}s")
    print(f"  path: {' -> '.join(cp)}")
    print(f"theoretical max speedup    : {total/cl:.2f}x  (unlimited workers, NO resource caps)")
    for J in [1,2,4,8,16,32,64]:
        mk_nores=simulate(nodes,J,caps,respect_res=False)
        mk_res=simulate(nodes,J,caps,respect_res=True)
        print(f"  -j{J:<3} makespan: no-caps={mk_nores:6.0f}s (par {total/mk_nores:4.2f}x) | with-caps={mk_res:6.0f}s (par {total/mk_res:4.2f}x)")
    # resource lower bound
    hg=sum(n["est"] for n in nodes.values() if "hermit_guest" in n["res"])
    mg=sum(n["est"] for n in nodes.values() if "manifest_guest" in n["res"])
    print(f"hermit_guest serial floor (cap=1): {hg:.0f}s   manifest_guest floor (cap=4): {mg/4:.0f}s")

print("\n\n##### SENSITIVITY: portable, raising hermit_guest cap (manifest_guest=4 fixed) #####")
nodes=load(os.path.join(HERE, "portable_nodes.txt"))
total=sum(n["est"] for n in nodes.values())
for hg in [1,2,4,8,16]:
    caps={"hermit_guest":hg,"manifest_guest":4}
    row=[]
    for J in [2,4,8,16,32]:
        mk=simulate(nodes,J,caps,respect_res=True)
        row.append(f"j{J}={mk:.0f}s/{total/mk:.2f}x")
    print(f"hermit_guest={hg:<2}: "+"  ".join(row))
