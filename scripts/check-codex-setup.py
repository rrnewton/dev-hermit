#!/usr/bin/env python3
"""Validate and generate dev-hermit's stock-Codex discovery adapters."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path


MIN_PROJECT_DOC_BYTES = 98_304
SOURCE_DIR = Path(".claude/skills")
TARGET_DIR = Path(".agents/skills")
SKIP_FILES = {"README.md", "README.md.orig"}
GENERATED_MARKER = "# Codex discovery entrypoint"
PLANNER_LINK = "../../agent-utils/skills/pr-landing-planner"
README_TEXT = """# Codex skill entrypoints

Stock Codex discovers repository skills here. The generated `SKILL.md` files
carry trigger metadata and route to the canonical coordinator instructions in
`.claude/skills/`; this avoids maintaining two policy bodies.

Run `scripts/check-codex-setup.py --write` after an intentional canonical skill
edit, then run `scripts/check-codex-setup.py`. Do not hand-edit generated
entrypoints.
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


def safe_generated_paths(root: Path, wrappers: dict[str, str]) -> None:
    """Reject generated paths that could redirect writes outside the repository."""
    current = root
    for part in TARGET_DIR.parts:
        current /= part
        if current.is_symlink():
            raise SetupError(f"{current}: generated directory must not be a symlink")
        if current.exists() and not current.is_dir():
            raise SetupError(f"{current}: generated directory path is not a directory")

    target_root = root / TARGET_DIR
    readme = target_root / "README.md"
    if readme.is_symlink():
        raise SetupError(f"{readme}: generated README must not be a symlink")
    if readme.exists() and not readme.is_file():
        raise SetupError(f"{readme}: generated README path is not a regular file")
    for name in wrappers:
        directory = target_root / name
        if directory.is_symlink():
            raise SetupError(f"{directory}: generated skill directory must not be a symlink")
        if directory.exists() and not directory.is_dir():
            raise SetupError(f"{directory}: generated skill path is not a directory")
        skill = directory / "SKILL.md"
        if skill.is_symlink():
            raise SetupError(f"{skill}: generated adapter must not be a symlink")
        if skill.exists() and not skill.is_file():
            raise SetupError(f"{skill}: generated adapter path is not a regular file")


def frontmatter(path: Path) -> tuple[str, str]:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise SetupError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise SetupError(f"{path}: unterminated YAML frontmatter")
    block = text[: end + 5]
    name_match = re.search(r'^name:\s*["\']?([^"\'\n]+?)["\']?\s*$', block, re.MULTILINE)
    if not name_match:
        raise SetupError(f"{path}: frontmatter lacks name")
    if not re.search(r"^description:\s*\S", block, re.MULTILINE):
        raise SetupError(f"{path}: frontmatter lacks description")
    return block.rstrip(), name_match.group(1).strip()


def wrapper_text(source: Path, block: str) -> str:
    relative = Path("../../..") / source
    return (
        f"{block}\n\n"
        f"{GENERATED_MARKER}\n\n"
        "This is a generated portable entrypoint. Read and follow "
        f"[the canonical coordinator skill]({relative.as_posix()}) completely "
        "before acting. Resolve any further relative references from the "
        "canonical file's directory.\n"
    )


def canonical_wrappers(root: Path) -> dict[str, str]:
    source_root = root / SOURCE_DIR
    if not source_root.is_dir():
        raise SetupError(f"missing canonical skill directory: {source_root}")
    wrappers: dict[str, str] = {}
    for source in sorted(source_root.glob("*.md")):
        if source.name in SKIP_FILES:
            continue
        block, name = frontmatter(source)
        if name != source.stem:
            raise SetupError(
                f"{source}: skill name {name!r} must match filename {source.stem!r}"
            )
        wrappers[name] = wrapper_text(source.relative_to(root), block)
    return wrappers


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
        child_dirs[:] = [
            name for name in child_dirs if name not in INSTRUCTION_SCAN_SKIP
        ]
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
        # Codex inserts separators while concatenating project instructions.
        largest = max(largest, sum(chain) + max(0, len(chain) - 1) * 2)
    return largest


