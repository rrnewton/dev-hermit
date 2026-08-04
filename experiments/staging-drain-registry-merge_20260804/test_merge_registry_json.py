#!/usr/bin/env python3
"""Unit tests for merge_registry_json — the keyed-array 3-way registry merge driver.

Run: python3 test_merge_registry_json.py   (no network, no cargo, no validate)

Every test mirrors the REAL shape of the file it targets so the driver is exercised against the
actual registry schemas, not toys. The headline case (test_disjoint_additions_ride_clean) is the
whole point of the staging drain: two PRs that append DIFFERENT entries must merge with exit 0.
"""

import unittest

from merge_registry_json import Schema, merge_keyed_array, MergeConflict, schema_for, _SCHEMAS


TEST_FILES = _SCHEMAS["tests/e2e/manifests/inventory/test-files.json"]
PORTABLE = _SCHEMAS["ci/dag/portable.json"]


def _files_doc(entries):
    return {"files": list(entries), "schema": 1}


def _f(path, why="w", runner="r", disposition="support-data"):
    return {"disposition": disposition, "path": path, "runner": runner, "why": why}


class TestFilesInventory(unittest.TestCase):
    def test_disjoint_additions_ride_clean(self):
        base = _files_doc([_f("a"), _f("b")])
        ours = _files_doc([_f("a"), _f("b"), _f("c")])      # PR1 adds c
        theirs = _files_doc([_f("a"), _f("b"), _f("d")])    # PR2 adds d
        merged = merge_keyed_array(base, ours, theirs, TEST_FILES)
        paths = [e["path"] for e in merged["files"]]
        self.assertEqual(paths, ["a", "b", "c", "d"])       # base order, then added sorted
        self.assertEqual(merged["schema"], 1)

    def test_same_new_entry_both_sides_is_clean(self):
        base = _files_doc([_f("a")])
        ours = _files_doc([_f("a"), _f("x")])
        theirs = _files_doc([_f("a"), _f("x")])             # identical add
        merged = merge_keyed_array(base, ours, theirs, TEST_FILES)
        self.assertEqual([e["path"] for e in merged["files"]], ["a", "x"])

    def test_add_add_same_key_different_value_conflicts(self):
        base = _files_doc([_f("a")])
        ours = _files_doc([_f("a"), _f("x", why="ours")])
        theirs = _files_doc([_f("a"), _f("x", why="theirs")])
        with self.assertRaises(MergeConflict) as cm:
            merge_keyed_array(base, ours, theirs, TEST_FILES)
        self.assertIn("files['x']", str(cm.exception))
        # best-effort partial keeps ours and stays valid
        self.assertEqual([e["path"] for e in cm.exception.partial["files"]], ["a", "x"])

    def test_one_side_edits_other_unchanged_takes_edit(self):
        base = _files_doc([_f("a", why="old")])
        ours = _files_doc([_f("a", why="old")])             # unchanged
        theirs = _files_doc([_f("a", why="new")])           # edited
        merged = merge_keyed_array(base, ours, theirs, TEST_FILES)
        self.assertEqual(merged["files"][0]["why"], "new")

    def test_delete_one_side_other_unchanged_deletes(self):
        base = _files_doc([_f("a"), _f("b")])
        ours = _files_doc([_f("a"), _f("b")])
        theirs = _files_doc([_f("a")])                      # PR removed b
        merged = merge_keyed_array(base, ours, theirs, TEST_FILES)
        self.assertEqual([e["path"] for e in merged["files"]], ["a"])

    def test_delete_modify_conflicts(self):
        base = _files_doc([_f("a", why="old")])
        ours = _files_doc([])                               # ours deletes a
        theirs = _files_doc([_f("a", why="changed")])       # theirs edits a
        with self.assertRaises(MergeConflict):
            merge_keyed_array(base, ours, theirs, TEST_FILES)


def _dag(steps, **caps):
    doc = {
        "resource_caps": {"hermit_guest": 1, "manifest_guest": 4},
        "mem_cap_factor": 1.25,
        "default_step_timeout": 600,
        "steps": list(steps),
    }
    doc.update(caps)
    return doc


def _step(group, job, timeout=120):
    return {"group": group, "job": job, "cmd": f"./{job}.sh", "timeout": timeout}


class TestPortableDag(unittest.TestCase):
    def test_disjoint_step_additions_ride_clean(self):
        base = _dag([_step("check", "a")])
        ours = _dag([_step("check", "a"), _step("check", "b")])   # PR1 adds check/b
        theirs = _dag([_step("check", "a"), _step("test", "c")])  # PR2 adds test/c
        merged = merge_keyed_array(base, ours, theirs, PORTABLE)
        keys = [(s["group"], s["job"]) for s in merged["steps"]]
        self.assertIn(("check", "b"), keys)
        self.assertIn(("test", "c"), keys)
        self.assertEqual(keys[0], ("check", "a"))                # base first

    def test_scalar_cap_one_side_changed_takes_it(self):
        base = _dag([_step("check", "a")], mem_cap_floor_bytes=8)
        ours = _dag([_step("check", "a")], mem_cap_floor_bytes=8)      # unchanged
        theirs = _dag([_step("check", "a")], mem_cap_floor_bytes=16)   # tuned up
        merged = merge_keyed_array(base, ours, theirs, PORTABLE)
        self.assertEqual(merged["mem_cap_floor_bytes"], 16)

    def test_scalar_cap_both_changed_differently_conflicts(self):
        base = _dag([_step("check", "a")], mem_cap_floor_bytes=8)
        ours = _dag([_step("check", "a")], mem_cap_floor_bytes=16)
        theirs = _dag([_step("check", "a")], mem_cap_floor_bytes=32)
        with self.assertRaises(MergeConflict) as cm:
            merge_keyed_array(base, ours, theirs, PORTABLE)
        self.assertIn("mem_cap_floor_bytes", str(cm.exception))

    def test_same_step_timeout_tuned_one_side(self):
        base = _dag([_step("check", "a", timeout=120)])
        ours = _dag([_step("check", "a", timeout=120)])
        theirs = _dag([_step("check", "a", timeout=240)])
        merged = merge_keyed_array(base, ours, theirs, PORTABLE)
        self.assertEqual(merged["steps"][0]["timeout"], 240)


class TestDeterminism(unittest.TestCase):
    def test_merge_is_direction_independent(self):
        base = _files_doc([_f("a")])
        p1 = _files_doc([_f("a"), _f("m"), _f("z")])
        p2 = _files_doc([_f("a"), _f("g")])
        forward = merge_keyed_array(base, p1, p2, TEST_FILES)
        backward = merge_keyed_array(base, p2, p1, TEST_FILES)
        self.assertEqual(
            [e["path"] for e in forward["files"]],
            [e["path"] for e in backward["files"]],
        )


class TestSchemaRouting(unittest.TestCase):
    def test_paths_route_to_the_right_schema(self):
        self.assertIs(schema_for("tests/e2e/manifests/inventory/test-files.json"), TEST_FILES)
        self.assertIs(schema_for("ci/dag/portable.json"), PORTABLE)
        self.assertIsNotNone(schema_for("ci/expected-e2e-plan.json"))
        self.assertIsNone(schema_for("run_matrix.py"))
        self.assertIsNone(schema_for("tests/backend-parity/matrix.tsv"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
