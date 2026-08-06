#!/usr/bin/env python3
"""NEGATIVE CONTROL for the self-determinism gate in capture_goldens.py.

Every guest in the ladder passed the gate on the first attempt. That is a
suspicious result, not a reassuring one: a gate that cannot refuse anything
produces exactly the same output, and "GOLDEN" would then mean nothing.

TWO PARTS, and the first is the one that actually proves anything.

PART 1 -- MUTATION BRACKET (decisive). Take a REAL captured golden and plant each
class of divergence the gate claims to catch, then feed it through the very same
`classify()` the capture uses -- not a reimplementation, which could drift. A
mutation that survives means the gate is blind to that class. This is decisive
because it does not depend on finding a guest that happens to misbehave.

PART 2 -- LIVE NEGATIVE ATTEMPTS (informative, and they FAILED to be negative).
The canonical nondeterministic guest from prior work is `id`: task
impl-dbi-golden-log-comparison recorded ptrace run 1 doing NSS
socket/connect/sendto/poll while run 2 went straight to write/close, a 14,838
message difference. That did NOT reproduce here -- `id` is stably deterministic
under this configuration, 325 log lines with no NSS traffic at all. The prior
finding was explicitly a COLD-cache effect; the cache is warm now, and
`--base-env minimal` removes the environment that steered the lookup. So this is
a narrowing of the prior result, not a contradiction of it, and it is the reason
Part 1 exists: no live guest available here is nondeterministic, so the gate must
be bracketed synthetically or not at all.

Also probed and found deterministic under hermit (all virtualized, so none can
serve as a negative control): /proc/uptime (120.00), /proc/loadavg (zeroed),
/proc/self/stat (pid 3), /proc/interrupts, and getent passwd root.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_goldens import classify, counts, run_once  # noqa: E402

HERE = Path(__file__).resolve().parent
BINARY = Path("/home/newton/work/dev-hermit/worktrees/verify/hermit/target/release/hermit")
LIBDIR = Path("/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64")
WORK = Path("/home/newton/work/dev-hermit/ignored/golden-capture")
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TERM": "dumb",
       "LC_ALL": "C", "TZ": "UTC", "LD_LIBRARY_PATH": str(LIBDIR)}


def mutations(golden: str):
    """One planted divergence per class the gate claims to detect."""
    lines = golden.splitlines(keepends=True)
    mid = len(lines) // 2
    # A syscall-value flip: change a digit inside a DETLOG line.
    val = next((i for i, l in enumerate(lines) if "DETLOG" in l and any(c.isdigit() for c in l)), mid)
    flipped = list(lines)
    line = flipped[val]
    for j, ch in enumerate(line):
        if ch.isdigit():
            flipped[val] = line[:j] + ("9" if ch != "9" else "0") + line[j + 1:]
            break
    yield "digit-flip in a DETLOG line", "".join(flipped)

    dropped = lines[:mid] + lines[mid + 1:]
    yield "one line dropped", "".join(dropped)

    added = lines[:mid] + ["INFO detcore: DETLOG injected extra message\n"] + lines[mid:]
    yield "one line inserted", "".join(added)

    yield "trailing truncation", "".join(lines[: max(1, len(lines) - 3)])


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    golden_path = HERE / "goldens" / "echo.log"
    if not golden_path.exists():
        print("bracket: run capture_goldens.py first", file=sys.stderr)
        return 2
    golden = golden_path.read_text()

    print("PART 1 -- mutation bracket against the real classify()")
    caught = 0
    total = 0
    # Positive control first: unmutated must be GOLDEN, or the gate is stuck-refusing.
    v, why = classify(0, 0, "x", "x", golden, golden)
    print(f"  {'unmutated (positive control)':44s} -> {v:14s} {why[:60]}")
    pos_ok = v == "GOLDEN"
    for name, mutant in mutations(golden):
        total += 1
        v, why = classify(0, 0, "x", "x", golden, mutant)
        hit = v == "NOT-A-GOLDEN"
        caught += hit
        print(f"  {name:44s} -> {v:14s} {'CAUGHT' if hit else 'MISSED'}")
    # Non-log channels the gate also claims.
    for name, args in [("exit code differs", (0, 1, "x", "x", golden, golden)),
                       ("guest stdout differs", (0, 0, "a", "b", golden, golden)),
                       ("empty log is NO-RESULT not a pass", (0, 0, "x", "x", "", ""))]:
        total += 1
        v, why = classify(*args)
        hit = v in ("NOT-A-GOLDEN", "NO-RESULT")
        caught += hit
        print(f"  {name:44s} -> {v:14s} {'CAUGHT' if hit else 'MISSED'}")

    print("\nPART 2 -- live negative attempts (expected to be non-negative here)")
    for gid, argv in [("id", ["/usr/bin/id"]), ("getent", ["/usr/bin/getent", "passwd", "root"])]:
        rc1, n1, so1, _ = run_once(BINARY, argv, WORK / f"br-{gid}.1.log", ENV, 180)
        rc2, n2, so2, _ = run_once(BINARY, argv, WORK / f"br-{gid}.2.log", ENV, 180)
        v, why = classify(rc1, rc2, so1, so2, n1, n2)
        c = counts(n1)
        print(f"  {gid:12s} -> {v:14s} Z={c['Z_commits']:>3} lines={c['log_lines']:>5}  {why[:50]}")

    ok = pos_ok and caught == total
    print(f"\nmutation bracket: {caught}/{total} planted divergences caught; "
          f"positive control {'held' if pos_ok else 'FAILED'}")
    if not ok:
        print("GATE IS NOT FULLY BRACKETED -- a GOLDEN verdict does not cover the MISSED classes.",
              file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
