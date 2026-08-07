#!/usr/bin/env python3
"""Tests for scripts/primary_checkout.py."""

from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

from scripts import primary_checkout


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


class _ParentWorkspaceFixture(unittest.TestCase):
    """A miniature dev-hermit parent: three product gitlinks, one Reverie pin."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "parent"
        self.root.mkdir()
        self.seeds: dict[str, Path] = {}
        for product in primary_checkout.PRODUCTS:
            remote = Path(self.temp.name) / f"{product}.git"
            seed = Path(self.temp.name) / f"{product}-seed"
            subprocess.run(
                ("git", "init", "--bare", "--initial-branch=main", str(remote)),
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ("git", "init", "--initial-branch=main", str(seed)),
                check=True,
                capture_output=True,
            )
            git(seed, "config", "user.email", "test@example.com")
            git(seed, "config", "user.name", "Test")
            (seed / "README").write_text("initial\n")
            git(seed, "add", "README")
            git(seed, "commit", "-m", "initial")
            git(seed, "remote", "add", "origin", str(remote))
            git(seed, "push", "origin", "main")
            subprocess.run(
                ("git", "clone", str(remote), str(self.root / product)),
                check=True,
                capture_output=True,
            )
            self.seeds[product] = seed

        reverie_rev = git(self.seeds["reverie"], "rev-parse", "HEAD")
        hermit_manifest = self.seeds["hermit"] / "Cargo.toml"
        hermit_manifest.write_text(
            "[package]\n"
            'name = "hermit-test"\n'
            'version = "0.1.0"\n\n'
            "[dependencies]\n"
            "reverie = { git = \"https://github.com/rrnewton/reverie.git\", "
            f'rev = "{reverie_rev}" }}\n'
        )
        lock_text = (
            "version = 3\n\n"
            "[[package]]\n"
            'name = "reverie-core"\n'
            'version = "0.2.0"\n'
            f'source = "git+https://github.com/rrnewton/reverie.git?rev={reverie_rev}#{reverie_rev}"\n'
        )
        generated = {
            Path("Cargo.lock"): lock_text,
            Path("liteinst-runtime-build/Cargo.lock"): lock_text,
            Path("ci/dag/portable.json"): f"liteinst-runtime-build-{reverie_rev[:8]}\n",
            Path("hermit-cli/tests/common/liteinst.rs"): (
                f"liteinst-runtime-build-{reverie_rev[:8]}\n"
            ),
            Path("hermit-install/build.rs"): f"liteinst-runtime-{reverie_rev[:8]}\n",
            Path("validate.sh"): f"liteinst-runtime-build-{reverie_rev[:8]}\n",
        }
        for relative, contents in generated.items():
            path = self.seeds["hermit"] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        git(
            self.seeds["hermit"],
            "add",
            "Cargo.toml",
            *(str(path) for path in generated),
        )
        git(self.seeds["hermit"], "commit", "-m", "pin reverie")
        git(self.seeds["hermit"], "push", "origin", "main")
        git(self.root / "hermit", "pull", "--ff-only", "origin", "main")

        subprocess.run(
            ("git", "init", "--initial-branch=main", str(self.root)),
            check=True,
            capture_output=True,
        )
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "add", *primary_checkout.PRODUCTS)
        git(self.root, "commit", "-m", "initial snapshot")
        parent_remote = Path(self.temp.name) / "parent.git"
        subprocess.run(
            ("git", "init", "--bare", "--initial-branch=main", str(parent_remote)),
            check=True,
            capture_output=True,
        )
        git(self.root, "remote", "add", "origin", str(parent_remote))
        git(self.root, "push", "origin", "main")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def advance(self, product: str) -> str:
        seed = self.seeds[product]
        with (seed / "README").open("a") as readme:
            readme.write("next\n")
        git(seed, "add", "README")
        git(seed, "commit", "-m", "next")
        git(seed, "push", "origin", "main")
        return git(seed, "rev-parse", "HEAD")

    def dirty_hermit_worktree(self) -> None:
        """Leave someone else's uncommitted work under hermit/, pin files included.

        Covers all three shapes seen in the field: a rewritten manifest rev, a
        tracked pin file missing from the working tree, and untracked scratch.
        """
        reverie_head = git(self.root / "reverie", "rev-parse", "HEAD")
        manifest = self.root / "hermit" / "Cargo.toml"
        manifest.write_text(manifest.read_text().replace(reverie_head, "b" * 40))
        lock = self.root / "hermit" / "Cargo.lock"
        lock.write_text(lock.read_text().replace(reverie_head, "0" * 40))
        (self.root / "hermit" / "liteinst-runtime-build" / "Cargo.lock").unlink()
        (self.root / "hermit" / "scratch-note.md").write_text("someone else's work\n")

    def drift_hermit_commit(self) -> str:
        """Commit a real Reverie pin drift IN the Hermit submodule. Returns its SHA."""
        lock = self.root / "hermit" / "Cargo.lock"
        reverie_head = git(self.root / "reverie", "rev-parse", "HEAD")
        lock.write_text(lock.read_text().replace(reverie_head, "0" * 40))
        git(self.root / "hermit", "add", "Cargo.lock")
        git(self.root / "hermit", "commit", "-m", "drift the lock")
        return git(self.root / "hermit", "rev-parse", "HEAD")


class PrimaryCheckoutTests(_ParentWorkspaceFixture):
    def test_fresh_updates_clean_repos_and_preserves_dirty_repo(self) -> None:
        hermit_remote = self.advance("hermit")
        git(self.root / "reverie", "checkout", "--detach")
        liteinst_head = git(self.root / "liteinst2", "rev-parse", "HEAD")
        self.advance("liteinst2")
        (self.root / "liteinst2" / "local-change").write_text("preserve\n")
        for index in range(24):
            (self.root / "liteinst2" / f"dirty-{index}").write_text("preserve\n")
        out, err = StringIO(), StringIO()

        result = primary_checkout.checkout_fresh(
            self.root, use_proxy=False, out=out, err=err
        )

        self.assertEqual(result, 0)
        self.assertEqual(git(self.root / "hermit", "rev-parse", "HEAD"), hermit_remote)
        self.assertEqual(git(self.root / "hermit", "branch", "--show-current"), "main")
        self.assertEqual(git(self.root / "reverie", "branch", "--show-current"), "main")
        self.assertEqual(git(self.root / "liteinst2", "rev-parse", "HEAD"), liteinst_head)
        self.assertTrue((self.root / "liteinst2" / "local-change").is_file())
        self.assertIn("liteinst2 is dirty", err.getvalue())
        self.assertIn("... 5 more path(s)", err.getvalue())

    def test_check_warns_for_stale_and_detached_without_blocking(self) -> None:
        self.advance("hermit")
        git(self.root / "reverie", "checkout", "--detach")
        out, err = StringIO(), StringIO()

        result = primary_checkout.check_freshness(
            self.root, use_proxy=False, out=out, err=err
        )
        strict_result = primary_checkout.check_freshness(
            self.root, strict=True, use_proxy=False, out=out, err=err
        )

        self.assertEqual(result, 0)
        self.assertEqual(strict_result, 1)
        self.assertIn("hermit: HEAD", err.getvalue())
        self.assertIn("reverie: branch is DETACHED", err.getvalue())

    def test_fresh_publishes_one_consistent_parent_snapshot(self) -> None:
        hermit_remote = self.advance("hermit")
        liteinst_remote = self.advance("liteinst2")
        out, err = StringIO(), StringIO()

        result = primary_checkout.checkout_fresh(
            self.root,
            publish_parent=True,
            strict=True,
            use_proxy=False,
            out=out,
            err=err,
        )

        self.assertEqual(result, 0, err.getvalue())
        self.assertEqual(git(self.root, "rev-parse", "HEAD:hermit"), hermit_remote)
        self.assertEqual(
            git(self.root, "rev-parse", "HEAD:reverie"),
            git(self.root / "reverie", "rev-parse", "HEAD"),
        )
        self.assertEqual(
            git(self.root, "rev-parse", "HEAD:liteinst2"), liteinst_remote
        )
        self.assertEqual(
            git(self.root, "rev-parse", "HEAD"),
            git(self.root, "rev-parse", "origin/main"),
        )
        self.assertIn("Published parent snapshot", out.getvalue())

    def test_snapshot_refuses_reverie_manifest_mismatch(self) -> None:
        original_parent = git(self.root, "rev-parse", "HEAD")
        self.advance("reverie")
        out, err = StringIO(), StringIO()

        result = primary_checkout.checkout_fresh(
            self.root,
            publish_parent=True,
            strict=True,
            use_proxy=False,
            out=out,
            err=err,
        )

        self.assertEqual(result, 1)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), original_parent)
        self.assertIn("not globally consistent", err.getvalue())

    def test_snapshot_refuses_stale_reverie_lock(self) -> None:
        original_parent = git(self.root, "rev-parse", "HEAD")
        lock = self.seeds["hermit"] / "Cargo.lock"
        reverie_head = git(self.root / "reverie", "rev-parse", "HEAD")
        lock.write_text(lock.read_text().replace(reverie_head, "0" * 40))
        git(self.seeds["hermit"], "add", "Cargo.lock")
        git(self.seeds["hermit"], "commit", "-m", "stale lock")
        git(self.seeds["hermit"], "push", "origin", "main")
        out, err = StringIO(), StringIO()

        result = primary_checkout.checkout_fresh(
            self.root,
            publish_parent=True,
            strict=True,
            use_proxy=False,
            out=out,
            err=err,
        )

        self.assertEqual(result, 1)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), original_parent)
        self.assertIn("Cargo.lock: stale Reverie source", err.getvalue())

    def test_snapshot_refuses_stale_revision_cache_key(self) -> None:
        original_parent = git(self.root, "rev-parse", "HEAD")
        cache = self.seeds["hermit"] / "validate.sh"
        cache.write_text("liteinst-runtime-build-00000000\n")
        git(self.seeds["hermit"], "add", "validate.sh")
        git(self.seeds["hermit"], "commit", "-m", "stale cache")
        git(self.seeds["hermit"], "push", "origin", "main")
        out, err = StringIO(), StringIO()

        result = primary_checkout.checkout_fresh(
            self.root,
            publish_parent=True,
            strict=True,
            use_proxy=False,
            out=out,
            err=err,
        )

        self.assertEqual(result, 1)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), original_parent)
        self.assertIn("validate.sh: cache keys=00000000", err.getvalue())

    def test_check_pins_passes_on_consistent_tree(self) -> None:
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 0, err.getvalue())
        self.assertIn("Reverie pin is internally consistent", out.getvalue())

    # ---------------------------------------------------------------------
    # check-pins evaluates the Hermit tree at the gitlink the parent commit
    # RECORDS, never the Hermit working tree. Each blocking case below is
    # therefore bracketed by its non-recorded twin: same drift left in the
    # working tree must NOT block. See check_pins.__doc__.
    # ---------------------------------------------------------------------

    def test_check_pins_blocks_recorded_stale_lock(self) -> None:
        # NEGATIVE CONTROL (mutation): a genuine pin drift, recorded by the
        # gitlink this commit would take, is still refused -- fail-closed.
        drifted = self.drift_hermit_commit()
        git(self.root, "add", "hermit")
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 1)
        self.assertIn("REVERIE PIN DRIFT", err.getvalue())
        self.assertIn(drifted, err.getvalue())
        self.assertIn("Cargo.lock: stale Reverie source", err.getvalue())

    def test_check_pins_ignores_unrecorded_committed_drift(self) -> None:
        # Same drift committed in the submodule, but the parent index still
        # records the old gitlink: this commit introduces no drift, so it must
        # not be blocked. (Refuse only what the commit would actually record.)
        self.drift_hermit_commit()
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 0, err.getvalue())

    def test_check_pins_ignores_dirty_worktree_pin_drift(self) -> None:
        # THE REGRESSION THIS FIXES: another agent's uncommitted work under
        # hermit/ -- a rewritten manifest rev, a deleted tracked pin file, and
        # untracked scratch -- must not block an unrelated parent commit.
        self.dirty_hermit_worktree()
        self.assertNotEqual(git(self.root / "hermit", "status", "--porcelain"), "")
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 0, err.getvalue())

    def test_check_pins_ignores_submodule_index_only_drift(self) -> None:
        # Staging a drifted manifest inside the submodule's own index does not
        # change the gitlink the parent records, so it must not block either.
        manifest = self.root / "hermit" / "detcore" / "Cargo.toml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            "[dependencies]\n"
            'reverie = { git = "https://github.com/rrnewton/reverie.git", '
            f'rev = "{"a" * 40}" }}\n'
        )
        git(self.root / "hermit", "add", "detcore/Cargo.toml")
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 0, err.getvalue())

    def test_check_pins_blocks_recorded_inconsistent_manifests(self) -> None:
        manifest = self.root / "hermit" / "detcore" / "Cargo.toml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            "[dependencies]\n"
            'reverie = { git = "https://github.com/rrnewton/reverie.git", '
            f'rev = "{"a" * 40}" }}\n'
        )
        git(self.root / "hermit", "add", "detcore/Cargo.toml")
        git(self.root / "hermit", "commit", "-m", "second rev")
        git(self.root, "add", "hermit")
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 1)
        self.assertIn("not internally consistent", err.getvalue())

    def test_check_pins_ignores_cache_key_drift(self) -> None:
        # The revision-keyed cache files are deliberately OUTSIDE the blocking
        # gate (7-char/heterogeneous keys would false-positive). Staling one in
        # the RECORDED tree must still not block check-pins.
        cache = self.root / "hermit" / "validate.sh"
        cache.write_text("liteinst-runtime-build-00000000\n")
        git(self.root / "hermit", "add", "validate.sh")
        git(self.root / "hermit", "commit", "-m", "stale cache key")
        git(self.root, "add", "hermit")
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 0, err.getvalue())

    def test_check_pins_does_not_block_when_gitlink_is_unreadable(self) -> None:
        # Unevaluable is not evidence of drift: warn loudly, never block.
        subprocess.run(
            (
                "git",
                "-C",
                str(self.root),
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{'c' * 40},hermit",
            ),
            check=True,
            capture_output=True,
        )
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 0)
        self.assertIn("NOT evaluated", err.getvalue())
        self.assertNotIn("BLOCKED", err.getvalue())


REPO_ROOT = Path(primary_checkout.__file__).resolve().parent.parent


class PreCommitHookTests(_ParentWorkspaceFixture):
    """End-to-end through the REAL .githooks/pre-commit, via a real `git commit`.

    Only a real commit reproduces the outage, because git itself is what exports
    GIT_INDEX_FILE -- and for the pathspec form parent policy mandates
    (`git commit -m msg -- <paths>`) it exports an ABSOLUTE path to the parent's
    temporary index, which overrides `git -C hermit`.
    """

    def setUp(self) -> None:
        super().setUp()
        hooks = self.root / ".githooks"
        hooks.mkdir()
        shutil.copy2(REPO_ROOT / ".githooks" / "pre-commit", hooks / "pre-commit")
        (self.root / "scripts").mkdir()
        shutil.copy2(
            REPO_ROOT / "scripts" / "primary_checkout.py",
            self.root / "scripts" / "primary_checkout.py",
        )
        git(self.root, "config", "core.hooksPath", ".githooks")
        # A parent-tracked manifest at a path that does not exist under hermit/.
        # This is the shmem_exec_obj/** shape: harmless in the parent, fatal once
        # an unscrubbed `git -C hermit ls-files` enumerates the parent's index.
        decoy = self.root / "crates-squat" / "Cargo.toml"
        decoy.parent.mkdir()
        decoy.write_text('[package]\nname = "squat"\n')
        (self.root / "notes.md").write_text("parent notes\n")
        git(self.root, "add", "crates-squat/Cargo.toml", "notes.md")
        git(self.root, "commit", "-m", "parent files", "--no-verify")

    def commit(self, *pathspec: str, message: str = "unrelated parent work", **env: str):
        environment = dict(os.environ, PRIMARY_CHECKOUT_DISABLE_PROXY="1", **env)
        return subprocess.run(
            ("git", "-C", str(self.root), "commit", "-m", message, "--", *pathspec),
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def test_hook_allows_unrelated_pathspec_commit_with_dirty_hermit(self) -> None:
        # POSITIVE CONTROL. An unrelated explicit-path parent commit, with the
        # Hermit submodule dirty (pin files included) and a parent-tracked decoy
        # manifest staged in the index. This is the exact shape that forced the
        # whole fleet onto HERMIT_PIN_DRIFT_OVERRIDE=1.
        self.dirty_hermit_worktree()
        before = git(self.root, "rev-parse", "HEAD")
        (self.root / "notes.md").write_text("parent notes, revised\n")
        git(self.root, "add", "notes.md")

        result = self.commit("notes.md")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(git(self.root, "rev-parse", "HEAD"), before)
        self.assertNotIn("BLOCKED", result.stderr)
        self.assertNotIn("crates-squat", result.stderr)
        # The gate ran and reached a verdict -- it was not skipped into silence.
        self.assertIn("Reverie pin is internally consistent", result.stdout + result.stderr)
        # ...and the submodule's dirt is untouched (Hard Invariant 5).
        self.assertTrue((self.root / "hermit" / "scratch-note.md").is_file())

    def test_hook_refuses_recorded_pin_drift(self) -> None:
        # NEGATIVE CONTROL (mutation). Plant a genuine pin drift and let the
        # commit record it: the hook must still refuse, and refuse nothing.
        drifted = self.drift_hermit_commit()
        before = git(self.root, "rev-parse", "HEAD")
        git(self.root, "add", "hermit")

        result = self.commit("hermit", message="advance hermit gitlink")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("REVERIE PIN DRIFT", result.stderr)
        self.assertIn(drifted, result.stderr)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), before)

    def test_hook_override_still_clears_a_real_refusal(self) -> None:
        # The documented escape hatch must remain functional -- but it is now
        # needed only for a deliberate in-flight drift, not for every commit.
        self.drift_hermit_commit()
        git(self.root, "add", "hermit")

        result = self.commit(
            "hermit", message="advance hermit gitlink", HERMIT_PIN_DRIFT_OVERRIDE="1"
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("HERMIT_PIN_DRIFT_OVERRIDE=1 set", result.stderr)


class GitEnvironmentScrubTests(unittest.TestCase):
    """`git -C <repo>` must mean <repo> even under an inherited git environment.

    Git exports GIT_INDEX_FILE into hook children; for the mandated pathspec
    commit form it is an ABSOLUTE path to the parent's temporary index, and it
    overrides `-C`. Unscrubbed, that made check-pins enumerate the parent's
    manifests and resolve them under hermit/.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.outer = Path(self.temp.name) / "outer"
        self.inner = self.outer / "inner"
        self.inner.mkdir(parents=True)
        for repo, name in ((self.outer, "outer.txt"), (self.inner, "inner.txt")):
            subprocess.run(
                ("git", "init", "-q", "--initial-branch=main", str(repo)),
                check=True,
                capture_output=True,
            )
            git(repo, "config", "user.email", "test@example.com")
            git(repo, "config", "user.name", "Test")
            (repo / name).write_text("x\n")
            git(repo, "add", name)
            git(repo, "commit", "-m", "initial")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_run_git_scrubs_inherited_index_by_default(self) -> None:
        leaked = str(self.outer / ".git" / "index")
        with unittest.mock.patch.dict(os.environ, {"GIT_INDEX_FILE": leaked}):
            scrubbed = primary_checkout.run_git(self.inner, "ls-files")
            inherited = primary_checkout.run_git(self.inner, "ls-files", inherit_repo_env=True)
        self.assertEqual(scrubbed.stdout.split(), ["inner.txt"])
        self.assertEqual(inherited.stdout.split(), ["outer.txt"])


if __name__ == "__main__":
    unittest.main()
