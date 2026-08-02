#!/usr/bin/env python3
"""Safely refresh and inspect the parent workspace's primary checkouts."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from typing import TextIO


PRODUCTS = ("hermit", "reverie", "liteinst2")
MAIN_REF = "refs/heads/main"
REVERIE_GIT_URL = "https://github.com/rrnewton/reverie.git"
SNAPSHOT_COMMIT_MESSAGE = "Advance product submodules as consistent snapshot"
REVERIE_SOURCE = re.compile(
    rf"^git\+{re.escape(REVERIE_GIT_URL)}\?rev=([0-9a-f]{{40}})#([0-9a-f]{{40}})$"
)
REVERIE_LOCKFILES = (Path("Cargo.lock"), Path("liteinst-runtime-build/Cargo.lock"))
REVERIE_CACHE_FILES = (
    Path("ci/dag/portable.json"),
    Path("hermit-cli/tests/common/liteinst.rs"),
    Path("hermit-install/build.rs"),
    Path("validate.sh"),
)
REVERIE_CACHE_KEY = re.compile(r"liteinst-runtime(?:-build)?-([0-9a-f]{8})")


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


def _walk_reverie_dependencies(value: object) -> list[str]:
    pins: list[str] = []
    if isinstance(value, Mapping):
        if value.get("git") == REVERIE_GIT_URL and isinstance(value.get("rev"), str):
            pins.append(value["rev"])
        for child in value.values():
            pins.extend(_walk_reverie_dependencies(child))
    elif isinstance(value, list):
        for child in value:
            pins.extend(_walk_reverie_dependencies(child))
    return pins


def reverie_manifest_pins(hermit: Path) -> tuple[set[str], int, list[str]]:
    """Return exact Reverie revisions from tracked Hermit Cargo manifests."""
    manifests = run_git(hermit, "ls-files", "-z", "--", "*Cargo.toml")
    if manifests.returncode != 0:
        return set(), 0, ["could not list tracked Hermit Cargo.toml files"]

    pins: list[str] = []
    errors: list[str] = []
    for relative in filter(None, manifests.stdout.split("\0")):
        path = hermit / relative
        try:
            with path.open("rb") as source:
                pins.extend(_walk_reverie_dependencies(tomllib.load(source)))
        except (OSError, tomllib.TOMLDecodeError) as error:
            errors.append(f"could not parse {relative}: {error}")
    return set(pins), len(pins), errors


def reverie_generated_pin_errors(hermit: Path, expected: str) -> list[str]:
    """Check generated lock sources and revision-keyed build cache paths."""
    errors: list[str] = []
    for relative in REVERIE_LOCKFILES:
        path = hermit / relative
        try:
            with path.open("rb") as source:
                lock = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError) as error:
            errors.append(f"could not parse {relative}: {error}")
            continue

        sources = [
            package.get("source", "")
            for package in lock.get("package", [])
            if isinstance(package, Mapping)
            and str(package.get("source", "")).startswith(f"git+{REVERIE_GIT_URL}")
        ]
        if not sources:
            errors.append(f"{relative}: no Reverie git sources found")
            continue
        for source in sources:
            match = REVERIE_SOURCE.fullmatch(str(source))
            if match is None or match.group(1) != expected or match.group(2) != expected:
                errors.append(f"{relative}: stale Reverie source {source}")

    expected_short = expected[:8]
    for relative in REVERIE_CACHE_FILES:
        path = hermit / relative
        try:
            keys = set(REVERIE_CACHE_KEY.findall(path.read_text()))
        except OSError as error:
            errors.append(f"could not read {relative}: {error}")
            continue
        if keys != {expected_short}:
            errors.append(
                f"{relative}: cache keys={','.join(sorted(keys)) or 'none'} "
                f"expected={expected_short}"
            )
    return errors


def publish_parent_snapshot(
    root: Path,
    *,
    use_proxy: bool = True,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Commit and push exact product-main gitlinks when the snapshot is coherent."""
    heads: dict[str, str] = {}
    failures: list[str] = []
    for product in PRODUCTS:
        repo = root / product
        status = run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
        branch = run_git(repo, "branch", "--show-current")
        head = run_git(repo, "rev-parse", "HEAD")
        remote = run_git(repo, "rev-parse", "origin/main")
        if status.returncode != 0 or status.stdout.strip():
            failures.append(f"{product}: primary is dirty; parent snapshot not advanced")
            continue
        if branch.returncode != 0 or branch.stdout.strip() != "main":
            failures.append(f"{product}: primary is not on main")
            continue
        head_sha = head.stdout.strip()
        remote_sha = remote.stdout.strip()
        if head.returncode != 0 or remote.returncode != 0 or head_sha != remote_sha:
            failures.append(f"{product}: primary HEAD does not equal fetched origin/main")
            continue
        heads[product] = head_sha

    if not failures and len(heads) == len(PRODUCTS):
        pins, pin_count, pin_errors = reverie_manifest_pins(root / "hermit")
        failures.extend(f"hermit: {message}" for message in pin_errors)
        expected = heads["reverie"]
        if pin_count == 0:
            failures.append("hermit: no tracked Cargo manifest pins rrnewton/reverie")
        elif pins != {expected}:
            failures.append(
                "Hermit Reverie pins are not globally consistent: "
                f"manifests={','.join(sorted(pins)) or 'none'} reverie/main={expected}"
            )
        failures.extend(
            f"hermit: {message}"
            for message in reverie_generated_pin_errors(root / "hermit", expected)
        )

    if failures:
        print("HARD WARNING: PARENT SUBMODULE SNAPSHOT NOT PUBLISHED", file=err)
        for failure in failures:
            print(f"  {failure}", file=err)
        return 1

    fetch = run_git(root, "fetch", "origin", "main", network=True, use_proxy=use_proxy)
    print_command_output(fetch, out if fetch.returncode == 0 else err)
    if fetch.returncode != 0:
        print("ERROR: parent origin/main fetch failed; snapshot not published.", file=err)
        return 1
    parent_branch = run_git(root, "branch", "--show-current").stdout.strip()
    parent_head = run_git(root, "rev-parse", "HEAD").stdout.strip()
    parent_remote = run_git(root, "rev-parse", "origin/main").stdout.strip()
    if parent_branch != "main" or not parent_head or parent_head != parent_remote:
        print(
            "HARD WARNING: parent is not current on main; refusing automatic gitlink commit "
            f"(branch={parent_branch or 'DETACHED'} HEAD={parent_head or 'unknown'} "
            f"origin/main={parent_remote or 'unknown'}).",
            file=err,
        )
        return 1

    staged = run_git(root, "diff", "--cached", "--quiet", "--", *PRODUCTS)
    if staged.returncode != 0:
        print(
            "HARD WARNING: product gitlinks are already staged; refusing to overwrite "
            "another coordinator operation.",
            file=err,
        )
        return 1

    add = run_git(root, "add", "--", *PRODUCTS)
    if add.returncode != 0:
        print_command_output(add, err)
        return 1
    for product in PRODUCTS:
        staged_head = run_git(root, "rev-parse", f":{product}").stdout.strip()
        if staged_head != heads[product]:
            print(
                "HARD WARNING: validated primary moved while staging parent gitlinks; "
                f"{product} index={staged_head or 'missing'} validated={heads[product]}.",
                file=err,
            )
            return 1
    changed = run_git(root, "diff", "--cached", "--quiet", "--", *PRODUCTS)
    if changed.returncode == 0:
        print(
            "Parent product snapshot already current: "
            + ", ".join(f"{name}={heads[name][:12]}" for name in PRODUCTS),
            file=out,
        )
        return 0
    if changed.returncode != 1:
        print("ERROR: could not inspect staged product gitlinks.", file=err)
        return 1

    commit = run_git(
        root,
        "commit",
        "--only",
        "-m",
        SNAPSHOT_COMMIT_MESSAGE,
        "--",
        *PRODUCTS,
    )
    print_command_output(commit, out if commit.returncode == 0 else err)
    if commit.returncode != 0:
        print("ERROR: automatic parent snapshot commit failed; push skipped.", file=err)
        return 1

    push = run_git(
        root,
        "push",
        "origin",
        "HEAD:refs/heads/main",
        network=True,
        use_proxy=use_proxy,
    )
    print_command_output(push, out if push.returncode == 0 else err)
    if push.returncode != 0:
        print(
            "HARD WARNING: parent snapshot commit is local but push failed; reconcile "
            "without force-pushing.",
            file=err,
        )
        return 1
    snapshot = run_git(root, "rev-parse", "HEAD").stdout.strip()
    print(
        f"Published parent snapshot {snapshot}: "
        + ", ".join(f"{name}={heads[name]}" for name in PRODUCTS),
        file=out,
    )
    return 0


