#!/usr/bin/env python3
"""Extract validate failure evidence without deciding the verdict.

The shared Rust ``validate_status`` reader is the sole verdict authority. This
producer helper only binds a raw ledger row to observable facts: failed DAG
substeps, membership in the measured-flake registry, and whether this exact
commit/cell is a solo ``-j 4`` reproduction of an earlier rerun-required red.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys
from typing import Mapping


CI_HUB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_HUB / "remediation"))
from nonzero_result import per_node_counts  # noqa: E402


DEFAULT_REGISTRY = Path(__file__).with_name("flaky-cells.json")


def flaky_cells(path: Path) -> set[str]:
    value = json.loads(path.read_text())
    rows = value.get("cells") if isinstance(value, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("flake registry cells must be a list")
    names = set()
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("cell"):
            continue
        name = str(row["cell"])
        names.add(name if "." in name else f"test.{name}")
    return names


def ledger_rows(path: Path | None) -> list[dict[str, object]]:
    if path is None or not path.is_file():
        return []
    rows = []
    with path.open(errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def failed_substeps(log_text: str) -> list[str]:
    return sorted(
        name
        for name, details in per_node_counts(log_text).items()
        if details.get("terminal") == "fail"
    )


# safe-ci-dag-runner tags every node ``<group>.<job>`` (see hermit/ci/dag/*.json).
# The GROUP already names WHICH sub-step failed, so a triager never has to read
# the aggregate lane name (`portable CI DAG manifest`) that today covers three
# unrelated causes. Groups whose failure is a BUILD/preparation step rather than a
# product test — a corrupt/missing artifact here is an INFRASTRUCTURE fault, not a
# code defect. (portable.json groups: check, setup, build, e2e, test, lint, doc.)
_BUILD_GROUPS = frozenset({"build", "setup"})

# Log signatures that make a failure an INFRASTRUCTURE fault (a corrupt/missing
# build artifact or a shared-cache fault), NOT a code defect. Each is a
# toolchain/linker/archive/cache message the PRODUCT under test cannot emit as a
# test assertion. To keep a guest that merely PRINTS these words from forging the
# class, a signature is only honored when it appears on a line attributed (by the
# ``[<node.tag>] `` stream prefix) to the SAME failing node — a test node's own
# stdout is never scanned for another node's infra signature.
_INFRA_SIGNATURES = (
    "in archive is not an object",   # corrupt static lib (e.g. libdynamorio_static.a)
    "archive has no index",          # ar/ranlib index corruption
    "malformed archive",             # truncated/garbled .a
    "file format not recognized",    # linker on a corrupt object
    "bad file descriptor while reading archive",
    "failed to verify the checksum", # cargo cache poisoning (specific, before generic)
    # CMakeCache relocation: a build dir/cache inherited from ANOTHER checkout
    # (e.g. a reflink/CoW seed) whose absolute paths no longer match this tree.
    # This is a stale-state INFRASTRUCTURE fault, not a source defect.
    "is different than the directory",  # "current CMakeCache.txt directory X is different than the directory Y"
    "does not match the source",        # CMAKE_HOME_DIRECTORY / cache dir mismatch
    "corrupted",                     # generic cargo/registry cache corruption tell
    # DynamoRIO third-party build/link fault: a stale / ABI-mismatched DynamoRIO
    # tree (reflink seed or partial rebuild) fails to link its drmemtrace op_*
    # symbols. Scoped to the `dynamorio::` namespace so a genuine PRODUCT link
    # error stays a code fault — a bare "undefined reference" is NOT a signature.
    "undefined reference to `dynamorio::",
    # Harness could not exec the target binary: GNU coreutils (`timeout`, `env`, …)
    # print "<prog>: failed to run command '<path>': No such file or directory"
    # (exit 127) when target/debug/hermit was never built or sits at a stale path.
    # An ENVIRONMENT/harness fault, not a product test assertion. Anchored to the
    # coreutils "<prog>: failed to run command" shape (leading ": ") so product
    # text merely containing "failed to run command" is less likely to match.
    ": failed to run command",
)

_NODE_PREFIX_RE = per_node_counts.__globals__["_NODE_PREFIX_RE"]

# An error-DIAGNOSTIC line, as emitted by rustc/cargo/gcc/clang/ld/collect2. Two
# shapes: a body that STARTS with ``error:`` / ``error[E0432]:`` (rustc, cargo),
# and a tool/file-prefixed ``<something>: error:`` (``collect2: error:``,
# ``ld: error:``, ``<file>:<line>: error:``). This is matched against a node's own
# stream line AFTER the ``[tag] `` prefix is stripped, so it is a FACT about the
# failing node, not a proxy — it records verbatim what the toolchain printed.
_ERROR_LINE_RE = re.compile(r"(?:^|[:\s])error(?:\[[^\]]*\])?:\s")

# Cargo's build-ORCHESTRATION envelopes: cargo prints one of these for EVERY
# build-script / compile / doc failure, then re-prints the real diagnostic the
# child emitted (under ``Caused by:`` / ``--- stderr``). The envelope names only
# the crate, never the cause, so two totally unrelated failures (a corrupt
# DynamoRIO archive vs a stale ``--locked`` Cargo.lock) both surface as
# ``error: failed to run custom build command for <crate>``. ``first_error_line``
# therefore skips these envelopes to reach the first SUBSTANTIVE error line,
# falling back to the envelope only when the node emitted nothing more specific.
_CARGO_ENVELOPES = (
    "error: failed to run custom build command for ",
    "error: could not compile ",
    "error: failed to compile ",
    "error: could not document ",
    "error: build failed",
)

# Linker-driver ENVELOPES: the exact analogue of the cargo envelopes above for the
# LINK step. ``collect2: error: ld returned 1 exit status`` (and the bare
# ``error: ld returned N exit status``) is emitted for EVERY link failure
# regardless of cause, so two totally unrelated link failures share it verbatim —
# it names no symbol and no library. ``first_error_line`` therefore treats it as an
# envelope and skips it to reach the substantive ``undefined reference`` diagnostic
# below, falling back to it only when the node emitted nothing more specific.
_LINK_ENVELOPES = (
    "collect2: error:",
    "error: ld returned ",
)

# A linker symbol-resolution diagnostic. GNU ld prints
# ``<site>: undefined reference to `<symbol>'`` with NO ``error:`` token, so
# ``_ERROR_LINE_RE`` alone never sees it — yet it is the single most interpretable
# fact a link failure carries: WHICH symbol is unresolved, hence WHICH library.
# It is recognized as a substantive error line so it wins over the generic
# collect2/ld envelopes and lets two DynamoRIO link failures with different missing
# symbols be told apart from the ledger row alone (the task's "error string is a
# fact" for the fedc81ed DynamoRIO fault). ``undefined reference to`` in the log
# stream is a toolchain diagnostic, never product test output.
_LINKER_DIAG_SUBSTR = "undefined reference to "

# Ledger rows must stay bounded; a path- or URL-bearing error line can be long.
_FIRST_ERROR_LINE_CAP = 500


def _bounded_error_line(line: str) -> str:
    if len(line) <= _FIRST_ERROR_LINE_CAP:
        return line
    return line[: _FIRST_ERROR_LINE_CAP - 1] + "…"


def _is_error_diagnostic(line: str) -> bool:
    """Whether ``line`` (already stream-prefix-stripped) is a toolchain error
    diagnostic worth surfacing: an ``error:``/``error[E…]:`` line from
    rustc/cargo/gcc/clang/ld, OR a GNU-ld ``undefined reference to `<symbol>'``
    line, which carries no ``error:`` token yet is the substantive cause of a link
    failure."""
    return bool(_ERROR_LINE_RE.search(line)) or _LINKER_DIAG_SUBSTR in line


def _is_generic_envelope(line: str) -> bool:
    """Whether ``line`` is a build-ORCHESTRATION envelope that names only the
    crate/step, never the cause — cargo's ``failed to run custom build command``
    family or the linker driver's ``collect2: error: ld returned N``. These are
    skipped so ``first_error_line`` reaches the substantive diagnostic beneath
    them, and used only as a last-resort fallback."""
    if any(line.startswith(env) for env in _CARGO_ENVELOPES):
        return True
    return any(env in line for env in _LINK_ENVELOPES)


def _first_error_line_for(log_text: str, node: str) -> str | None:
    """The first SUBSTANTIVE error-diagnostic line on ``node``'s own stream, or
    None when the node emitted no error-shaped line at all.

    Order-preserving and node-scoped: iterate the node's lines in emission order,
    skip generic build-orchestration envelopes (cargo's ``failed to run custom
    build command`` family and the linker's ``collect2: error: ld returned N``),
    and return the first remaining substantive error line verbatim
    (leading/trailing stream whitespace trimmed, length-capped). A GNU-ld
    ``undefined reference to `<symbol>'`` line counts as substantive even though it
    lacks an ``error:`` token — it names the unresolved symbol (hence the library),
    the actual fact behind a link failure. If every error line the node emitted IS
    an envelope, the first envelope is returned rather than None — the crate name
    is still a fact, just a weaker one. Returns None only when the node emitted no
    error line: a triager then falls back to the sub-step/fault classification,
    never to a fabricated string. This is the interpretable "error string is a
    fact" field — it lets two failures sharing one gate name AND one failing node
    (``build.runtime_release`` dying on a truncated-object DynamoRIO link failure
    vs a stale ``--locked`` Cargo.lock) be told apart from the ledger row alone,
    without reopening the log."""
    fallback: str | None = None
    for body in _node_lines(log_text, node):
        line = body.strip()
        if not _is_error_diagnostic(line):
            continue
        if fallback is None:
            fallback = line
        if not _is_generic_envelope(line):
            return _bounded_error_line(line)
    return _bounded_error_line(fallback) if fallback is not None else None


def _node_lines(log_text: str, node: str) -> list[str]:
    """Lines of the merged DAG stream attributed to ``node`` by its ``[tag] ``
    prefix. Attribution is by the EXACT node tag, so one node's stdout can never
    be read as another node's evidence."""
    out = []
    for raw in log_text.splitlines():
        m = _NODE_PREFIX_RE.match(raw)
        if m and m.group(1) == node:
            out.append(m.group(2))
    return out


