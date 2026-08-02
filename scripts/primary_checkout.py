#!/usr/bin/env python3
"""Safely refresh and inspect the parent workspace's primary checkouts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import TextIO


PRODUCTS = ("hermit", "reverie", "liteinst2")
MAIN_REF = "refs/heads/main"


def run_git(
    repo: Path,
    *args: str,
    network: bool = False,
    use_proxy: bool = True,
) -> subprocess.CompletedProcess[str]:
    command: list[str] = []
    if network and use_proxy and not os.environ.get("PRIMARY_CHECKOUT_DISABLE_PROXY"):
        proxy = shutil.which(os.environ.get("WITH_PROXY", "with-proxy"))
        if proxy:
            command.append(proxy)
    command.extend(("git", "-C", str(repo), *args))
    return subprocess.run(command, text=True, capture_output=True, check=False)


def print_command_output(result: subprocess.CompletedProcess[str], stream: TextIO) -> None:
    for output in (result.stdout, result.stderr):
        if output:
            print(output.rstrip(), file=stream)


def checkout_fresh(
    root: Path,
    *,
    use_proxy: bool = True,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    failures = 0
    for product in PRODUCTS:
        repo = root / product
        if not (repo / ".git").exists():
            print(f"ERROR: primary checkout is not initialized: {repo}", file=err)
            failures += 1
            continue

        status = run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
        if status.returncode != 0:
            print(f"ERROR: could not inspect {product}:", file=err)
            print_command_output(status, err)
            failures += 1
            continue
        if status.stdout.strip():
            print(
                f"WARNING: {product} is dirty; preserving it and skipping checkout-fresh:",
                file=err,
            )
            dirty_lines = status.stdout.rstrip().splitlines()
            for line in dirty_lines[:20]:
                print(f"  {line}", file=err)
            if len(dirty_lines) > 20:
                print(f"  ... {len(dirty_lines) - 20} more path(s)", file=err)
            continue

        print(f"Refreshing {product}...", file=out)
        fetch = run_git(repo, "fetch", "origin", "main", network=True, use_proxy=use_proxy)
        print_command_output(fetch, out if fetch.returncode == 0 else err)
        if fetch.returncode != 0:
            print(f"ERROR: {product} fetch failed; checkout left unchanged.", file=err)
            failures += 1
            continue

        local_main = run_git(repo, "show-ref", "--verify", "--quiet", MAIN_REF)
        checkout_args = ("checkout", "main")
        if local_main.returncode != 0:
            checkout_args = ("checkout", "-b", "main", "--track", "origin/main")
        checkout = run_git(repo, *checkout_args)
        print_command_output(checkout, out if checkout.returncode == 0 else err)
        if checkout.returncode != 0:
            print(f"ERROR: {product} could not check out main.", file=err)
            failures += 1
            continue

        pull = run_git(
            repo,
            "pull",
            "--ff-only",
            "origin",
            "main",
            network=True,
            use_proxy=use_proxy,
        )
        print_command_output(pull, out if pull.returncode == 0 else err)
        if pull.returncode != 0:
            print(f"ERROR: {product} could not fast-forward main.", file=err)
            failures += 1
            continue

        branch = run_git(repo, "branch", "--show-current").stdout.strip()
        head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
        remote = run_git(repo, "rev-parse", "origin/main").stdout.strip()
        if branch != "main" or not head or head != remote:
            print(
                f"ERROR: {product} ended at branch={branch or 'DETACHED'} "
                f"HEAD={head or 'unknown'} origin/main={remote or 'unknown'}; no reset performed.",
                file=err,
            )
            failures += 1
            continue
        print(f"{product}: main is current at {head}", file=out)
    return 1 if failures else 0


def check_freshness(
    root: Path,
    *,
    strict: bool = False,
    use_proxy: bool = True,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    warnings: list[str] = []
    current: list[str] = []
    for product in PRODUCTS:
        repo = root / product
        if not (repo / ".git").exists():
            warnings.append(f"{product}: primary checkout is not initialized")
            continue

        branch_result = run_git(repo, "branch", "--show-current")
        head_result = run_git(repo, "rev-parse", "HEAD")
        remote_result = run_git(
            repo,
            "ls-remote",
            "--exit-code",
            "origin",
            MAIN_REF,
            network=True,
            use_proxy=use_proxy,
        )
        branch = branch_result.stdout.strip()
        head = head_result.stdout.strip()
        remote = remote_result.stdout.split(maxsplit=1)[0] if remote_result.stdout.strip() else ""

        if branch_result.returncode != 0 or head_result.returncode != 0:
            warnings.append(f"{product}: could not inspect branch/HEAD")
            continue
        if remote_result.returncode != 0 or not remote:
            warnings.append(f"{product}: could not query live origin/main")
            continue
        if branch != "main":
            warnings.append(f"{product}: branch is {branch or 'DETACHED'}, expected main")
        if head != remote:
            warnings.append(f"{product}: HEAD {head} differs from origin/main {remote}")
        if branch == "main" and head == remote:
            current.append(f"{product}={head[:12]}")

    if warnings:
        print("HARD WARNING: PRIMARY CHECKOUT FRESHNESS", file=err)
        for warning in warnings:
            print(f"  {warning}", file=err)
        print("Run `make checkout-fresh`; dirty primaries are preserved and skipped.", file=err)
    else:
        print(f"Primary checkouts are current on main: {', '.join(current)}", file=out)
    return 1 if strict and warnings else 0


def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("fresh", help="refresh every clean primary checkout")
    check = subparsers.add_parser("check", help="warn about detached or stale primaries")
    check.add_argument("--strict", action="store_true", help="return nonzero on warnings")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if args.command == "fresh":
        return checkout_fresh(root)
    return check_freshness(root, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
