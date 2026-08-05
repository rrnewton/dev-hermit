from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stop_units  # noqa: E402


def completed(command: list[str], rc: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, rc, stdout, stderr)


class FakeSystemctl:
    def __init__(self) -> None:
        self.active = {"validate-a.service", "validate-b.scope"}
        self.stops: list[str] = []

    def __call__(self, command: list[str], **_kwargs: object):
        if "list-units" in command:
            return completed(
                command,
                stdout=(
                    "validate-b.scope loaded active running B\n"
                    "validate-a.service loaded active running A\n"
                ),
            )
        if "is-active" in command:
            unit = command[-1]
            return completed(command, rc=0 if unit in self.active else 3)
        if "stop" in command:
            unit = command[-1]
            self.stops.append(unit)
            self.active.discard(unit)
            return completed(command)
        raise AssertionError(command)


class StopUnitsTest(unittest.TestCase):
    def test_all_stops_each_enumerated_unit_and_verifies_inactive(self) -> None:
        fake = FakeSystemctl()

        self.assertEqual(0, stop_units.main(["--all"], run=fake))
        self.assertEqual(["validate-a.service", "validate-b.scope"], fake.stops)
        self.assertEqual(set(), fake.active)

    def test_targeted_stop_never_accepts_an_unrelated_unit(self) -> None:
        fake = FakeSystemctl()

        self.assertEqual(
            2,
            stop_units.main(["--unit", "github-runner.service"], run=fake),
        )
        self.assertEqual([], fake.stops)

    def test_inactive_unit_is_reported_without_a_stop(self) -> None:
        fake = FakeSystemctl()

        self.assertEqual(
            0,
            stop_units.main(["--unit", "validate-old.service"], run=fake),
        )
        self.assertEqual([], fake.stops)


if __name__ == "__main__":
    unittest.main()
