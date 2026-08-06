#!/usr/bin/env python3
"""Capture PINNED, self-verified ptrace golden INFO logs for the prefix-depth ratchet.

THE ONE RULE THIS ENFORCES: a golden is only a golden if it is deterministic
AGAINST ITSELF. Every guest is captured TWICE and the two normalized logs must be
byte-identical. A guest that fails that gate is recorded as NOT-A-GOLDEN and no
golden file is written for it -- because a nondeterministic reference makes every
backend look divergent, and the resulting Y/Z would measure our own noise.

WHY THE ENV IS PINNED THE WAY IT IS (this is not incidental)
------------------------------------------------------------
Guests run under `--base-env minimal -e LC_ALL=C -e TZ=UTC`, the same convention
the preload-vs-ptrace env-equalisation work used, so these goldens stay
comparable after that lands.

It is also load-bearing for correctness here. hermit itself needs
`LD_LIBRARY_PATH` at runtime on this host (its release binary reports
`libunwind-x86_64.so.8 => not found` without it, and refuses to start), but that
variable is inherited by the guest, whose loader then searches those directories
-- and every probe of them lands in the DETLOG as an absolute host path.

RUNTIME DIR: use the fbsource libunwind, NOT ignored/lu-parity:
    LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
Both dirs carry libunwind-x86_64.so.8, so hermit starts under either and the
captures in this experiment were valid under lu-parity. But lu-parity ships
libunwind-ptrace only as a STATIC .a with no .so.0, so anything needing
libunwind-ptrace fails there with `libunwind-ptrace.so.0: cannot open shared
object file` -- which reads as a broken build and is not one. fbsource carries
both, so it is the strictly safer default. (lu-parity is still correct for
PKG_CONFIG_PATH and LIBRARY_PATH, which are build-time.)

Measured both ways, the choice does NOT move Z: /bin/true Z=14 and
/bin/echo hello Z=43 under either dir. Z counts scheduler turns, not the
individual loader probes, so it is insensitive to how many probes fail per turn
(echo probes the fbsource dir 129 times vs lu-parity 51, with Z=43 either way).

Measured on /bin/true:

    default host env : 177 log lines, 45 lu-parity path mentions, Z = 14
    --base-env minimal : 114 log lines,  0 lu-parity path mentions, Z =  5

So Z -- the denominator of every Y/Z prefix-depth ratio -- is a function of the
environment, not just of the guest. A ratio quoted without its env pin is
meaningless. `--base-env minimal` keeps hermit's own LD_LIBRARY_PATH out of the
guest, which is why the goldens contain no host paths and are comparable across
checkouts.

NORMALIZATION: exactly one thing is stripped, the leading wall-clock timestamp,
which is the only irreproducible datum in the stream. Virtual time
(`1_767_225_600.000_500_000s`) is deterministic and is deliberately KEPT -- it is
signal, not noise. Nothing else is normalized: no address ordinalization, no
numeric masking. If a future comparison needs those, it should say so explicitly
rather than have this capture quietly discard them.

Z is the count of `COMMIT` messages, which equals the scheduler turn count that
hermit itself reports ("the hermit scheduler ran N turns").
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The ladder, cheapest first. Each rung adds one capability over the last.
GUESTS = [
    ("true", ["/bin/true"], "minimal: no output, no file I/O"),
    ("echo", ["/bin/echo", "hermit-golden"], "adds stdout write"),
    ("cat-hostname", ["/bin/cat", "/etc/hostname"], "adds file open/read"),
    ("wc-passwd", ["/usr/bin/wc", "-l", "/etc/passwd"], "adds a small coreutil with buffered read"),
    ("head-passwd", ["/usr/bin/head", "-1", "/etc/passwd"], "adds early-exit read"),
    ("sh-pipeline", ["/bin/sh", "-c", "/bin/echo a | /usr/bin/wc -c"],
     "adds fork/exec + pipe: two guest processes"),
    ("sh-loop-exec", ["/bin/sh", "-c", "for i in 1 2 3; do /bin/echo $i; done"],
     "adds repeated fork/exec"),
]

# Only the wall-clock prefix is removed. See module docstring.
TS = re.compile(r"^\S+Z\s+")


def normalize(text: str) -> str:
    return "".join(TS.sub("", line) for line in text.splitlines(keepends=True))


def run_once(binary: Path, guest: list[str], log: Path, env_pin: dict, timeout: int):
    """One ptrace capture. Returns (rc, normalized_log_text, stdout, stderr)."""
    cmd = [
        str(binary), "--log", "info", "--log-file", str(log),
        "run", "--base-env", "minimal", "-e", "LC_ALL=C", "-e", "TZ=UTC", "--strict",
        "--", *guest,
    ]
    if log.exists():
        log.unlink()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env_pin)
    except subprocess.TimeoutExpired:
        return 124, "", "", "TIMEOUT"
    text = log.read_text(errors="replace") if log.exists() else ""
    return p.returncode, normalize(text), p.stdout, p.stderr


def counts(norm: str) -> dict:
    lines = norm.splitlines()
    return {
        "log_lines": len(lines),
        "detlog": sum(1 for line in lines if "DETLOG" in line),
        "Z_commits": sum(1 for line in lines if "COMMIT" in line),
    }


def first_diff(a: str, b: str) -> dict:
    la, lb = a.splitlines(), b.splitlines()
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return {"index": i, "run1": x[:200], "run2": y[:200]}
    if len(la) != len(lb):
        return {"index": min(len(la), len(lb)),
                "run1": "<eof>" if len(la) < len(lb) else la[len(lb)][:200],
                "run2": "<eof>" if len(lb) < len(la) else lb[len(la)][:200]}
    return {}


def classify(rc1: int, rc2: int, so1: str, so2: str, n1: str, n2: str) -> tuple[str, str]:
    """THE single self-determinism decision. One verifier, called by every consumer.

    capture_goldens.py and bracket_gate.py both call this exact function, so the
    negative control exercises the real decision rather than a copy of it that
    could drift. Returns (verdict, detail) where verdict is one of
    GOLDEN / NOT-A-GOLDEN / NO-RESULT.

    NO-RESULT is never a pass: an empty or timed-out capture has compared
    nothing, and calling that "deterministic" is the exact failure this whole
    exercise exists to prevent.
    """
    if rc1 == 124 or rc2 == 124:
        return "NO-RESULT", "timed out"
    if not n1 or not n2:
        return "NO-RESULT", "empty INFO log -- nothing to compare"
    if rc1 != rc2:
        return "NOT-A-GOLDEN", f"exit differs run1={rc1} run2={rc2}"
    if so1 != so2:
        return "NOT-A-GOLDEN", "guest stdout differs between runs"
    if n1 != n2:
        d = first_diff(n1, n2)
        return "NOT-A-GOLDEN", (f"INFO log differs at line {d.get('index')}: "
                                f"run1={d.get('run1','')!r} run2={d.get('run2','')!r}")
    return "GOLDEN", "run1 == run2 after timestamp strip"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--hermit-sha", required=True, help="40-hex SHA the binary was built from")
    ap.add_argument("--libdir", type=Path, required=True,
                    help="dir holding libunwind for hermit's OWN runtime (kept out of the guest)")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--workdir", type=Path, default=Path("/home/newton/work/dev-hermit/ignored/golden-capture"))
    args = ap.parse_args()

    if not args.binary.exists():
        print(f"capture: binary not found: {args.binary}", file=sys.stderr)
        return 2
    args.workdir.mkdir(parents=True, exist_ok=True)
    gdir = HERE / "goldens"
    gdir.mkdir(exist_ok=True)

    # hermit's own process env. LD_LIBRARY_PATH is here so hermit can start; the
    # guest does NOT inherit it, because of --base-env minimal.
    env_pin = {
        "PATH": "/usr/bin:/bin", "HOME": "/tmp", "TERM": "dumb",
        "LC_ALL": "C", "TZ": "UTC", "LD_LIBRARY_PATH": str(args.libdir),
    }

    binary_sha = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    rows, manifest = [], []

    for gid, guest, why in GUESTS:
        log1 = args.workdir / f"{gid}.1.log"
        log2 = args.workdir / f"{gid}.2.log"
        rc1, n1, so1, _ = run_once(args.binary, guest, log1, env_pin, args.timeout)
        rc2, n2, so2, _ = run_once(args.binary, guest, log2, env_pin, args.timeout)

        c = counts(n1)
        verdict, detail = classify(rc1, rc2, so1, so2, n1, n2)

        golden_sha = ""
        if verdict == "GOLDEN":
            out = gdir / f"{gid}.log"
            out.write_text(n1)
            golden_sha = hashlib.sha256(n1.encode()).hexdigest()

        print(f"{verdict:14s} {gid:14s} Z={c['Z_commits']:>4} detlog={c['detlog']:>5} "
              f"lines={c['log_lines']:>5} rc={rc1}  {detail if verdict!='GOLDEN' else ''}")

        rows.append({"guest": gid, "argv": " ".join(guest), "rung": why, "verdict": verdict,
                     "Z_commits": c["Z_commits"], "detlog_msgs": c["detlog"],
                     "log_lines": c["log_lines"], "exit": rc1,
                     "golden_sha256": golden_sha, "detail": detail})
        manifest.append({
            "guest": gid, "argv": guest, "verdict": verdict, "Z_commits": c["Z_commits"],
            "detlog_msgs": c["detlog"], "log_lines": c["log_lines"], "guest_exit": rc1,
            "golden_sha256": golden_sha, "detail": detail,
        })

    pin = {
        "hermit_sha": args.hermit_sha,
        "hermit_binary_sha256": binary_sha,
        "profile": "release",
        "backend": "ptrace",
        "flags": ["--log info", "--log-file <path>", "run", "--base-env minimal",
                  "-e LC_ALL=C", "-e TZ=UTC", "--strict"],
        "guest_env": {"base": "minimal", "explicit": ["LC_ALL=C", "TZ=UTC"]},
        "hermit_process_env": env_pin,
        "normalization": "leading wall-clock timestamp stripped; nothing else",
        "Z_definition": "count of COMMIT messages == hermit's reported scheduler turn count",
        "self_determinism_gate": "captured twice; byte-identical after normalization or NOT-A-GOLDEN",
        "host": {},
        "goldens": manifest,
    }
    try:
        pin["host"] = {
            "kernel": subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip(),
            "nproc": subprocess.run(["nproc"], capture_output=True, text=True).stdout.strip(),
            "cpu": next((l.split(":", 1)[1].strip() for l in Path("/proc/cpuinfo").read_text().splitlines()
                         if l.startswith("model name")), "?"),
        }
    except Exception as exc:  # host facts are evidence, not control flow
        pin["host"] = {"error": str(exc)}

    (HERE / "manifest.json").write_text(json.dumps(pin, indent=2) + "\n")
    with (HERE / "results.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    ngold = sum(1 for r in rows if r["verdict"] == "GOLDEN")
    print(f"\n{ngold}/{len(rows)} guests produced a self-verified golden")
    return 0 if ngold else 1


if __name__ == "__main__":
    raise SystemExit(main())
