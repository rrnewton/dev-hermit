#!/usr/bin/env python3
"""MUTATION proof for the ledger `producer` provenance column.

Task `every-ledger-writer-stamps-producer-and-the-reader-refuses-rows-without-it`.
Establishing who wrote ONE row (the schema-4 correction carrying
`corrects`/`correction_author`) previously took JSON-whitespace forensics plus a
`git log -S` across three repos. `producer` is the column that makes that a
lookup instead of an investigation.

BOTH DIRECTIONS ARE ASSERTED, and the harness is built so that neither direction
can pass vacuously:

  * The REFUSAL direction is driven against a FIXTURE predicate whose epoch is
    SET, because the shipped predicate is deliberately inert
    (`applies_from_finished_at: null`) until all four writers deploy. A test that
    only exercised the shipped predicate would prove the gate compiles, not that
    it fires.
  * The ACCEPTANCE direction is asserted for EACH of the four registered writer
    slugs, so a typo in any one slug is caught here rather than at activation --
    when it would refuse that writer's every row.
  * The GRANDFATHER direction asserts the 729 pre-existing rows still qualify,
    which is the property that keeps activation from blocking all landing.

`row_qualification` takes the predicate as a PARAMETER, so every scenario builds
its own predicate dict and nothing touches the live file or the process-wide
cache.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

CI_HUB = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CI_HUB))

import qualifying_receipt as qr  # noqa: E402

LIVE_PREDICATE = CI_HUB / "validate" / "qualifying-receipt.json"
SHA = "a" * 40

#: An instant AFTER every row in the historical ledger, used as the fixture
#: epoch. The newest live row finishes 2026-08-07; anything at/after this is
#: "written under the new rule".
EPOCH = "2026-08-08T00:00:00Z"
BEFORE_EPOCH = "2026-08-07T20:15:31Z"  # the real correction row's timestamp
AFTER_EPOCH = "2026-08-08T09:00:00Z"


def live_predicate() -> dict:
    return json.loads(LIVE_PREDICATE.read_text())


def enforcing_predicate() -> dict:
    """The live predicate with the epoch FLIPPED ON. This is the mutation."""
    pred = live_predicate()
    pred["producer"]["applies_from_finished_at"] = EPOCH
    return pred


def green_row(*, finished_at: str, producer=None, omit_producer: bool = False) -> dict:
    """A schema-5 clean full-coverage PASS -- qualifying on every other clause,
    so the ONLY thing any assertion below can turn on is provenance."""
    row = {
        "schema_version": 5,
        "commit": SHA,
        "commit_anchored": True,
        "tree_dirty": False,
        "selection_mode": "full",
        "profile": "full",
        "result": "pass",
        "failures": 0,
        "executed_tests": 427,
        "filtered_tests": 0,
        "finished_at": finished_at,
        "coverage": {
            "planned_test_nodes": 19,
            "executed_test_nodes": 19,
            "zero_executed_nodes": [],
            "absent_nodes": [],
        },
    }
    if not omit_producer:
        row["producer"] = producer
    return row


class ControlTest(unittest.TestCase):
    """Without a working baseline the other directions prove nothing."""

    def test_the_fixture_row_qualifies_on_every_non_provenance_clause(self) -> None:
        # If this ever fails, every refusal below could be a false positive
        # caused by an unrelated clause rather than by the producer gate.
        ok, reason = qr.row_qualification(
            green_row(finished_at=AFTER_EPOCH, producer="hermit-validate-sh"),
            SHA,
            enforcing_predicate(),
        )
        self.assertTrue(ok, f"control row must qualify, got: {reason}")


class RefusalDirectionTest(unittest.TestCase):
    """NEGATIVE: plant the violating row, confirm refusal, with the gate LIVE."""

    def test_a_row_without_producer_is_refused_under_enforcement(self) -> None:
        ok, reason = qr.row_qualification(
            green_row(finished_at=AFTER_EPOCH, omit_producer=True),
            SHA,
            enforcing_predicate(),
        )
        self.assertFalse(ok)
        self.assertIn("producer", reason)
        self.assertIn("missing", reason)

    def test_an_unregistered_writer_is_refused(self) -> None:
        """Presence is not the bar. An invented writer name is exactly as
        unattributable as none -- this is what stops a hand-writer from
        satisfying the column with an arbitrary string."""
        ok, reason = qr.row_qualification(
            green_row(finished_at=AFTER_EPOCH, producer="hermit-valrs"),
            SHA,
            enforcing_predicate(),
        )
        self.assertFalse(ok)
        self.assertIn("unknown-writer", reason)

    def test_a_receipt_shaped_producer_OBJECT_does_not_satisfy_the_column(self) -> None:
        """`receipt.producer` is an OBJECT (`{definition: {...}}`) binding a
        receipt to the producer DEFINITION -- a different thing one nesting
        level up (`receipt.ledger_record` holds the row). If that object were
        ever copied down onto a row, it must NOT read as a writer name."""
        ok, reason = qr.row_qualification(
            green_row(
                finished_at=AFTER_EPOCH,
                producer={"definition": {"validate.sh": "8" * 40}},
            ),
            SHA,
            enforcing_predicate(),
        )
        self.assertFalse(ok)
        self.assertIn("producer", reason)

    def test_dropping_finished_at_does_not_dodge_the_gate(self) -> None:
        """The epoch is keyed on `finished_at`, so an absent/garbage timestamp
        must fail CLOSED -- otherwise deleting one field opts a row out."""
        for ts in ("", "not-a-timestamp", "2026-08-08"):
            with self.subTest(finished_at=ts):
                row = green_row(finished_at=ts, omit_producer=True)
                self.assertTrue(qr.producer_enforced_for(row, enforcing_predicate()))
                ok, _ = qr.row_qualification(row, SHA, enforcing_predicate())
                self.assertFalse(ok)
        row = green_row(finished_at=AFTER_EPOCH, omit_producer=True)
        del row["finished_at"]
        self.assertTrue(qr.producer_enforced_for(row, enforcing_predicate()))


class AcceptanceDirectionTest(unittest.TestCase):
    """POSITIVE: the gate must not be a blanket refusal, and each registered
    writer must actually be accepted under its exact shipped slug."""

    def test_every_registered_writer_slug_is_accepted(self) -> None:
        known = live_predicate()["producer"]["known"]
        for slug in known:
            with self.subTest(writer=slug):
                ok, reason = qr.row_qualification(
                    green_row(finished_at=AFTER_EPOCH, producer=slug),
                    SHA,
                    enforcing_predicate(),
                )
                self.assertTrue(ok, f"{slug} must be accepted, got: {reason}")

    def test_each_writer_emits_a_slug_the_registry_ACTUALLY_REGISTERS(self) -> None:
        """Bind the registry to the WRITERS, not to a copy of itself.

        An earlier version of this test compared `known` against a hardcoded
        list -- which is the registry echoing itself and proves nothing. It
        passed while the registry named `hermit-validate-rs` and the real
        `scripts/validate.rs` emitted the bare `validate.rs`, i.e. while the
        predicate would have refused that writer's every row. The slug is read
        out of each writer's SOURCE here, so drift fails loudly.

        hermit/ and reverie/ are submodules: assert when materialized, skip that
        writer (named) when not, and never silently count it as checked.
        """
        root = CI_HUB.parent
        writers = {
            "ci-hub/validate/finalize_receipt.py": r'^PRODUCER\s*=\s*"([^"]+)"',
            "hermit/validate.sh": r'schema_version\\":4,\\"producer\\":\\"([^\\"]+)',
            "hermit/scripts/validate.rs": r'LEDGER_PRODUCER:\s*&str\s*=\s*"([^"]+)"',
            "reverie/validate.sh": r'\\"producer\\":\\"([^\\"]+)',
        }
        known = set(live_predicate()["producer"]["known"])
        checked, skipped = [], []
        for rel, pattern in writers.items():
            path = root / rel
            if not path.is_file():
                skipped.append(rel)
                continue
            found = re.search(pattern, path.read_text(), re.MULTILINE)
            self.assertIsNotNone(found, f"{rel}: no producer stamp found")
            slug = found.group(1)
            self.assertIn(
                slug,
                known,
                f"{rel} emits {slug!r}, which the predicate would REFUSE",
            )
            checked.append(f"{rel}={slug}")
        print(
            f"\nWRITER<->REGISTRY BINDING: checked={len(checked)}/4 "
            f"[{', '.join(checked)}]"
            + (f" skipped(not materialized)={skipped}" if skipped else "")
        )
        self.assertTrue(checked, "no writer source was reachable to check")


class GrandfatherTest(unittest.TestCase):
    """The property that makes activation safe: history keeps its authority."""

    def test_a_pre_epoch_row_without_producer_still_qualifies(self) -> None:
        ok, reason = qr.row_qualification(
            green_row(finished_at=BEFORE_EPOCH, omit_producer=True),
            SHA,
            enforcing_predicate(),
        )
        self.assertTrue(ok, f"pre-epoch history must survive, got: {reason}")

    def test_a_grandfathered_row_reports_null_not_false(self) -> None:
        """It must never CLAIM a provenance it does not carry -- the same
        discipline as coverage_satisfied on a grandfathered schema-4 receipt."""
        verdict = qr.producer_verdict(
            green_row(finished_at=BEFORE_EPOCH, omit_producer=True),
            enforcing_predicate(),
        )
        self.assertIs(verdict, qr.ProducerVerdict.GRANDFATHERED)


class ShippedPredicateIsInertTest(unittest.TestCase):
    """The shipped file must be inert, and must SAY it is inert deliberately."""

    def test_the_live_predicate_declares_the_column(self) -> None:
        # Both engines default the clause inert when absent, so the live file
        # DECLARING it is what stops that default from silently disarming the
        # real predicate.
        pred = live_predicate()
        self.assertIn("producer", pred)
        self.assertTrue(pred["producer"]["required"])

    def test_the_live_predicate_refuses_nothing_today(self) -> None:
        """Shipping inert is deliberate (three repos, staged deployment). This
        pins that intent: if someone flips the epoch without landing the
        writers, this test fails and names why."""
        pred = live_predicate()
        self.assertIsNone(
            pred["producer"]["applies_from_finished_at"],
            "flipping this epoch requires all four writers deployed first",
        )
        ok, reason = qr.row_qualification(
            green_row(finished_at=AFTER_EPOCH, omit_producer=True), SHA, pred
        )
        self.assertTrue(ok, f"shipped predicate must not refuse yet, got: {reason}")


class CountedMutationSummary(unittest.TestCase):
    """States BOTH counts, as the task requires, rather than asserting only
    that some assertion somewhere passed."""

    def test_counts_both_directions(self) -> None:
        pred = enforcing_predicate()
        refused_cases = [
            green_row(finished_at=AFTER_EPOCH, omit_producer=True),
            green_row(finished_at=AFTER_EPOCH, producer="hermit-valrs"),
            green_row(finished_at=AFTER_EPOCH, producer=""),
            green_row(finished_at=AFTER_EPOCH, producer={"definition": {}}),
        ]
        accepted_cases = [
            green_row(finished_at=AFTER_EPOCH, producer=slug)
            for slug in live_predicate()["producer"]["known"]
        ] + [green_row(finished_at=BEFORE_EPOCH, omit_producer=True)]

        refused = sum(1 for r in refused_cases if not qr.row_qualifies(r, SHA, pred))
        accepted = sum(1 for r in accepted_cases if qr.row_qualifies(r, SHA, pred))

        known_n = len(live_predicate()["producer"]["known"])
        self.assertEqual(refused, 4, "all 4 unattributable shapes must be refused")
        self.assertEqual(accepted, known_n + 1, "every registered writer + 1 grandfathered")
        print(
            f"\nPRODUCER MUTATION COUNTS: refused={refused}/4 "
            f"(no-producer, unregistered, empty, object) "
            f"accepted={accepted}/{known_n + 1} ({known_n} registered writers + 1 pre-epoch grandfathered)"
        )


class RustEngineParityTest(unittest.TestCase):
    """The Rust binary is the LANDING authority; the Python module is its twin.

    Asserting only the Python half would leave the engine that actually gates a
    merge unproven -- and a clause present in one language and absent in the
    other is precisely the cross-language drift this shared predicate exists to
    remove. Driven through the real `ci-hub validate-status`, not a unit hook.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import shutil
        import subprocess

        if shutil.which("rust-script") is None:
            raise unittest.SkipTest("rust-script unavailable")
        sys.path.insert(0, str(CI_HUB / "validate" / "tests"))
        from test_qualifying_receipt_mutation import _green_row

        cls._green_row = staticmethod(_green_row)
        cls._subprocess = subprocess

    def _ask(self, row: dict, pred: dict) -> str:
        import os
        import tempfile

        d = Path(tempfile.mkdtemp())
        (d / "pred.json").write_text(json.dumps(pred))
        (d / "l.jsonl").write_text(json.dumps(row) + "\n")
        env = dict(os.environ)
        env["QUALIFYING_RECEIPT_PREDICATE"] = str(d / "pred.json")
        proc = self._subprocess.run(
            [
                "rust-script", "--force", str(CI_HUB / "ci-hub.rs"),
                "validate-status", "--ledger", str(d / "l.jsonl"),
                "--sha", SHA, "--json",
            ],
            capture_output=True, text=True, env=env, timeout=900,
        )
        self.assertTrue(proc.stdout, f"no report: {proc.stderr[-600:]}")
        return json.loads(proc.stdout)["verdict"]

    def _row(self, producer=None, finished: str = AFTER_EPOCH) -> dict:
        row = self._green_row(SHA)
        row["finished_at"] = finished
        if producer is not None:
            row["producer"] = producer
        return row

    def test_rust_moves_in_both_directions_and_agrees_with_python(self) -> None:
        pred = enforcing_predicate()
        refused, accepted = 0, 0
        for producer in (None, "hermit-valrs"):
            row = self._row(producer=producer)
            self.assertEqual(self._ask(row, pred), "NOT-VALIDATED")
            self.assertFalse(qr.row_qualifies(row, SHA, pred), "engines must agree")
            refused += 1
        for slug in live_predicate()["producer"]["known"]:
            row = self._row(producer=slug)
            self.assertEqual(self._ask(row, pred), "VALIDATED")
            self.assertTrue(qr.row_qualifies(row, SHA, pred), "engines must agree")
            accepted += 1
        grandfathered = self._row(finished=BEFORE_EPOCH)
        self.assertEqual(self._ask(grandfathered, pred), "VALIDATED")
        accepted += 1
        self.assertEqual((refused, accepted), (2, len(live_predicate()["producer"]["known"]) + 1))
        print(
            f"\nRUST ENGINE COUNTS: refused={refused}/2 (absent, unregistered) "
            f"accepted={accepted} (every registered writer + 1 grandfathered)"
        )

    def test_rust_is_inert_under_the_shipped_predicate(self) -> None:
        self.assertEqual(self._ask(self._row(), live_predicate()), "VALIDATED")


if __name__ == "__main__":
    unittest.main()