def _infra_signature_for(log_text: str, node: str) -> str | None:
    """The first infrastructure signature seen on ``node``'s own lines, or None.
    Case-insensitive; returns the canonical signature string (not the raw line)."""
    hay = "\n".join(_node_lines(log_text, node)).lower()
    for sig in _INFRA_SIGNATURES:
        if sig in hay:
            return sig
    return None


def classify_failed_substeps(
    log_text: str, *, flaky_registry: set[str] | None = None
) -> list[dict[str, object]]:
    """Turn an aggregate DAG failure into a per-node, triageable verdict list.

    For every node that terminated ``✗ FAIL`` this returns one record:
    ``{"node", "group", "sub_step_class", "fault_class", "infra_signature",
    "first_error_line", "known_flaky"}`` — sorted by node. This is the read-side
    answer to
    "one-gate-name-for-three-unrelated-causes": instead of a single lane name and
    exit=1, a consumer sees WHICH node failed, whether it is a build/prep step or
    a product test, and whether the failure is an INFRASTRUCTURE fault (corrupt
    artifact / poisoned cache) or a CODE fault.

    ``fault_class`` rules (biased so a real code defect is never hidden as infra):
      * ``infrastructure`` — an infra signature (corrupt archive, poisoned cargo
        cache, …) appears on THIS node's own stream lines. This wins regardless of
        group: a build node dying on a corrupt ``.a`` and a test node whose prep
        hit a poisoned cache are both infra.
      * ``code`` — otherwise. A failing build/setup node with no infra signature
        is a genuine compile error in the change under test (still ``code``); a
        failing test/e2e/lint/doc node is a product failure.
    ``known_flaky`` is advisory only (a failing test node whose name is in the
    measured-flake registry); it never downgrades ``fault_class`` — a flake is
    still a code-domain result to be re-run, not an infra fault.
    """
    registry = flaky_registry or set()
    records: list[dict[str, object]] = []
    for node in failed_substeps(log_text):
        group = node.split(".", 1)[0] if "." in node else ""
        signature = _infra_signature_for(log_text, node)
        fault = "infrastructure" if signature else "code"
        sub_step = "dependency-build" if group in _BUILD_GROUPS else "lane-run"
        cell = node if "." in node else f"test.{node}"
        records.append(
            {
                "node": node,
                "group": group,
                "sub_step_class": sub_step,
                "fault_class": fault,
                "infra_signature": signature,
                "first_error_line": _first_error_line_for(log_text, node),
                "known_flaky": fault == "code"
                and sub_step == "lane-run"
                and cell in registry,
            }
        )
    return records


