#!/usr/bin/env python3
"""Bounded KVM probe. KVM ignores SIGTERM, so reap by PGID with SIGKILL."""
import os, signal, subprocess, sys, time
B="/home/newton/work/dev-hermit/worktrees/audit/hermit/target/release/hermit"
env=dict(os.environ, LD_LIBRARY_PATH="/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib")
def probe(args, secs):
    cmd=[B]+args
    t0=time.time()
    p=subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       start_new_session=True, env=env)
    try:
        out,err=p.communicate(timeout=secs)
        return ("EXITED", p.returncode, time.time()-t0, out, err)
    except subprocess.TimeoutExpired:
        pgid=os.getpgid(p.pid)
        # SIGTERM first purely to record that it is ignored, then SIGKILL.
        os.killpg(pgid, signal.SIGTERM); time.sleep(3)
        survived = p.poll() is None
        os.killpg(pgid, signal.SIGKILL)
        try: out,err=p.communicate(timeout=15)
        except Exception: out,err=b"",b""
        return ("HUNG(SIGTERM_ignored=%s)"%survived, None, time.time()-t0, out, err)
for label,args,secs in [
    ("kvm non-strict /bin/echo", ["run","--backend","kvm","--base-env=minimal","--","/bin/echo","hi"], 45),
    ("kvm --strict /bin/echo",   ["run","--backend","kvm","--strict","--base-env=minimal","--","/bin/echo","hi"], 45),
]:
    st,rc,dt,out,err=probe(args,secs)
    print(f"{label:28} -> {st} rc={rc} wall={dt:.1f}s stdout={out[:40]!r}")
    tail=[l for l in err.decode(errors='replace').split('\n') if l.strip()][-2:]
    for l in tail: print(f"    | {l[:150]}")