def local_link_problems(root: Path) -> list[str]:
    """Return broken local Markdown links in canonical coordinator skills."""
    problems: list[str] = []
    for source in sorted((root / SOURCE_DIR).glob("*.md")):
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
            # The planner is an optional pinned submodule skill. Its adapter
            # and canonical links may be dangling until agent-utils is
            # explicitly materialized; planner_bridge reports that condition.
            if target.startswith("pr-landing-planner/"):
                continue
            if not (source.parent / target).exists():
                problems.append(f"{source}: broken local link {raw_target!r}")
    return problems


def planner_source(root: Path, write: bool, problems: list[str]) -> bool:
    legacy = root / SOURCE_DIR / "pr-landing-planner"
    target = root / TARGET_DIR / "pr-landing-planner"
    if not legacy.is_symlink():
        return False
    legacy_actual = os.readlink(legacy)
    if legacy_actual != PLANNER_LINK:
        problems.append(
            f"{legacy}: points to {legacy_actual!r}, expected {PLANNER_LINK!r}"
        )
    # The current parent pin is intentionally quarantined: merely finding a
    # symlink does not prove the pinned planner has current landing semantics.
    # Do not publish it to stock Codex until an isolated agent-utils pin update
    # passes check-agent-utils-pin and this quarantine is deliberately removed.
    if target.is_symlink() and os.readlink(target) == PLANNER_LINK and write:
        target.unlink()
    elif path_exists(target):
        problems.append(
            f"{target}: planner entry is quarantined until the agent-utils pin "
            "is semantically reviewed"
        )
    return True


def check(root: Path, write: bool) -> int:
    problems: list[str] = []
    try:
        wrappers = canonical_wrappers(root)
        limit = configured_doc_limit(root)
        chain_bytes = instruction_chain_bytes(root)
        problems.extend(local_link_problems(root))
        safe_generated_paths(root, wrappers)
    except (OSError, tomllib.TOMLDecodeError, SetupError) as error:
        print(f"check-codex-setup: ERROR {error}", file=sys.stderr)
        return 1

    if chain_bytes > limit:
        problems.append(
            f"largest root+nested AGENTS.md chain is {chain_bytes} bytes, "
            f"above configured limit {limit}"
        )

    target_root = root / TARGET_DIR
    if write:
        # All owned paths were checked before the first mkdir/write so a
        # planted symlink cannot redirect even a partial regeneration.
        target_root.mkdir(parents=True, exist_ok=True)
        (target_root / "README.md").write_text(README_TEXT)
        for name, expected in wrappers.items():
            directory = target_root / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "SKILL.md").write_text(expected)

    planner_present = planner_source(root, write, problems)
    expected_names = set(wrappers)

    for name, expected in wrappers.items():
        directory = target_root / name
        skill = directory / "SKILL.md"
        if not skill.is_file():
            problems.append(f"{skill}: missing Codex skill adapter")
            continue
        if skill.read_text() != expected:
            problems.append(
                f"{skill}: stale adapter (run scripts/check-codex-setup.py --write)"
            )
        entries = list(directory.iterdir())
        if len(entries) != 1 or entries[0].name != "SKILL.md":
            extras = sorted(entry.name for entry in entries if entry.name != "SKILL.md")
            problems.append(
                f"{directory}: generated directory must contain only SKILL.md; "
                f"extra entries={extras}"
            )

    readme = target_root / "README.md"
    if not readme.is_file() or readme.is_symlink():
        problems.append(f"{readme}: missing regular generated README")
    elif readme.read_text() != README_TEXT:
        problems.append(
            f"{readme}: stale generated README "
            "(run scripts/check-codex-setup.py --write)"
        )

    if target_root.is_dir():
        for entry in target_root.iterdir():
            if entry.name == "README.md" or entry.name in expected_names:
                continue
            problems.append(f"{entry}: unowned Codex skill entry")

    if problems:
        for problem in problems:
            print(f"check-codex-setup: ERROR {problem}", file=sys.stderr)
        return 1

    print(
        "check-codex-setup: PASS "
        f"adapters={len(wrappers)} planner_source={str(planner_present).lower()} "
        f"instruction_chain_bytes={chain_bytes} configured_limit={limit}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="validate or regenerate stock-Codex skill discovery adapters"
    )
    parser.add_argument("--root", type=Path, help="override the dev-hermit root")
    parser.add_argument(
        "--write", action="store_true", help="regenerate owned adapter SKILL.md files"
    )
    args = parser.parse_args()
    root = (args.root or Path(__file__).resolve().parent.parent).resolve()
    return check(root, args.write)


if __name__ == "__main__":
    raise SystemExit(main())
