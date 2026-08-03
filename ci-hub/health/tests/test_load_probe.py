#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import load_probe


class LoadProbeTest(unittest.TestCase):
    def test_cpu_utilization_excludes_iowait(self) -> None:
        before = load_probe.CpuCounters(total=1_000, idle=500, iowait=100)
        after = load_probe.CpuCounters(total=2_000, idle=1_100, iowait=200)
        measured = load_probe.cpu_measurement(before, after, cpus=100)
        self.assertAlmostEqual(measured.executing_percent, 30.0)
        self.assertAlmostEqual(measured.idle_percent, 60.0)
        self.assertAlmostEqual(measured.iowait_percent, 10.0)
        self.assertAlmostEqual(measured.executing_cores, 30.0)

    def test_state_breakdown_separates_zombies(self) -> None:
        samples = {
            1: load_probe.ProcessSample(0, "R", "one"),
            2: load_probe.ProcessSample(0, "S", "two"),
            3: load_probe.ProcessSample(0, "Z", "three"),
            4: load_probe.ProcessSample(0, "D", "four"),
            5: load_probe.ProcessSample(0, "Z", "five"),
        }
        self.assertEqual(
            load_probe.process_states(samples),
            {"R": 1, "S": 1, "D": 1, "Z": 2},
        )

    def test_high_load_does_not_override_measured_cpu_verdict(self) -> None:
        cpu = load_probe.CpuMeasurement(31.45, 63.86, 4.69, 99.38, 316)
        memory = load_probe.MemoryMeasurement(100, 75, 75.0, 0, 0.0)
        verdict = load_probe.decide(
            cpu,
            memory,
            max_executing_percent=50.0,
            min_memory_available_percent=10.0,
        )
        self.assertTrue(verdict.suitable)
        self.assertIn("31.45% <= policy 50.00%", verdict.reasons[0])

    def test_busy_cpu_is_not_suitable(self) -> None:
        cpu = load_probe.CpuMeasurement(50.01, 49.99, 0.0, 2.0, 4)
        memory = load_probe.MemoryMeasurement(100, 75, 75.0, 0, 0.0)
        verdict = load_probe.decide(
            cpu,
            memory,
            max_executing_percent=50.0,
            min_memory_available_percent=10.0,
        )
        self.assertFalse(verdict.suitable)

    def test_process_stat_parser_handles_spaces(self) -> None:
        fields = ["S", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]
        sample = load_probe.parse_process_stat("42 (name with spaces) " + " ".join(fields))
        self.assertEqual(sample.name, "name with spaces")
        self.assertEqual(sample.state, "S")
        self.assertEqual(sample.ticks, 23)

    def test_malformed_cpu_input_fails_loudly(self) -> None:
        with self.assertRaisesRegex(load_probe.ProbeUnavailable, "did not advance"):
            load_probe.cpu_measurement(
                load_probe.CpuCounters(10, 5, 1),
                load_probe.CpuCounters(10, 5, 1),
                cpus=1,
            )

    def test_cli_unavailable_is_loud_and_nonzero(self) -> None:
        error = io.StringIO()
        with mock.patch.object(
            load_probe,
            "run",
            side_effect=load_probe.ProbeUnavailable("PID data hidden"),
        ), contextlib.redirect_stderr(error):
            code = load_probe.main([])
        self.assertEqual(code, 2)
        self.assertIn("LOAD PROBE UNAVAILABLE: PID data hidden", error.getvalue())
        self.assertIn("COST ACTUAL", error.getvalue())


if __name__ == "__main__":
    unittest.main()
