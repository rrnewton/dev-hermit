#!/usr/bin/env python3
"""Mutation-test the SHIPPED parity checker (`hermit log-diff`).

A parity check that never fails is worthless. This plants known divergences into
one side of a KNOWN-CLEAN log pair and asserts the checker flags each one — and,
just as important, asserts it does NOT flag the things it is specifically
designed to tolerate (the wall-clock prefix). Both directions, with a kill score.

TARGET: `hermit log-diff <A> <B>` — the shipped comparator, not a reimplementation.
Its default policy (LogDiffOpts::default) is: no stripping, no address
canonicalization, Deterministic message set (DETLOG + scheduler COMMIT).
`[memory]` stack/heap records are DETLOG, so they are in scope.

BASELINE: two ptrace runs of one guest under a PINNED environment. Pinning is
non-negotiable — an unpinned environment puts INVOCATION_ID / systemd scope names
into the guest's envp and therefore its initial stack, and every stack hash then
differs run-to-run under every backend (measured 2026-08-05: 3/3 distinct).
Without pinning there is no clean baseline to mutate.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BIN = ROOT / "worktrees/covnode/hermit/target/debug/hermit"
LIBS = ROOT / "ignored/haskell-drb/hostlibs"
BOX = ROOT / "scripts/hermit-box-run"
RUNS = HERE / "runs"

PINNED = ["PATH=/usr/bin:/bin", f"LD_LIBRARY_PATH={LIBS}", "HOME=/tmp",
          "TERM=dumb", "LC_ALL=C", "TZ=UTC"]


def produce(tag: str) -> Path:
    """One boxed ptrace run with stack+heap detlogs; returns the log path."""
    RUNS.mkdir(parents=True, exist_ok=True)
    log = RUNS / f"{tag}.log"
    inner = (f"env -i {' '.join(PINNED)} {BIN} --log info --log-file {log} "
             f"run --strict --detlog-stack --detlog-heap -- /bin/echo hello "
             f"> {RUNS/tag}.stdout 2> {RUNS/tag}.stderr")
    subprocess.run([str(BOX), "--cpu-budget", "120", "--wall", "180",
                    "--label", f"base-{tag}", "--", "bash", "-c", inner],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=400)
    return log


def check(a: Path, b: Path) -> tuple[bool, str]:
    """Run the SHIPPED checker. Returns (flagged?, evidence line)."""
    p = subprocess.run([str(BIN), "log-diff", str(a), str(b)],
                       capture_output=True, text=True, timeout=300,
                       env={"LD_LIBRARY_PATH": str(LIBS), "PATH": "/usr/bin:/bin"})
    blob = p.stdout + p.stderr
    # The checker reports differences in prose; "no substantive differences" is
    # its clean verdict. Treat ANY of: nonzero rc, a Mismatch line, or an
    # extra-messages line as FLAGGED.
    clean = "no substantive differences" in blob
    flagged = (not clean) or p.returncode != 0
    ev = ""
    for key in ("Mismatch at log messages", "extra messages not matched",
                "ZERO", "no substantive differences"):
        m = [l for l in blob.splitlines() if key in l]
        if m:
            ev = m[0].strip()[:150]
            break
    return flagged, f"rc={p.returncode} {ev}"


# --- mutations -------------------------------------------------------------
# Each takes the clean text and returns mutated text, or None if inapplicable.

def m_heap_hash(t: str) -> str | None:
    """Flip one hex digit of a [heap] record's content hash."""
    for line in t.splitlines():
        if "[memory]" in line and "[heap]->" in line:
            h = line.split("[heap]->")[1].strip()
            new = ("f" if h[0] != "f" else "0") + h[1:]
            return t.replace(line, line.replace(h, new), 1)
    return None


def m_stack_hash(t: str) -> str | None:
    for line in t.splitlines():
        if "[memory]" in line and "[stack]->" in line:
            h = line.split("[stack]->")[1].strip()
            new = ("f" if h[0] != "f" else "0") + h[1:]
            return t.replace(line, line.replace(h, new), 1)
    return None


def m_drop_memory_record(t: str) -> str | None:
    lines = t.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if "[memory]" in line:
            del lines[i]
            return "".join(lines)
    return None


