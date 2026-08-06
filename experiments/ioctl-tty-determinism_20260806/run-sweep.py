#!/usr/bin/env python3
"""ioctl / tty determinism sweep.

Matrix: guest x tty-configuration x backend. Each cell is run as TWO SEPARATE
hermit invocations ("double-run"), each with `--log info --log-file`, so both
the guest-visible bytes and the deterministic trace can be compared.

Three questions, kept separate:

  SELF-DETERMINISM  within one tty configuration, do two separate hermit
                    invocations agree byte-for-byte on guest output, and does
                    address-normalized COMMIT+DETLOG parity hold?

  HOST-GEOMETRY     across configurations differing ONLY in the host terminal
  LEAK              size (pty 24x80 / 40x120 / 50x200), does guest-visible
                    output change? If yes, the host terminal is reaching the
                    guest.

  BACKEND PARITY    at one fixed configuration, does each backend agree with
                    the ptrace reference on guest output and on DETLOG?

`--verify-strict` is NOT used as a gate: it is red on this box for /bin/true
(a DEBUG line carries the CPU's initial_local_apic_id). `--verify` IS recorded,
but note it replaces stdio with non-ttys, so in pty configurations it does not
actually exercise the tty path -- that is itself a result, recorded in the
`verify_saw_tty` column.

Writes results.csv, geometry-leak.csv, backend-parity.csv.
"""
import csv
import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HERMIT = os.environ.get(
    "HERMIT_BIN",
    "/home/newton/work/dev-hermit/worktrees/dbi/hermit/target/release/hermit",
)
ENV = dict(os.environ)
ENV.setdefault("LD_LIBRARY_PATH", os.path.expanduser("~/.local/hermit-deps/lu/usr/lib64"))
ENV["TERM"] = "xterm-256color"
# COLUMNS/LINES would let a guest learn the width WITHOUT an ioctl, confounding
# the variable under test. Remove them so TIOCGWINSZ is the only width channel.
ENV.pop("COLUMNS", None)
ENV.pop("LINES", None)

TIMEOUT = int(os.environ.get("SWEEP_TIMEOUT", "90"))
REPS = int(os.environ.get("SWEEP_REPS", "3"))

GUESTS = {
    # direct enumeration of the tty ioctl surface
    "probe":        [str(HERE / "probe")],
    # minimal: reads geometry and acts on it (exit status carries the columns)
    "winsz-branch": [str(HERE / "winsz"), "branch"],
    # minimal: reads geometry and never acts on it
    "winsz-silent": [str(HERE / "winsz"), "silent"],
    # real-world consumers that format their output to the terminal
    "ls-C":         ["/bin/ls", "-C", "/etc"],
    "stty-a":       ["/bin/stty", "-a"],
}

TTYCFGS = {
    "pipe":      ["pipe"],
    "pty24x80":  ["24", "80"],
    "pty40x120": ["40", "120"],
    "pty50x200": ["50", "200"],
}
GEOMS = ["pty24x80", "pty40x120", "pty50x200"]

# kvm is excluded from the matrix: it does not complete this workload on this
# box with this binary (see README "Backends actually exercised").
BACKENDS = ["ptrace", "dbi", "sabre"]
REFERENCE = "ptrace"

# The harness (ptyrun.py) separates the guest's stdout from hermit's stderr at
# the DESCRIPTOR level -- fd 0/1 on one pty, fd 2 on another -- so the captured
# stream is already the guest's own output. No post-hoc text filtering is
# applied: nothing is subtracted from the measurement after the fact.
def filter_guest(guest, raw):
    return raw


def invoke(guest, ttycfg, hermit_args, logfile=None):
    args = list(hermit_args)
    if logfile:
        args = ["--log", "info", "--log-file", str(logfile)] + args
    argv = ([sys.executable, str(HERE / "ptyrun.py")] + TTYCFGS[ttycfg] + ["--"]
            + [HERMIT] + args + ["--"] + GUESTS[guest])
    try:
        p = subprocess.run(argv, capture_output=True, timeout=TIMEOUT, env=ENV)
    except subprocess.TimeoutExpired:
        return b"<<TIMEOUT>>", 124
    return filter_guest(guest, p.stdout), p.returncode


def logdiff(a, b):
    """Address-normalized COMMIT+DETLOG parity. Returns (verdict, n_compared)."""
    if not (a.exists() and b.exists()):
        return "nolog", ""
    try:
        p = subprocess.run([HERMIT, "log-diff", str(a), str(b)],
                           capture_output=True, timeout=300, env=ENV)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    out = p.stdout.decode("utf8", "replace") + p.stderr.decode("utf8", "replace")
    n = ""
    for tok in out.split("("):
        if "DETLOG messages compared" in tok:
            n = tok.split("|")[0].strip()
            break
    if "no substantive differences found" in out:
        return "same", n
    if "differences found" in out:
        return "DIFF", n
    return f"unknown(rc={p.returncode})", n


