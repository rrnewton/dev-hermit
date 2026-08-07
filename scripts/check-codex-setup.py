#!/usr/bin/env python3
"""Validate dev-hermit's shared Claude/Codex skill packages."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path


MIN_PROJECT_DOC_BYTES = 98_304
SOURCE_DIR = Path(".claude/skills")
TARGET_DIR = Path(".agents/skills")
LLMS_LINK = Path(".llms/skills")
LLMS_TARGET = "../.claude/skills"
SKIP_FILES = {"README.md", "README.md.orig"}
PLANNER_LINK = "../../agent-utils/skills/pr-landing-planner"
README_TEXT = """# Codex skill entrypoints

Stock Codex discovers repository skills here. Each tracked entry is a
whole-package symlink to the canonical package in `.claude/skills/<name>/`, so
Claude, Codex, and `.llms` consumers read the same `SKILL.md` and bundled
resources. Do not replace package links with generated pointer files or with a
link to `SKILL.md` alone.

`pr-landing-planner` is the deliberate external-package exception. The checker
accepts only the fixed `.claude/skills/pr-landing-planner` link to
`agent-utils/skills/pr-landing-planner` and rejects a duplicate `.agents`
entry. Codex uses the registered agent-utils package named by `AGENTS.md`;
absence here is intentional, not an instruction to skip the mandatory planner.

Run `scripts/check-codex-setup.py` after an intentional skill change. The
checker is read-only and rejects wrong, dangling, escaping, root-level, and
file-only links.
"""
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCED_CODE = re.compile(r"(?:```|~~~)[^\n]*\n.*?(?:```|~~~)", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")
WIKI_LINK = re.compile(r"\[\[[^\]\n]+\]\]")
INSTRUCTION_SCAN_SKIP = {
    ".git",
    ".agents",
    ".claude",
    ".codex",
    ".llms",
    "ignored",
    "node_modules",
    "scratch",
    "target",
    "worktrees",
}


class SetupError(Exception):
    """A repository-local Codex setup invariant failed."""


def path_exists(path: Path) -> bool:
    """Return true for ordinary entries and dangling symlinks."""
    return path.exists() or path.is_symlink()


def contained(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def parent_index_entry(root: Path, path: str) -> tuple[str, str] | None:
    """Return (mode, object) for one exact stage-0 path in the parent index."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--stage", "--", path],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SetupError(f"cannot inspect parent index path {path!r}: {result.stderr.strip()}")
    for line in result.stdout.splitlines():
        metadata, separator, recorded_path = line.partition("\t")
        fields = metadata.split()
        if (
            separator
            and recorded_path == path
            and len(fields) == 3
            and fields[2] == "0"
        ):
            return fields[0], fields[1]
    return None


def git_tree_entry(repo: Path, commit: str, path: str) -> tuple[str, str] | None:
    """Return (mode, object) for one exact path in a committed Git tree."""
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-z", commit, "--", path],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SetupError(
            f"cannot inspect {repo}@{commit}:{path}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    record = result.stdout.split(b"\0", 1)[0]
    if not record:
        return None
    metadata, separator, recorded_path = record.partition(b"\t")
    fields = metadata.split()
    if not separator or len(fields) != 3 or recorded_path.decode() != path:
        raise SetupError(f"unexpected git ls-tree record for {repo}@{commit}:{path}")
    return fields[0].decode(), fields[2].decode()


def normalize_repo_parts(parts: list[str]) -> list[str] | None:
    normalized: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not normalized:
                return None
            normalized.pop()
        else:
            normalized.append(part)
    return normalized


def indexed_submodule_path_exists(repo: Path, commit: str, path: Path) -> bool:
    """Resolve a path, including committed symlinks, inside one gitlink tree."""
    pending = normalize_repo_parts(list(path.parts))
    if pending is None:
        return False
    resolved: list[str] = []
    followed = 0
    while pending:
        component = pending.pop(0)
        candidate = "/".join([*resolved, component])
        entry = git_tree_entry(repo, commit, candidate)
        if entry is None:
            return False
        mode, object_id = entry
        if mode == "120000":
            followed += 1
            if followed > 32:
                return False
            target = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "blob", object_id],
                capture_output=True,
                check=False,
            )
            if target.returncode != 0:
                raise SetupError(
                    f"cannot read indexed symlink {repo}@{commit}:{candidate}"
                )
            link = target.stdout.decode()
            if link.startswith("/"):
                return False
            replacement = normalize_repo_parts(
                [*resolved, *Path(link).parts, *pending]
            )
            if replacement is None:
                return False
            resolved = []
            pending = replacement
            continue
        if pending and mode != "040000":
            return False
        resolved.append(component)
    return True