def m_syscall_result(t: str) -> str | None:
    """Change a numeric syscall RESULT — the class `--unsafe-strip-lines` erases."""
    m = re.search(r"(finish syscall #\d+: \w+\([^)]*\) = Ok\()(\d+)(\))", t)
    if not m:
        return None
    return t[: m.start(2)] + str(int(m.group(2)) + 1) + t[m.end(2) :]


def m_syscall_arg_hex(t: str) -> str | None:
    m = re.search(r"DETLOG \[syscall\].*?0x([0-9a-f]{4,})", t)
    if not m:
        return None
    old = m.group(1)
    new = ("f" if old[0] != "f" else "0") + old[1:]
    return t[: m.start(1)] + new + t[m.end(1) :]


def m_swap_two_records(t: str) -> str | None:
    """Reorder two adjacent [memory] records: ordering must matter."""
    lines = t.splitlines(keepends=True)
    idx = [i for i, l in enumerate(lines) if "[memory]" in l]
    for a, b in zip(idx, idx[1:]):
        if lines[a] != lines[b]:
            lines[a], lines[b] = lines[b], lines[a]
            return "".join(lines)
    return None


def m_wallclock_only(t: str) -> str | None:
    """NEGATIVE CONTROL: change ONLY the wall-clock prefixes.

    The canonical policy strips exactly this. If the checker flags it, the
    checker is over-strict and every real run would be a false red.
    """
    out, n = re.subn(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z",
                     "2099-12-31T23:59:59.999999Z", t, flags=re.M)
    return out if n else None


MUTANTS = [
    ("heap-record-hash", m_heap_hash, True),
    ("stack-record-hash", m_stack_hash, True),
    ("drop-one-memory-record", m_drop_memory_record, True),
    ("syscall-numeric-result", m_syscall_result, True),
    ("syscall-hex-argument", m_syscall_arg_hex, True),
    ("swap-two-memory-records", m_swap_two_records, True),
    ("wall-clock-prefix-only", m_wallclock_only, False),  # must NOT be flagged
]


def main() -> int:
    a, b = produce("runA"), produce("runB")
    if not (a.exists() and b.exists()):
        print("FATAL: baseline logs not produced", file=sys.stderr)
        return 2
    results = []

    flagged, ev = check(a, b)
    print(f"[positive control] clean pair            -> flagged={flagged}  {ev}")
    results.append({"case": "clean-pair", "expect_flagged": False,
                    "flagged": flagged, "ok": not flagged, "evidence": ev})
    if flagged:
        print("  !! baseline is NOT clean; mutation results below are unusable", file=sys.stderr)

    clean_text = b.read_text(errors="replace")
    for name, fn, expect in MUTANTS:
        mutated = fn(clean_text)
        if mutated is None or mutated == clean_text:
            print(f"[mutant] {name:26s} -> NOT APPLICABLE (pattern absent)")
            results.append({"case": name, "expect_flagged": expect,
                            "flagged": None, "ok": None, "evidence": "not applicable"})
            continue
        mp = RUNS / f"mutant-{name}.log"
        mp.write_text(mutated)
        flg, ev = check(a, mp)
        ok = (flg == expect)
        verdict = "KILLED" if (expect and flg) else (
            "SURVIVED <-- CHECKER BLIND" if expect else (
                "correctly tolerated" if not flg else "FALSE POSITIVE <-- over-strict"))
        print(f"[mutant] {name:26s} -> flagged={str(flg):5s} {verdict}")
        results.append({"case": name, "expect_flagged": expect, "flagged": flg,
                        "ok": ok, "evidence": ev})

    killable = [r for r in results if r["expect_flagged"] and r["flagged"] is not None]
    killed = [r for r in killable if r["flagged"]]
    controls = [r for r in results if not r["expect_flagged"] and r["flagged"] is not None]
    controls_ok = [r for r in controls if not r["flagged"]]
    score = {
        "mutants_planted": len(killable),
        "mutants_killed": len(killed),
        "kill_score": f"{len(killed)}/{len(killable)}",
        "controls_planted": len(controls),
        "controls_correctly_not_flagged": len(controls_ok),
        "not_applicable": sum(1 for r in results if r["flagged"] is None),
    }
    print(f"\nKILL SCORE: {score['kill_score']} mutants killed; "
          f"controls {len(controls_ok)}/{len(controls)} correctly not flagged; "
          f"{score['not_applicable']} not applicable")
    (HERE / "results.json").write_text(json.dumps({"score": score, "cases": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