def checkout_fresh(
    root: Path,
    *,
    publish_parent: bool = False,
    strict: bool = False,
    use_proxy: bool = True,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    failures = 0
    skipped = 0
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
            skipped += 1
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
    if publish_parent and failures == 0 and skipped == 0:
        failures += publish_parent_snapshot(
            root, use_proxy=use_proxy, out=out, err=err
        )
    elif publish_parent and skipped:
        print(
            "HARD WARNING: parent snapshot not published because a primary checkout "
            "was dirty and preserved.",
            file=err,
        )
    return 1 if failures or (strict and skipped) else 0


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

        gitlink = run_git(root, "rev-parse", f":{product}")
        recorded = gitlink.stdout.strip()
        if gitlink.returncode != 0 or not recorded:
            warnings.append(f"{product}: parent index has no gitlink")
        elif remote and recorded != remote:
            warnings.append(
                f"{product}: parent gitlink {recorded} differs from origin/main {remote}"
            )

    reverie_head = run_git(root / "reverie", "rev-parse", "HEAD").stdout.strip()
    pins, pin_count, pin_errors = reverie_manifest_pins(root / "hermit")
    warnings.extend(f"hermit: {message}" for message in pin_errors)
    if pin_count == 0:
        warnings.append("hermit: no tracked Cargo manifest pins rrnewton/reverie")
    elif reverie_head and pins != {reverie_head}:
        warnings.append(
            "Hermit Reverie manifest pin mismatch: "
            f"manifests={','.join(sorted(pins)) or 'none'} reverie={reverie_head}"
        )
    if reverie_head:
        warnings.extend(
            f"hermit: {message}"
            for message in reverie_generated_pin_errors(root / "hermit", reverie_head)
        )

    if warnings:
        print("HARD WARNING: PRIMARY CHECKOUT FRESHNESS", file=err)
        for warning in warnings:
            print(f"  {warning}", file=err)
        print(
            "Run `make checkout-fresh`; dirty primaries are preserved and skipped, "
            "and only a coherent snapshot is published.",
            file=err,
        )
    else:
        print(f"Primary checkouts are current on main: {', '.join(current)}", file=out)
    return 1 if strict and warnings else 0


def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    fresh = subparsers.add_parser("fresh", help="refresh every clean primary checkout")
    fresh.add_argument(
        "--publish-parent",
        action="store_true",
        help="commit and push coherent product gitlinks to parent main",
    )
    fresh.add_argument(
        "--strict",
        action="store_true",
        help="return nonzero when a dirty primary must be skipped",
    )
    check = subparsers.add_parser("check", help="warn about detached or stale primaries")
    check.add_argument("--strict", action="store_true", help="return nonzero on warnings")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if args.command == "fresh":
        return checkout_fresh(
            root, publish_parent=args.publish_parent, strict=args.strict
        )
    return check_freshness(root, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
