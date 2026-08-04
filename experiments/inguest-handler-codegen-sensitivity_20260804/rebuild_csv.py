import csv, glob, os, re, hashlib
WARMUP=1
def field(txt, key):
    m = re.search(r'^\s*'+re.escape(key)+r'\s*:\s*([0-9.]+)', txt, re.M)
    return float(m.group(1)) if m else None
sha={}
for v,p in [("A","so/A.so"),("B","so/B.so"),("C","so/C.so"),("D","so/D.so")]:
    sha[v]=hashlib.sha256(open(p,'rb').read()).hexdigest()[:8]
rows=[]
for tf in sorted(glob.glob("raw/*.time")):
    b=os.path.basename(tf)
    m=re.match(r'(\w+)_N(\d+)_r(\d+)\.time$', b)
    if not m: continue
    variant,N,rep=m.group(1),int(m.group(2)),int(m.group(3))
    if rep<=WARMUP: continue
    txt=open(tf).read()
    u=field(txt,"User time (seconds)"); s=field(txt,"System time (seconds)")
    if u is None or s is None: 
        print("MISS",b); continue
    cpu=u+s
    dh=pi="-"
    if variant!="native":
        ef=tf.replace(".time",".err")
        if os.path.exists(ef):
            et=open(ef).read()
            line=[l for l in et.splitlines() if "backend run complete" in l]
            if line:
                dhm=re.search(r'direct_hook=(\d+)',line[0]); pim=re.search(r'ptrace_installation=(\d+)',line[0])
                dh=dhm.group(1) if dhm else "-"; pi=pim.group(1) if pim else "-"
    rows.append([variant,sha.get(variant,"-"),N,rep,0,f"{u:.2f}",f"{s:.2f}",f"{cpu:.4f}",dh,pi])
rows.sort(key=lambda r:(r[0],r[2],r[3]))
with open("results.csv","w",newline="") as f:
    w=csv.writer(f); w.writerow(["variant","so_sha8","N","rep","rc","user_s","sys_s","cpu_s","direct_hook","ptrace_installation"]); w.writerows(rows)
print("rebuilt",len(rows),"rows")
