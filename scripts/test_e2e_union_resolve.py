#!/usr/bin/env python3
"""Regression tests for scripts/e2e-union-resolve.py.

The defect these pin (rrnewton/hermit#1807): the resolver filtered every
base-existing key out of its comparison, so a PR that MODIFIED an existing row
-- rather than adding one -- had its change silently discarded while the drivers
still reported the success status UNIONED. Both `scripts/e2e-union-rebase.sh`
and `ci-hub/landing/union-rebase.sh` shell out to this resolver, so the whole
e2e-manifest ratchet bucket could be union-rebased into a no-op with no signal.

Each managed format is bracketed on BOTH sides: the qualifying case must be
preserved (the fix is not inert) and the violating case must be refused.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RESOLVER = Path(__file__).resolve().parent / "e2e-union-resolve.py"

TOML_REL = "tests/e2e/manifests/c-programs.toml"
JSON_REL = "tests/e2e/manifests/inventory/test-files.json"
TSV_REL = "tests/backend-parity/matrix.tsv"


def toml_doc(*blocks):
    return "".join(
        '[[test]]\nid = "%s"\nbackends_enabled = [%s]\n\n' % (bid, enabled)
        for bid, enabled in blocks
    )


def json_doc(*rows):
    return json.dumps({"files": [{"path": p, "owner": o} for p, o in rows]}, indent=2)


def tsv_doc(*rows):
    return "test_name\tbackend\n" + "".join("%s\t%s\n" % r for r in rows)


class ResolverCase(unittest.TestCase):
    def run_resolver(self, rel, base, ours, theirs):
        """Return (exit_code, output_text_or_None)."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
                (d / name).write_text(text)
            out = d / "out"
            proc = subprocess.run(
                [sys.executable, str(RESOLVER), rel,
                 str(d / "base"), str(d / "ours"), str(d / "theirs"), str(out)],
                capture_output=True, text=True,
            )
            return proc.returncode, (out.read_text() if out.exists() else None)


class TomlUnion(ResolverCase):
    def test_pr_addition_is_unioned(self):
        """POSITIVE CONTROL: a pure addition still unions, so the fix is not inert."""
        base = toml_doc(("alpha", '"ptrace"'))
        rc, out = self.run_resolver(
            TOML_REL, base, base, base + toml_doc(("gamma", '"ptrace"')))
        self.assertEqual(rc, 0)
        self.assertIn('id = "gamma"', out)
        self.assertIn('id = "alpha"', out)

    def test_pr_modification_survives_when_target_untouched(self):
        """#1807: the PR's ratchet must NOT be silently dropped."""
        base = toml_doc(("alpha", '"ptrace"'))
        theirs = toml_doc(("alpha", '"ptrace", "liteinst"'))
        rc, out = self.run_resolver(TOML_REL, base, base, theirs)
        self.assertEqual(rc, 0)
        self.assertIn('backends_enabled = ["ptrace", "liteinst"]', out)

    def test_both_sides_modified_is_refused(self):
        """#1807: the documented exit 3 must actually fire, not silently pick a side."""
        base = toml_doc(("alpha", '"ptrace"'))
        ours = toml_doc(("alpha", '"ptrace", "sabre"'))
        theirs = toml_doc(("alpha", '"ptrace", "liteinst"'))
        rc, _ = self.run_resolver(TOML_REL, base, ours, theirs)
        self.assertEqual(rc, 3)

    def test_pr_modification_of_row_deleted_on_target_is_refused(self):
        base = toml_doc(("alpha", '"ptrace"'), ("beta", '"ptrace"'))
        ours = toml_doc(("beta", '"ptrace"'))
        theirs = toml_doc(("alpha", '"ptrace", "liteinst"'), ("beta", '"ptrace"'))
        rc, _ = self.run_resolver(TOML_REL, base, ours, theirs)
        self.assertEqual(rc, 3)

    def test_converged_modification_is_not_a_conflict(self):
        base = toml_doc(("alpha", '"ptrace"'))
        moved = toml_doc(("alpha", '"ptrace", "liteinst"'))
        rc, out = self.run_resolver(TOML_REL, base, moved, moved)
        self.assertEqual(rc, 0)
        self.assertIn('backends_enabled = ["ptrace", "liteinst"]', out)

    def test_target_only_change_is_preserved(self):
        base = toml_doc(("alpha", '"ptrace"'))
        ours = toml_doc(("alpha", '"ptrace", "sabre"'))
        rc, out = self.run_resolver(TOML_REL, base, ours, base)
        self.assertEqual(rc, 0)
        self.assertIn('backends_enabled = ["ptrace", "sabre"]', out)

    def test_pr_deletion_never_drops_a_row(self):
        """The union never removes a row on its own authority."""
        base = toml_doc(("alpha", '"ptrace"'), ("beta", '"ptrace"'))
        theirs = toml_doc(("alpha", '"ptrace"'))
        rc, out = self.run_resolver(TOML_REL, base, base, theirs)
        self.assertEqual(rc, 0)
        self.assertIn('id = "beta"', out)

    def test_both_sides_added_the_same_id_differently_is_refused(self):
        """The FIRST safety property in the docstring, and it had no test.

        A mutant deleting merge_keyed's add-side conflict branch left the suite
        green while silently discarding the PR's row -- the exact #1807 shape.
        """
        base = toml_doc(("alpha", '"ptrace"'))
        ours = base + toml_doc(("gamma", '"ptrace", "sabre"'))
        theirs = base + toml_doc(("gamma", '"ptrace", "liteinst"'))
        rc, _ = self.run_resolver(TOML_REL, base, ours, theirs)
        self.assertEqual(rc, 3)

    def test_both_sides_added_the_same_id_identically_is_accepted(self):
        base = toml_doc(("alpha", '"ptrace"'))
        both = base + toml_doc(("gamma", '"ptrace"'))
        rc, out = self.run_resolver(TOML_REL, base, both, both)
        self.assertEqual(rc, 0)
        self.assertIn('id = "gamma"', out)

    def test_absent_and_empty_sides_are_both_treated_as_absent(self):
        """The drivers materialize a missing stage as an EMPTY file."""
        theirs = toml_doc(("alpha", '"ptrace"'))
        rc, out = self.run_resolver(TOML_REL, "", "", theirs)
        self.assertEqual(rc, 0)
        self.assertIn('id = "alpha"', out)


