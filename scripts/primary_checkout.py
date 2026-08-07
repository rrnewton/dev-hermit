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
FULL_SHA = re.compile(r"[0-9a-f]{40}")

# Repo-scoped git environment variables OVERRIDE `git -C <repo>`, and git exports
# them into every hook child. Measured on this box with a probe pre-commit hook:
#
#   git commit -m m -- <path>   =>  GIT_INDEX_FILE=/ABS/.git/next-index-<pid>.lock
#   git commit / --amend        =>  GIT_INDEX_FILE=.git/index      (relative)
#
# So an unscrubbed `git -C hermit ls-files` inside a pre-commit hook enumerates
# the PARENT's index, after which this module resolves those paths under hermit/.
# That is precisely the 2026-08-06 fleet-wide false block: the parent index holds
# 47 `*Cargo.toml` entries (crates-squat-staging/**, experiments/**,
# shmem_exec_obj/** -- none of which exist in the Hermit repo), every open
# ENOENTed, and check-pins concluded "no tracked Cargo manifest pins", forcing
# every agent onto HERMIT_PIN_DRIFT_OVERRIDE=1. `git -C <repo>` must mean <repo>,
# so scrub these unless a caller explicitly wants the in-flight parent index.
GIT_REPO_SCOPED_ENV = (
    "GIT_INDEX_FILE",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_INDEX_VERSION",
)
# Reads of a recorded gitlink's tree must never trigger a promisor round trip:
# a pre-commit hook has no business going to the network, and the Hermit primary
# carries a partial-clone promisor remote for reverie.
NO_LAZY_FETCH = {"GIT_NO_LAZY_FETCH": "1"}


