#!/usr/bin/env python3
"""Prefix-parity depth Y/Z per (guest, backend) against the ptrace golden.

DEFINITION (#316 -- record it, because the number is only monotonic under a fixed one):
  record  = a line containing `DETLOG` or `COMMIT turn`
  Z       = number of records in the PTRACE GOLDEN for that rung
  Y       = length of the longest common PREFIX of records between backend and golden
  normalization = the real wall-clock prefix ONLY. Syscall values, counts, flags and
                  virtual time are compared verbatim. Full INFO log, not stdout.
This is det4's definition unchanged, so these numbers are comparable to
ai_docs/prefix-parity-depth-remeasured_20260806.md.

TWO THINGS THIS HARNESS DOES THAT A NAIVE ONE GETS WRONG:

1. WHERE THE RECORDS LIVE. The `dbi` backend runs detcore inside a DynamoRIO client
   that does NOT honour `--log-file`: no file is created and every DETLOG goes to
   STDERR. Reading only the log file scores dbi 0 records and looks like a crash.
   So records are taken from the log file when it has any, else from stderr.

2. WHETHER THE BACKEND ACTUALLY ENGAGED. `--backend=X` is a LABEL, not evidence.
   `e9patch` reports `candidate_sites=0; mapped_sites=0` and then runs the ordinary
   ptrace runtime -- so it scores a PERFECT Z/Z while instrumenting nothing. A ratchet
   that accepts that is reporting a fiction. Engagement is therefore asserted from an
   observable in the run's own output, and a backend that cannot prove it engaged is
   reported NOT-ENGAGED, never as a parity score.
"""
import re
import subprocess
import sys
import os
import json

HERMIT = os.environ.get(
    "HERMIT_BIN",
    "/home/newton/work/dev-hermit/ignored/det4-parity/hermit/target/release/hermit",
)
WALL = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")
REC = re.compile(r"DETLOG|COMMIT turn")

# rung label -> argv (direct argv; only the pipeline rung needs a shell, and it says so)
RUNGS = [
    ("true",           ["/bin/true"]),
    ("echo",           ["/bin/echo", "hi"]),
    ("cat-hostname",   ["/bin/cat", "/etc/hostname"]),
    ("wc-hostname",    ["/bin/wc", "-c", "/etc/hostname"]),
    ("fork-exec-pipe", ["/bin/sh", "-c", "/bin/echo a | /bin/wc -c"]),
    ("python-startup", ["/usr/bin/python3", "-c", "print(1)"]),
    ("dd-10k",         ["/bin/sh", "-c", "/bin/dd if=/dev/zero of=/dev/null bs=1 count=10000 2>/dev/null"]),
    ("dd-100k",        ["/bin/sh", "-c", "/bin/dd if=/dev/zero of=/dev/null bs=1 count=100000 2>/dev/null"]),
]

BACKENDS = os.environ.get("BACKENDS", "ptrace,dbi,sabre,liteinst,e9patch").split(",")

# An observable that binds "this backend actually instrumented the guest".
# (pattern to find, pattern that REFUTES engagement)
ENGAGE = {
    "ptrace":   (r"", r""),                       # the reference itself
    "dbi":      (r"under DynamoRIO", r""),
    "sabre":    (r"hermit::sabre", r""),
    "liteinst": (r"activation verified \(traps=(\d+), hooks=(\d+)\)", r"traps=0, hooks=0"),
    "e9patch":  (r"mapped_sites=(\d+)", r"mapped_sites=0"),
    "kvm":      (r"", r""),
}


def run(backend, argv, tag, timeout):
    d = "/home/newton/work/dev-hermit/ignored/w2-ratchet/w"
    os.makedirs(d, exist_ok=True)
    logf = f"{d}/{tag}-{backend}.log"
    if os.path.exists(logf):
        os.unlink(logf)
    cmd = [HERMIT, "--log", "info", "--log-file", logf, "run",
           f"--backend={backend}", "--strict", "--"] + argv
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = (
        "/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib:"
        + env.get("LD_LIBRARY_PATH", "")
    )
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, timeout=timeout)
        rc, err = p.returncode, p.stderr.decode(errors="replace")
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT", ""
    text = ""
    if os.path.exists(logf):
        text = open(logf, errors="replace").read()
    recs = [WALL.sub("", l).rstrip() for l in text.splitlines() if REC.search(l)]
    if not recs:                       # dbi: detcore logs to stderr, not --log-file
        recs = [WALL.sub("", l).rstrip() for l in err.splitlines() if REC.search(l)]
    if os.path.exists(logf):
        os.unlink(logf)
    # Engagement evidence may land in EITHER stream: sabre writes `hermit::sabre`
    # to the log file while dbi/e9patch/liteinst announce themselves on stderr.
    both = err + "\n" + text
    if rc != 0:
        return recs, f"rc={rc}", both
    return recs, "ok", both


