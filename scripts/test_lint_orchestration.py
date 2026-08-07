#!/usr/bin/env python3
"""The `make lint` target must run EVERY gate, not stop at the first failure.

WHY THIS EXISTS. Make aborts a recipe at the first nonzero line. With gates
written as bare recipe lines, `make lint` reported only its earliest failure and
silently skipped everything after it. Measured 2026-08-07 on origin/main:
rustfmt failed on six unformatted `scripts/*.rs`, so shellcheck never ran and
the 133 discovered `scripts/test_*.py` tests never ran either -- a planted
failing test appeared ZERO times in the output. Tests that are wired but never
executed are worse than absent, because the wiring reads as coverage.

These are static assertions on the recipe. That is deliberate: actually running
`make lint` takes minutes and depends on network, submodules, and a hermit
checkout, so a functional test here would be slow and flaky. The functional
proof is the mutation bracket recorded on the task -- planted failure surfaced
under the new recipe (134 tests, 2 failures, nonzero exit), invisible under the
old one (0 occurrences, 0 "Ran N tests" lines).
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"

# Every gate the lint target is responsible for. If a gate is added to the
# recipe without being added here the coverage test below still passes, but a
# gate REMOVED from the recipe fails it -- which is the direction that matters.
# Gates run through one of two helpers: `gate` (plain) or `counted` (also
# asserts a nonzero input count and prints it). Both are valid; a gate that
# appears under NEITHER is missing from the recipe, which is what this checks.
EXPECTED_GATES = (
    "rustfmt",
    "shellcheck",
    "py-compile",
    "py-unittest",
    "error-proxies",
    "gitmodules",
    "agent-utils-pin",
    "primary-fresh",
    "codex-setup",
    "claude-md-size",
    "portability",
    "harness-help",
    "compat-envelope",
)


def lint_recipe() -> str:
    text = MAKEFILE.read_text()
    start = text.index("\nlint:")
    # The recipe ends at the next line that starts in column 0 and is not blank.
    rest = text[start + 1 :]
    lines = rest.split("\n")
    body = [lines[0]]
    for line in lines[1:]:
        if line and not line[0].isspace():
            break
        body.append(line)
    return "\n".join(body)


class LintOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.recipe = lint_recipe()

    def test_every_expected_gate_is_present(self) -> None:
        for gate in EXPECTED_GATES:
            self.assertRegex(
                self.recipe,
                rf"(?:gate|counted)\s+{re.escape(gate)}\s",
                f"lint gate '{gate}' is missing from the recipe",
            )

    def test_the_python_suite_is_a_gate_and_discovers_tests(self) -> None:
        # The regression was specifically that this never ran.
        self.assertIn("python3 -m unittest discover -s scripts", self.recipe)
        self.assertRegex(self.recipe, r"(?:gate|counted)\s+py-unittest\s")

    def test_failures_accumulate_instead_of_aborting(self) -> None:
        # A `failures` accumulator plus a single terminal exit is what keeps a
        # failing early gate from hiding every later one.
        self.assertIn("failures=", self.recipe)
        self.assertIn('failures="$$failures $$name"', self.recipe)
        self.assertRegex(self.recipe, r'if \[ -n "\$\$failures" \]')

    def test_the_target_still_fails_when_any_gate_fails(self) -> None:
        # Continuing past a failure must not turn lint green.
        #
        # Asserting `"exit 1" in recipe` is NOT enough and this test used to do
        # exactly that: the `command -v ... || { ...; exit 1; }` dependency
        # guards already contain the string, so deleting the accumulator's own
        # `exit 1` left the assertion passing while `make lint` reported every
        # failing gate and then exited ZERO. Mutation testing caught it. Bind the
        # exit to the failure branch instead of to the file.
        self.assertIn("lint: FAILED gates:", self.recipe)
        start = self.recipe.index('if [ -n "$$failures" ]')
        branch = self.recipe[start : self.recipe.index("fi;", start)]
        self.assertIn(
            "exit 1",
            branch,
            "the failure branch does not exit nonzero, so lint would report "
            "failing gates and still succeed",
        )

    def test_no_gate_runs_as_a_bare_aborting_recipe_line(self) -> None:
        # Any gate command left as its own recipe line reintroduces the abort.
        offenders = []
        for line in self.recipe.split("\n"):
            stripped = line.strip()
            if not stripped.startswith(("rustfmt ", "shellcheck ", "python3 -m ")):
                continue
            if "gate " in line or line.lstrip().startswith("@#"):
                continue
            offenders.append(stripped)
        self.assertEqual(
            offenders, [], f"these gates would abort the recipe: {offenders}"
        )

    def test_every_failing_gate_is_named_not_just_the_first(self) -> None:
        self.assertIn('printf \'lint gate FAILED   : %s (rc=%s)\\n\'', self.recipe)


if __name__ == "__main__":
    unittest.main()


class CountedGateTests(unittest.TestCase):
    """The countable suites must refuse to pass with zero inputs.

    All four tools happen to fail closed on empty input today (measured
    2026-08-07: rustfmt rc=1, shellcheck rc=2, py_compile rc=1, unittest rc=5
    "NO TESTS RAN"). That is a property of the tools, not of this target -- one
    `shopt -s nullglob` and an empty run would start reporting OK. `counted`
    binds it in the recipe so the guarantee survives that change.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.recipe = lint_recipe()

    def test_the_countable_suites_use_the_counted_helper(self) -> None:
        for gate in ("rustfmt", "shellcheck", "py-compile", "py-unittest"):
            self.assertRegex(
                self.recipe,
                rf"counted\s+{re.escape(gate)}\s",
                f"'{gate}' can pass without proving it checked anything",
            )

    def test_zero_inputs_is_a_failure_not_a_pass(self) -> None:
        self.assertIn("refusing to pass vacuously", self.recipe)
        # The refusal must record the gate as failed, not merely print.
        idx = self.recipe.index("refusing to pass vacuously")
        window = self.recipe[idx : idx + 220]
        self.assertIn('failures="$$failures $$name"', window)

    def test_the_count_is_reported_beside_the_verdict(self) -> None:
        # An operator must be able to see 139 tests vs 3 without a second run.
        #
        # Asserting `"%s %s checked" in recipe` is NOT enough, and this test used
        # to do exactly that: the FAILED branch also carries the count, so
        # stripping it from the OK branch left the assertion passing while a
        # passing gate went back to printing no evidence of work. Mutation
        # testing caught it -- the same vacuous-substring trap as the `exit 1`
        # assertion above. Bind the count to the OK branch specifically.
        ok = [
            line for line in self.recipe.split("\n")
            if "lint gate OK" in line and "printf" in line
        ]
        self.assertTrue(ok, "no OK-verdict printf found in the recipe")
        self.assertTrue(
            any("checked" in line for line in ok),
            f"no OK verdict reports a count; found: {ok}",
        )