def indexed_gitlink_target(
    root: Path, candidate: Path
) -> tuple[bool, str | None]:
    """Resolve parent-index symlinks and validate any reached indexed gitlink."""
    if not (root / ".git").exists():
        return False, None
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(Path(os.path.abspath(root)))
    except ValueError:
        return False, None
    pending = normalize_repo_parts(list(relative.parts))
    if pending is None:
        return False, f"{candidate}: local link escapes repository"
    resolved: list[str] = []
    followed = 0
    while pending:
        component = pending.pop(0)
        current_parts = [*resolved, component]
        current = "/".join(current_parts)
        entry = parent_index_entry(root, current)
        if entry is not None and entry[0] == "120000":
            followed += 1
            if followed > 32:
                return True, f"{candidate}: parent-index symlink cycle"
            target = subprocess.run(
                ["git", "-C", str(root), "cat-file", "blob", entry[1]],
                capture_output=True,
                check=False,
            )
            if target.returncode != 0:
                raise SetupError(f"cannot read parent-index symlink {current}")
            link = target.stdout.decode()
            live_component = root.joinpath(*current_parts)
            if not live_component.is_symlink():
                return True, f"{candidate}: indexed symlink {current} is not a live symlink"
            if os.readlink(live_component) != link:
                return True, f"{candidate}: live symlink {current} differs from parent index"
            if link.startswith("/"):
                return True, f"{candidate}: parent-index symlink {current} is absolute"
            replacement = normalize_repo_parts(
                [*resolved, *Path(link).parts, *pending]
            )
            if replacement is None:
                return True, f"{candidate}: parent-index symlink {current} escapes repository"
            resolved = []
            pending = replacement
            continue
        live_component = root.joinpath(*current_parts)
        if live_component.is_symlink():
            return (
                True,
                f"{candidate}: symlink component {current} is not mode 120000 "
                "in the parent index",
            )
        if entry is not None and entry[0] == "160000":
            commit = entry[1]
            repo = root.joinpath(*current_parts)
            name = Path(current).as_posix()
            if not (repo / ".git").exists():
                return True, f"{candidate}: indexed gitlink {name}@{commit} is not materialized"
            subpath = Path(*pending)
            if pending and not indexed_submodule_path_exists(repo, commit, subpath):
                return (
                    True,
                    f"{candidate}: indexed gitlink {name}@{commit} does not contain "
                    f"{subpath.as_posix()}",
                )
            return True, None
        resolved.append(component)
    return False, None


def frontmatter(path: Path) -> str:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise SetupError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise SetupError(f"{path}: unterminated YAML frontmatter")
    block = text[4:end]
    lines = block.splitlines()
    if len(lines) != 2 or not lines[0].startswith("name: "):
        raise SetupError(
            f"{path}: frontmatter must contain exactly name then description"
        )
    name = lines[0].removeprefix("name: ")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise SetupError(f"{path}: invalid lowercase-hyphenated skill name {name!r}")
    if not lines[1].startswith('description: "') or not lines[1].endswith('"'):
        raise SetupError(f"{path}: description must be one double-quoted scalar")
    description = lines[1][len('description: "') : -1]
    if not description.strip() or '"' in description or "\\" in description:
        raise SetupError(
            f"{path}: description must be nonempty and contain no escapes"
        )
    instructions = text[end + 5 :]
    if not instructions.strip():
        raise SetupError(f"{path}: metadata has no skill instructions")
    return name


def require_real_internal_directory(root: Path, path: Path, purpose: str) -> None:
    if not path.is_dir() or path.is_symlink():
        raise SetupError(f"{path}: {purpose} must be a real directory")
    if not contained(path, root):
        raise SetupError(f"{path}: {purpose} escapes repository root")


def check_discovery_ancestors(root: Path) -> None:
    for relative, purpose in (
        (Path(".agents"), "Codex discovery ancestor"),
        (Path(".claude"), "canonical skill ancestor"),
        (Path(".llms"), "Claude discovery ancestor"),
    ):
        require_real_internal_directory(root, root / relative, purpose)

    claude = root / "CLAUDE.md"
    if not claude.is_symlink():
        raise SetupError(f"{claude}: Claude policy entrypoint must be a symlink")
    actual = os.readlink(claude)
    if actual != "AGENTS.md":
        raise SetupError(f"{claude}: points to {actual!r}, expected 'AGENTS.md'")
    if not contained(claude, root) or claude.resolve() != (root / "AGENTS.md").resolve():
        raise SetupError(f"{claude}: dangling, escaping, or misresolved policy link")