def engaged(backend, err):
    find, refute = ENGAGE[backend]
    if refute and re.search(refute, err):
        m = re.search(refute, err)
        return False, m.group(0)
    if find:
        m = re.search(find, err)
        if not m:
            return False, "no engagement evidence in output"
        return True, m.group(0)
    return True, "n/a"


def prefix(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n if len(a) == len(b) else n


def main():
    only = os.environ.get("ONLY")
    tmo = int(os.environ.get("TMO", "300"))
    rows = []
    for tag, argv in RUNGS:
        if only and tag not in only.split(","):
            continue
        g1, s1, _ = run("ptrace", argv, tag, tmo)
        g2, s2, _ = run("ptrace", argv, tag, tmo)
        if s1 != "ok" or s2 != "ok" or not g1:
            print(f"{tag:16s} GOLDEN TOOL-ERROR ({s1}/{s2})")
            continue
        if g1 != g2:
            d = prefix(g1, g2)
            print(f"{tag:16s} GOLDEN NOT SELF-DETERMINISTIC at {d}/{len(g1)} -- rung DISQUALIFIED")
            rows.append({"rung": tag, "Z": len(g1), "golden": "DISQUALIFIED"})
            continue
        Z = len(g1)
        print(f"{tag:16s} golden self-check PASS  Z={Z}")
        rows.append({"rung": tag, "Z": Z, "golden": "PASS", "backends": {}})
        for b in BACKENDS:
            if b == "ptrace":
                rows[-1]["backends"][b] = {"Y": Z, "Z": Z, "state": "SANITY Z/Z (double-run)"}
                print(f"    {b:9s} {Z}/{Z}   SANITY (golden double-run)")
                continue
            recs, st, err = run(b, argv, tag, tmo)
            if st == "TIMEOUT":
                rows[-1]["backends"][b] = {"state": "TOOL-ERROR timeout"}
                print(f"    {b:9s} TOOL-ERROR (timeout {tmo}s)")
                continue
            ok, ev = engaged(b, err)
            if not ok:
                rows[-1]["backends"][b] = {"state": f"NOT-ENGAGED ({ev})",
                                           "would_have_scored": f"{prefix(recs,g1)}/{Z}"}
                print(f"    {b:9s} NOT-ENGAGED [{ev}] -- would have scored "
                      f"{prefix(recs,g1)}/{Z}, NOT a parity result")
                continue
            if not recs:
                rows[-1]["backends"][b] = {"state": f"TOOL-ERROR no records ({st})"}
                print(f"    {b:9s} TOOL-ERROR (no records, {st})")
                continue
            Y = prefix(recs, g1)
            # DIAGNOSTIC, NOT THE RATCHET NUMBER. dbi renders the raw host pid where
            # the golden has the determinized DetPid(3), and that pid changes every
            # run. Folding pid-shaped integers shows the headroom hiding behind that
            # one blocker; it is reported separately and never as the parity score.
            PID = re.compile(r"(dettid |DetPid\()\d+")
            fold = lambda xs: [PID.sub(r"\1<pid>", x) for x in xs]
            Y_nopid = prefix(fold(recs), fold(g1))
            first = ""
            if Y < Z:
                gb = g1[Y] if Y < len(g1) else "<golden end>"
                bb = recs[Y] if Y < len(recs) else "<backend end>"
                first = f"\n        golden : {gb[:150]}\n        {b:6s} : {bb[:150]}"
            rows[-1]["backends"][b] = {"Y": Y, "Z": Z, "state": st,
                                       "backend_records": len(recs), "evidence": ev,
                                       "Y_if_pid_virtualized": Y_nopid}
            extra = "" if Y_nopid == Y else f"  [diagnostic: {Y_nopid}/{Z} if the leaked host pid were virtualized]"
            print(f"    {b:9s} {Y}/{Z}   (backend emitted {len(recs)} records) [{ev}]{extra}{first}")
    json.dump(rows, open("/home/newton/work/dev-hermit/ignored/w2-ratchet/ratchet.json", "w"), indent=1)


if __name__ == "__main__":
    main()
