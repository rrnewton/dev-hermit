#!/usr/bin/env python3
"""Command-line front end for `headline.py`, so a rust-script emitter can call it.

WHY THIS FILE EXISTS. The executed-count rule lives in `headline.py`, but all
three emission sites it is meant to protect --- `render-scorecard.rs`,
`expansion-dag.rs` and `collect-e9patch-compat.rs` --- are `rust-script`. A Rust
program cannot import a Python module, and `headline.py` has no `__main__` and
is not executable, so it could not be invoked either. That is the whole reason
"it exists but nothing calls it": nothing *could*.

Shelling out is the idiomatic route here rather than a second implementation in
Rust: `render-scorecard.rs:253` already does
`Command::new(script_dir().join("check-scorecard-provenance.py"))`, and a Rust
port would be a second copy of the rule that drifts from this one and needs its
own mutation bracket to stay honest.

This file is ADDITIVE. It does not modify `headline.py`, which is another
agent's uncommitted work.

CONTRACT (the emitter side of the wire):

    headline_cli.py --headline LABEL:PASSED:EXECUTED:DENOMINATOR [...]

  exit 0  every headline rendered; the rendering is on stdout
  exit 2  REFUSED --- the summary is not renderable as stated, reason on stderr

Exit 2 is the load-bearing half. An emitter must treat it as "do not print a
headline", not as "print an empty one": a refusal that the caller ignores is the
same defect one level up.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from headline import Headline, HeadlineError, render_all  # noqa: E402


def parse_spec(spec: str) -> Headline:
    parts = spec.split(":")
    if len(parts) != 4:
        raise HeadlineError(
            f"{spec!r}: expected LABEL:PASSED:EXECUTED:DENOMINATOR, got {len(parts)} field(s)"
        )
    label, rest = parts[0], parts[1:]
    if not label:
        raise HeadlineError(f"{spec!r}: empty label")
    try:
        passed, executed, denominator = (int(x) for x in rest)
    except ValueError as exc:
        raise HeadlineError(f"{spec!r}: non-integer count ({exc})") from exc
    return Headline(label=label, passed=passed, executed=executed, denominator=denominator)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--headline", action="append", default=[], metavar="L:P:E:D",
                    help="one headline; repeatable. Every field is required, "
                         "including EXECUTED -- that is the point of the rule.")
    args = ap.parse_args(argv)
    try:
        headlines = [parse_spec(s) for s in args.headline]
        # render_all refuses an empty summary rather than printing a clean total.
        sys.stdout.write(render_all(headlines) + "\n")
    except HeadlineError as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
