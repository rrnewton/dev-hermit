#!/usr/bin/env python3
"""Tests for scripts/primary_checkout.py."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import primary_checkout


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


class _PrimaryFixture(unittest.TestCase):
    """Shared parent+products fixture. Holds no tests of its own so that the
    suites below do not re-run each other's cases."""

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

class PrimaryCheckoutTests(_PrimaryFixture):
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

    def test_check_pins_blocks_stale_lock(self) -> None:
        # Drift the working-tree lock without touching the manifests. check_pins
        # reads the working tree, so no commit is needed.
        lock = self.root / "hermit" / "Cargo.lock"
        reverie_head = git(self.root / "reverie", "rev-parse", "HEAD")
        lock.write_text(lock.read_text().replace(reverie_head, "0" * 40))
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 1)
        self.assertIn("REVERIE PIN DRIFT", err.getvalue())
        self.assertIn("Cargo.lock: stale Reverie source", err.getvalue())

    def test_check_pins_blocks_inconsistent_manifests(self) -> None:
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
        self.assertEqual(result, 1)
        self.assertIn("not internally consistent", err.getvalue())

    def test_check_pins_ignores_cache_key_drift(self) -> None:
        # The revision-keyed cache files are deliberately OUTSIDE the blocking
        # gate (7-char/heterogeneous keys would false-positive). Staling one
        # must NOT block check-pins.
        cache = self.root / "hermit" / "validate.sh"
        cache.write_text("liteinst-runtime-build-00000000\n")
        out, err = StringIO(), StringIO()
        result = primary_checkout.check_pins(self.root, out=out, err=err)
        self.assertEqual(result, 0, err.getvalue())


if __name__ == "__main__":
    unittest.main()