def run_git(
    repo: Path,
    *args: str,
    network: bool = False,
    use_proxy: bool = True,
    inherit_repo_env: bool = False,
    env_overrides: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run git against ``repo``.

    ``inherit_repo_env=True`` keeps GIT_INDEX_FILE and friends, which is correct
    only when ``repo`` IS the repository whose in-flight commit set them (see
    GIT_REPO_SCOPED_ENV). Every other call must be scrubbed.
    """
    env = dict(os.environ)
    if not inherit_repo_env:
        for name in GIT_REPO_SCOPED_ENV:
            env.pop(name, None)
    if env_overrides:
        env.update(env_overrides)
    command: list[str] = []
    if network and use_proxy and not os.environ.get("PRIMARY_CHECKOUT_DISABLE_PROXY"):
        proxy = shutil.which(os.environ.get("WITH_PROXY", "with-proxy"))
        if proxy:
            command.append(proxy)
    command.extend(("git", "-C", str(repo), *args))
    return subprocess.run(command, text=True, capture_output=True, check=False, env=env)


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


def indexed_submodule_commit(root: Path, product: str) -> tuple[str | None, str]:
    """Return the ``product`` commit this parent commit will record, and its source.

    Deliberately INHERITS the git environment: inside a pre-commit hook,
    GIT_INDEX_FILE names the exact index being committed -- for the mandated
    pathspec form (``git commit -m msg -- <paths>``) a temporary index built from
    HEAD plus the named paths -- so this yields the gitlink the commit will
    actually record, whether or not the caller staged a submodule bump. Outside a
    hook it reads .git/index. Falls back to HEAD, then to ``(None, reason)``.
    """
    staged = run_git(root, "ls-files", "--stage", "-z", "--", product, inherit_repo_env=True)
    if staged.returncode == 0:
        for entry in filter(None, staged.stdout.split("\0")):
            metadata, _, name = entry.partition("\t")
            fields = metadata.split()
            if name == product and len(fields) >= 2 and fields[0] == "160000":
                return fields[1], "parent index"
    head = run_git(root, "rev-parse", f"HEAD:{product}")
    recorded = head.stdout.strip()
    if head.returncode == 0 and FULL_SHA.fullmatch(recorded):
        return recorded, "parent HEAD"
    return None, f"{product} is not recorded as a gitlink in the parent index or HEAD"


def _tracked_manifest_paths(hermit: Path, commit: str | None) -> tuple[list[str], list[str]]:
    """Tracked ``*Cargo.toml`` paths at ``commit``, or in the working tree if None."""
    if commit is None:
        listed = run_git(hermit, "ls-files", "-z", "--", "*Cargo.toml")
        if listed.returncode != 0:
            return [], ["could not list tracked Hermit Cargo.toml files"]
        return list(filter(None, listed.stdout.split("\0"))), []
    listed = run_git(
        hermit, "ls-tree", "-r", "-z", "--name-only", commit, env_overrides=NO_LAZY_FETCH
    )
    if listed.returncode != 0:
        return [], [f"could not list Cargo.toml files at {commit[:12]}"]
    # `ls-files -- '*Cargo.toml'` globs across '/', so a basename suffix test is
    # the equivalent filter over a flat ls-tree listing.
    return [
        name for name in filter(None, listed.stdout.split("\0")) if name.endswith("Cargo.toml")
    ], []


def _read_tracked(hermit: Path, commit: str | None, relative: Path | str) -> tuple[str | None, str]:
    """Contents of a tracked Hermit file at ``commit``, or from the working tree if None."""
    if commit is None:
        try:
            return (hermit / relative).read_text(), ""
        except OSError as error:
            return None, f"{error}"
    blob = run_git(
        hermit, "cat-file", "blob", f"{commit}:{relative}", env_overrides=NO_LAZY_FETCH
    )
    if blob.returncode != 0:
        return None, (blob.stderr.strip() or f"missing at {commit[:12]}")
    return blob.stdout, ""


def reverie_manifest_pins(
    hermit: Path, commit: str | None = None
) -> tuple[set[str], int, list[str]]:
    """Return exact Reverie revisions from tracked Hermit Cargo manifests.

    ``commit`` selects the recorded submodule tree; None reads the working tree.
    """
    names, errors = _tracked_manifest_paths(hermit, commit)
    pins: list[str] = []
    for relative in names:
        text, failure = _read_tracked(hermit, commit, relative)
        if text is None:
            errors.append(f"could not parse {relative}: {failure}")
            continue
        try:
            pins.extend(_walk_reverie_dependencies(tomllib.loads(text)))
        except tomllib.TOMLDecodeError as error:
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

        # The parent's own index is exactly what we want here, so keep the
        # in-flight GIT_INDEX_FILE a pre-commit hook hands us.
        gitlink = run_git(root, "rev-parse", f":{product}", inherit_repo_env=True)
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


def check_pins(
    root: Path,
    *,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Blocking, offline Reverie-pin *consistency* check.

    Unlike ``check`` (which also inspects primary-checkout freshness, parent
    gitlinks, and queries the network), this verifies only that the Reverie
    *pin* is internally consistent across the Hermit checkout: every tracked
    Cargo.toml rev is identical, and every tracked Cargo.lock source (including
    the nested liteinst-runtime-build/Cargo.lock) references that same SHA.

    It performs NO network access and does NOT look at primary freshness or
    parent gitlinks, so it never blocks a commit that merely repairs a stale
    gitlink; it blocks only a *silent pin drift* where one Reverie reference
    was moved without the others. That makes it safe to run in a pre-commit
    hook. Two dimensions are deliberately EXCLUDED from this blocking gate and
    left to the warning-only ``check``:
      * currency (is the pin on reverie main?) -- networked; also enforced by
        hermit's own check-reverie-pin.rs hook + CI;
      * the revision-keyed build-cache paths (REVERIE_CACHE_FILES) -- those use
        7-char short SHAs and a heterogeneous key scheme (e.g.
        hermit-install/build.rs), so a blocking equality check on them would
        false-positive on a healthy tree. They remain a warning-only signal.

    WHAT IT READS (changed 2026-08-06 after a fleet-wide false block): the
    Hermit tree at the gitlink this parent commit will RECORD, never the Hermit
    working tree. A parent commit records a submodule *commit*, so the recorded
    commit is the only pin state it can possibly introduce; another agent's
    in-flight edits under hermit/ are not part of it and must not block an
    unrelated parent commit. Hermit's own pre-commit hook and CI guard the
    Hermit working tree, and the warning-only ``check`` still reports it.

    FAILURE POSTURE. Fail-CLOSED on real drift: the recorded tree is readable
    and its Reverie references disagree -> exit 1. Fail-OPEN, loudly, when the
    gate cannot be evaluated at all (no gitlink, or the recorded commit is
    absent from the local object store) -- an unevaluable gate is not evidence
    of drift, and blocking there is what turned a stale premise into an outage.
    """
    hermit = root / "hermit"
    commit, origin = indexed_submodule_commit(root, "hermit")
    unevaluable = "" if commit is not None else origin
    if commit is not None:
        readable = run_git(
            hermit, "cat-file", "-e", f"{commit}^{{commit}}", env_overrides=NO_LAZY_FETCH
        )
        if readable.returncode != 0:
            unevaluable = (
                f"recorded hermit gitlink {commit[:12]} ({origin}) is not in the local "
                "Hermit object store"
            )
            commit = None
    if commit is None:
        print(
            f"WARNING: {unevaluable}; Reverie pin drift NOT evaluated "
            "(this gate never blocks on being unevaluable).",
            file=err,
        )
        return 0

    errors: list[str] = []
    pins, pin_count, pin_errors = reverie_manifest_pins(hermit, commit)
    errors.extend(f"hermit: {message}" for message in pin_errors)
    expected: str | None = None
    if pin_count == 0:
        errors.append("hermit: no tracked Cargo manifest pins rrnewton/reverie")
    elif len(pins) != 1:
        errors.append(
            "Hermit Reverie manifest pins are not internally consistent: "
            f"manifests={','.join(sorted(pins))}"
        )
    else:
        expected = next(iter(pins))
        for relative in REVERIE_LOCKFILES:
            text, failure = _read_tracked(hermit, commit, relative)
            if text is None:
                errors.append(f"hermit: could not parse {relative}: {failure}")
                continue
            try:
                lock = tomllib.loads(text)
            except tomllib.TOMLDecodeError as error:
                errors.append(f"hermit: could not parse {relative}: {error}")
                continue
            sources = [
                package.get("source", "")
                for package in lock.get("package", [])
                if isinstance(package, Mapping)
                and str(package.get("source", "")).startswith(f"git+{REVERIE_GIT_URL}")
            ]
            if not sources:
                errors.append(f"hermit: {relative}: no Reverie git sources found")
                continue
            for source in sources:
                match = REVERIE_SOURCE.fullmatch(str(source))
                if match is None or match.group(1) != expected or match.group(2) != expected:
                    errors.append(f"hermit: {relative}: stale Reverie source {source}")

    if errors:
        print(
            f"REVERIE PIN DRIFT at recorded hermit gitlink {commit} ({origin}): "
            "tracked Reverie references disagree - BLOCKED",
            file=err,
        )
        for message in errors:
            print(f"  {message}", file=err)
        print(
            "Every Cargo.toml rev and every tracked Cargo.lock source (including the "
            "nested liteinst-runtime-build/Cargo.lock) must reference ONE identical "
            "Reverie SHA. This is the pin state the commit would RECORD -- the Hermit "
            "working tree was not read, so cleaning it will not clear this. Re-run the "
            "Reverie pin update (hermit/docs/updating-reverie.md) so all references move "
            "together and re-point the gitlink, or override a deliberate in-flight state "
            "with HERMIT_PIN_DRIFT_OVERRIDE=1 git commit ...",
            file=err,
        )
        return 1
    print(
        f"Reverie pin is internally consistent at recorded hermit gitlink {commit[:12]} "
        f"({origin}): {expected} ({pin_count} manifest revision entries; tracked "
        "Cargo.lock sources agree).",
        file=out,
    )
    return 0


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
    subparsers.add_parser(
        "check-pins",
        help="offline: block on Reverie pin drift across manifests and Cargo.lock sources",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if args.command == "fresh":
        return checkout_fresh(
            root, publish_parent=args.publish_parent, strict=args.strict
        )
    if args.command == "check-pins":
        return check_pins(root)
    return check_freshness(root, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
