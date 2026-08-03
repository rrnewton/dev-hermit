#!/usr/bin/env python3
"""Shared wall/CPU estimate and completion reporting for command-line tools."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence


def _seconds(value: float) -> str:
    return f"{value:.3f}s"


def print_estimate(*, tool: str, wall_seconds: float, cpu_seconds: float, basis: str) -> None:
    print(
        "COST ESTIMATE "
        f"tool={tool} wall={_seconds(wall_seconds)} cpu={_seconds(cpu_seconds)} "
        f"basis={basis!r}",
        file=sys.stderr,
        flush=True,
    )


def print_actual(
    *,
    tool: str,
    wall_seconds: float,
    cpu_user_seconds: float,
    cpu_system_seconds: float,
    exit_description: str,
) -> None:
    cpu_seconds = cpu_user_seconds + cpu_system_seconds
    print(
        "COST ACTUAL "
        f"tool={tool} wall={_seconds(wall_seconds)} cpu={_seconds(cpu_seconds)} "
        f"cpu_user={_seconds(cpu_user_seconds)} cpu_system={_seconds(cpu_system_seconds)} "
        f"exit={exit_description}",
        file=sys.stderr,
        flush=True,
    )


def _exit_description(status: int) -> tuple[int, str]:
    if os.WIFEXITED(status):
        code = os.WEXITSTATUS(status)
        return code, str(code)
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        return 128 + sig, f"signal:{sig}"
    return 1, "unknown"


def run_command(
    command: Sequence[str],
    *,
    tool: str,
    estimate_wall_seconds: float,
    estimate_cpu_seconds: float,
    basis: str,
) -> int:
    print_estimate(
        tool=tool,
        wall_seconds=estimate_wall_seconds,
        cpu_seconds=estimate_cpu_seconds,
        basis=basis,
    )
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    old_handlers: dict[int, signal.Handlers] = {}

    def forward(signum: int, _frame: object) -> None:
        if process is None:
            return
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, forward)
        try:
            process = subprocess.Popen(list(command), start_new_session=True)
        except OSError as error:
            print(f"tool-cost: cannot launch {command[0]!r}: {error}", file=sys.stderr)
            print_actual(
                tool=tool,
                wall_seconds=time.monotonic() - started,
                cpu_user_seconds=0.0,
                cpu_system_seconds=0.0,
                exit_description="127",
            )
            return 127

        while True:
            try:
                _pid, status, usage = os.wait4(process.pid, 0)
                break
            except InterruptedError:
                continue
        code, description = _exit_description(status)
        process.returncode = code
        print_actual(
            tool=tool,
            wall_seconds=time.monotonic() - started,
            cpu_user_seconds=usage.ru_utime,
            cpu_system_seconds=usage.ru_stime,
            exit_description=description,
        )
        return code
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print an up-front cost estimate and final wall/CPU usage around a command."
    )
    parser.add_argument("--tool", required=True, help="stable tool/operation name")
    parser.add_argument("--estimate-wall-seconds", type=float, required=True)
    parser.add_argument("--estimate-cpu-seconds", type=float, required=True)
    parser.add_argument("--basis", required=True, help="parameters/history behind the estimate")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    if args.estimate_wall_seconds < 0 or args.estimate_cpu_seconds < 0:
        parser.error("estimates must be non-negative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run_command(
        args.command,
        tool=args.tool,
        estimate_wall_seconds=args.estimate_wall_seconds,
        estimate_cpu_seconds=args.estimate_cpu_seconds,
        basis=args.basis,
    )


if __name__ == "__main__":
    raise SystemExit(main())