class PrimaryFreshnessTests(_PrimaryFixture):
    """The single freshness invariant over every primary.

    Bracketed both ways throughout: a check that only ever reports drift proves
    nothing, so every negative case is paired with a positive one.
    """

    def drift_kinds(self, primary: str) -> set[str]:
        drifts, _ = primary_checkout.primary_freshness_report(self.root)
        return {d.kind for d in drifts if d.primary == primary}

    def test_clean_primary_reports_no_drift(self) -> None:
        """POSITIVE bracket: a fresh primary must come back empty."""
        self.assertEqual(self.drift_kinds("hermit"), set())

    def test_detects_bare_flip_that_a_refs_only_check_cannot_see(self) -> None:
        """The symptom that recurred four times and was invisible to `check`.

        Under core.bare=true the directory still has .git, `branch
        --show-current` still answers and `rev-parse HEAD` still answers -- so a
        refs-only inspection reports a perfectly healthy primary while every
        work-tree operation fails.
        """
        repo = self.root / "hermit"
        self.assertEqual(self.drift_kinds("hermit"), set())  # fresh beforehand
        git(repo, "config", "core.bare", "true")

        # The primitives the legacy check relies on still look healthy...
        self.assertTrue((repo / ".git").exists())
        self.assertEqual(git(repo, "rev-parse", "--is-bare-repository"), "true")
        # ...but a real work-tree op fails, which is the actual breakage.
        broken = subprocess.run(
            ("git", "-C", str(repo), "status", "--short"),
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(broken.returncode, 0)

        self.assertIn("bare", self.drift_kinds("hermit"))

    def test_restore_safe_repairs_only_the_bare_flip(self) -> None:
        repo = self.root / "hermit"
        git(repo, "config", "core.bare", "true")
        out, err = StringIO(), StringIO()
        code = primary_checkout.check_primary_freshness(
            self.root, restore_safe=True, out=out, err=err
        )
        self.assertEqual(git(repo, "rev-parse", "--is-bare-repository"), "false")
        self.assertIn("RESTORED", out.getvalue())
        # Repaired, and the repair is verified by re-evaluating rather than
        # assumed: hermit must no longer report bare.
        self.assertNotIn("bare", self.drift_kinds("hermit"))
        self.assertIn(code, (0, 1, 2))

    def test_behind_is_classified_not_merely_reported_as_differing(self) -> None:
        """`differs from origin/main` is not actionable; behind/ahead/diverged are."""
        self.advance("hermit")  # move the remote forward
        repo = self.root / "hermit"
        subprocess.run(("git", "-C", str(repo), "fetch", "origin", "main"),
                       check=True, capture_output=True)
        kinds = self.drift_kinds("hermit")
        self.assertIn("behind", kinds)
        self.assertNotIn("ahead", kinds)
        self.assertNotIn("diverged", kinds)

    def test_ahead_and_diverged_are_distinguished_from_behind(self) -> None:
        repo = self.root / "hermit"
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test")
        (repo / "local.txt").write_text("local\n")
        git(repo, "add", "local.txt")
        git(repo, "commit", "-m", "local only")
        self.assertIn("ahead", self.drift_kinds("hermit"))

        self.advance("hermit")  # now both sides have moved
        subprocess.run(("git", "-C", str(repo), "fetch", "origin", "main"),
                       check=True, capture_output=True)
        kinds = self.drift_kinds("hermit")
        self.assertIn("diverged", kinds)
        self.assertNotIn("behind", kinds)

    def test_never_fast_forwards_or_resets_on_its_own(self) -> None:
        """#320: detect and report; do not move a shared integration surface."""
        self.advance("hermit")
        repo = self.root / "hermit"
        subprocess.run(("git", "-C", str(repo), "fetch", "origin", "main"),
                       check=True, capture_output=True)
        before = git(repo, "rev-parse", "HEAD")
        primary_checkout.check_primary_freshness(
            self.root, restore_safe=True, out=StringIO(), err=StringIO()
        )
        self.assertEqual(git(repo, "rev-parse", "HEAD"), before)

    def test_dirty_is_reported_without_proposing_to_discard(self) -> None:
        repo = self.root / "hermit"
        (repo / "someone-elses-work.txt").write_text("not mine\n")
        drifts, _ = primary_checkout.primary_freshness_report(self.root)
        dirty = [d for d in drifts if d.primary == "hermit" and d.kind == "dirty"]
        self.assertTrue(dirty)
        # Invariant 5: never suggest destroying changes we did not create.
        for drift in dirty:
            for banned in ("reset", "checkout --", "clean", "stash"):
                self.assertNotIn(banned, drift.remediation)

    def test_exit_codes_separate_undetermined_from_drift(self) -> None:
        """An unevaluable primary is exit 2 -- nothing proven is not a pass."""
        out, err = StringIO(), StringIO()
        clean = primary_checkout.check_primary_freshness(self.root, out=out, err=err)
        self.assertEqual(clean, 0)
        self.assertIn("PRIMARY FRESHNESS OK", out.getvalue())

        git(self.root / "hermit", "remote", "set-url", "origin", str(self.root / "nope.git"))
        out, err = StringIO(), StringIO()
        self.assertEqual(
            primary_checkout.check_primary_freshness(self.root, out=out, err=err), 2
        )
        self.assertIn("unknown", err.getvalue())


class LegacyCheckBlindSpotTests(_PrimaryFixture):
    """`check` is advisory and called from the pre-commit hook with `|| true`,
    so its exit policy is deliberately left alone -- but it must no longer be
    BLIND to the two classes it could not see."""

    def test_check_now_reports_the_bare_flip(self) -> None:
        out, err = StringIO(), StringIO()
        primary_checkout.check_freshness(self.root, out=out, err=err)
        self.assertNotIn("core.bare", err.getvalue())  # positive bracket

        git(self.root / "hermit", "config", "core.bare", "true")
        out, err = StringIO(), StringIO()
        code = primary_checkout.check_freshness(self.root, out=out, err=err)
        self.assertIn("core.bare=true", err.getvalue())
        self.assertEqual(code, 0)  # still advisory: must not start blocking commits
