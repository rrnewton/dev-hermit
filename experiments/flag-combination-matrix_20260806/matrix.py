#!/usr/bin/env python3
"""Flag-combination matrix: does every point between --strict and hermit-as-noop work?

Owner requirement (#125): confidence that ALL combinations of hermit's flags still
make sense and do not crash — not just the strict-determinism path.

TWO THINGS THIS ASSERTS, and the second is the one that stops it being vacuous:
  1. NO CRASH  — the run exits 0.
  2. CORRECT OUTPUT — guest stdout is byte-identical to running the guest NATIVELY.
     A combo that exits 0 while printing garbage is a FAILURE, not a pass. Without
     this an all-green matrix would prove only that hermit does not segfault.

HONEST LABELLING (#11/#42): a relaxed combo passing is NOT a determinism claim.
Every row carries the strongest claim its flags actually support:
  RELAXED    — one or more determinization axes disabled. No determinism claim. Ever.
  L1         — all axes on, strict. Ran deterministically THIS TIME; single run.
  L2(strip)  — ran under --verify (double-run, matched). Deliberately NOT written
               "L2": bare --verify selects the lossy Stripped comparator, which is
               measurably blind to numeric and 0x differences (6/6 -> 4/6 on a
               planted-mutant kill score). Calling it L2 would overstate it.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BIN = ROOT / "worktrees/oci/hermit/target/release/hermit"
LIBS = ROOT / "ignored/haskell-drb/hostlibs"

GUEST = ["/usr/bin/sha256sum", str(HERE / "fixture.txt")]

# The five binary relaxation axes, as (on-flag, off-flag).
# MEASURED CLI FACT: --virtualize-{cpuid,time,metadata} have NO positive form --
# they are default-ON and only NEGATABLE. So "on" is expressed by OMITTING the
# flag, not by passing it. (An earlier version of this harness passed the positive
# forms and got `error: unexpected argument` on 20/32 combos.)
AXES = [
    ("sequentialize", None, "--no-sequentialize-threads"),
    ("virt-cpuid", None, "--no-virtualize-cpuid"),
    ("virt-time", None, "--no-virtualize-time"),
    ("virt-metadata", None, "--no-virtualize-metadata"),
    ("det-io", None, "--no-deterministic-io"),
]


def native_output() -> str:
    return subprocess.run(GUEST, capture_output=True, text=True, timeout=60).stdout


def run_combo(flags: list[str], timeout: int = 120) -> tuple[int, str, str]:
    env = {"PATH": "/usr/bin:/bin", "LD_LIBRARY_PATH": str(LIBS), "HOME": "/tmp",
           "TERM": "dumb", "LC_ALL": "C", "TZ": "UTC"}
    cmd = [str(BIN), "run", *flags, "--", *GUEST]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr[-400:]
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT"


def label(flags: list[str]) -> str:
    relaxed = [f for f in flags if f.startswith("--no-")]
    if relaxed:
        return "RELAXED"
    if "--verify" in flags:
        return "L2(strip)"
    return "L1"


def main() -> int:
    if not BIN.exists():
        print(f"matrix: binary not found: {BIN}", file=sys.stderr)
        return 2
    (HERE / "fixture.txt").write_text("hermit flag-combination matrix fixture\n" * 64)
    want = native_output()
    if not want.strip():
        print("matrix: native reference produced no output", file=sys.stderr)
        return 2
    print(f"native reference: {want.strip()[:40]}…\n")

    rows = []
    # --- the relaxation spine: all 32 points between fully-on and fully-off ---
    for combo in itertools.product([True, False], repeat=len(AXES)):
        flags = [off for (_, _, off), keep in zip(AXES, combo) if not keep]
        rc, out, err = run_combo(flags)
        rows.append({
            "combo": "+".join(n for (n, _, _), k in zip(AXES, combo) if k) or "(all relaxed)",
            "flags": " ".join(flags), "rc": rc,
            "output_correct": out == want, "claim": label(flags),
            "detail": "" if rc == 0 and out == want else err.strip()[:180],
        })

    # --- orthogonal modes on the two anchor points ---
    all_on: list[str] = []  # every axis on == pass nothing
    all_off = [off for (_, _, off) in AXES]
    extras = [
        ("strict", ["--strict"]),
        ("strict+verify", ["--strict", "--verify"]),
        ("strict+verify+detlog", ["--strict", "--verify", "--detlog-stack", "--detlog-heap"]),
        ("strict+chaos", ["--strict", "--chaos"]),
        ("all-on+detlog", all_on + ["--detlog-stack", "--detlog-heap"]),
        ("all-relaxed+verify", all_off + ["--verify"]),
        ("bare (no flags)", []),
    ]
    for name, flags in extras:
        rc, out, err = run_combo(flags, timeout=240)
        # --verify suppresses guest stdout on the parent, so correctness is
        # asserted by exit code there and noted explicitly rather than faked.
        verify = "--verify" in flags
        rows.append({
            "combo": name, "flags": " ".join(flags) or "(none)", "rc": rc,
            "output_correct": None if verify else (out == want),
            "claim": label(flags),
            "detail": "" if rc == 0 else err.strip()[:180],
        })

    ok = [r for r in rows if r["rc"] == 0 and r["output_correct"] is not False]
    bad = [r for r in rows if r not in ok]
    print(f"{'combo':34s} {'rc':>3s} {'output':8s} {'claim':10s}")
    print("-" * 62)
    for r in rows:
        oc = {True: "correct", False: "WRONG", None: "n/a"}[r["output_correct"]]
        print(f"{r['combo']:34s} {r['rc']:>3d} {oc:8s} {r['claim']:10s}"
              + (f"  {r['detail'][:60]}" if r["detail"] else ""))
    print(f"\n{len(ok)}/{len(rows)} combos OK (exit 0 and output correct where observable)")
    if bad:
        print("FAILURES:")
        for r in bad:
            print(f"  {r['combo']}: rc={r['rc']} correct={r['output_correct']} {r['detail']}")
    (HERE / "results.json").write_text(json.dumps(rows, indent=2))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
