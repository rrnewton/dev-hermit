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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pane_owner
import run_registry


ROOT = Path(__file__).resolve().parents[2]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UNIT_RE = re.compile(r"^validate-[A-Za-z0-9_.@:-]+$")
Runner = Callable[..., subprocess.CompletedProcess[str]]
TERMINAL_STATES = frozenset(("failed", "inactive"))


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



def _prepend_path(existing: str, entry: str) -> str:
    """Put `entry` first in a colon-separated search path, without duplicating it."""
    parts = [p for p in existing.split(":") if p and p != entry]
    return ":".join([entry, *parts])



# Directories that may hold libunwind's SHARED objects at run time, best first.
# `ignored/lu-parity/usr/lib64` is deliberately last: it has the .pc and the
# static `libunwind-ptrace.a`, but no `libunwind-ptrace.so*`.
RUNTIME_CANDIDATES = (
    "~/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib",
    "~/.local/hermit-deps/lu/usr/lib64",
)


def _libunwind_runtime_dir(root: Path, link_dir: Path) -> Path:
    """Pick the directory that can actually satisfy the loader.

    `libunwind-ptrace.so.0` is THE file that matters, because pointing
    LIBRARY_PATH at a tree carrying a shared `libunwind-ptrace.so` is what makes
    the link depend on it in the first place (see build_systemd_command). A
    directory that has `libunwind.so.8` but NOT the ptrace variant cannot satisfy
    that dependency, so it must never be preferred over one that can.

    `link_dir` competes on equal terms rather than being a last resort. It used
    to be excluded on the belief that it ships only a static
    `libunwind-ptrace.a`; that is no longer true of `ignored/lu-parity`, which
    now carries the shared objects too and can satisfy build, link and run on its
    own. Excluding it meant that with the first candidate absent the probe
    selected `~/.local/hermit-deps/lu/usr/lib64` -- which has only the static
    `.a` -- on a `libunwind.so.8` tiebreak, producing exactly the
    `libunwind-ptrace.so.0: cannot open shared object file` failure this probe
    exists to prevent.

    Order is still candidates-then-link_dir, so on a host where a candidate has
    the ptrace object the selection is unchanged.
    """
    candidates = [Path(c).expanduser() for c in RUNTIME_CANDIDATES] + [link_dir]
    # First pass: only a directory that carries the object the link needs.
    for candidate in candidates:
        if (candidate / "libunwind-ptrace.so.0").exists():
            return candidate
    # Second pass: no ptrace object anywhere. A tree with the base library is
    # still the best available guess for a statically-linked-ptrace build.
    for candidate in candidates:
        if (candidate / "libunwind.so.8").exists():
            return candidate
    return link_dir


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

    # libunwind is not installed system-wide on this host class, so
    # `unwind-sys`'s build.rs panics on
    #   pkg-config --libs --cflags libunwind-ptrace
    # and every DAG lane that builds the workspace fails with an environment
    # fault that looks exactly like a product red. The .pc and .so live in the
    # repo; point the unit at them when they are present. Nothing is
    # substituted or weakened if they are absent -- the unit simply runs as it
    # did before and the build fails loudly, as it should.
    #
    # THREE variables are required, and they are NOT interchangeable -- getting
    # this wrong yields a DIFFERENT failure at a LATER node, which is why
    # propagating only the first and third still looked broken:
    #   PKG_CONFIG_PATH  build time. Without it unwind-sys's build.rs panics on
    #                    `pkg-config --libs --cflags libunwind-ptrace`.
    #   LIBRARY_PATH     LINK time. pkg-config emits `-lunwind-ptrace
    #                    -lunwind-generic -lunwind`, and the linker still has to
    #                    FIND those .so files. Without it the build gets past
    #                    build.rs and then dies with
    #                    `rust-lld: error: unable to find library -lunwind`
    #                    while linking reverie-liteinst.
    #   LD_LIBRARY_PATH  RUN time, for the produced binaries. It does NOT help
    #                    the link -- the loader is not the linker, and it must
    #                    NOT be assumed equal to LIBRARY_PATH. See below.
    #
    # THE LINK DIR AND THE RUNTIME DIR NEED NOT BE THE SAME DIRECTORY, which has
    # bitten twice, so the runtime directory is PROBED rather than assumed --
    # see `_libunwind_runtime_dir`. A future relocation is picked up by adding a
    # path to RUNTIME_CANDIDATES, not by rediscovering the failure.
    #
    # NOTE ON WHY LD_LIBRARY_PATH IS LOAD-BEARING AT ALL, since it is easy to
    # conclude it is redundant and delete it. Measured on this host class
    # (libunwind-devel installed system-wide, three units, one variable changed
    # per run):
    #   no LIBRARY_PATH, no LD_LIBRARY_PATH -> `-lunwind-ptrace` resolves to the
    #     system STATIC /usr/lib64/libunwind-ptrace.a, ldd shows no ptrace
    #     dependency, and the binary runs.
    #   LIBRARY_PATH only -> the link now prefers the SHARED
    #     `libunwind-ptrace.so` in the pointed-at tree, and the binary dies with
    #     `libunwind-ptrace.so.0: cannot open shared object file` because
    #     /usr/lib64 ships that library only as a static `.a`.
    # In other words LIBRARY_PATH is what CREATES the runtime dependency that
    # LD_LIBRARY_PATH then satisfies. The three are a set: propagate all of them
    # or none. This also means the probe must key on `libunwind-ptrace.so.0`
    # specifically, not on libunwind generally.
    lu_env: list[str] = []
    lu_root = root / "ignored/lu-parity/usr/lib64"
    if (lu_root / "pkgconfig/libunwind-ptrace.pc").exists():
        runtime = _libunwind_runtime_dir(root, lu_root)
        pkg_config = _prepend_path(
            environment.get("PKG_CONFIG_PATH", ""), str(lu_root / "pkgconfig")
        )
        library = _prepend_path(environment.get("LIBRARY_PATH", ""), str(lu_root))
        ld_library = _prepend_path(environment.get("LD_LIBRARY_PATH", ""), str(runtime))
        lu_env = [
            "--setenv",
            f"PKG_CONFIG_PATH={pkg_config}",
            "--setenv",
            f"LIBRARY_PATH={library}",
            "--setenv",
            f"LD_LIBRARY_PATH={ld_library}",
        ]

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
        *lu_env,
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


