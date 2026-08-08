#!/usr/bin/env python3
"""Corpus-wide can-it-fail sweep over the backend-parity C fixture corpus.

For each fixture: build it clean, run it, then plant a deliberate violation of
the exact property its first failure guard checks, rebuild, and re-run. The
fixture "can fail" only if the planted violation changes what its consumer
observes.

The consumer's oracle is taken from the manifest rather than assumed: every
backend-parity-c entry declares observation = {status: true, stdout: true,
stderr: false}, so a mutation counts as CAUGHT if it changes the exit status
OR stdout, and NOT-caught if it changes neither. stderr changes alone do NOT
count -- the manifest says nobody is looking at stderr.

Nothing in the repository is modified: sources are copied out and mutated in
place in the copy.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path("/home/newton/work/dev-hermit/hermit")
FIXTURES = REPO / "tests/backend-parity/fixtures"
WORK = Path("/home/newton/work/dev-hermit/ignored/mutsweep")
SRC = WORK / "src"
BIN = WORK / "bin"
CFLAGS = ["-O2", "-g", "-std=c11", "-Wall", "-Wextra", "-Werror", "-D_GNU_SOURCE"]


def balanced(text, open_at):
    """Index just past the ')' matching the '(' at open_at."""
    depth = 0
    i = open_at
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def body_span(text, start):
    """(text, end_index) of the statement or block following a condition."""
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "{":
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    return text[i : j + 1], j + 1
            j += 1
        return text[i:], len(text)
    j = text.find(";", i)
    return (text[i : j + 1], j + 1) if j != -1 else (text[i : i + 200], min(len(text), i + 200))


# A "contract check" is an `if` whose outcome the consumer can observe. Three
# shapes exist in this corpus and all three count:
#   hard   : else { fprintf(stderr,...); return 1; }   -> visible in exit status
#   helper : fail("...")                               -> visible in exit status
#   soft   : ok++                                      -> visible ONLY in stdout
# The soft shape is the majority. Treating only the hard shape as a check would
# report most of the corpus as having no oracle, which is false: `ok=N` is a
# real, if lossy, signal and the manifest says stdout is observed.
FAIL_BODY = re.compile(
    r"return\s+[1-9]|exit\s*\(\s*[1-9]|abort\s*\(|\b(?:fail|die)\s*\(|\bok\s*(?:\+\+|\+=)"
)


def find_failure_guard(text):
    """First `if (COND)` that decides a failure path. Returns (start, end, cond).

    The failure path may sit in EITHER branch. The dominant shape in this corpus
    is the else-branch form

        if (CONTRACT_HELD) { ok++; } else { fprintf(stderr, ...); return 1; }

    so a detector that only inspects the if-body misses most of the corpus and
    silently reports those fixtures as having no oracle at all.
    """
    for m in re.finditer(r"\bif\s*\(", text):
        open_at = text.index("(", m.start())
        close = balanced(text, open_at)
        if close == -1:
            continue
        cond = text[open_at + 1 : close]
        then_body, then_end = body_span(text, close + 1)
        region = then_body
        else_m = re.match(r"\s*else\b", text[then_end:])
        if else_m:
            region += body_span(text, then_end + else_m.end())[0]
        if not FAIL_BODY.search(region):
            continue
        return open_at, close, cond
    return None


def mutate(text):
    """Negate the first failure guard: a correct system now takes the failure path.

    This is the standard conditional-negation mutation operator. It simulates
    the observation being wrong, and so answers exactly the question asked:
    if the property this fixture checks were violated, would anyone notice?
    """
    site = find_failure_guard(text)
    if site is None:
        return None, "no failure guard found (no `if (...)` leading to a nonzero return/exit/abort)"
    open_at, close, cond = site
    mutant = text[: open_at + 1] + "!(" + cond + ")" + text[close:]
    return mutant, cond.strip().replace("\n", " ")[:90]


HERMIT = REPO / "target/debug/hermit"


def run(binary, timeout=90, under_hermit=True):
    """Run the fixture the way its consumer does.

    These fixtures pin HERMIT-VIRTUALIZED values -- cpuid_probe, for instance,
    asserts the fabricated CPUID identity, which no bare host satisfies. Judging
    them by a native run would report a working fixture as broken, so the
    verdict is taken from the hermit run and the native run is recorded only for
    contrast.
    """
    cmd = (
        [str(HERMIT), "run", "--backend", "ptrace", "--base-env", "minimal", "--", str(binary)]
        if under_hermit
        else [str(binary)]
    )
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr[:200]
    except subprocess.TimeoutExpired:
        return "timeout", "", ""


def compile_one(src, out):
    p = subprocess.run(
        ["cc", *CFLAGS, "-pthread", str(src), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    return p.returncode == 0, (p.stderr or p.stdout)[-300:]


def main():
    for d in (SRC, BIN):
        d.mkdir(parents=True, exist_ok=True)
    sources = sorted(FIXTURES.glob("*.c"))
    results = []
    for i, source in enumerate(sources, 1):
        name = source.stem
        text = source.read_text()
        row = {"fixture": name}

        clean_src = SRC / f"{name}.c"
        clean_src.write_text(text)
        ok, err = compile_one(clean_src, BIN / name)
        if not ok:
            row.update(verdict="COULD-NOT-TEST", reason=f"clean build failed: {err.strip()[:120]}")
            results.append(row)
            print(f"[{i}/{len(sources)}] {name}: COULD-NOT-TEST (build)", flush=True)
            continue
        rc0, out0, _ = run(BIN / name)
        rcn, outn, _ = run(BIN / name, under_hermit=False)
        row["clean_native_rc"] = rcn
        row["clean_rc"] = rc0
        row["clean_stdout"] = out0.strip()[:90]

        mutant, note = mutate(text)
        if mutant is None:
            row.update(verdict="OBSERVATION-ONLY", reason=note,
                       stdout_is_constant=not any(ch.isdigit() for ch in out0))
            results.append(row)
            print(f"[{i}/{len(sources)}] {name}: OBSERVATION-ONLY"
                  f" (no internal oracle; clean rc={rc0})", flush=True)
            continue
        row["mutated_guard"] = note

        mut_src = SRC / f"{name}.mut.c"
        mut_src.write_text(mutant)
        ok, err = compile_one(mut_src, BIN / f"{name}.mut")
        if not ok:
            row.update(
                verdict="COULD-NOT-TEST",
                reason=f"mutant build failed (-Werror): {err.strip()[:120]}",
            )
            results.append(row)
            print(f"[{i}/{len(sources)}] {name}: COULD-NOT-TEST (mutant build)", flush=True)
            continue
        rc1, out1, _ = run(BIN / f"{name}.mut")
        row["mut_rc"] = rc1
        row["mut_stdout"] = out1.strip()[:90]

        status_changed = rc0 != rc1
        stdout_changed = out0 != out1
        row["status_changed"] = status_changed
        row["stdout_changed"] = stdout_changed

        if rc0 != 0:
            # A fixture that is already red clean has an inert bracket: the
            # oracle cannot separate "clean" from "violated". This is the
            # membarrier_query class.
            row["verdict"] = "FAILS-CLEAN"
            row["reason"] = f"already rc={rc0} unmutated; negative bracket is inert"
        elif status_changed or stdout_changed:
            row["verdict"] = "CAN-FAIL"
            row["caught_by"] = (
                "status+stdout"
                if status_changed and stdout_changed
                else ("status" if status_changed else "stdout-only")
            )
        else:
            row["verdict"] = "CANNOT-FAIL"
            row["reason"] = "planted violation changed neither exit status nor stdout"
        results.append(row)
        print(
            f"[{i}/{len(sources)}] {name}: {row['verdict']}"
            f" (clean rc={rc0} -> mut rc={rc1})",
            flush=True,
        )

    (WORK / "results.json").write_text(json.dumps(results, indent=1))
    tally = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("\n=== TALLY ===")
    for k, v in sorted(tally.items()):
        print(f"  {k}: {v}")
    print(f"  TOTAL: {len(results)}")


if __name__ == "__main__":
    sys.exit(main())
