#!/usr/bin/env python3
"""Score the residue matrix, keeping ATTRIBUTION separate from VERDICT.

Three things are reported per (mode, backend) and they answer different questions:

  verify_ok      hermit's own `--strict --verify` verdict over VER runs. Recorded, not
                 trusted as the last word: --verify uses the Stripped comparator, which
                 erases numeric literals -- exactly how the parent sweep's
                 FileContents(<raw host inode>) defect passed it while the logs differed.

  self_detlog    r1 vs r2..rN in CONTENT mode (raw hash equality, what `hermit log-diff`
                 does). This is the strict self-determinism question.

  residual       the SAME comparison with `FileContents(<digits>)` masked. Its ONLY job is
                 to separate "diverges because of the already-filed raw-inode defect
                 (detlog_embeds_raw_host)" from "diverges for some FURTHER reason". A cell
                 that is clean only after masking is reported as ATTRIBUTED, never as a
                 pass -- masking an environment-derived value to make a check go green is
                 the #140 anti-pattern, and is not what this column does.

Cross-backend uses STRUCTURAL mode (addresses ordinalized) because e9patch loads the guest
at a different base; content mode would flag 100% of records on a pure relocation.
"""
from __future__ import annotations
import os, re, sys, csv, hashlib, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import normparity  # noqa: E402

FILECONTENTS = re.compile(r"FileContents\(\d+\)")


def sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    except OSError:
        return None


def masked_compare(a, b):
    """Content-mode record streams with FileContents(N) masked; returns n_diffs."""
    xs = [FILECONTENTS.sub("FileContents(<masked>)", l)
          for l in normparity.normalize(a, keep_hash_values=True)]
    ys = [FILECONTENTS.sub("FileContents(<masked>)", l)
          for l in normparity.normalize(b, keep_hash_values=True)]
    d = sum(1 for x, y in zip(xs, ys) if x != y) + abs(len(xs) - len(ys))
    return d


def content_compare(a, b):
    _, _, diffs = normparity.compare(a, b, keep_hash_values=True, limit=10**6)
    return len(diffs)


def structural_compare(a, b):
    _, _, diffs = normparity.compare(a, b, keep_hash_values=False, limit=10**6)
    return len(diffs)


def filecontents_ids(path):
    ids = set()
    for line in open(path, errors="replace"):
        for m in re.finditer(r"FileContents\((\d+)\)", line):
            ids.add(m.group(1))
    return ids


def main():
    out = os.path.join(BASE, "data", "sweep")
    modes = sorted(os.listdir(out)) if os.path.isdir(out) else []
    rows = []
    for m in modes:
        for b in ("ptrace", "e9patch"):
            d = os.path.join(out, m, b)
            if not os.path.isdir(d):
                continue
            vrcs, vok = [], 0
            for i in range(1, 9):
                p = os.path.join(d, f"v{i}.rc")
                if not os.path.exists(p):
                    continue
                rc = open(p).read().strip()
                vrcs.append(rc)
                err = open(os.path.join(d, f"v{i}.err"), errors="replace").read()
                if rc == "0" and "Determinism verified" in err:
                    vok += 1
            rlogs, routs, rrcs = [], [], []
            for i in range(1, 9):
                p = os.path.join(d, f"r{i}.rc")
                if not os.path.exists(p):
                    continue
                rrcs.append(open(p).read().strip())
                rlogs.append(os.path.join(d, f"r{i}.log"))
                routs.append(os.path.join(d, f"r{i}.out"))
            if not rlogs:
                continue
            n = len(rlogs)
            stdout_distinct = len({sha(o) for o in routs})
            self_diffs = [content_compare(rlogs[0], rlogs[i]) for i in range(1, n)]
            resid = [masked_compare(rlogs[0], rlogs[i]) for i in range(1, n)]
            ids = set()
            for l in rlogs:
                ids |= filecontents_ids(l)
            rows.append(dict(
                mode=m, backend=b,
                n_verify=len(vrcs), verify_ok=vok, verify_rcs="/".join(sorted(set(vrcs))),
                n_detlog=n, detlog_rcs="/".join(sorted(set(rrcs))),
                stdout_distinct=stdout_distinct,
                self_detlog_maxdiff=max(self_diffs) if self_diffs else 0,
                residual_maxdiff=max(resid) if resid else 0,
                filecontents_ids=len(ids),
            ))
    # cross-backend, structural, r1 vs r1
    xrows = []
    for m in modes:
        a = os.path.join(out, m, "ptrace", "r1.log")
        b = os.path.join(out, m, "e9patch", "r1.log")
        if os.path.exists(a) and os.path.exists(b):
            xrows.append(dict(
                mode=m,
                stdout_eq=int(sha(a.replace(".log", ".out")) == sha(b.replace(".log", ".out"))),
                structural_diffs=structural_compare(a, b),
            ))

    with open(os.path.join(BASE, "data", "results.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(os.path.join(BASE, "data", "cross-backend.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(xrows[0].keys()))
        w.writeheader(); w.writerows(xrows)

    hdr = ("mode", "be", "vfy", "n", "rc", "sout", "self", "resid", "fcids")
    print("%-24s %-8s %-5s %-3s %-5s %-5s %-6s %-6s %s" % hdr)
    for r in rows:
        print("%-24s %-8s %d/%-3d %-3d %-5s %-5d %-6d %-6d %d" % (
            r["mode"], r["backend"], r["verify_ok"], r["n_verify"], r["n_detlog"],
            r["detlog_rcs"], r["stdout_distinct"], r["self_detlog_maxdiff"],
            r["residual_maxdiff"], r["filecontents_ids"]))
    print()
    print("%-24s %-9s %s" % ("mode", "stdout_eq", "structural_diffs(ptrace vs e9patch)"))
    for r in xrows:
        print("%-24s %-9d %d" % (r["mode"], r["stdout_eq"], r["structural_diffs"]))


if __name__ == "__main__":
    main()
