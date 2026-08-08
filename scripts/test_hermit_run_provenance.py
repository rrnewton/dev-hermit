#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("hermit-run-provenance.py")
SPEC = importlib.util.spec_from_file_location("hermit_run_provenance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
provenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provenance
SPEC.loader.exec_module(provenance)


BOOT = "11111111-2222-3333-4444-555555555555"
AGENT_SCOPE = (
    "/user.slice/user-212630.slice/user@212630.service/"
    "3pai_sandbox.slice/run-p100-iINV.scope"
)
BOX_SCOPE = "/user.slice/user-212630.slice/user@212630.service/safe.slice/box-1.scope"


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.proc = root / "proc"
        self.cgroups = root / "cgroup"
        self.receipts = root / "receipts"
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/random/boot_id").write_text(BOOT + "\n")
        self.cgroups.mkdir()

    def cgroup(self, path: str) -> None:
        (self.cgroups / path.lstrip("/")).mkdir(parents=True, exist_ok=True)

    def process(self, pid: int, start_ticks: int, cgroup: str) -> None:
        self.cgroup(cgroup)
        base = self.proc / str(pid)
        base.mkdir(parents=True, exist_ok=True)
        # fields[0] is proc field 3; fields[19] is proc field 22 starttime.
        fields = ["S", "1"] + ["0"] * 17 + [str(start_ticks)]
        (base / "stat").write_text(f"{pid} (fixture process) " + " ".join(fields) + "\n")
        (base / "cgroup").write_text(f"0::{cgroup}\n")

    def remove_process(self, pid: int) -> None:
        shutil.rmtree(self.proc / str(pid))


class ProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))

    def record_live(self) -> Path:
        self.fixture.process(100, 1_000, AGENT_SCOPE)
        self.fixture.process(200, 2_000, BOX_SCOPE)
        return provenance.record_live_domain(
            pid=200,
            owner_pid=100,
            agent="hermit-w21",
            slot="w21",
            task="owned-run-test",
            invocation_id="inv-1",
            receipt_dir=self.fixture.receipts,
            proc_root=self.fixture.proc,
            cgroup_root=self.fixture.cgroups,
        )

    def query(self, pid: int) -> provenance.QueryResult:
        return provenance.query(
            pid=pid,
            receipt_dir=self.fixture.receipts,
            proc_root=self.fixture.proc,
            cgroup_root=self.fixture.cgroups,
        )

    def test_live_owner_is_attributed_and_never_an_orphan(self) -> None:
        path = self.record_live()
        result = self.query(200)
        self.assertEqual(result.classification, provenance.LIVE_OWNER)
        self.assertEqual(result.reason, "exact-owner-live")
        self.assertEqual(result.receipt.agent, "hermit-w21")
        self.assertEqual(result.receipt.slot, "w21")
        self.assertEqual(result.receipt.task, "owned-run-test")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_exact_owner_absence_makes_the_domain_a_proven_orphan(self) -> None:
        self.record_live()
        self.fixture.remove_process(100)
        result = self.query(200)
        self.assertEqual(result.classification, provenance.PROVEN_ORPHAN)
        self.assertEqual(result.reason, "exact-owner-absent")

    def test_owner_pid_reuse_fails_closed(self) -> None:
        self.record_live()
        self.fixture.remove_process(100)
        self.fixture.process(100, 1_001, AGENT_SCOPE)
        result = self.query(200)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertEqual(result.reason, "owner-identity-mismatch")

    def test_wrong_process_cgroup_has_no_receipt(self) -> None:
        self.record_live()
        other = "/user.slice/other.scope"
        self.fixture.cgroup(other)
        (self.fixture.proc / "200/cgroup").write_text(f"0::{other}\n")
        result = self.query(200)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertEqual(result.reason, "no-matching-receipt")

    def test_duplicate_domain_receipts_fail_closed(self) -> None:
        path = self.record_live()
        duplicate = self.fixture.receipts / ("f" * 64 + ".json")
        duplicate.write_text(path.read_text())
        duplicate.chmod(0o600)
        result = self.query(200)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertEqual(result.reason, "duplicate-domain-receipts")

    def test_any_malformed_receipt_fails_closed(self) -> None:
        self.record_live()
        broken = self.fixture.receipts / "broken.json"
        broken.write_text("not-json\n")
        broken.chmod(0o600)
        result = self.query(200)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertTrue(result.reason.startswith("malformed-receipt:broken.json:"))

    def test_nonprivate_receipt_fails_closed(self) -> None:
        path = self.record_live()
        path.chmod(0o644)
        result = self.query(200)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertIn("not private", result.reason)

    def test_live_record_refuses_the_shared_agent_cgroup(self) -> None:
        self.fixture.process(100, 1_000, AGENT_SCOPE)
        self.fixture.process(200, 2_000, AGENT_SCOPE)
        with self.assertRaisesRegex(provenance.ProvenanceError, "unique child domain"):
            provenance.record_live_domain(
                pid=200,
                owner_pid=100,
                agent="hermit-w21",
                slot="w21",
                task="unsafe-shared-domain",
                invocation_id=None,
                receipt_dir=self.fixture.receipts,
                proc_root=self.fixture.proc,
                cgroup_root=self.fixture.cgroups,
            )

    def test_existing_attestation_binds_only_the_explicit_identity_set(self) -> None:
        orphan_scope = AGENT_SCOPE.replace("run-p100-", "run-p999-")
        self.fixture.process(300, 3_000, orphan_scope)
        self.fixture.process(301, 3_001, orphan_scope)
        provenance.attest_existing_orphan_domain(
            pids=[300, 301],
            agent="scorecard-baseline-cli",
            slot="scorecard",
            task="capture-immediate-pre-tightening-scorecard-baseline",
            invocation_id="claude-invocation-1",
            attested_by="hermit-coord",
            confirmation=provenance.ATTEST_CONFIRMATION,
            receipt_dir=self.fixture.receipts,
            proc_root=self.fixture.proc,
            cgroup_root=self.fixture.cgroups,
        )
        self.assertEqual(self.query(300).classification, provenance.PROVEN_ORPHAN)
        self.assertEqual(self.query(301).classification, provenance.PROVEN_ORPHAN)

        self.fixture.process(302, 3_002, orphan_scope)
        result = self.query(302)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertEqual(result.reason, "process-not-in-attested-member-set")

        self.fixture.remove_process(300)
        self.fixture.process(300, 9_999, orphan_scope)
        result = self.query(300)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertEqual(result.reason, "process-not-in-attested-member-set")

    def test_existing_attestation_refuses_a_live_embedded_launcher(self) -> None:
        self.fixture.process(100, 1_000, AGENT_SCOPE)
        self.fixture.process(300, 3_000, AGENT_SCOPE)
        with self.assertRaisesRegex(provenance.ProvenanceError, "still present"):
            provenance.attest_existing_orphan_domain(
                pids=[300],
                agent="hermit-w21",
                slot="w21",
                task="live-run",
                invocation_id=None,
                attested_by="hermit-coord",
                confirmation=provenance.ATTEST_CONFIRMATION,
                receipt_dir=self.fixture.receipts,
                proc_root=self.fixture.proc,
                cgroup_root=self.fixture.cgroups,
            )

    def test_existing_attestation_requires_the_exact_acknowledgement(self) -> None:
        orphan_scope = AGENT_SCOPE.replace("run-p100-", "run-p999-")
        self.fixture.process(300, 3_000, orphan_scope)
        with self.assertRaisesRegex(provenance.ProvenanceError, "requires confirmation"):
            provenance.attest_existing_orphan_domain(
                pids=[300],
                agent="scorecard-baseline-cli",
                slot="scorecard",
                task="capture-immediate-pre-tightening-scorecard-baseline",
                invocation_id=None,
                attested_by="hermit-coord",
                confirmation="yes",
                receipt_dir=self.fixture.receipts,
                proc_root=self.fixture.proc,
                cgroup_root=self.fixture.cgroups,
            )

    def test_receipt_round_trip_is_strict(self) -> None:
        path = self.record_live()
        payload = json.loads(path.read_text())
        payload["unexpected"] = True
        path.write_text(json.dumps(payload))
        result = self.query(200)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertIn("unexpected fields", result.reason)

    def test_tampered_receipt_digest_fails_closed(self) -> None:
        path = self.record_live()
        payload = json.loads(path.read_text())
        payload["task"] = "different-task"
        path.write_text(json.dumps(payload))
        result = self.query(200)
        self.assertEqual(result.classification, provenance.UNPROVEN)
        self.assertIn("digest does not match", result.reason)

    def test_cli_record_then_query_round_trip(self) -> None:
        self.fixture.process(100, 1_000, AGENT_SCOPE)
        self.fixture.process(200, 2_000, BOX_SCOPE)
        common = [
            sys.executable,
            str(SCRIPT),
            "--receipt-dir",
            str(self.fixture.receipts),
            "--proc-root",
            str(self.fixture.proc),
            "--cgroup-root",
            str(self.fixture.cgroups),
        ]
        recorded = subprocess.run(
            common
            + [
                "record-live",
                "--pid",
                "200",
                "--owner-pid",
                "100",
                "--agent",
                "hermit-w21",
                "--slot",
                "w21",
                "--task",
                "owned-run-test",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        queried = subprocess.run(
            common + ["query", "--pid", "200"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(queried.returncode, 0, queried.stderr)
        payload = json.loads(queried.stdout)
        self.assertEqual(payload["classification"], provenance.LIVE_OWNER)
        self.assertEqual(payload["receipt"]["task"], "owned-run-test")


if __name__ == "__main__":
    unittest.main()
