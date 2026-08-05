from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-codex-setup.py")


class CheckCodexSetupTest(unittest.TestCase):
    def fixture(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / ".claude/skills").mkdir(parents=True)
        (root / ".codex").mkdir()
        (root / "AGENTS.md").write_text("root policy\n")
        (root / ".codex/config.toml").write_text(
            "project_doc_max_bytes = 98304\n"
        )
        (root / ".claude/skills/example.md").write_text(
            "---\n"
            "name: example\n"
            'description: "Use for an example task."\n'
            "---\n\n"
            "Do the example task.\n"
        )
        return root

    def run_check(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), "--root", str(root), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_write_then_check_and_detect_stale_adapter(self) -> None:
        root = self.fixture()
        generated = self.run_check(root, "--write")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        adapter = root / ".agents/skills/example/SKILL.md"
        self.assertTrue(adapter.is_file())
        self.assertIn("../../../.claude/skills/example.md", adapter.read_text())

        source = root / ".claude/skills/example.md"
        source.write_text(source.read_text().replace("example task", "revised task"))
        stale = self.run_check(root)
        self.assertEqual(stale.returncode, 1)
        self.assertIn("stale adapter", stale.stderr)

    def test_rejects_default_sized_instruction_budget(self) -> None:
        root = self.fixture()
        (root / ".codex/config.toml").write_text(
            "project_doc_max_bytes = 32768\n"
        )
        result = self.run_check(root, "--write")
        self.assertEqual(result.returncode, 1)
        self.assertIn("below the 98304-byte coordinator minimum", result.stderr)

    def test_rejects_broken_canonical_skill_link(self) -> None:
        root = self.fixture()
        source = root / ".claude/skills/example.md"
        source.write_text(source.read_text() + "\n[missing](missing.md)\n")
        result = self.run_check(root, "--write")
        self.assertEqual(result.returncode, 1)
        self.assertIn("broken local link 'missing.md'", result.stderr)

    def test_counts_every_policy_in_deepest_nested_chain(self) -> None:
        root = self.fixture()
        (root / "AGENTS.md").write_text("r" * 40_000)
        (root / "product/nested").mkdir(parents=True)
        (root / "product/AGENTS.md").write_text("p" * 40_000)
        (root / "product/nested/AGENTS.md").write_text("n" * 20_000)
        result = self.run_check(root, "--write")
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "largest root+nested AGENTS.md chain is 100004 bytes", result.stderr
        )

    def test_write_refuses_symlinked_skill_directory_without_touching_target(self) -> None:
        root = self.fixture()
        external = root.parent / f"{root.name}-outside"
        external.mkdir()
        self.addCleanup(external.rmdir)
        sentinel = external / "SKILL.md"
        sentinel.write_text("outside sentinel\n")
        self.addCleanup(sentinel.unlink)
        (root / ".agents/skills").mkdir(parents=True)
        (root / ".agents/skills/example").symlink_to(external, target_is_directory=True)

        result = self.run_check(root, "--write")

        self.assertEqual(result.returncode, 1)
        self.assertIn("must not be a symlink", result.stderr)
        self.assertEqual(sentinel.read_text(), "outside sentinel\n")

    def test_rejects_wrong_legacy_planner_target(self) -> None:
        root = self.fixture()
        (root / ".claude/skills/pr-landing-planner").symlink_to("../../wrong-target")

        result = self.run_check(root, "--write")

        self.assertEqual(result.returncode, 1)
        self.assertIn("expected '../../agent-utils/skills/pr-landing-planner'", result.stderr)

    def test_quarantines_planner_from_generated_codex_entries(self) -> None:
        root = self.fixture()
        (root / ".claude/skills/pr-landing-planner").symlink_to(
            "../../agent-utils/skills/pr-landing-planner"
        )

        result = self.run_check(root, "--write")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((root / ".agents/skills/pr-landing-planner").exists())

    def test_rejects_extra_file_inside_generated_adapter(self) -> None:
        root = self.fixture()
        generated = self.run_check(root, "--write")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        extra = root / ".agents/skills/example/runtime-state.json"
        extra.write_text("{}\n")

        result = self.run_check(root)

        self.assertEqual(result.returncode, 1)
        self.assertIn("must contain only SKILL.md", result.stderr)
        self.assertIn("runtime-state.json", result.stderr)

    def test_rejects_prose_wiki_link_but_allows_literal_code(self) -> None:
        root = self.fixture()
        source = root / ".claude/skills/example.md"
        source.write_text(
            source.read_text()
            + "\nCargo supports `[[bin]]` syntax. See [[local-memory-only]].\n"
        )

        result = self.run_check(root, "--write")

        self.assertEqual(result.returncode, 1)
        self.assertIn("unsupported wiki link '[[local-memory-only]]'", result.stderr)
        self.assertNotIn("unsupported wiki link '[[bin]]'", result.stderr)


if __name__ == "__main__":
    unittest.main()
