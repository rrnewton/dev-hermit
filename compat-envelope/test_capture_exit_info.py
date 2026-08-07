#!/usr/bin/env python3
"""Both halves of the bracket for the exit-code and INFO-log producers.

The model (`capture_parity`) is trusted because the shipped population contains
96 FALSE stdout-parity values in `scorecard.csv` — it demonstrably fires. A new
producer earns the same trust only by showing both halves:

  NEGATIVE  a planted wrong value is DETECTED
  POSITIVE  the unmodified population still PASSES

The positive half is not a formality. A producer that returned False for
everything would pass every negative test and be useless, and a producer that
returned None for everything would pass by never claiming anything. Both are
checked here.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SPEC = Path(__file__).resolve().parent / "capture_exit_info.py"
_s = importlib.util.spec_from_file_location("cei", SPEC)
cei = importlib.util.module_from_spec(_s)
_s.loader.exec_module(cei)


def par(a, b):
    """The comparison both producers use: equality, unknown-preserving."""
    return None if (a is None or b is None) else (a == b)


class ExitCodeParity(unittest.TestCase):
    def test_positive_matching_exit_codes_pass(self):
        self.assertIs(par(0, 0), True)
        self.assertIs(par(3, 3), True)

    def test_planted_wrong_exit_code_is_detected(self):
        self.assertIs(par(0, 1), False)
        self.assertIs(par(0, 3), False)

    def test_unobserved_side_is_unknown_not_a_mismatch(self):
        """None must NOT read as False: an unmeasured cell is not a failure."""
        self.assertIsNone(par(None, 0))
        self.assertIsNone(par(0, None))


class InfoLogParity(unittest.TestCase):
    BASE = "INFO detcore: DETLOG [syscall] inbound syscall: brk(NULL) = ?"

    def test_positive_identical_logs_pass(self):
        a = cei._digest(cei.normalise_info_log(self.BASE).encode())
        b = cei._digest(cei.normalise_info_log(self.BASE).encode())
        self.assertIs(par(a, b), True)

    def test_planted_single_byte_change_is_detected(self):
        a = cei._digest(cei.normalise_info_log(self.BASE).encode())
        b = cei._digest(cei.normalise_info_log(self.BASE.replace("brk", "brl")).encode())
        self.assertIs(par(a, b), False)

    def test_planted_extra_record_is_detected(self):
        a = cei._digest(cei.normalise_info_log(self.BASE).encode())
        b = cei._digest(cei.normalise_info_log(self.BASE + "\nINFO extra").encode())
        self.assertIs(par(a, b), False)

    def test_wall_clock_prefix_alone_does_NOT_trip_it(self):
        """The one thing normalisation removes. Without this the producer would
        report every cell as divergent and the negatives above would be vacuous."""
        a = cei._digest(cei.normalise_info_log("2026-08-07T01:02:03.400000Z  " + self.BASE).encode())
        b = cei._digest(cei.normalise_info_log("2026-08-07T09:09:09.900000Z  " + self.BASE).encode())
        self.assertIs(par(a, b), True)

    def test_an_ADDRESS_change_IS_detected(self):
        """Addresses are deliberately NOT normalised: allocation-order change is
        real divergence, and hiding it is the fake-green move."""
        a = cei._digest(cei.normalise_info_log("INFO x = 0x403000").encode())
        b = cei._digest(cei.normalise_info_log("INFO x = 0x20e9ea000").encode())
        self.assertIs(par(a, b), False)


class HarnessFailureIsNotData(unittest.TestCase):
    """The defect this producer nearly shipped with.

    hermit panicking under a /tmp --log-file exits 1 and writes nothing. Reading
    that 1 as a guest exit code made BOTH sides agree at "1v1" and produced
    exit_code_parity=True — a green manufactured from two broken runs.
    """

    def test_panic_yields_unknown_not_an_exit_code(self):
        class FakeOut:
            returncode = 1
            stdout = b""
            stderr = (b"thread 'main' panicked at global_opts.rs:61:50:\n"
                      b"Failed to open log file: Os { code: 2, kind: NotFound }\n")
        import subprocess
        real = subprocess.run
        subprocess.run = lambda *a, **k: FakeOut()
        try:
            r = cei.run_cell("/nonexistent/hermit", ["/bin/true"], "ptrace")
        finally:
            subprocess.run = real
        self.assertIsNone(r["exit_code"], "a hermit panic must not become a guest exit code")
        self.assertIsNone(r["info_digest"])
        self.assertIn("did not start", r["reason"])
        # and therefore the two broken sides cannot agree
        self.assertIsNone(par(r["exit_code"], r["exit_code"]))


if __name__ == "__main__":
    unittest.main()