def package_symlink_problems(root: Path, package: Path) -> list[str]:
    problems: list[str] = []
    for directory, child_dirs, filenames in os.walk(package, followlinks=False):
        for name in [*child_dirs, *filenames]:
            entry = Path(directory) / name
            if not entry.is_symlink():
                continue
            if not entry.exists():
                problems.append(f"{entry}: dangling canonical package link")
            elif not contained(entry, root):
                problems.append(f"{entry}: canonical package link escapes repository")
    return problems


def canonical_packages(root: Path) -> tuple[dict[str, Path], list[str]]:
    source_root = root / SOURCE_DIR
    require_real_internal_directory(root, source_root, "canonical skill root")
    packages: dict[str, Path] = {}
    problems: list[str] = []
    for entry in sorted(source_root.iterdir()):
        if entry.name in SKIP_FILES:
            continue
        if entry.name == "pr-landing-planner" and entry.is_symlink():
            continue
        if entry.is_symlink():
            problems.append(f"{entry}: canonical skill package must not be a symlink")
            continue
        if not entry.is_dir():
            problems.append(
                f"{entry}: flat canonical skill is unsupported; use <slug>/SKILL.md"
            )
            continue
        skill = entry / "SKILL.md"
        if not skill.is_file() or skill.is_symlink():
            problems.append(f"{skill}: missing regular canonical SKILL.md")
            continue
        try:
            name = frontmatter(skill)
        except (OSError, SetupError) as error:
            problems.append(str(error))
            continue
        if name != entry.name:
            problems.append(
                f"{skill}: skill name {name!r} must match package {entry.name!r}"
            )
            continue
        packages[name] = entry
        problems.extend(package_symlink_problems(root, entry))
    return packages, problems


def configured_doc_limit(root: Path) -> int:
    config = root / ".codex/config.toml"
    if not config.is_file():
        raise SetupError(f"missing project Codex config: {config}")
    with config.open("rb") as handle:
        data = tomllib.load(handle)
    value = data.get("project_doc_max_bytes")
    if not isinstance(value, int):
        raise SetupError(f"{config}: project_doc_max_bytes must be an integer")
    if value < MIN_PROJECT_DOC_BYTES:
        raise SetupError(
            f"{config}: project_doc_max_bytes={value} is below the "
            f"{MIN_PROJECT_DOC_BYTES}-byte coordinator minimum"
        )
    return value


def instruction_chain_bytes(root: Path) -> int:
    root_policy = root / "AGENTS.md"
    if not root_policy.is_file():
        raise SetupError(f"missing root policy: {root_policy}")

    policies: dict[Path, int] = {}
    for directory, child_dirs, filenames in os.walk(root):
        child_dirs[:] = [name for name in child_dirs if name not in INSTRUCTION_SCAN_SKIP]
        if "AGENTS.md" in filenames:
            path = Path(directory) / "AGENTS.md"
            policies[Path(directory)] = path.stat().st_size

    largest = 0
    for directory in policies:
        current = directory
        chain: list[int] = []
        while True:
            if current in policies:
                chain.append(policies[current])
            if current == root:
                break
            if root not in current.parents:
                raise SetupError(f"instruction path escaped repository root: {current}")
            current = current.parent
        largest = max(largest, sum(chain) + max(0, len(chain) - 1) * 2)
    return largest


def local_link_problems(root: Path, packages: dict[str, Path]) -> list[str]:
    """Return broken local Markdown links anywhere in canonical packages."""
    problems: list[str] = []
    for package in packages.values():
        for source in sorted(package.rglob("*.md")):
            if source.is_symlink():
                continue
            content = source.read_text()
            prose = INLINE_CODE.sub("", FENCED_CODE.sub("", content))
            for match in WIKI_LINK.finditer(prose):
                problems.append(
                    f"{source}: unsupported wiki link {match.group(0)!r}; "
                    "use a versioned Markdown link"
                )
            for match in MARKDOWN_LINK.finditer(content):
                raw_target = match.group(1)
                if raw_target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                target = raw_target.split("#", 1)[0]
                if not target:
                    continue
                candidate = source.parent / target
                indexed, indexed_problem = indexed_gitlink_target(root, candidate)
                if indexed_problem:
                    problems.append(indexed_problem)
                elif not candidate.exists():
                    problems.append(f"{source}: broken local link {raw_target!r}")
                elif not contained(candidate, root):
                    problems.append(f"{source}: local link escapes repository {raw_target!r}")
                elif indexed:
                    continue
    return problems


