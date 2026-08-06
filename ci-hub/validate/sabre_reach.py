#!/usr/bin/env python3
"""A SaBRe cell that patched NOTHING is a ptrace cell wearing a SaBRe label.

MEASURED 2026-08-06, this box, hermit release AND debug binaries
-----------------------------------------------------------------
`hermit --log=info run --backend sabre -- <guest>` emits, on the fallback path:

    INFO hermit::sabre::fallback: SaBRe ptrace fallback completed patched_sites=0

`patched_sites` is the SaBRe analogue of e9patch's `mapped_sites`, and it splits
SaBRe into TWO REGIMES THAT THE SCORECARD CANNOT CURRENTLY TELL APART:

  regime A -- patched_sites == 0  -> hermit runs the guest through the PTRACE
      FALLBACK. Measured inbound-syscall counts vs ptrace on the same guest:
          /bin/echo   ptrace 132   sabre 120   (patched_sites=0)
          churn3      ptrace  64   sabre  55   (patched_sites=0)
      ~86-91% of ptrace's syscalls, rc=0, no warning on stderr. It scores as a
      near-perfect SaBRe cell BECAUSE SABRE DID NOTHING -- it is ptrace.

  regime B -- patched_sites > 0   -> the in-guest SaBRe path runs, and only the
      syscalls the MAIN ELF issues are intercepted. The prior measurement
      (ai_docs/sabre-strict-parity-reachability-wall-measured_20260806.md,
      fixture t_dyn) recorded ptrace determinizing 49 syscalls and SaBRe 4:
      every ld.so/libc-startup syscall invisible.

THE TRAP, and why this gate exists
-----------------------------------
The two regimes fail in OPPOSITE directions, so a cell that reports only
pass/fail cannot be interpreted at all:

  * regime A looks EXCELLENT (near-full syscall parity) and means SaBRe was
    never exercised;
  * regime B looks POOR (4 of 49) and is the only regime that is actually a
    SaBRe result.

Reporting them under one "sabre" label lets a corpus average the two into a
number that describes neither. `patched_sites` is the CONDITION that must
travel with every SaBRe verdict -- exactly the "carry the condition with the
value" predicate.

WHY patched_sites==0 IS THE COMMON CASE
----------------------------------------
Same root cause as the e9patch reachability wall, confirmed by disassembly:
a dynamically linked guest issues its syscalls from libc, so the main ELF
contains no `syscall` instruction to rewrite.

    objdump -d <guest> | grep -c '\\tsyscall'
      /bin/echo, /bin/true -> 0   -> patched_sites=0
      churn3 (inline-asm syscall, in-image site) -> 1 -> patched_sites=0 (!)

Note the churn3 row: e9patch DOES rewrite that same binary (`mapped_sites=1`),
SaBRe does not. So SaBRe's reach on this build is narrower than e9patch's, and
`patched_sites=0` was observed on EVERY guest tried here -- meaning every
SaBRe cell measured on this box to date is a regime-A cell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

REAL = "sabre-exercised"
FALLBACK = "vacuous-ptrace-fallback"
UNKNOWN = "unknown-no-counter"

# `patched_sites=N`, wherever it appears (fallback line today, but the gate
# keys on the counter itself rather than on the surrounding sentence, so a
# reworded log line does not silently turn into UNKNOWN).
_COUNTER = re.compile(r"patched_sites=(?P<patched>\d+)")
_FALLBACK_LINE = re.compile(r"sabre::fallback")


@dataclass
class SabreReach:
    state: str
    reason: str
    patched_sites: Optional[int] = None
    fallback_announced: bool = False

    @property
    def is_sabre_result(self) -> bool:
        return self.state == REAL


def parse_patched_sites(text: str) -> Optional[int]:
    matches = _COUNTER.findall(text or "")
    if not matches:
        return None
    # Last wins: the fallback summary is emitted after any per-object lines.
    return int(matches[-1])


def classify_run(run_output: str) -> SabreReach:
    """Which regime did this SaBRe run actually execute in?"""
    patched = parse_patched_sites(run_output)
    announced = bool(_FALLBACK_LINE.search(run_output or ""))
    if patched is None:
        return SabreReach(
            UNKNOWN,
            "no patched_sites counter in the run output -- cannot tell whether "
            "SaBRe patched anything, and an unanswerable question is not a pass",
            None,
            announced,
        )
    if patched == 0:
        return SabreReach(
            FALLBACK,
            "patched_sites=0: SaBRe rewrote nothing and the guest ran through "
            "the ptrace fallback. Near-full syscall parity here measures "
            "ptrace, not SaBRe, and must not be recorded as a SaBRe result",
            patched,
            announced,
        )
    return SabreReach(
        REAL,
        f"{patched} site(s) patched -- in-guest SaBRe path. NOTE: reach is "
        "main-ELF-only, so loader/libc-startup syscalls are still "
        "uninstrumented; this cell cannot claim FULL parity",
        patched,
        announced,
    )


def qualify_parity_cell(*, backend: str, parity_pass: bool,
                        run_output: str) -> SabreReach:
    """Gate a scorecard cell. A passing SaBRe cell that patched nothing is
    refused; other backends are unaffected."""
    if backend != "sabre":
        return SabreReach(REAL, f"backend={backend}: reach gate does not apply")
    verdict = classify_run(run_output)
    if verdict.state == FALLBACK and parity_pass:
        verdict.reason = "PARITY PASS REFUSED -- " + verdict.reason
    return verdict


def full_parity_claim_allowed(run_output: str) -> tuple[bool, str]:
    """The task's negative condition: a main-ELF-only run cannot claim full
    parity. TRUE requires a real SaBRe run AND loader coverage, and loader
    coverage is not achievable on this build at all -- so this is False for
    every regime today. It is a predicate, not a constant: it becomes True the
    moment a run reports loader-object patching."""
    verdict = classify_run(run_output)
    if verdict.state != REAL:
        return False, verdict.reason
    return False, (
        f"patched_sites={verdict.patched_sites} covers the main ELF only; "
        "ld.so/libc-startup syscalls are not intercepted, so full detlog "
        "parity against ptrace is unreachable for this cell"
    )


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-output", help="file containing the run's stderr/log")
    args = ap.parse_args(argv)
    if not args.run_output:
        print(__doc__.strip().splitlines()[0])
        return 2
    v = classify_run(open(args.run_output, errors="replace").read())
    print(f"sabre-reach: {v.state} (patched_sites={v.patched_sites}) -- {v.reason}")
    return 0 if v.is_sabre_result else 1


if __name__ == "__main__":
    raise SystemExit(main())
