#!/usr/bin/env python3
"""STANDING cross-backend detlog parity gate.

Fails loudly when a backend pair that parity TODAY stops matching. Designed so it
cannot silently pass everything: every run plants a mutation and asserts the
comparison still catches it.

WHY IT IS SHAPED THIS WAY
-------------------------
1. GREEN TODAY, OR IT GETS DISABLED. A gate that is red from birth teaches people
   to bypass it. Measured 2026-08-06: ptrace<->e9patch is a clean cross-backend
   pair (172|172 DETLOG messages, zero differences, heap records byte-identical),
   while DBI is 0/6 on three unfixed product defects. So the ENFORCING set is the
   pair that passes; DBI sits in KNOWN_RED, which may shrink and may not grow.

2. NO ADDRESS NORMALIZATION. The obvious "fix" — hash address-relative offsets —
   was measured and REJECTED: normalizing turns DBI's divergence RED->GREEN, but
   that divergence is real (DynamoRIO relocates the guest's static/heap by exactly
   +0x1000). Normalizing would delete the only signal that DR moves guest memory,
   against the standing requirement that heap parity means "same address, same
   contents". Instead this gate reports RANGE equality and CONTENT-HASH equality
   as SEPARATE facts, so a page-granular relocation is distinguishable from a
   content difference without discarding either.

3. COUNTS TRAVEL WITH THE VERDICT. Zero compared messages is a NO-RESULT, never a
   pass. A gate that "passes" over an empty comparison is the failure mode this
   whole lane exists to prevent.

4. PINNED ENVIRONMENT. Non-negotiable. An unpinned environment puts INVOCATION_ID
   and systemd scope names into the guest's envp, hence its initial stack, and
   every stack hash then differs under every backend (measured: 3/3 distinct on
   ptrace alone). Without pinning this gate would be permanently, falsely red.

EXIT CODES
  0  all enforced pairs matched AND the self-check caught its planted mutation
  1  a parity regression (an enforced pair diverged)
  2  the gate could not establish a result (no binary, zero messages, ...)
  3  SELF-CHECK FAILED: the comparison did not catch a planted divergence, so a
     pass from this gate would be meaningless
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOX = ROOT / "scripts/hermit-box-run"

# Backend pairs this gate ENFORCES. Measured green 2026-08-06.
ENFORCED_PAIRS = [("ptrace", "e9patch")]

# Pairs known red, with the reason. This set may SHRINK, never grow silently:
# `--check-known-red` fails if one of these starts passing (promote it) and the
# test suite pins the membership.
KNOWN_RED = {
    ("ptrace", "dbi"): (
        "DETLOG dtid is the raw host TID (7 distinct across 7 runs vs ptrace's "
        "constant dtid 3); --log-file ignored (log goes to stderr, no wall-clock "
        "prefix); zero [heap] records emitted. Separately, DynamoRIO relocates "
        "guest static/heap by +0x1000 — real, structural, NOT to be normalized away."
    ),
    ("ptrace", "kvm"): "KVM cannot complete a guest (startup livelock; CPU-timeout, burned core).",
    ("ptrace", "sabre"): (
        "UNMEASURED. SaBRe emits its DETLOG on stderr and already has a rescue path "
        "(run.rs extract_sabre_detlogs); run this gate with the pair added to see "
        "whether that rescue is sufficient. ~30 minutes."
    ),
    ("ptrace", "liteinst"): (
        "UNMEASURED and never probed at all — its corpus number is stdout-only. "
        "Run this gate with the pair added; that is the cheapest information gain "
        "left on the parity map. ~30 minutes."
    ),
}

GUEST = ["/bin/echo", "hello"]
PINNED = ["PATH=/usr/bin:/bin", "HOME=/tmp", "TERM=dumb", "LC_ALL=C", "TZ=UTC"]
TS = re.compile(
    r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d\d \d\d:\d\d:\d\d\.\d+"
    r"|\d+-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z) +"
)


def messages(text: str) -> list[str]:
    """Wall-clock prefix stripped (the one irreproducible datum); rest exact."""
    return [m.strip() for m in TS.split(text) if m.strip()]


def memory_records(msgs: list[str]) -> list[tuple[str, str]]:
    """(range, content-hash) pairs — reported SEPARATELY, never merged."""
    out = []
    for m in msgs:
        if "[memory]" not in m:
            continue
        rng = re.search(r"(0x[0-9a-f]+-0x[0-9a-f]+)", m)
        h = re.search(r"->([0-9a-f]{8,})", m)
        out.append((rng.group(1) if rng else "?", h.group(1) if h else "?"))
    return out


def run_backend(binary: Path, libs: Path, backend: str, log: Path, out: Path) -> int:
    # A failed launch must not inherit valid logs from an earlier invocation and turn
    # the wrapper failure into a MATCH.
    for path in (log, out, Path(f"{out}.stdout")):
        path.unlink(missing_ok=True)
    pin = " ".join(PINNED + [f"LD_LIBRARY_PATH={libs}"])
    inner = (f"env -i {pin} {binary} --backend {backend} --log info --log-file {log} "
             f"run --strict --detlog-stack --detlog-heap -- {' '.join(GUEST)} "
             f"> {out}.stdout 2> {out}")
    try:
        completed = subprocess.run(
            [str(BOX), "--cpu-budget", "180", "--wall", "240",
             "--label", f"paritygate-{backend}", "--", "bash", "-c", inner],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return 124
    return completed.returncode


def load(log: Path, err: Path) -> list[str]:
    """Prefer the log file; fall back to stderr (DBI misroutes — see KNOWN_RED)."""
    for p in (log, err):
        if p.exists() and p.stat().st_size > 0:
            m = messages(p.read_text(errors="replace"))
            if m:
                return m
    return []


def compare(a: list[str], b: list[str]) -> dict:
    res: dict = {"a_msgs": len(a), "b_msgs": len(b)}
    if not a or not b:
        res["verdict"] = "NO-RESULT"
        return res
    ma, mb = memory_records(a), memory_records(b)
    res["ranges_equal"] = [r for r, _ in ma] == [r for r, _ in mb]
    res["content_equal"] = [h for _, h in ma] == [h for _, h in mb]
    res["memory_records"] = f"{len(ma)}|{len(mb)}"
    res["verdict"] = "MATCH" if a == b else "DIVERGE"
    if a != b:
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                res["first_diff_index"] = i
                res["first_diff"] = [x[:160], y[:160]]
                break
    return res


def self_check(a: list[str]) -> tuple[bool, str]:
    """Plant a divergence and assert the comparison catches it.

    Without this the gate could pass because the comparison is inert rather than
    because the backends agree.
    """
    if len(a) < 2:
        return False, "too few messages to mutate"
    mutated = list(a)
    target = next((i for i, m in enumerate(mutated) if "->" in m), None)
    if target is None:
        return False, "no hash-bearing record to mutate"
    h = mutated[target].split("->")[-1]
    mutated[target] = mutated[target].replace(h, ("f" if h[0] != "f" else "0") + h[1:])
    caught = compare(a, mutated)["verdict"] == "DIVERGE"
    return caught, "planted a 1-hex-digit content-hash flip"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--libs", type=Path, required=True, help="dir containing libunwind")
    # NOT under /tmp: Hermit's container hides host /tmp, so a --log-file there is
    # written into the container's tmpfs and vanishes. The guest then produces no
    # log, the comparison has nothing to compare, and the gate exits 3. Default to
    # an ignored dir at the repo root instead.
    ap.add_argument("--workdir", type=Path, default=ROOT / "ignored/parity-gate")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    if not args.binary.exists():
        print(f"parity-gate: binary not found: {args.binary}", file=sys.stderr)
        return 2
    args.workdir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "backend_runs": {},
        "enforced": {},
        "known_red": {f"{a}->{b}": r for (a, b), r in KNOWN_RED.items()},
    }
    logs: dict[str, list[str]] = {}
    backend_failed = False
    for backend in sorted({b for pair in ENFORCED_PAIRS for b in pair}):
        log, err = args.workdir / f"{backend}.log", args.workdir / f"{backend}.err"
        backend_rc = run_backend(args.binary, args.libs, backend, log, err)
        report["backend_runs"][backend] = {"returncode": backend_rc}
        if backend_rc != 0:
            backend_failed = True
            logs[backend] = []
            print(f"parity-gate: {backend}: BOXED RUN FAILED (wrapper rc={backend_rc}); "
                  "refusing output", file=sys.stderr)
            continue
        logs[backend] = load(log, err)
        print(f"parity-gate: {backend}: {len(logs[backend])} messages")

    rc = 2 if backend_failed else 0
    for a, b in ENFORCED_PAIRS:
        r = compare(logs.get(a, []), logs.get(b, []))
        report["enforced"][f"{a}->{b}"] = r
        print(f"parity-gate: {a} vs {b}: {r['verdict']} "
              f"({r['a_msgs']}|{r['b_msgs']} msgs; memory {r.get('memory_records','-')}; "
              f"ranges_equal={r.get('ranges_equal')} content_equal={r.get('content_equal')})")
        if r["verdict"] == "NO-RESULT":
            print(f"parity-gate: NO-RESULT for {a} vs {b} — a pass here would be meaningless",
                  file=sys.stderr)
            rc = max(rc, 2)
        elif r["verdict"] == "DIVERGE":
            print(f"parity-gate: PARITY REGRESSION {a} vs {b} at message "
                  f"{r.get('first_diff_index')}", file=sys.stderr)
            for line in r.get("first_diff", []):
                print(f"    {line}", file=sys.stderr)
            rc = max(rc, 1)

    base = next((v for v in logs.values() if v), [])
    caught, how = self_check(base)
    report["self_check"] = {"caught": caught, "mutation": how}
    print(f"parity-gate: self-check ({how}): {'CAUGHT' if caught else 'NOT CAUGHT'}")
    if not caught:
        print("parity-gate: SELF-CHECK FAILED — the comparison did not catch a planted "
              "divergence, so a pass from this gate is meaningless", file=sys.stderr)
        rc = 3

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    print(f"parity-gate: exit {rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