def planner_source(root: Path, problems: list[str]) -> bool:
    source = root / SOURCE_DIR / "pr-landing-planner"
    target = root / TARGET_DIR / "pr-landing-planner"
    if not source.is_symlink():
        return False
    actual = os.readlink(source)
    if actual != PLANNER_LINK:
        problems.append(f"{source}: points to {actual!r}, expected {PLANNER_LINK!r}")
    if path_exists(target):
        problems.append(
            f"{target}: planner is an external agent-utils package; "
            "do not add a duplicate Codex entry"
        )
    return True


def discovery_link_problems(
    root: Path, packages: dict[str, Path]
) -> list[str]:
    problems: list[str] = []
    target_root = root / TARGET_DIR
    if target_root.is_symlink():
        return [f"{target_root}: Codex skill root must be a real directory"]
    if not target_root.is_dir():
        return [f"{target_root}: missing Codex skill directory"]

    readme = target_root / "README.md"
    if not readme.is_file() or readme.is_symlink():
        problems.append(f"{readme}: missing regular README")
    elif readme.read_text() != README_TEXT:
        problems.append(f"{readme}: stale package-link documentation")

    for name, package in packages.items():
        link = target_root / name
        expected = f"../../.claude/skills/{name}"
        if not link.is_symlink():
            kind = "missing" if not path_exists(link) else "must be a package symlink"
            problems.append(f"{link}: {kind}")
            continue
        actual = os.readlink(link)
        if actual != expected:
            problems.append(f"{link}: points to {actual!r}, expected {expected!r}")
            continue
        if not link.exists():
            problems.append(f"{link}: dangling package symlink")
            continue
        if not contained(link, root):
            problems.append(f"{link}: package symlink escapes repository")
            continue
        if link.resolve() != package.resolve():
            problems.append(f"{link}: does not resolve to canonical package {package}")
            continue
        if not (link / "SKILL.md").samefile(package / "SKILL.md"):
            problems.append(f"{link}: SKILL.md is not shared with canonical package")

    expected_names = set(packages)
    for entry in target_root.iterdir():
        if entry.name == "README.md" or entry.name in expected_names:
            continue
        problems.append(f"{entry}: unowned Codex skill entry")

    llms = root / LLMS_LINK
    if not llms.is_symlink():
        problems.append(f"{llms}: .llms skill discovery must be a directory symlink")
    else:
        actual = os.readlink(llms)
        if actual != LLMS_TARGET:
            problems.append(f"{llms}: points to {actual!r}, expected {LLMS_TARGET!r}")
        elif not llms.exists() or llms.resolve() != (root / SOURCE_DIR).resolve():
            problems.append(f"{llms}: dangling or misresolved skill-root link")
    return problems


def check(root: Path) -> int:
    problems: list[str] = []
    try:
        check_discovery_ancestors(root)
        packages, package_problems = canonical_packages(root)
        problems.extend(package_problems)
        limit = configured_doc_limit(root)
        chain_bytes = instruction_chain_bytes(root)
        problems.extend(local_link_problems(root, packages))
        problems.extend(discovery_link_problems(root, packages))
        planner_present = planner_source(root, problems)
    except (OSError, tomllib.TOMLDecodeError, SetupError) as error:
        print(f"check-codex-setup: ERROR {error}", file=sys.stderr)
        return 1

    if chain_bytes > limit:
        problems.append(
            f"largest root+nested AGENTS.md chain is {chain_bytes} bytes, "
            f"above configured limit {limit}"
        )

    if problems:
        for problem in problems:
            print(f"check-codex-setup: ERROR {problem}", file=sys.stderr)
        return 1

    print(
        "check-codex-setup: PASS "
        f"packages={len(packages)} planner_source={str(planner_present).lower()} "
        f"instruction_chain_bytes={chain_bytes} configured_limit={limit}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="validate shared Claude/Codex skill package discovery"
    )
    parser.add_argument("--root", type=Path, help="override the dev-hermit root")
    args = parser.parse_args()
    root = (args.root or Path(__file__).resolve().parent.parent).resolve()
    return check(root)


if __name__ == "__main__":
    raise SystemExit(main())
