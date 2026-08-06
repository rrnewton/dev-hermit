#!/usr/bin/env python3
"""Launch one detached, admitted Hermit validation through ci-hub."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UNIT_RE = re.compile(r"^validate-[A-Za-z0-9_.@:-]+$")
Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_command(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def checked_output(
    command: Sequence[str], *, run: Runner, purpose: str
) -> str:
    result = run(list(command), check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"{purpose}: {detail}")
    return result.stdout.strip()


def sanitize_unit(raw: str) -> str:
    unit = raw.removesuffix(".service")
    if not UNIT_RE.fullmatch(unit):
        raise ValueError(
            f"invalid unit {raw!r}; expected validate- followed by letters, digits, or ._@:-"
        )
    return unit


def default_unit(agent: str, target: str) -> str:
    safe_agent = re.sub(r"[^A-Za-z0-9_.@:-]+", "-", agent).strip("-.")
    if not safe_agent:
        raise ValueError("--agent must contain at least one unit-safe character")
    return sanitize_unit(f"validate-{safe_agent}-{target[:12]}-{int(time.time())}")


def validate_checkout(checkout: Path, target: str, *, run: Runner) -> Path:
    checkout = checkout.resolve()
    if not SHA_RE.fullmatch(target):
        raise ValueError("--target must be an exact lowercase 40-hex commit SHA")
    if not (checkout / "validate.sh").is_file():
        raise ValueError(f"missing validate.sh in checkout {checkout}")

    top = Path(
        checked_output(
            ["git", "-C", str(checkout), "rev-parse", "--show-toplevel"],
            run=run,
            purpose="cannot resolve checkout root",
        )
    ).resolve()
    if top != checkout:
        raise ValueError(f"--checkout must name the repository root ({top}), not {checkout}")

    head = checked_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD^{commit}"],
        run=run,
        purpose="cannot resolve checkout HEAD",
    )
    if head != target:
        raise ValueError(f"checkout HEAD is {head}, not requested exact target {target}")

    dirty = checked_output(
        ["git", "-C", str(checkout), "status", "--porcelain=v1"],
        run=run,
        purpose="cannot inspect checkout cleanliness",
    )
    if dirty:
        first = dirty.splitlines()[0]
        raise ValueError(f"checkout is dirty ({first}); refusing unrepeatable validation")
    return checkout


def preflight(root: Path, checkout: Path, target: str, *, run: Runner) -> None:
    command = [
        str(root / "ci-hub/validate/preflight_validate.py"),
        "--head",
        target,
        "--repo-checkout",
        str(checkout),
    ]
    result = run(command, cwd=root, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"validation admission refused target: {detail}")


def build_systemd_command(
    *,
    root: Path,
    checkout: Path,
    target: str,
    agent: str,
    unit: str,
    log: Path,
    pr: int | None,
    validate_args: Sequence[str],
    wait: int,
    hold: int,
    child_deadline: int,
    environment: Mapping[str, str],
) -> list[str]:
    home = environment.get("HOME", "")
    path = environment.get("PATH", "")
    if not home or not path:
        raise ValueError("HOME and PATH must be set so cargo/rustup resolve inside the user unit")

    child = ["/usr/bin/env"]
    if pr is not None:
        child.append(f"PR_NUMBER={pr}")
    child.extend(["with-proxy", "./validate.sh", *(validate_args or ["full"])])

    return [
        "systemd-run",
        "--user",
        "--collect",
        "--unit",
        unit,
        "--description",
        f"ci-hub full validation {target[:12]} ({agent})",
        "--working-directory",
        str(checkout),
        "--setenv",
        f"HOME={home}",
        "--setenv",
        f"PATH={path}",
        "--setenv",
        "CI_HUB_VALIDATE_PRODUCER=systemd-user-v1",
        "--property",
        f"StandardOutput=append:{log}",
        "--property",
        f"StandardError=append:{log}",
        str(root / "ci-hub/ci-hub"),
        "validate-lock",
        "run",
        "--agent",
        agent,
        "--kind",
        "validate",
        "--target",
        target,
        "--wait",
        str(wait),
        "--hold",
        str(hold),
        "--child-deadline",
        str(child_deadline),
        "--",
        *child,
    ]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Launch validate.sh as a detached systemd user service whose only admission "
            "path is ci-hub validate-lock."
        )
    )
    result.add_argument("--checkout", required=True, type=Path)
    result.add_argument("--agent", required=True)
    result.add_argument("--target", required=True)
    result.add_argument("--pr", type=int)
    result.add_argument("--unit", help="validate-* unit name; .service suffix is optional")
    result.add_argument("--log", type=Path, help="durable log (default: ignored/validate/<unit>.log)")
    result.add_argument("--wait", type=int, default=7200, help="validate-lock queue wait bound")
    result.add_argument("--hold", type=int, default=1200, help="validate-lock lease seconds")
    result.add_argument("--child-deadline", type=int, default=3600)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--json", action="store_true")
    result.add_argument(
        "validate_args",
        nargs=argparse.REMAINDER,
        help="validate.sh arguments after -- (default: full)",
    )
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    run: Runner = run_command,
    environment: Mapping[str, str] | None = None,
    root: Path = ROOT,
) -> int:
    args = parser().parse_args(argv)
    validate_args = list(args.validate_args)
    if validate_args[:1] == ["--"]:
        validate_args.pop(0)
    if args.pr is not None and args.pr <= 0:
        print("validate-run: REFUSED: --pr must be positive", file=sys.stderr)
        return 2
    if min(args.wait, args.hold, args.child_deadline) <= 0:
        print("validate-run: REFUSED: wait/hold/child-deadline must be positive", file=sys.stderr)
        return 2

    try:
        checkout = validate_checkout(args.checkout, args.target, run=run)
        preflight(root, checkout, args.target, run=run)
        unit = sanitize_unit(args.unit) if args.unit else default_unit(args.agent, args.target)
        log = (args.log or root / "ignored/validate" / f"{unit}.log").resolve()
        command = build_systemd_command(
            root=root.resolve(),
            checkout=checkout,
            target=args.target,
            agent=args.agent,
            unit=unit,
            log=log,
            pr=args.pr,
            validate_args=validate_args,
            wait=args.wait,
            hold=args.hold,
            child_deadline=args.child_deadline,
            environment=environment or os.environ,
        )
        if not args.dry_run:
            log.parent.mkdir(parents=True, exist_ok=True)
            result = run(command, cwd=root, check=False)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
                raise RuntimeError(f"systemd-run refused service: {detail}")
    except (RuntimeError, ValueError) as error:
        print(f"validate-run: REFUSED: {error}", file=sys.stderr)
        return 2

    report = {
        "schema_version": 1,
        "action": "would-start" if args.dry_run else "started",
        "unit": f"{unit}.service",
        "target": args.target,
        "checkout": str(checkout),
        "log": str(log),
        "admission": "ci-hub validate-lock",
        "producer": "systemd-user-v1",
        "command": command if args.dry_run else None,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"validate-run: {report['action'].upper()} {report['unit']} "
            f"target={args.target} admission={report['admission']}"
        )
        print(f"LOG {log}")
        print(f"MONITOR systemctl --user show {unit}.service -p ActiveState -p SubState -p ExecMainStatus")
        if args.dry_run:
            print(f"COMMAND {shlex.join(command)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
