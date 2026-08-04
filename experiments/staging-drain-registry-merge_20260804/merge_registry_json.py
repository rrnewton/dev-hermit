#!/usr/bin/env python3
"""Format-aware git merge driver for Hermit's hand-maintained keyed-array registry JSONs.

Purpose (staging-branch drain, task `staging-branch-merge-all-prs-test-once`): when many open
PRs are merged into one staging branch, the append-style registry files collide on a small set
of shared files even though the changes are semantically disjoint. For the files that are
*generated* (``ci/expected-e2e-plan.json``), the correct fix is to REGENERATE from the merged
sources, not to merge the output. This tool handles the OTHER class: hand-maintained JSON files
whose payload is a keyed array of value-objects, where two PRs each append/modify distinct keys.

It performs a real 3-way merge keyed on a stable identity per entry:

  * ``tests/e2e/manifests/inventory/test-files.json`` — array ``files``, key = ``path``
  * ``ci/dag/portable.json``                          — array ``steps``, key = (``group``,``job``)
  * ``ci/expected-e2e-plan.json``                     — array ``cells``, key = full tuple
    (fallback only; prefer regenerating this generated file — see the recipe doc)

Contract (git merge driver): ``merge_registry_json.py %O %A %B %P``
  %O = common ancestor (base), %A = current/ours (also the OUTPUT path), %B = other/theirs,
  %P = the pathname in the worktree (used to pick the schema). Exit 0 = clean merge written to
  %A; exit 1 = genuine conflict (a value changed incompatibly on both sides) — %A is left with a
  best-effort merge and the conflicting keys are reported on stderr, so the drain records it as a
  REAL finding (per the owner: "a PR that cannot merge is a real finding, handled separately").

Determinism: surviving base entries keep base order; newly added keys are appended in sorted key
order regardless of which side added them, so the output is independent of merge direction.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

# --- schema table: path suffix -> (array field, key function) ------------------------------------

KeyFn = Callable[[dict], Any]


def _key_path(entry: dict) -> Any:
    return entry.get("path")


def _key_group_job(entry: dict) -> Any:
    return (entry.get("group"), entry.get("job"))


def _key_cell(entry: dict) -> Any:
    return (
        entry.get("backend"),
        entry.get("category"),
        entry.get("lane"),
        entry.get("mode"),
        entry.get("test"),
    )


@dataclass(frozen=True)
class Schema:
    array_field: str
    key_fn: KeyFn


_SCHEMAS: dict[str, Schema] = {
    "tests/e2e/manifests/inventory/test-files.json": Schema("files", _key_path),
    "ci/dag/portable.json": Schema("steps", _key_group_job),
    "ci/expected-e2e-plan.json": Schema("cells", _key_cell),
}


def schema_for(path: str) -> Optional[Schema]:
    for suffix, schema in _SCHEMAS.items():
        if path == suffix or path.endswith("/" + suffix) or path.endswith(suffix):
            return schema
    return None


# --- core 3-way merge --------------------------------------------------------------------------


class MergeConflict(Exception):
    def __init__(self, conflicts: list[str], partial: dict) -> None:
        super().__init__("; ".join(conflicts))
        self.conflicts = conflicts
        self.partial = partial


def _three_way_value(o: Any, a: Any, b: Any, present_o: bool, present_a: bool, present_b: bool):
    """Return (resolved_or_None, kept: bool, conflict: bool).

    kept=False means the entry is deleted in the merged result.
    """
    # Normalise "absent" so equality comparisons are meaningful.
    if present_a == present_b and a == b:
        # both sides identical (both absent, or both present-equal)
        return (a if present_a else None, present_a, False)
    if present_a == present_o and a == o:
        # ours unchanged from base -> take theirs (including their deletion)
        return (b if present_b else None, present_b, False)
    if present_b == present_o and b == o:
        # theirs unchanged from base -> take ours (including our deletion)
        return (a if present_a else None, present_a, False)
    # both sides diverged from base and from each other -> real conflict
    return (None, False, True)


def merge_keyed_array(base: dict, ours: dict, theirs: dict, schema: Schema) -> dict:
    field = schema.array_field
    key_fn = schema.key_fn

    def index(doc: dict) -> dict:
        out: dict[Any, dict] = {}
        for entry in doc.get(field, []) or []:
            out[key_fn(entry)] = entry
        return out

    o_idx, a_idx, b_idx = index(base), index(ours), index(theirs)
    all_keys = set(o_idx) | set(a_idx) | set(b_idx)

    resolved: dict[Any, dict] = {}
    conflicts: list[str] = []
    for k in all_keys:
        o, a, b = o_idx.get(k), a_idx.get(k), b_idx.get(k)
        value, kept, conflict = _three_way_value(
            o, a, b, k in o_idx, k in a_idx, k in b_idx
        )
        if conflict:
            conflicts.append(f"{field}[{k!r}]")
            # best-effort: prefer ours so the file stays valid JSON
            if k in a_idx:
                resolved[k] = a
            elif k in b_idx:
                resolved[k] = b
            continue
        if kept:
            resolved[k] = value

    # deterministic order: base order for survivors, then added keys sorted
    ordered: list[dict] = []
    emitted: set[Any] = set()
    for entry in base.get(field, []) or []:
        k = key_fn(entry)
        if k in resolved and k not in emitted:
            ordered.append(resolved[k])
            emitted.add(k)
    for k in sorted((k for k in resolved if k not in emitted), key=lambda x: json.dumps(x, sort_keys=True)):
        ordered.append(resolved[k])
        emitted.add(k)

    # merge non-array top-level fields with a scalar 3-way
    merged: dict = {}
    top_keys = set(base) | set(ours) | set(theirs)
    for tk in sorted(top_keys):
        if tk == field:
            merged[tk] = ordered
            continue
        o, a, b = base.get(tk), ours.get(tk), theirs.get(tk)
        po, pa, pb = tk in base, tk in ours, tk in theirs
        value, kept, conflict = _three_way_value(o, a, b, po, pa, pb)
        if conflict:
            conflicts.append(f"top-level field {tk!r}")
            merged[tk] = a if pa else b
        elif kept:
            merged[tk] = value

    if conflicts:
        raise MergeConflict(conflicts, merged)
    return merged


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path: str, doc: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        sys.stderr.write("usage: merge_registry_json.py %O %A %B %P\n")
        return 2
    base_path, ours_path, theirs_path = argv[0], argv[1], argv[2]
    worktree_path = argv[3]
    schema = schema_for(worktree_path)
    if schema is None:
        sys.stderr.write(f"merge_registry_json: no schema for {worktree_path}; declining\n")
        return 2  # let git fall back to the default driver
    base, ours, theirs = _load(base_path), _load(ours_path), _load(theirs_path)
    try:
        merged = merge_keyed_array(base, ours, theirs, schema)
    except MergeConflict as exc:
        _dump(ours_path, exc.partial)
        sys.stderr.write(
            f"merge_registry_json: CONFLICT in {worktree_path}: {', '.join(exc.conflicts)}\n"
        )
        return 1
    _dump(ours_path, merged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
