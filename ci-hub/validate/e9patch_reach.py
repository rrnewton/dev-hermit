#!/usr/bin/env python3
"""An e9patch cell that instrumented NOTHING is not an e9patch result.

MEASURED 2026-08-06, on this box, with the release `hermit` binary
-------------------------------------------------------------------
`hermit --backend e9patch run --strict -- <guest>` prints a banner:

    Backend: e9patch preprocessing + ptrace runtime;
    candidate_sites=0; mapped_sites=0; b0_sites=0; ...

For every ordinary guest tried, `mapped_sites` is **0**:

    /bin/true, /bin/echo, /usr/bin/seq, /bin/sh, /usr/bin/wc, /usr/bin/python3
        -> candidate_sites=0  mapped_sites=0
    hermit itself (a large Rust binary)
        -> candidate_sites=57 mapped_sites=57

ROOT CAUSE, confirmed by disassembly rather than inferred: e9patch rewrites the
MAIN ELF, but a dynamically linked guest issues its syscalls from `libc.so`, so
the main ELF contains no `syscall` instruction to rewrite.

    objdump -d <guest> | grep -c '\\tsyscall'
      /bin/echo /bin/true /usr/bin/seq /usr/bin/python3 ... -> 0   (18 of 18 checked)
      hermit                                                -> 33
      tests/backend-parity/fixtures/cpuid_probe.c (compiled) -> 0

WHY THIS MATTERS MORE THAN IT LOOKS
-----------------------------------
The banner says "e9patch preprocessing + ptrace runtime". With `mapped_sites=0`
NOTHING IS REWRITTEN, so the guest runs under the plain ptrace runtime. A
parity sweep of e9patch-vs-ptrace over such a corpus therefore compares ptrace
WITH ITSELF and reports 100% parity — a perfect score that exercised none of
the backend under test. That is a vacuous green of the most expensive kind: it
reads as evidence that e9patch has reached parity.

So `mapped_sites` is the CONDITION that must travel with the verdict. A cell
without it cannot be distinguished from a real one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

REAL = "e9patch-exercised"
VACUOUS = "vacuous-ptrace-passthrough"
UNKNOWN = "unknown-no-banner"

_BANNER = re.compile(
    r"candidate_sites=(?P<cand>\d+);\s*mapped_sites=(?P<mapped>\d+)")


@dataclass
class ReachVerdict:
    state: str
    reason: str
    candidate_sites: Optional[int] = None
    mapped_sites: Optional[int] = None

    @property
    def is_e9patch_result(self) -> bool:
        return self.state == REAL


def parse_banner(text: str) -> Optional[tuple[int, int]]:
    m = _BANNER.search(text or "")
    if not m:
        return None
    return int(m.group("cand")), int(m.group("mapped"))


def classify_run(stdout_or_stderr: str) -> ReachVerdict:
    """Did this e9patch run actually instrument anything?"""
    parsed = parse_banner(stdout_or_stderr)
    if parsed is None:
        return ReachVerdict(
            UNKNOWN,
            "no e9patch banner found -- cannot tell whether the backend did "
            "anything, and an unanswerable question is not a pass")
    cand, mapped = parsed
    if mapped == 0:
        return ReachVerdict(
            VACUOUS,
            "mapped_sites=0: nothing was rewritten, so the guest ran under the "
            "plain ptrace runtime. A parity verdict here compares ptrace with "
            "itself and is not evidence about e9patch",
            cand, mapped)
    return ReachVerdict(REAL, f"{mapped} site(s) rewritten", cand, mapped)


def qualify_parity_cell(*, backend: str, parity_pass: bool,
                        run_output: str) -> ReachVerdict:
    """Gate a scorecard cell. A passing e9patch cell that rewrote nothing is
    refused; other backends are unaffected."""
    if backend != "e9patch":
        return ReachVerdict(REAL, f"backend={backend}: reach gate does not apply")
    verdict = classify_run(run_output)
    if verdict.state == REAL:
        return verdict
    if parity_pass:
        verdict.reason = ("PARITY PASS REFUSED -- " + verdict.reason)
    return verdict


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-output", help="file containing the run's banner output")
    args = ap.parse_args(argv)
    if not args.run_output:
        print(__doc__.strip().splitlines()[0])
        return 2
    v = classify_run(open(args.run_output, errors="replace").read())
    print(f"e9patch-reach: {v.state} (candidate={v.candidate_sites}, "
          f"mapped={v.mapped_sites}) -- {v.reason}")
    return 0 if v.is_e9patch_result else 1


if __name__ == "__main__":
    raise SystemExit(main())
