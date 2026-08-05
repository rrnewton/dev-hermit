#!/usr/bin/env python3
"""Compatibility shim for the canonical Rust qualified-row verifier.

Historical callers invoked this file directly. It deliberately contains no
receipt predicate: every invocation delegates to ``ci-hub ledger
qualified-rows``, which resolves Reverie main once and uses the same exact-head
semantic verifier as validation/landing consumers.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CI_HUB = ROOT / "ci-hub" / "ci-hub"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--hermit-repo", type=Path, default=ROOT / "hermit")
    parser.add_argument("--repo", default="rrnewton/hermit")
    args = parser.parse_args()
    command = [str(CI_HUB), "ledger", "qualified-rows",
               "--hermit-repo", str(args.hermit_repo), "--repo", args.repo]
    if args.ledger is not None:
        command += ["--ledger", str(args.ledger)]
    os.execv(command[0], command)
    raise AssertionError("os.execv returned")


if __name__ == "__main__":
    raise SystemExit(main())
