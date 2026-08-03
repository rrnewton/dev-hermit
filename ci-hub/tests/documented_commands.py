#!/usr/bin/env python3
"""Extract and exercise every fenced shell command in the ci-hub READMEs."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
DOCS = (ROOT / "ci-hub/README.md", ROOT / "ci-hub/landing/README.md")
EXPECTED_COMMANDS = 25
FENCE = re.compile(r"^```(?P<language>[A-Za-z0-9_-]*)\s*$")
FATAL_OUTPUT = (
    "gh auth login",
    "Traceback (most recent call last):",
    "ModuleNotFoundError:",
    "No such file or directory",
    "unrecognized subcommand",
    "unexpected argument",
    "Could not execute cargo",
)


class DocsCommandError(RuntimeError):
    """A documented command could not be classified or exercised."""


@dataclass(frozen=True)
class DocumentedCommand:
    path: Path
    line: int
    text: str
    mode: str

    @property
    def label(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def _logical_commands(lines: list[tuple[int, str]]) -> Iterable[tuple[int, str]]:
    pending: list[str] = []
    start = 0
    for line_number, line in lines:
        stripped = line.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if not pending:
            start = line_number
        pending.append(line)
        if line.rstrip().endswith("\\"):
            continue
        command = "\n".join(pending).strip()
        pending = []
        if command:
            yield start, command
    if pending:
        yield start, "\n".join(pending).strip()


def _classify(text: str) -> str:
    normalized = " ".join(text.replace("\\\n", " ").split())
    if normalized.startswith("cd "):
        return "setup"
    if re.match(r"^(?:\./)?ci-hub/landing/land-pr\.sh\s", normalized):
        return "parse"
    match = re.match(r"^(?:\./)?ci-hub/ci-hub\s+(\S+)", normalized)
    if match:
        command = match.group(1)
        if command in {"fresh", "health", "runner-health"}:
            return "live-read"
        if command in {
            "help",
            "quickstart",
            "obligations",
            "inherit-obligations",
            "watch-obligations",
            "history",
            "local-history",
            "validate-worktrees",
        }:
            return "local-read"
        if command == "land-lock" and re.search(r"\bland-lock\s+status\b", normalized):
            return "local-read"
        if command in {"land-lock", "refresh-history", "resolve-obligation"}:
            return "parse"
        raise DocsCommandError(f"unclassified ci-hub subcommand: {normalized}")
    if normalized.startswith("with-proxy gh "):
        return "live-read"
    raise DocsCommandError(f"unclassified shell command: {normalized}")


def extract_commands(paths: Iterable[Path] = DOCS) -> list[DocumentedCommand]:
    commands: list[DocumentedCommand] = []
    for path in paths:
        inside = False
        shell = False
        block: list[tuple[int, str]] = []
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            marker = FENCE.match(line)
            if marker:
                if not inside:
                    inside = True
                    shell = marker.group("language") in {"bash", "sh", "shell"}
                    block = []
                else:
                    if shell:
                        _check_shell_syntax(path, block)
                        for start, text in _logical_commands(block):
                            commands.append(
                                DocumentedCommand(path, start, text, _classify(text))
                            )
                    inside = False
                    shell = False
                    block = []
                continue
            if inside and shell:
                block.append((line_number, line))
        if inside:
            raise DocsCommandError(f"{path}: unterminated fenced block")
    if len(commands) != EXPECTED_COMMANDS:
        raise DocsCommandError(
            f"extracted {len(commands)} commands, expected {EXPECTED_COMMANDS}; "
            "classify and account for every new or removed documented invocation"
        )
    return commands


def _check_shell_syntax(path: Path, block: list[tuple[int, str]]) -> None:
    source = "\n".join(line for _, line in block) + "\n"
    result = subprocess.run(
        ["bash", "-n"], input=source, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        start = block[0][0] if block else 0
        raise DocsCommandError(
            f"{path}:{start}: invalid shell: {result.stderr.strip()}"
        )


def _render(text: str) -> str:
    return (
        text.replace("OBLIGATION_ID", "missing-obligation")
        .replace("REPAIR_SHA", "a" * 40)
        .replace("PR_NUMBER", os.environ.get("CI_HUB_DOCS_MERGED_PR", "1278"))
    )


def _business_output(output: str) -> str:
    return "\n".join(
        line
        for line in output.splitlines()
        if line.strip() and not line.startswith(("COST ESTIMATE ", "COST ACTUAL "))
    )


def _external_help(command: str) -> str:
    normalized = " ".join(command.replace("\\\n", " ").split())
    if normalized.startswith("with-proxy gh api "):
        return "gh api --help"
    return "gh pr view --help"


def _nested_land_and_arm(command: str) -> str | None:
    marker = "./ci-hub/remediation/land_and_arm.py"
    offset = command.find(marker)
    return command[offset:] if offset >= 0 else None


def _workspace_state(root: Path, *, include_ignored: bool) -> str:
    arguments = ["git", "status", "--porcelain=v1", "--untracked-files=all"]
    if include_ignored:
        arguments.append("--ignored=matching")
    result = subprocess.run(
        arguments,
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _tracked_mtimes(root: Path) -> dict[str, int]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    mtimes: dict[str, int] = {}
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = os.fsdecode(raw_path)
        path = root / relative
        if path.is_file() or path.is_symlink():
            mtimes[relative] = path.lstat().st_mtime_ns
    return mtimes


def _changed_mtimes(before: dict[str, int], after: dict[str, int]) -> list[str]:
    return sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )


def _run_one(
    command: DocumentedCommand,
    *,
    root: Path,
    environment: dict[str, str],
    live: bool,
    verify_purity: bool,
) -> list[str]:
    rendered = _render(command.text)
    run_environment = environment.copy()
    executed = rendered
    allowed = {0}
    if command.mode == "parse" or (command.mode == "live-read" and not live):
        run_environment["CI_HUB_DOCS_PARSE_ONLY"] = "1"
        if command.mode == "live-read" and rendered.startswith("with-proxy gh "):
            executed = _external_help(rendered)
    elif command.mode == "live-read":
        allowed = {0, 1, 2} if re.match(r"^(?:\./)?ci-hub/ci-hub\s", rendered) else {0}

    timeout = 120 if live else 45
    before = _workspace_state(root, include_ignored=True) if verify_purity else ""
    before_mtimes = _tracked_mtimes(root) if verify_purity else {}
    try:
        result = subprocess.run(
            executed,
            cwd=root,
            env=run_environment,
            shell=True,
            executable="/bin/bash",
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DocsCommandError(
            f"{command.label}: {command.mode} command exceeded {timeout}s: {executed}"
        ) from error
    output = result.stdout + result.stderr
    fatal = next((marker for marker in FATAL_OUTPUT if marker in output), None)
    if fatal is not None:
        raise DocsCommandError(
            f"{command.label}: fatal diagnostic {fatal!r}\n"
            f"command: {executed}\noutput:\n{output}"
        )
    if result.returncode not in allowed:
        raise DocsCommandError(
            f"{command.label}: exit {result.returncode}, expected {sorted(allowed)}\n"
            f"command: {executed}\noutput:\n{output}"
        )
    if command.mode != "setup" and not _business_output(output):
        raise DocsCommandError(
            f"{command.label}: silent success (no domain output)\ncommand: {executed}"
        )
    reports = [
        f"PASS {command.mode:10} {command.label} exit={result.returncode} "
        f"output={len(_business_output(output).splitlines())} line(s)"
    ]

    nested = _nested_land_and_arm(rendered)
    if nested is not None:
        nested_result = subprocess.run(
            nested,
            cwd=root,
            env=run_environment | {"CI_HUB_DOCS_PARSE_ONLY": "1"},
            shell=True,
            executable="/bin/bash",
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
        nested_output = nested_result.stdout + nested_result.stderr
        if nested_result.returncode != 0 or not _business_output(nested_output):
            raise DocsCommandError(
                f"{command.label}: nested land_and_arm invocation failed parse validation\n"
                f"command: {nested}\noutput:\n{nested_output}"
            )
        reports.append(f"PASS parse-nested {command.label} land_and_arm.py")
    if verify_purity:
        after = _workspace_state(root, include_ignored=True)
        changed_mtimes = _changed_mtimes(before_mtimes, _tracked_mtimes(root))
        if after != before or changed_mtimes:
            raise DocsCommandError(
                f"{command.label}: command mutated a clean checkout\n"
                f"command: {executed}\nstatus before:\n{before}\nstatus after:\n{after}"
                f"\ntracked mtimes changed: {changed_mtimes or 'none'}"
            )
    return reports


def _run_tg_quickstart(
    binary: str,
    *,
    root: Path,
    environment: dict[str, str],
    verify_purity: bool,
) -> list[str]:
    """Exercise the source-owned TaskGraph primer without opening its database."""
    before = _workspace_state(root, include_ignored=True) if verify_purity else ""
    before_mtimes = _tracked_mtimes(root) if verify_purity else {}
    with tempfile.TemporaryDirectory(prefix="tg-quickstart-") as temporary:
        home = Path(temporary)
        run_environment = environment | {
            "HOME": str(home),
            "TG_DB_PATH": str(home / "must-not-exist.db"),
        }
        try:
            result = subprocess.run(
                [binary, "quickstart"],
                cwd=root,
                env=run_environment,
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise DocsCommandError("tg quickstart exceeded 15s") from error
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise DocsCommandError(
                f"tg quickstart exited {result.returncode}\noutput:\n{output}"
            )
        business = _business_output(output)
        required = (
            "TaskGraph agent quickstart",
            "tg claim TASK_ID",
            "tg note TASK_ID",
            "TG_DB_PATH",
        )
        missing = [marker for marker in required if marker not in business]
        if missing:
            raise DocsCommandError(
                f"tg quickstart is missing agent workflow markers {missing}\noutput:\n{output}"
            )
        created = sorted(str(path.relative_to(home)) for path in home.rglob("*"))
        if created:
            raise DocsCommandError(
                "tg quickstart mutated its isolated HOME: " + ", ".join(created)
            )
    if verify_purity:
        after = _workspace_state(root, include_ignored=True)
        changed_mtimes = _changed_mtimes(before_mtimes, _tracked_mtimes(root))
        if after != before or changed_mtimes:
            raise DocsCommandError(
                "tg quickstart mutated the checkout\n"
                f"status before:\n{before}\nstatus after:\n{after}"
                f"\ntracked mtimes changed: {changed_mtimes or 'none'}"
            )
    return [
        "PASS quickstart tg external-source exit=0 "
        f"output={len(_business_output(output).splitlines())} line(s) purity=verified"
    ]


def _evaluate_closeout(
    *,
    head: str,
    origin_main: str,
    unpushed: int,
    dirty: str,
    dirty_note: str | None,
) -> list[str]:
    if unpushed:
        raise DocsCommandError(
            f"closeout refused: HEAD {head} has {unpushed} commit(s) not on "
            f"origin/main {origin_main}; commit and push before handoff"
        )
    note = (dirty_note or "").strip()
    if dirty and len(note) < 20:
        raise DocsCommandError(
            "closeout refused: parent is dirty; commit/push task-owned work or pass "
            "--dirty-note with an explicit ownership/reason statement\n" + dirty
        )
    if not dirty and note:
        raise DocsCommandError(
            "closeout refused: --dirty-note was supplied but the parent is clean; "
            "remove the stale exception"
        )
    reports = [f"CLOSEOUT PUSHED: head={head} origin_main={origin_main} unpushed=0"]
    if dirty:
        reports.append("CLOSEOUT DIRTY PATHS:\n" + dirty.rstrip())
        reports.append("CLOSEOUT DIRTY NOTE: " + note)
    else:
        reports.append("CLOSEOUT WORKTREE: clean")
    return reports


def closeout_guard(*, root: Path, dirty_note: str | None) -> list[str]:
    fetch = subprocess.run(
        ["with-proxy", "git", "fetch", "--quiet", "origin", "main"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if fetch.returncode != 0:
        raise DocsCommandError(
            "closeout refused: cannot refresh origin/main through with-proxy\n"
            + fetch.stdout
            + fetch.stderr
        )

    def git_output(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise DocsCommandError(
                f"closeout refused: git {' '.join(arguments)} failed\n"
                + result.stdout
                + result.stderr
            )
        return result.stdout.strip()

    return _evaluate_closeout(
        head=git_output("rev-parse", "HEAD"),
        origin_main=git_output("rev-parse", "origin/main"),
        unpushed=int(git_output("rev-list", "--count", "origin/main..HEAD")),
        dirty=git_output("status", "--porcelain=v1", "--untracked-files=all"),
        dirty_note=dirty_note,
    )


def run(*, root: Path = ROOT, live: bool = False) -> list[str]:
    commands = extract_commands(
        (root / "ci-hub/README.md", root / "ci-hub/landing/README.md")
    )
    reports: list[str] = []
    verify_purity = not _workspace_state(root, include_ignored=False).strip()
    with tempfile.TemporaryDirectory(prefix="ci-hub-docs-") as temporary:
        state = Path(temporary)
        home = state / "home"
        checkout = home / "work/dev-hermit"
        checkout.parent.mkdir(parents=True)
        checkout.symlink_to(root, target_is_directory=True)
        environment = os.environ.copy()
        original_home = Path(environment.get("HOME", str(Path.home())))
        environment.update(
            {
                "HOME": str(home),
                "CARGO_HOME": environment.get(
                    "CARGO_HOME", str(original_home / ".cargo")
                ),
                "RUSTUP_HOME": environment.get(
                    "RUSTUP_HOME", str(original_home / ".rustup")
                ),
                "XDG_CACHE_HOME": environment.get(
                    "XDG_CACHE_HOME", str(original_home / ".cache")
                ),
                "GH_CONFIG_DIR": environment.get(
                    "GH_CONFIG_DIR", str(original_home / ".config/gh")
                ),
                "DEV_HERMIT_PARENT": str(root),
                "CI_HUB_LANDING_LOCK": str(state / "landing.lock"),
                "CI_HUB_OBLIGATIONS_STORE": str(state / "obligations.jsonl"),
                "CI_HUB_MAIN_HEALTH_TIMEOUT": "4",
                "CI_HUB_MAIN_HEALTH_DEADLINE": "8",
                "CI_HUB_PR_STATUS_TIMEOUT": "8",
                "CI_HUB_PR_STATUS_DEADLINE": "12",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        for command in commands:
            reports.extend(
                _run_one(
                    command,
                    root=root,
                    environment=environment,
                    live=live,
                    verify_purity=verify_purity,
                )
            )
        tg_binary = os.environ.get("CI_HUB_TG_BIN") or shutil.which("tg")
        if tg_binary:
            reports.extend(
                _run_tg_quickstart(
                    tg_binary,
                    root=root,
                    environment=environment,
                    verify_purity=verify_purity,
                )
            )
        else:
            reports.append(
                "SKIP quickstart tg tool-unavailable; fbsource tg integration owns its purity test"
            )
    reports.append(
        "PASS checkout-purity "
        + ("verified-no-writes" if verify_purity else "skipped-dirty-input")
    )
    return reports


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="execute networked read-only examples instead of their pure help/parse probes",
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--closeout",
        action="store_true",
        help="require pushed HEAD and clean parent, or an explicit dirty-tree note",
    )
    parser.add_argument(
        "--dirty-note",
        help="explicit ownership/reason for concurrent parent dirt at closeout",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.closeout:
            for report in closeout_guard(
                root=args.root.resolve(), dirty_note=args.dirty_note
            ):
                print(report)
            return 0
        if args.dirty_note:
            raise DocsCommandError("--dirty-note requires --closeout")
        if args.list:
            for command in extract_commands(
                (args.root / "ci-hub/README.md", args.root / "ci-hub/landing/README.md")
            ):
                print(f"{command.mode:10} {command.label} {shlex.join([command.text])}")
            return 0
        reports = run(root=args.root.resolve(), live=args.live)
    except DocsCommandError as error:
        print(f"DOCUMENTED COMMAND FAILURE: {error}", file=sys.stderr)
        return 1
    for report in reports:
        print(report)
    print(
        f"DOCUMENTED COMMANDS: PASS commands={EXPECTED_COMMANDS} "
        f"probes={len(reports)} live={str(args.live).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
