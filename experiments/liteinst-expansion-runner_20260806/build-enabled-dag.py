#!/usr/bin/env python3
"""Emit the LiteInst-enabled subset of an expansion sweep as a safe-ci-dag-runner DAG.

Why this exists: `compat-envelope/expansion-dag.rs` builds its cell superset as
`test_harness.sh plan` UNION `test_harness.sh audit-gaps` (expansion-dag.rs:449).
A cell that is `backends_enabled = [... "liteinst" ...]` but `ci = false` is in
NEITHER set, so the expansion sweep never emits it -- 25 of the 28 LiteInst-enabled
cells at hermit 52d56e5c (README finding 5). This script reads the enablement
directly out of the pinned manifests and patches those cells back in, REUSING
expansion-dag.rs's own generated `run-expansion-cell.sh` and its exact cmd template
so the execution context stays the runner's, not this script's.

It also sets `cpu_timeout` on every step, which expansion-dag.rs never emits, so
`safe-ci-dag-runner` does not apply its 10 s `DEFAULT_SMALL_CPU_TIMEOUT`
(agent-utils/py/safe_ci_dag_runner/model.py:27) to cells whose wall budget is
minutes (README finding 6).

Run it AFTER expansion-dag.rs has produced <run-dir>/dag.json; it writes
<run-dir>/dag-enabled28.json beside it.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import re
import shlex


def enabled_liteinst_cells(repo: Path) -> list[tuple[str, str, str]]:
    """(bucket, test_id, mode) for every manifest cell whose backends_enabled names liteinst."""
    cells: list[tuple[str, str, str]] = []
    for path in sorted(glob.glob(str(repo / "tests/e2e/manifests/*.toml"))):
        text = Path(path).read_text(encoding="utf-8")
        for block in re.split(r"^\[\[test\]\]", text, flags=re.M)[1:]:
            ident = re.search(r'^id\s*=\s*"([^"]+)"', block, re.M)
            if not ident:
                continue
            test_id = ident.group(1)
            for mode_block in re.finditer(
                r"^\[test\.modes\.(\w+)\]([\s\S]*?)(?=^\[|\Z)", block, re.M
            ):
                mode, body = mode_block.group(1), mode_block.group(2)
                enabled = re.search(r"backends_enabled\s*=\s*(\[[^\]]*\])", body, re.S)
                if enabled and "liteinst" in enabled.group(1):
                    cells.append((test_id.split("/", 1)[0], test_id, mode))
    return cells


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True, help="pinned hermit checkout")
    parser.add_argument("--run-dir", type=Path, required=True, help="expansion-dag.rs evidence run dir")
    parser.add_argument("--lane", default="portable")
    parser.add_argument("--timeout", type=int, default=600, help="per-step wall budget (s)")
    parser.add_argument("--cpu-timeout", type=int, default=180, help="per-step CPU budget (s)")
    parser.add_argument("--out", type=Path, help="default: <run-dir>/dag-enabled28.json")
    args = parser.parse_args()

    repo = args.repo.resolve()
    run_dir = args.run_dir.resolve()
    out = (args.out or run_dir / "dag-enabled28.json").resolve()

    generated = json.loads((run_dir / "dag.json").read_text(encoding="utf-8"))
    by_job = {step["job"]: step for step in generated["steps"]}
    helper = run_dir / "run-expansion-cell.sh"
    if not helper.is_file():
        raise SystemExit(f"missing expansion-dag.rs cell runner: {helper}")

    cells = enabled_liteinst_cells(repo)
    steps = []
    reused = 0
    for bucket, test_id, mode in cells:
        short = test_id.rsplit("/", 1)[-1]
        job = f"{short}__{mode}__liteinst"
        if job in by_job:
            steps.append(by_job[job])
            reused += 1
            continue
        slug = f"{bucket}__{test_id.replace('/', '-')}__{mode}__liteinst"
        cell_dir = run_dir / slug
        # Byte-identical in shape to expansion-dag.rs's own cmd template.
        cmd = " ".join(
            [
                "bash",
                shlex.quote(str(helper)),
                shlex.quote(str(repo)),
                shlex.quote(str(cell_dir)),
                shlex.quote(args.lane),
                shlex.quote(bucket),
                shlex.quote(test_id),
                shlex.quote(mode),
                "liteinst",
            ]
        )
        steps.append(
            {
                "group": bucket,
                "job": job,
                "desc": f"expansion cell {bucket}/{short} [{mode}] on liteinst",
                "cmd": cmd,
                "deps": [],
                "timeout": args.timeout,
                "hint": {
                    "est_duration_s": 180,
                    "rss_baseline_bytes": 805306368,
                    "hard_mem_max_bytes": 1207959552,
                    "classification": "cpu-bound",
                },
            }
        )

    for step in steps:
        step["timeout"] = args.timeout
        step["cpu_timeout"] = args.cpu_timeout

    out.write_text(
        json.dumps(
            {"default_step_timeout": args.timeout, "resource_caps": {}, "steps": steps}, indent=1
        ),
        encoding="utf-8",
    )
    print(
        f"{len(steps)} LiteInst-enabled cells "
        f"({reused} emitted by expansion-dag.rs, {len(steps) - reused} invisible to it) -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