def sha(b):
    return hashlib.sha256(b).hexdigest()[:16]


def main():
    logs = HERE / "logs"; logs.mkdir(exist_ok=True)
    out = HERE / "out";   out.mkdir(exist_ok=True)
    rows, hashes, logpaths = [], {}, {}

    for guest in GUESTS:
        for backend in BACKENDS:
            for ttycfg in TTYCFGS:
                tag = f"{guest}.{backend}.{ttycfg}"
                base = ["run", f"--backend={backend}", "--strict"]

                res = []
                for rep in [chr(ord("A") + i) for i in range(REPS)]:
                    lf = logs / f"{tag}.{rep}.log"
                    o, rc = invoke(guest, ttycfg, base, logfile=lf)
                    (out / f"{tag}.{rep}.txt").write_bytes(o)
                    res.append((o, rc, lf))

                obs = {(sha(o), rc) for o, rc, _ in res}
                hA, hB = sha(res[0][0]), sha(res[-1][0])
                dbl = "SAME" if len(obs) == 1 else "DIFF"
                dl, dln = logdiff(res[0][2], res[1][2])

                # hermit's own gate. `verify_saw_tty` asks whether the guest
                # still had a terminal once --verify was in play: the winsz
                # guests answer with exit 70 when the fd is not a tty.
                _, vrc = invoke(guest, ttycfg, base + ["--verify", "--verify-allow", "both"])
                saw = ""
                if guest.startswith("winsz"):
                    saw = "no" if vrc == 70 else ("yes" if ttycfg != "pipe" else "n/a-pipe")

                # The guest-visible observable is stdout AND exit status: the
                # winsz guests print nothing and answer through exit status
                # alone, so comparing bytes only would call them "stable".
                hashes[(guest, backend, ttycfg)] = f"{hA}/exit{res[0][1]}"
                logpaths[(guest, backend, ttycfg)] = res[0][2]
                rows.append(dict(
                    guest=guest, backend=backend, ttycfg=ttycfg,
                    exit_A=res[0][1], exit_B=res[-1][1], reps=REPS,
                    sha_A=hA, sha_B=hB, bytes_A=len(res[0][0]),
                    double_run=dbl, detlog_parity_AB=dl, detlog_msgs=dln,
                    logfile_written=("yes" if res[0][2].exists() else "NO"),
                    verify_rc=vrc, verify_saw_tty=saw,
                ))
                print(f"{guest:13s} {backend:7s} {ttycfg:10s} dbl={dbl:4s} "
                      f"detlog={dl:5s}({dln:>5s}) exit={res[0][1]:<4d} "
                      f"bytes={len(res[0][0]):<6d} vrc={vrc} tty={saw}", flush=True)

    # ---- host-geometry leak: only the host terminal size differs ----
    leak = []
    for guest in GUESTS:
        for backend in BACKENDS:
            hs = [hashes.get((guest, backend, g)) for g in GEOMS]
            dl, dln = logdiff(logpaths.get((guest, backend, GEOMS[0])),
                              logpaths.get((guest, backend, GEOMS[1]))) \
                if logpaths.get((guest, backend, GEOMS[0])) else ("nolog", "")
            verdict = "LEAK" if len(set(hs)) > 1 else "output-stable"
            leak.append(dict(guest=guest, backend=backend,
                             **{g: hs[i] for i, g in enumerate(GEOMS)},
                             output_verdict=verdict,
                             detlog_80_vs_120=dl, detlog_msgs=dln))
            print(f"GEOM {guest:13s} {backend:7s} {verdict:14s} detlog={dl}", flush=True)

    # ---- cross-backend parity against ptrace, at one fixed configuration ----
    par = []
    for guest in GUESTS:
        for ttycfg in TTYCFGS:
            ref = hashes.get((guest, REFERENCE, ttycfg))
            for backend in BACKENDS:
                if backend == REFERENCE:
                    continue
                h = hashes.get((guest, backend, ttycfg))
                dl, dln = logdiff(logpaths.get((guest, REFERENCE, ttycfg)),
                                  logpaths.get((guest, backend, ttycfg)))
                par.append(dict(guest=guest, ttycfg=ttycfg, backend=backend,
                                sha_ptrace=ref, sha_backend=h,
                                output_parity="same" if ref == h else "DIFF",
                                detlog_parity=dl, detlog_msgs=dln))

    for name, data in (("results.csv", rows), ("geometry-leak.csv", leak),
                       ("backend-parity.csv", par)):
        with open(HERE / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
            w.writeheader(); w.writerows(data)
        print(f"wrote {name} ({len(data)} rows)")


if __name__ == "__main__":
    main()
