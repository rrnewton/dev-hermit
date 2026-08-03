#!/usr/bin/env python3
"""Decision-table tests for the flaky-failure attribution classifier.

The classifier (`attribute`) is a pure function of an `Evidence` object, so the
whole three-cause decision procedure is tested here without hermit or a loaded
host. Each test encodes one of the owner's real examples so a regression that
re-blurs the three causes is caught mechanically.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import attribution as A


def host(**kw: object) -> A.HostConditions:
    base = dict(load1=1.0, nproc=316, concurrent_procs=2, cpu_pressure_avg10=0.0)
    base.update(kw)
    return A.HostConditions(**base)  # type: ignore[arg-type]


def loaded_host() -> A.HostConditions:
    # ~470 concurrent hermit procs on a 316-core box, high load -- the infra case.
    return host(load1=600.0, concurrent_procs=470, cpu_pressure_avg10=80.0)


class ShapeTest(unittest.TestCase):
    def test_timeout_is_hang(self) -> None:
        self.assertEqual(
            A.classify_shape(exit_code=124, timed_out=True, text=""), A.SHAPE_HANG
        )

    def test_panic_is_crash(self) -> None:
        self.assertEqual(
            A.classify_shape(exit_code=101, timed_out=False,
                             text="thread 'main' panicked at src/x.rs"),
            A.SHAPE_CRASH,
        )

    def test_fatal_signal_exit_is_crash(self) -> None:
        self.assertEqual(
            A.classify_shape(exit_code=139, timed_out=False, text=""), A.SHAPE_CRASH
        )

    def test_verify_mismatch_is_mismatch(self) -> None:
        self.assertEqual(
            A.classify_shape(exit_code=1, timed_out=False,
                             text="run was nondeterministic; runs diverged"),
            A.SHAPE_MISMATCH,
        )

    def test_build_token_is_harness(self) -> None:
        self.assertEqual(
            A.classify_shape(exit_code=1, timed_out=False, text="BUILD_FAIL"),
            A.SHAPE_HARNESS,
        )

    def test_plain_nonzero_is_nonzero(self) -> None:
        self.assertEqual(
            A.classify_shape(exit_code=2, timed_out=False, text="assertion values"),
            A.SHAPE_NONZERO,
        )

    def test_clean_is_pass(self) -> None:
        self.assertEqual(
            A.classify_shape(exit_code=0, timed_out=False, text="ok"), A.SHAPE_PASS
        )


class HostPressureTest(unittest.TestCase):
    def test_quiet_host_no_pressure(self) -> None:
        under, reasons = A.host_under_pressure(host())
        self.assertFalse(under)
        self.assertEqual(reasons, [])

    def test_stampede_is_pressure(self) -> None:
        under, reasons = A.host_under_pressure(loaded_host())
        self.assertTrue(under)
        self.assertTrue(any("concurrent" in r for r in reasons))
        self.assertTrue(any("load1" in r for r in reasons))

    def test_missing_host_is_not_pressure(self) -> None:
        self.assertEqual(A.host_under_pressure(None), (False, []))


class AttributionTest(unittest.TestCase):
    # --- INFRASTRUCTURE: the reverie-wedge / ~470-proc case -------------------
    def test_hang_under_load_clean_at_low_load_is_infrastructure(self) -> None:
        ev = A.Evidence(
            shape=A.SHAPE_HANG,
            host=loaded_host(),
            low_load=A.LowLoadControl(runs=10, failures=0),
            timed_out=True,
        )
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.INFRASTRUCTURE)
        self.assertEqual(result.confidence, "high")
        self.assertIn("Do NOT change product code", result.next_step)

    def test_hang_without_control_is_indeterminate_names_the_test(self) -> None:
        ev = A.Evidence(shape=A.SHAPE_HANG, host=loaded_host(), timed_out=True)
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.INDETERMINATE)
        # The value of INDETERMINATE is that it prescribes the decisive probe.
        self.assertIn("low load", result.next_step.lower())

    # --- HERMIT_NONDETERMINISM: the vfork-reap race ---------------------------
    def test_schedule_divergence_is_hermit_even_under_load(self) -> None:
        # A load-dependent HERMIT race and an infra hang BOTH need load; only the
        # product bug produces a localizable schedule (COMMIT) divergence.
        ev = A.Evidence(
            shape=A.SHAPE_MISMATCH,
            host=loaded_host(),
            divergence=A.Divergence("commit", first_line="COMMIT turn 5, dettid 3"),
        )
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.HERMIT_NONDETERMINISM)
        self.assertEqual(result.confidence, "high")

    def test_hang_even_at_low_load_is_not_infra(self) -> None:
        ev = A.Evidence(
            shape=A.SHAPE_HANG,
            host=host(),
            low_load=A.LowLoadControl(runs=10, failures=4),
            timed_out=True,
        )
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.HERMIT_NONDETERMINISM)

    def test_deterministic_crash_at_low_load_is_hermit(self) -> None:
        ev = A.Evidence(
            shape=A.SHAPE_CRASH,
            host=host(),
            low_load=A.LowLoadControl(runs=5, failures=5),
        )
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.HERMIT_NONDETERMINISM)

    # --- ENVIRONMENT: the /sys/module/refcnt read -----------------------------
    def test_detlog_host_value_divergence_is_environment(self) -> None:
        ev = A.Evidence(
            shape=A.SHAPE_MISMATCH,
            host=host(),
            divergence=A.Divergence(
                "detlog",
                first_line="DETLOG read /sys/module/nf_conntrack/refcnt = 7",
                host_value_shaped=True,
            ),
        )
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.ENVIRONMENT)
        self.assertEqual(result.confidence, "high")

    def test_external_read_on_mismatch_leans_environment(self) -> None:
        ev = A.Evidence(
            shape=A.SHAPE_MISMATCH,
            host=host(),
            external_reads=["inbound openat /sys/module/x/refcnt"],
        )
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.ENVIRONMENT)

    def test_detlog_divergence_without_host_shape_is_hermit(self) -> None:
        ev = A.Evidence(
            shape=A.SHAPE_MISMATCH,
            host=host(),
            divergence=A.Divergence("detlog", first_line="DETLOG value 41 vs 42"),
        )
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.HERMIT_NONDETERMINISM)
        self.assertEqual(result.confidence, "medium")

    # --- HARNESS_ERROR --------------------------------------------------------
    def test_build_failure_is_harness_error(self) -> None:
        ev = A.Evidence(shape=A.SHAPE_HARNESS, note="BUILD_FAIL")
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.HARNESS_ERROR)

    # --- INDETERMINATE always prescribes a next probe -------------------------
    def test_bare_mismatch_is_indeterminate_prescribes_log_diff(self) -> None:
        ev = A.Evidence(shape=A.SHAPE_MISMATCH, host=host())
        result = A.attribute(ev)
        self.assertEqual(result.verdict, A.INDETERMINATE)
        self.assertIn("log-diff", result.next_step)

    def test_every_verdict_has_a_next_step(self) -> None:
        for verdict in A.ALL_VERDICTS:
            self.assertTrue(A.NEXT_STEP[verdict].strip())


class ExternalReadScanTest(unittest.TestCase):
    def test_scan_finds_sysfs_and_time(self) -> None:
        text = (
            "DETLOG inbound openat /sys/module/foo/refcnt\n"
            "DETLOG inbound clock_gettime CLOCK_MONOTONIC\n"
            "DETLOG finish read = Ok(4)\n"
        )
        hits = A.scan_external_reads(text)
        self.assertEqual(len(hits), 2)

    def test_scan_ignores_proc_self_maps(self) -> None:
        # /proc/self/maps is hermit's own deterministic bookkeeping, not a
        # varying host read.
        hits = A.scan_external_reads("DETLOG openat /proc/self/maps\n")
        self.assertEqual(hits, [])


class CaptureRunTest(unittest.TestCase):
    def test_failure_is_preserved_success_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ok = A.capture_run(["true"], label="ok", bundle_root=root)
            self.assertFalse(ok.failed)
            self.assertIsNone(ok.bundle_dir)

            bad = A.capture_run(["false"], label="bad", bundle_root=root)
            self.assertTrue(bad.failed)
            self.assertIsNotNone(bad.bundle_dir)
            bundle = Path(bad.bundle_dir)  # type: ignore[arg-type]
            self.assertTrue((bundle / "meta.json").is_file())
            self.assertTrue((bundle / "stdout").is_file())
            self.assertTrue((bundle / "stderr").is_file())

    def test_timeout_is_captured_as_hang(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            res = A.capture_run(
                ["sleep", "5"], label="slow", timeout_s=0.2, bundle_root=root
            )
            self.assertTrue(res.timed_out)
            self.assertEqual(res.shape, A.SHAPE_HANG)
            self.assertTrue(res.failed)

    def test_captured_bundle_round_trips_into_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            res = A.capture_run(
                ["sleep", "5"], label="hang", timeout_s=0.2, bundle_root=root
            )
            ev = A._evidence_from_bundle(Path(res.bundle_dir))  # type: ignore[arg-type]
            self.assertEqual(ev.shape, A.SHAPE_HANG)
            result = A.attribute(ev)
            # A bare hang with no control is honestly INDETERMINATE.
            self.assertEqual(result.verdict, A.INDETERMINATE)

    def test_missing_binary_is_not_a_crash_of_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            res = A.capture_run(
                ["definitely-not-a-real-binary-xyz"],
                label="missing",
                bundle_root=Path(directory),
            )
            self.assertTrue(res.failed)
            self.assertEqual(res.exit_code, 127)


def run_as_selftest() -> int:
    """Entry point for `attribution.py selftest`."""
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = unittest.TextTestRunner(stream=buffer, verbosity=2).run(suite)
    print(buffer.getvalue())
    passed = result.wasSuccessful()
    print(f"attribution selftest: {'PASS' if passed else 'FAIL'} "
          f"({result.testsRun} tests)")
    return 0 if passed else 1


if __name__ == "__main__":
    unittest.main()
