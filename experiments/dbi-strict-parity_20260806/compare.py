#!/usr/bin/env python3
"""DBI-vs-ptrace strict parity harness — the cross-backend comparator the product lacks.

`compare_two_runs` in hermit-cli has exactly two live callers and BOTH are
same-backend (run-twice, or record-vs-replay). So the product can answer "is this
backend self-consistent?" and cannot answer "does this backend match ptrace?".
This harness answers the second question, outside the product, so the gap can be
measured before the comparator is built.

METHOD (mirrors the BitwiseInfoV1 policy, and the parts that are not mirrored are
named in the README):
  * ENVIRONMENT IS PINNED. Non-negotiable: an unpinned environment puts
    INVOCATION_ID / scope names into the guest's envp, hence into its initial
    stack, and every stack hash then differs under EVERY backend. Measured
    2026-08-05: unpinned gives 3/3 distinct on ptrace alone.
  * Wall-clock prefix STRIPPED (the one irreproducible datum); everything else
    compared exactly.
  * Counts travel with the verdict: zero compared messages is a NO-RESULT, never
    a match.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BIN = ROOT / "worktrees/covnode/hermit/target/debug/hermit"
LIBS = ROOT / "ignored/haskell-drb/hostlibs"
BOX = ROOT / "scripts/hermit-box-run"

TS = re.compile(
    r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d\d \d\d:\d\d:\d\d\.\d+"
    r"|\d+-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z) +"
)

# The pinned environment. Anything not listed here does not reach the guest.
PINNED = {
    "PATH": "/usr/bin:/bin",
    "LD_LIBRARY_PATH": str(LIBS),
    "HOME": "/tmp",
    "TERM": "dumb",
    "LC_ALL": "C",
    "TZ": "UTC",
}


def messages(path: Path) -> list[str]:
    """Log messages with the wall-clock prefix stripped; everything else exact."""
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return []
    return [m.strip() for m in TS.split(raw) if m.strip()]


def subset(msgs: list[str], needle: str) -> list[str]:
    return [m for m in msgs if needle in m]


def run(backend: str, guest: list[str], log: Path, out: Path) -> int:
    """Run one backend inside the box.

    The child's STDERR is redirected INSIDE the boxed command, because the box
    wrapper summarises child output rather than passing it through — and DBI's
    entire INFO/DETLOG stream arrives on stderr (GAP-1). Without this the DBI
    side reads as 1 message and the comparison is vacuous.
    """
    env_args = " ".join(f"{k}={v}" for k, v in PINNED.items())
    inner = (
        f"env -i {env_args} {BIN} --backend {backend} --log info "
        f"--log-file {log} run --strict --detlog-stack --detlog-heap -- "
        + " ".join(guest)
        + f" > {out.with_suffix('.stdout')} 2> {out}"
    )
    cmd = [str(BOX), "--cpu-budget", "180", "--wall", "240",
           "--label", f"{backend}-{log.stem}", "--", "bash", "-c", inner]
    return subprocess.run(cmd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=600).returncode


def compare(name: str, guest: list[str], d: Path) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    res: dict = {"test": name, "guest": " ".join(guest)}
    logs = {}
    for be in ("ptrace", "dbi"):
        log, out = d / f"{be}.log", d / f"{be}.boxout"
        try:
            res[f"{be}_boxrc"] = run(be, guest, log, out)
        except subprocess.TimeoutExpired:
            res[f"{be}_boxrc"] = "TIMEOUT"
        # GAP-1: `--log-file` is silently ignored on DBI; its INFO/DETLOG stream
        # goes to STDERR instead (measured: ptrace logfile 85149 B / stderr 0
        # lines; dbi logfile ABSENT / stderr 375 DETLOG lines). Source each
        # backend from wherever it actually emits, so the parity comparison is
        # about the CONTENT and not about this routing defect.
        if log.exists() and log.stat().st_size > 0:
            logs[be], res[f"{be}_log_source"] = messages(log), "log-file"
        else:
            logs[be], res[f"{be}_log_source"] = messages(out), "stderr (GAP-1)"
        res[f"{be}_msgs"] = len(logs[be])

    for label, needle in (("info", ""), ("stack", "[memory]"), ("heap", "[memory]")):
        pass  # placeholder replaced below

    p, b = logs["ptrace"], logs["dbi"]
    res["info_ptrace"], res["info_dbi"] = len(p), len(b)
    if not p or not b:
        res["info_verdict"] = "NO-RESULT (zero messages on a side)"
    else:
        res["info_verdict"] = "MATCH" if p == b else "DIVERGE"
        if p != b:
            for i, (x, y) in enumerate(zip(p, b)):
                if x != y:
                    res["info_first_diff_index"] = i
                    res["info_first_diff_ptrace"] = x[:240]
                    res["info_first_diff_dbi"] = y[:240]
                    break
            else:
                res["info_first_diff_index"] = min(len(p), len(b))
                res["info_first_diff_ptrace"] = "(length differs only)"
                res["info_first_diff_dbi"] = f"ptrace={len(p)} dbi={len(b)}"

    for kind, tag in (("stack", "[stack]"), ("heap", "[heap]")):
        ps = [m for m in p if "[memory]" in m and tag in m]
        bs = [m for m in b if "[memory]" in m and tag in m]
        res[f"{kind}_ptrace_records"], res[f"{kind}_dbi_records"] = len(ps), len(bs)
        res[f"{kind}_ptrace_hash"] = hashlib.sha256("\n".join(ps).encode()).hexdigest()[:16]
        res[f"{kind}_dbi_hash"] = hashlib.sha256("\n".join(bs).encode()).hexdigest()[:16]
        if not ps and not bs:
            res[f"{kind}_verdict"] = "NO-RESULT (no records either side)"
        elif not ps or not bs:
            res[f"{kind}_verdict"] = "NO-RESULT (records on one side only)"
        else:
            res[f"{kind}_verdict"] = "MATCH" if ps == bs else "DIVERGE"
    return res


CORPUS = [
    ("true", ["/bin/true"]),
    ("echo", ["/bin/echo", "hello"]),
    ("pwd", ["/bin/pwd"]),
    ("date-utc", ["/bin/date", "-u", "+%Y"]),
    ("head-etc-hostname", ["/usr/bin/head", "-c", "16", "/etc/hostname"]),
    ("wc-self", ["/usr/bin/wc", "-c", "/bin/true"]),
]


def main() -> int:
    only = sys.argv[1:] or None
    results = []
    for name, guest in CORPUS:
        if only and name not in only:
            continue
        print(f"== {name}: {' '.join(guest)}", flush=True)
        r = compare(name, guest, HERE / "runs" / name)
        results.append(r)
        print(f"   info {r['info_verdict']} (ptrace {r['info_ptrace']} / dbi {r['info_dbi']} msgs)"
              f" | stack {r['stack_verdict']} | heap {r['heap_verdict']}", flush=True)
    (HERE / "results.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote {HERE/'results.json'} ({len(results)} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