def service_properties(
    unit: str, *, run: Runner
) -> dict[str, str] | None:
    result = run(
        [
            "systemctl",
            "--user",
            "show",
            f"{unit}.service",
            "--property=ActiveState",
            "--property=SubState",
            "--property=ExecMainStatus",
            "--property=Result",
            "--no-pager",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def wait_for_unit(
    unit: str,
    record: Path,
    *,
    run: Runner,
    poll_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    seen = False
    missing = 0
    while True:
        properties = service_properties(unit, run=run)
        if properties is None:
            missing += 1
            try:
                durable = run_registry.read_record(record)
            except RuntimeError:
                durable = {}
            if durable.get("state") == "completed":
                return durable
            if seen or missing >= 50:
                raise RuntimeError(
                    f"{unit}.service disappeared before publishing a terminal result; "
                    f"inspect {record} and the durable log"
                )
            sleep(poll_seconds)
            continue
        seen = True
        if properties.get("ActiveState") in TERMINAL_STATES:
            try:
                status = int(properties.get("ExecMainStatus", ""))
            except ValueError:
                status = None
            return {
                "state": "completed",
                "result": properties.get("Result", "unknown"),
                "exit_code": status,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        sleep(poll_seconds)


def emit_report(report: Mapping[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(dict(report), sort_keys=True), flush=True)
        return
    event = report.get("event", "state").upper()
    print(
        f"validate-run: {event} {report['unit']} target={report['target']} "
        f"state={report.get('state', 'unknown')}",
        flush=True,
    )
    if report.get("pane_id"):
        print(
            f"PANE workspace={report['workspace_id']} tab={report['tab_id']} "
            f"pane={report['pane_id']}",
            flush=True,
        )
    if report.get("log"):
        print(f"LOG {report['log']}", flush=True)


def attach(
    raw_unit: str,
    *,
    root: Path,
    run: Runner,
    json_output: bool,
    poll_seconds: float,
    sleep: Callable[[float], None],
) -> int:
    unit = sanitize_unit(raw_unit)
    record_path = run_registry.record_path(root, unit)
    record = run_registry.read_record(record_path)
    emit_report(
        {
            **record,
            "event": "attached",
            "unit": f"{unit}.service",
        },
        json_output=json_output,
    )
    final = wait_for_unit(
        unit,
        record_path,
        run=run,
        poll_seconds=poll_seconds,
        sleep=sleep,
    )
    updated = run_registry.update_record(record_path, **final)
    emit_report(
        {**updated, "event": "finished", "unit": f"{unit}.service"},
        json_output=json_output,
    )
    status = updated.get("exit_code")
    return status if isinstance(status, int) and 0 <= status <= 125 else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Launch validate.sh as a detached systemd user service whose only admission "
            "path is ci-hub validate-lock."
        )
    )
    result.add_argument("--checkout", type=Path)
    result.add_argument("--agent")
    result.add_argument("--target")
    result.add_argument("--pr", type=int)
    result.add_argument(
        "--attach",
        metavar="VALIDATE-UNIT",
        help="reattach to a durable validate-* handle without launching another run",
    )
    result.add_argument("--unit", help="validate-* unit name; .service suffix is optional")
    result.add_argument("--log", type=Path, help="durable log (default: ignored/validate/<unit>.log)")
    result.add_argument("--wait", type=int, default=7200, help="validate-lock queue wait bound")
    result.add_argument("--hold", type=int, default=1200, help="validate-lock lease seconds")
    result.add_argument("--child-deadline", type=int, default=3600)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--json", action="store_true")
    result.add_argument(
        "--caller-poll-seconds",
        type=float,
        default=1.0,
        help="poll cadence while the caller blocks on the detached service",
    )
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
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = parser().parse_args(argv)
    if args.caller_poll_seconds <= 0:
        print("validate-run: REFUSED: caller poll seconds must be positive", file=sys.stderr)
        return 2
    if args.attach:
        if any((args.checkout, args.agent, args.target, args.pr, args.unit, args.log, args.dry_run)):
            print(
                "validate-run: REFUSED: --attach cannot be combined with launch arguments",
                file=sys.stderr,
            )
            return 2
        try:
            return attach(
                args.attach,
                root=root.resolve(),
                run=run,
                json_output=args.json,
                poll_seconds=args.caller_poll_seconds,
                sleep=sleep,
            )
        except (RuntimeError, ValueError) as error:
            print(f"validate-run: REFUSED: {error}", file=sys.stderr)
            return 2
    if args.checkout is None or not args.agent or not args.target:
        print(
            "validate-run: REFUSED: launch requires --checkout, --agent, and --target",
            file=sys.stderr,
        )
        return 2
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
        record_path = run_registry.record_path(root.resolve(), unit)
        started_at = datetime.now(timezone.utc).isoformat()
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
        if args.dry_run:
            pane = None
        else:
            log.parent.mkdir(parents=True, exist_ok=True)
            run_registry.write_record(
                record_path,
                {
                    "schema_version": 1,
                    "state": "preparing",
                    "unit": f"{unit}.service",
                    "target": args.target,
                    "checkout": str(checkout),
                    "log": str(log),
                    "agent": args.agent,
                    "pr": args.pr,
                    "started_at": started_at,
                    "producer": "systemd-user-v1",
                    "admission": "ci-hub validate-lock",
                    "pane_role": "observer-only",
                },
            )
            pane = pane_owner.create_pane(
                root=root.resolve(),
                checkout=checkout,
                unit=unit,
                target=args.target,
                log=log,
                record=record_path,
                pr=args.pr,
                started_at=started_at,
                run=run,
                environment=environment or os.environ,
                sleep=sleep,
            )
            run_registry.update_record(
                record_path,
                state="launching",
                workspace_id=pane.workspace_id,
                tab_id=pane.tab_id,
                pane_id=pane.pane_id,
                pane_title=pane.title,
            )
            result = run(command, cwd=root, check=False)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
                run_registry.update_record(
                    record_path,
                    state="refused",
                    result="systemd-launch-refused",
                    detail=detail,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
                raise RuntimeError(f"systemd-run refused service: {detail}")
            run_registry.update_record(record_path, state="running")
    except (RuntimeError, ValueError) as error:
        print(f"validate-run: REFUSED: {error}", file=sys.stderr)
        return 2

    report = {
        "schema_version": 1,
        "event": "would-start" if args.dry_run else "handle",
        "state": "planned" if args.dry_run else "running",
        "unit": f"{unit}.service",
        "target": args.target,
        "checkout": str(checkout),
        "log": str(log),
        "record": str(record_path),
        "admission": "ci-hub validate-lock",
        "producer": "systemd-user-v1",
        "pane_role": "observer-only",
        "workspace_id": pane.workspace_id if pane else pane_owner.WORKSPACE_LABEL,
        "tab_id": pane.tab_id if pane else None,
        "pane_id": pane.pane_id if pane else None,
        "command": command if args.dry_run else None,
    }
    emit_report(report, json_output=args.json)
    if args.dry_run:
        if not args.json:
            print(f"PANE-PLAN workspace={pane_owner.WORKSPACE_LABEL} role=observer-only")
            print(f"COMMAND {shlex.join(command)}")
        return 0

    try:
        final = wait_for_unit(
            unit,
            record_path,
            run=run,
            poll_seconds=args.caller_poll_seconds,
            sleep=sleep,
        )
        updated = run_registry.update_record(record_path, **final)
    except RuntimeError as error:
        print(
            f"validate-run: WAIT-INTERRUPTED {unit}.service; RUN CONTINUES independently: {error}",
            file=sys.stderr,
        )
        return 2
    emit_report(
        {**updated, "event": "finished", "unit": f"{unit}.service"},
        json_output=args.json,
    )
    status = updated.get("exit_code")
    return status if isinstance(status, int) and 0 <= status <= 125 else 2


if __name__ == "__main__":
    raise SystemExit(main())
