#!/usr/bin/env python3
"""Honest DBI B3 sweep: run every c-programs verify cell under ptrace and DBI,
strict --verify (bitwise determinism). Denominator = ptrace-passing set."""
import tomllib, pathlib, subprocess, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path.cwd()
OUT = ROOT / "ignored/dbi-b3-sweep_20260803"
HERMIT = str(ROOT / "target/release/hermit")
TIMEOUT = 100

data = tomllib.loads((ROOT/"tests/e2e/manifests/c-programs.toml").read_text())
tests = data["test"]
def en(t, be):
    return be in t.get("modes",{}).get("verify",{}).get("backends_enabled",[])

work = []
for t in tests:
    tid = t["id"]
    work.append((tid, "ptrace", en(t,"ptrace")))
    work.append((tid, "dbi", en(t,"dbi")))

def run_one(tid, be, enabled):
    flag = "--include-manual" if enabled else "--probe-disabled"
    cmd = [ "ci/test_harness.sh","run","--test",tid,"--mode","verify",
            "--backend",be, flag,
            "--results", str(OUT/f"{tid.replace('/','_')}.{be}.jsonl") ]
    t0=time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                           env={**__import__('os').environ, "HERMIT_BIN":HERMIT})
        out = p.stdout + p.stderr
        verdict = "UNKNOWN"
        for line in p.stdout.splitlines():
            ls=line.strip()
            if ls.startswith(("PASS","FAIL","SKIP","ERROR")):
                verdict = ls.split()[0]; break
        if verdict=="UNKNOWN" and p.returncode!=0:
            verdict="FAIL"
    except subprocess.TimeoutExpired:
        verdict="HANG"; out="timeout"
    dt=time.time()-t0
    (OUT/f"{tid.replace('/','_')}.{be}.log").write_text(out[-4000:])
    return (tid, be, enabled, verdict, round(dt,1))

results={}
started=time.time()
with ThreadPoolExecutor(max_workers=8) as ex:
    futs=[ex.submit(run_one,*w) for w in work]
    done=0
    for f in as_completed(futs):
        tid,be,enabled,verdict,dt=f.result()
        results.setdefault(tid,{})[be]=(verdict,enabled,dt)
        done+=1
        if done%20==0:
            print(f"{done}/{len(work)} ({time.time()-started:.0f}s)",flush=True)

# write CSV
rows=["id,ptrace,dbi,dbi_manifest_enabled,dbi_sec"]
for tid in sorted(results):
    r=results[tid]
    pv=r.get("ptrace",("NA",False,0))[0]
    dv,den,dsec=r.get("dbi",("NA",False,0))
    rows.append(f"{tid},{pv},{dv},{den},{dsec}")
(OUT/"results.csv").write_text("\n".join(rows)+"\n")

# summary
ptrace_pass=[t for t in results if results[t].get("ptrace",("",))[0]=="PASS"]
dbi_pass=[t for t in results if results[t].get("dbi",("",))[0]=="PASS"]
dbi_pass_of_ptrace=[t for t in ptrace_pass if results[t].get("dbi",("",))[0]=="PASS"]
summary={
 "total":len(results),
 "ptrace_pass":len(ptrace_pass),
 "dbi_pass":len(dbi_pass),
 "dbi_pass_within_ptrace_corpus":len(dbi_pass_of_ptrace),
 "b3_ratio": round(len(dbi_pass_of_ptrace)/max(1,len(ptrace_pass)),3),
 "elapsed_sec": round(time.time()-started,1),
}
(OUT/"summary.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