def row_failed_substeps(row: Mapping[str, object]) -> set[str]:
    result = set()
    gates = row.get("gates")
    if not isinstance(gates, list):
        return result
    for gate in gates:
        if not isinstance(gate, Mapping):
            continue
        substeps = gate.get("failed_substeps")
        if isinstance(substeps, list):
            result.update(str(value) for value in substeps if value)
    return result


def prior_requires_rerun(row: Mapping[str, object]) -> bool:
    concurrent = row.get("concurrent_validates")
    jobs = row.get("dag_jobs")
    return (
        row.get("known_flaky_failure") is True
        or isinstance(concurrent, int) and not isinstance(concurrent, bool) and concurrent > 0
        or isinstance(jobs, int) and not isinstance(jobs, bool) and jobs > 4
    )


def build_evidence(
    *,
    log_text: str,
    registry: set[str],
    prior: list[dict[str, object]],
    commit: str,
    dag_jobs: int,
    concurrent_validates: int | None,
) -> dict[str, object]:
    cells = failed_substeps(log_text)
    cell_set = set(cells)
    flaky_failed = sorted(cell for cell in cells if cell in registry)
    classes = classify_failed_substeps(log_text, flaky_registry=registry)
    # Top-level, standalone verbatim fault line (already bounded by
    # _first_error_line_for). This is the headline line the PRODUCER serializes
    # with one jq extraction (`.first_error_line`) as a peer of the other scalar
    # fields, so a red row is attributable to WHICH BUG — not just which bucket —
    # from the row alone, after the /tmp log is evicted. It is the first failing
    # node's first substantive error line (node-sorted, matching the classes
    # order); the per-node lines remain in ``failed_substep_classes`` for a
    # multi-node red. Deliberately NOT classified/parsed: a class is a lossy
    # summary and ``failed_substep_classes`` already carries it.
    first_error_line = next(
        (
            cls["first_error_line"]
            for cls in classes
            if cls.get("first_error_line")
        ),
        None,
    )
    confirmation_row: dict[str, object] | None = None
    if dag_jobs == 4 and concurrent_validates == 0 and cell_set:
        candidates = [
            row
            for row in prior
            if str(row.get("commit") or "") == commit
            and prior_requires_rerun(row)
            and not cell_set.isdisjoint(row_failed_substeps(row))
        ]
        if candidates:
            confirmation_row = max(
                candidates, key=lambda row: str(row.get("finished_at") or "")
            )
    return {
        "failed_substeps": cells,
        "failed_substep_classes": classes,
        "first_error_line": first_error_line,
        "flaky_failed_substeps": flaky_failed,
        "known_flaky_failure": bool(flaky_failed),
        "solo_rerun_confirmation": confirmation_row is not None,
        "solo_rerun_of": (
            {
                "finished_at": confirmation_row.get("finished_at"),
                "log_file": confirmation_row.get("log_file"),
            }
            if confirmation_row is not None
            else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--dag-jobs", required=True, type=int)
    parser.add_argument("--concurrent-validates", required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args(argv)
    if args.concurrent_validates == "null":
        concurrent = None
    else:
        try:
            concurrent = int(args.concurrent_validates)
        except ValueError:
            print("failure_evidence: --concurrent-validates must be integer or null", file=sys.stderr)
            return 2
    try:
        log_text = args.log.read_text(errors="replace")
        registry = flaky_cells(args.registry)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"failure_evidence: cannot load evidence: {error}", file=sys.stderr)
        return 2
    evidence = build_evidence(
        log_text=log_text,
        registry=registry,
        prior=ledger_rows(args.ledger),
        commit=args.commit,
        dag_jobs=args.dag_jobs,
        concurrent_validates=concurrent,
    )
    print(json.dumps(evidence, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
