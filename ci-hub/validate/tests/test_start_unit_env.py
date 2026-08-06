#!/usr/bin/env python3
"""Network-free tests for the env the validate systemd unit inherits.

WHY THIS EXISTS. The unit gets a DELIBERATELY MINIMAL environment (systemd-run
does not inherit the caller's), so anything the build needs must be passed
explicitly. libunwind is not installed system-wide on this host class, and when
its three variables are missing the workspace build dies INSIDE the unit --
which surfaces as a failed DAG lane, i.e. it reads as a product red when it is
an environment fault. Worse, cache-warming cannot paper over it: unwind-sys's
build.rs emits `cargo:rerun-if-env-changed=PKG_CONFIG_PATH`, so cargo re-runs it
precisely when the variable is absent.

The three variables are NOT interchangeable, and that is the specific trap this
guards -- propagating only two still fails, just later and with a different
message:
  PKG_CONFIG_PATH  build time; without it build.rs panics on pkg-config.
  LIBRARY_PATH     LINK time; without it the link dies `rust-lld: error: unable
                   to find library -lunwind` even though build.rs succeeded.
  LD_LIBRARY_PATH  RUN time only. The loader is not the linker; this one alone
                   never fixes a link.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import start_unit as su  # noqa: E402


TARGET = "9" * 40


def setenvs(command: list[str]) -> dict[str, str]:
    """Extract the KEY=VALUE pairs systemd-run is told to set."""
    out: dict[str, str] = {}
    for flag, value in zip(command, command[1:]):
        if flag == "--setenv" and "=" in value:
            key, _, val = value.partition("=")
            out[key] = val
    return out


def build(root: Path, environment: dict[str, str]) -> list[str]:
    return su.build_systemd_command(
        root=root,
        checkout=root / "hermit",
        target=TARGET,
        agent="test-agent",
        unit="validate-test.service",
        log=root / "out.log",
        pr=None,
        validate_args=["full"],
        wait=1,
        hold=1,
        child_deadline=1,
        environment=environment,
    )


class StartUnitEnvTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.lu = self.root / "ignored/lu-parity/usr/lib64"
        self.base_env = {"HOME": "/home/agent", "PATH": "/usr/bin"}

    def plant_libunwind(self) -> None:
        (self.lu / "pkgconfig").mkdir(parents=True, exist_ok=True)
        (self.lu / "pkgconfig/libunwind-ptrace.pc").write_text("Name: libunwind-ptrace\n")

    # -- POSITIVE: the qualifying case fires, and fires COMPLETELY ------------

    def test_all_three_libunwind_vars_are_propagated(self) -> None:
        self.plant_libunwind()
        env = setenvs(build(self.root, dict(self.base_env)))
        # LIBRARY_PATH is the one that was missing: build.rs would pass and the
        # LINK would then fail. Assert all three, not just the two obvious ones.
        for var in ("PKG_CONFIG_PATH", "LIBRARY_PATH", "LD_LIBRARY_PATH"):
            self.assertIn(var, env, f"{var} must reach the unit")
        self.assertEqual(env["PKG_CONFIG_PATH"], str(self.lu / "pkgconfig"))
        self.assertEqual(env["LIBRARY_PATH"], str(self.lu))
        self.assertEqual(env["LD_LIBRARY_PATH"], str(self.lu))

    def test_existing_caller_values_are_preserved_not_clobbered(self) -> None:
        self.plant_libunwind()
        env = setenvs(
            build(
                self.root,
                {**self.base_env, "LIBRARY_PATH": "/opt/x", "LD_LIBRARY_PATH": "/opt/y"},
            )
        )
        # Repo path first (it must win), caller's entries retained after it.
        self.assertEqual(env["LIBRARY_PATH"], f"{self.lu}:/opt/x")
        self.assertEqual(env["LD_LIBRARY_PATH"], f"{self.lu}:/opt/y")

    def test_repo_path_is_not_duplicated_when_already_present(self) -> None:
        self.plant_libunwind()
        env = setenvs(build(self.root, {**self.base_env, "LIBRARY_PATH": str(self.lu)}))
        self.assertEqual(env["LIBRARY_PATH"], str(self.lu))

    # -- NEGATIVE: absent libunwind must not fabricate anything --------------

    def test_absent_libunwind_sets_none_of_the_three(self) -> None:
        # No .pc planted. The unit must run exactly as before and let the build
        # fail loudly -- silently substituting a path would be worse than the bug.
        env = setenvs(build(self.root, dict(self.base_env)))
        for var in ("PKG_CONFIG_PATH", "LIBRARY_PATH", "LD_LIBRARY_PATH"):
            self.assertNotIn(var, env, f"{var} must not be invented when libunwind is absent")

    # -- the pre-existing contract must survive ------------------------------

    def test_home_and_path_still_propagate(self) -> None:
        self.plant_libunwind()
        env = setenvs(build(self.root, dict(self.base_env)))
        self.assertEqual(env["HOME"], "/home/agent")
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["CI_HUB_VALIDATE_PRODUCER"], "systemd-user-v1")

    def test_missing_home_or_path_is_refused(self) -> None:
        for missing in ("HOME", "PATH"):
            env = dict(self.base_env)
            del env[missing]
            with self.assertRaises(ValueError):
                build(self.root, env)


if __name__ == "__main__":
    unittest.main()
