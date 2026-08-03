#!/usr/bin/env python3
"""Run and classify the bounded ci-hub health probe used by dev-hermit CI."""

from __future__ import annotations

import argparse
import os
import resource
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI_HUB = ROOT / "ci-hub/ci-hub"


def classify(returncode: int, output: str) -> tuple[bool, str]:
    if "# ci-hub/health tool COST ACTUAL" not in output:
        return False, "ci-hub did not report final wall/CPU cost"
    if "GitHub main health:" not in output or "CI health:" not in output:
        return False, "ci-hub did not emit both live health sections"
    if returncode in (0, 1):
        return True, "GitHub responded; health state is authoritative"
    partial_markers = ("UNAVAILABLE", "PARTIAL RESULT", "NO CURRENT-TIP RUNS")
    if returncode == 2 and any(marker in output for marker in partial_markers):
        return (
            True,
            "GitHub was unavailable or incomplete; ci-hub returned a bounded partial result",
        )
    return (
        False,
        f"ci-hub failed without a classified external-service result (exit {returncode})",
    )


def _cpu_limit(seconds: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="rrnewton/dev-hermit")
    parser.add_argument("--wall-seconds", type=int, default=30)
    parser.add_argument("--cpu-seconds", type=int, default=15)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    if args.wall_seconds <= 0 or args.cpu_seconds <= 0:
        parser.error("wall and CPU limits must be positive")

    process = subprocess.Popen(
        [str(CI_HUB), "health", "--repo", args.repo],
        cwd=ROOT,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=lambda: _cpu_limit(args.cpu_seconds),
    )
    try:
        stdout, stderr = process.communicate(timeout=args.wall_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        output = stdout + stderr
        args.log.write_text(output)
        print(output, end="")
        print(
            f"ci-hub broken: live health exceeded the {args.wall_seconds}s wall bound",
            file=sys.stderr,
        )
        return 1

    output = stdout + stderr
    args.log.write_text(output)
    print(output, end="")
    accepted, reason = classify(process.returncode, output)
    annotation = "notice" if accepted else "error"
    print(f"::{annotation}::{reason}")
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
