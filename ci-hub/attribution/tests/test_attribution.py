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


class KillSignatureTest(unittest.TestCase):
    """The cpu/wall KILL SIGNATURE: livelock (retry-futile) vs contention.

    Every ratio below is a REAL measurement from the local step_profiles store
    (`ci-hub/history/query.py kill-taxonomy`), not an invented number, so these
    tests pin the classifier to observed production shapes.
    """

    def test_detcore_misc_livelock_is_a_real_red(self) -> None:
        # MEASURED: test.detcore_misc wall_timeout, 600.013s wall / 607.785s cpu.
        ev = A.Evidence(shape=A.SHAPE_HANG, host=loaded_host(), timed_out=True,
                        wall_s=600.013, cpu_s=607.785)
        res = A.attribute(ev)
        self.assertEqual(res.verdict, A.HERMIT_NONDETERMINISM)
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.signals["kill_verdict"], "livelock")
        # The whole point for the ledger: retry cannot clear this, so the red is REAL.
        self.assertIs(res.signals["retry_futile"], True)

    def test_liteinst_strict_livelock(self) -> None:
        # MEASURED: test.liteinst_strict wall_timeout, 900.013s wall / 901.205s cpu.
        ev = A.Evidence(shape=A.SHAPE_HANG, timed_out=True,
                        wall_s=900.013, cpu_s=901.205)
        self.assertEqual(A.attribute(ev).verdict, A.HERMIT_NONDETERMINISM)

    def test_oom_is_NOT_read_as_livelock(self) -> None:
        """The bracket that keeps the classifier honest.

        MEASURED: test.strict_compat OOM, 64.462s wall / 8221.18s cpu -> ratio
        127.5.  A parallel build hitting a memory ceiling has a huge ratio.  If
        OOM were not excluded BEFORE the ratio test, every OOM in the store
        (17 of 27 kills) would be mislabelled a livelock and condemn its commit.
        """
        ev = A.Evidence(shape=A.SHAPE_HANG, timed_out=True, oom=True,
                        wall_s=64.462, cpu_s=8221.18)
        res = A.attribute(ev)
        self.assertEqual(res.signals["kill_verdict"], "oom")
        self.assertNotEqual(res.signals["kill_verdict"], "livelock")
        self.assertEqual(res.verdict, A.INFRASTRUCTURE)
        self.assertNotEqual(res.verdict, A.HERMIT_NONDETERMINISM)
        # OOM is not established as retry-futile either way -- must not be True.
        self.assertIsNone(res.signals["retry_futile"])

    def test_wait_bound_hang_does_NOT_decide_infra(self) -> None:
        """The ASYMMETRY: a low ratio rules out a livelock but picks no cause.

        A starved process and a futex/deadlock wedge both sit at cpu ~= 0 and
        are opposite causes, so contention must fall through to the low-load
        control rather than declaring INFRASTRUCTURE.
        """
        ev = A.Evidence(shape=A.SHAPE_HANG, host=loaded_host(), timed_out=True,
                        wall_s=600.0, cpu_s=1.2)
        res = A.attribute(ev)
        self.assertEqual(res.verdict, A.INDETERMINATE)
        self.assertIn("WAITING, not spinning", " ".join(res.reasons))

    def test_wait_bound_plus_control_still_reaches_infrastructure(self) -> None:
        # With the decisive control, contention resolves as before.
        ev = A.Evidence(shape=A.SHAPE_HANG, host=loaded_host(), timed_out=True,
                        wall_s=600.0, cpu_s=1.2,
                        low_load=A.LowLoadControl(runs=5, failures=0))
        self.assertEqual(A.attribute(ev).verdict, A.INFRASTRUCTURE)

    def test_ratio_is_ignored_when_the_run_was_not_killed(self) -> None:
        """Gate 1: a high ratio on a fast failure is just CPU-bound work."""
        ev = A.Evidence(shape=A.SHAPE_HANG, timed_out=False, wall_s=3.0, cpu_s=2.9)
        res = A.attribute(ev)
        self.assertEqual(res.verdict, A.INDETERMINATE)
        self.assertNotIn("kill_verdict", res.signals)

    def test_schema1_bundle_without_cpu_degrades_not_guesses(self) -> None:
        """An old bundle carrying no cpu_s must stay INDETERMINATE, never get a
        fabricated ratio."""
        ev = A.Evidence(shape=A.SHAPE_HANG, timed_out=True, wall_s=600.0, cpu_s=None)
        res = A.attribute(ev)
        self.assertEqual(res.verdict, A.INDETERMINATE)
        self.assertNotIn("kill_verdict", res.signals)

    def test_capture_run_records_cpu_seconds(self) -> None:
        """PRODUCER bracket: the bundle must actually carry cpu_s, else the
        classifier above can never fire in production."""
        import json as _json
        with tempfile.TemporaryDirectory() as directory:
            res = A.capture_run(
                ["sh", "-c", "i=0; while [ $i -lt 200000 ]; do i=$((i+1)); done; exit 7"],
                label="spin", bundle_root=Path(directory),
            )
            self.assertIsNotNone(res.cpu_s)
            self.assertGreater(res.cpu_s, 0.0)   # it really burned CPU
            meta = _json.loads((Path(res.bundle_dir) / "meta.json").read_text())
            self.assertIn("cpu_s", meta)
            self.assertGreater(meta["cpu_s"], 0.0)


class KillSignatureModuleTest(unittest.TestCase):
    """Unit tests for the shared table itself."""

    def test_ratio_never_divides_by_a_missing_denominator(self) -> None:
        self.assertIsNone(A.ks.cpu_wall_ratio(10.0, None))
        self.assertIsNone(A.ks.cpu_wall_ratio(10.0, 0.0))
        self.assertIsNone(A.ks.cpu_wall_ratio(None, 10.0))

    def test_no_kill_means_unknown(self) -> None:
        self.assertEqual(A.ks.classify_kill(None, 1.0), A.ks.UNKNOWN)

    def test_threshold_boundaries(self) -> None:
        k = A.ks.KILL_WALL_TIMEOUT
        self.assertEqual(A.ks.classify_kill(k, 0.80), A.ks.LIVELOCK)   # inclusive
        self.assertEqual(A.ks.classify_kill(k, 0.79), A.ks.AMBIGUOUS)
        self.assertEqual(A.ks.classify_kill(k, 0.30), A.ks.AMBIGUOUS)
        self.assertEqual(A.ks.classify_kill(k, 0.29), A.ks.CONTENTION)

    def test_retry_futility_axis(self) -> None:
        self.assertIs(A.ks.retry_futile(A.ks.LIVELOCK), True)
        self.assertIs(A.ks.retry_futile(A.ks.CONTENTION), False)
        # Unknown must never read as a confirmed real failure.
        self.assertIsNone(A.ks.retry_futile(A.ks.UNKNOWN))
        self.assertIsNone(A.ks.retry_futile(A.ks.AMBIGUOUS))


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