class JsonUnion(ResolverCase):
    def test_pr_addition_is_unioned(self):
        base = json_doc(("a.c", "x"))
        rc, out = self.run_resolver(
            JSON_REL, base, base, json_doc(("a.c", "x"), ("b.c", "y")))
        self.assertEqual(rc, 0)
        self.assertEqual({r["path"] for r in json.loads(out)["files"]}, {"a.c", "b.c"})

    def test_pr_modification_survives_when_target_untouched(self):
        base = json_doc(("a.c", "x"))
        rc, out = self.run_resolver(JSON_REL, base, base, json_doc(("a.c", "z")))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["files"][0]["owner"], "z")

    def test_both_sides_modified_is_refused(self):
        rc, _ = self.run_resolver(
            JSON_REL, json_doc(("a.c", "x")), json_doc(("a.c", "y")), json_doc(("a.c", "z")))
        self.assertEqual(rc, 3)

    def test_both_sides_added_the_same_path_differently_is_refused(self):
        base = json_doc(("a.c", "x"))
        rc, _ = self.run_resolver(
            JSON_REL, base, json_doc(("a.c", "x"), ("b.c", "y")),
            json_doc(("a.c", "x"), ("b.c", "z")))
        self.assertEqual(rc, 3)

    def test_pr_bump_of_a_top_level_field_survives(self):
        """inventory carries `schema`, which ci/test_harness.sh hard-asserts."""
        def doc(schema):
            return json.dumps({"schema": schema, "files": [{"path": "a.c", "owner": "x"}]})

        rc, out = self.run_resolver(JSON_REL, doc(2), doc(2), doc(3))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["schema"], 3)

    def test_both_sides_bumping_a_top_level_field_is_refused(self):
        def doc(schema):
            return json.dumps({"schema": schema, "files": [{"path": "a.c", "owner": "x"}]})

        rc, _ = self.run_resolver(JSON_REL, doc(2), doc(3), doc(4))
        self.assertEqual(rc, 3)

    def test_empty_side_does_not_crash_the_json_parser(self):
        rc, out = self.run_resolver(JSON_REL, "", "", json_doc(("a.c", "x")))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["files"][0]["path"], "a.c")


class TsvUnion(ResolverCase):
    def test_pr_addition_is_unioned(self):
        base = tsv_doc(("alpha", "ptrace"))
        rc, out = self.run_resolver(
            TSV_REL, base, base, tsv_doc(("alpha", "ptrace"), ("gamma", "ptrace")))
        self.assertEqual(rc, 0)
        self.assertIn("gamma", out)

    def test_pr_modification_survives_when_target_untouched(self):
        base = tsv_doc(("alpha", "ptrace"))
        rc, out = self.run_resolver(TSV_REL, base, base, tsv_doc(("alpha", "liteinst")))
        self.assertEqual(rc, 0)
        self.assertIn("alpha\tliteinst", out)

    def test_both_sides_modified_is_refused(self):
        rc, _ = self.run_resolver(
            TSV_REL, tsv_doc(("alpha", "ptrace")),
            tsv_doc(("alpha", "sabre")), tsv_doc(("alpha", "liteinst")))
        self.assertEqual(rc, 3)

    def test_both_sides_added_the_same_row_differently_is_refused(self):
        base = tsv_doc(("alpha", "ptrace"))
        rc, _ = self.run_resolver(
            TSV_REL, base, tsv_doc(("alpha", "ptrace"), ("gamma", "sabre")),
            tsv_doc(("alpha", "ptrace"), ("gamma", "liteinst")))
        self.assertEqual(rc, 3)

    def test_empty_side_does_not_crash_the_tsv_path(self):
        """matrix.tsv is absent from hermit main, so every side is an empty file."""
        rc, out = self.run_resolver(TSV_REL, "", "", tsv_doc(("alpha", "ptrace")))
        self.assertEqual(rc, 0)
        self.assertIn("alpha\tptrace", out)


class UnmanagedFile(ResolverCase):
    def test_unmanaged_path_is_refused(self):
        rc, _ = self.run_resolver("src/main.rs", "a", "b", "c")
        self.assertEqual(rc, 4)


if __name__ == "__main__":
    unittest.main()
