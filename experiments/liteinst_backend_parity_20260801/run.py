#!/usr/bin/env python3
"""Measure LiteInst on the backend-parity corpus's SP/ST scope.

This is deliberately a live runner, not a projection of a checked-in ratchet.
It compares three strict runs against ptrace for guest-visible parity and runs
Hermit's strict verifier once per backend for DETLOG determinism evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time


EXCLUDED = {
    "pthread_lifecycle": "creates four pthreads",
    "process_wait_accounting": "forks and waits for child processes",
    "process_wait_lifecycle": "forks children and exercises SIGCHLD",
}
EXPECTED_MATRIX_ROWS = 23
BUCKET = "backend-parity-spst"
SCORECARD_HEADER = (
    "run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,"
    "test_id,test_mode,backend,cell_state,outcome,deterministic,parity,"
    "output_hash,duration_ms,max_rss_kb,reason"
).split(",")


def die(message: str) -> None:
    raise SystemExit(message)


def load_runner(repo: Path):
    path = repo / "tests/backend-parity/run_matrix.py"
    spec = importlib.util.spec_from_file_location("backend_parity_runner", path)
    if spec is None or spec.loader is None:
        die(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def pinned_reverie_sha(repo: Path) -> str:
    lock = (repo / "Cargo.lock").read_text(encoding="utf-8")
    shas = set(re.findall(r"rrnewton/reverie\.git\?rev=([0-9a-f]{40})", lock))
    if len(shas) != 1:
        die(f"expected one pinned Reverie SHA in Cargo.lock, found {sorted(shas)}")
    return shas.pop()


def scoped_guest(runner, name: str, fixtures):
    guest, status, stdout = runner.case_command(name, fixtures)
    if name == "random_sources":
        guest = [*guest, "--root-only"]
    return guest, status, stdout


def validate_stdout(name: str, actual: bytes, expected: bytes | None) -> str | None:
    if expected is not None:
        if actual != expected:
            return f"stdout={actual!r}, expected={expected!r}"
        return None
    markers = {
        "virtual_clock": b"clock matrix success\n",
        "heap_growth": b"heap ",
        "anonymous_mmap_layout": b"multiple ",
        "shared_anonymous_mmap": b"shared ",
        "random_sources": b"getrandom[0]=",
        "virtual_pid": b"pid=",
    }
    marker = markers[name]
    if marker not in actual:
        return f"stdout omitted marker {marker!r}"
    return None


def run_strict(runner, hermit: Path, backend: str, name: str, fixtures):
    guest, expected_status, expected_stdout = scoped_guest(runner, name, fixtures)
    started = time.monotonic()
    observations: list[tuple[int, bytes]] = []
    for iteration in range(runner.RUNS):
        command = runner.hermit_command(
            hermit, backend, guest, name, strict=True, verify=False
        )
        result = runner.run_with_timeout(command)
        if result is None:
            return False, f"strict run {iteration + 1} timed out", b"", 0, time.monotonic() - started
        if result.returncode != expected_status:
            diagnostic = result.stderr.decode(errors="replace").strip()
            return (
                False,
                f"strict run {iteration + 1} exited {result.returncode}, expected "
                f"{expected_status}: {diagnostic[-300:]}",
                result.stdout,
                result.returncode,
                time.monotonic() - started,
            )
        stdout_error = validate_stdout(name, result.stdout, expected_stdout)
        if stdout_error:
            return False, f"strict run {iteration + 1} {stdout_error}", result.stdout, result.returncode, time.monotonic() - started
        observations.append((result.returncode, result.stdout))
    if any(observation != observations[0] for observation in observations[1:]):
        return False, "strict observations differed across 3 runs", observations[0][1], observations[0][0], time.monotonic() - started
    return True, "3/3 strict observations matched", observations[0][1], observations[0][0], time.monotonic() - started


def run_verify(runner, hermit: Path, backend: str, name: str, fixtures):
    guest, expected_status, _ = scoped_guest(runner, name, fixtures)
    return runner.run_case_verify(
        hermit, backend, name, guest, expected_status, "detlog"
    )


def smoke(runner, hermit: Path) -> None:
    command = runner.hermit_command(
        hermit, "liteinst", ["/bin/true"], "exit_zero", strict=True
    )
    result = runner.run_with_timeout(command)
    if result is None:
        die("LiteInst smoke timed out")
    if result.returncode != 0:
        die(
            "LiteInst smoke failed: "
            + result.stderr.decode(errors="replace").strip()[-500:]
        )


def scorecard_row(
    *,
    run_id: str,
    run_utc: str,
    hermit_sha: str,
    reverie_sha: str,
    name: str,
    backend: str,
    l1_ok: bool,
    l2_ok: bool,
    parity: bool,
    stdout: bytes,
    duration: float,
    detail: str,
) -> dict[str, str]:
    if backend == "ptrace":
        parity = l1_ok and l2_ok
    else:
        parity = l1_ok and l2_ok and parity
    scope = "single-process/single-thread; dynamic ELF; random_sources uses --root-only"
    return {
        "run_id": run_id,
        "run_utc": run_utc,
        "hermit_sha": hermit_sha,
        "reverie_sha": reverie_sha,
        "dirty": "false",
        "run_mode": "expansion",
        "lane": "portable",
        "bucket": BUCKET,
        "test_id": f"{BUCKET}/{name}",
        "test_mode": "verify",
        "backend": backend,
        "cell_state": "disabled",
        "outcome": "pass" if l2_ok else "fail",
        "deterministic": "1" if l2_ok else "0",
        "parity": "1" if parity else "0",
        "output_hash": hashlib.sha256(stdout).hexdigest() if l1_ok else "",
        "duration_ms": str(round(duration * 1000)),
        "max_rss_kb": "",
        "reason": " ".join(
            f"{scope}; parity=bitwise stdout+exit vs ptrace after 3 strict runs; {detail}".splitlines()
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--hermit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()

    repo = args.repo.resolve()
    hermit = args.hermit.resolve()
    output_dir = args.output_dir.resolve()
    if not hermit.is_file() or not os.access(hermit, os.X_OK):
        die(f"Hermit binary is unavailable: {hermit}")
    dirty = git(repo, "status", "--porcelain")
    if dirty:
        die(f"refusing dirty Hermit checkout:\n{dirty}")

    runner = load_runner(repo)
    matrix = runner.read_matrix()
    names = [row["test_name"] for row in matrix]
    if len(names) != EXPECTED_MATRIX_ROWS:
        die(
            f"matrix changed from {EXPECTED_MATRIX_ROWS} rows to {len(names)}; "
            "review SP/ST classification before rerunning"
        )
    missing = set(EXCLUDED).difference(names)
    if missing:
        die(f"expected exclusions missing from matrix: {sorted(missing)}")
    selected = [name for name in names if name not in EXCLUDED]

    epoch = int(time.time())
    run_id = args.run_id or f"liteinst-spst-{epoch}"
    run_utc = f"@{epoch}"
    hermit_sha = git(repo, "rev-parse", "HEAD")
    reverie_sha = pinned_reverie_sha(repo)
    smoke(runner, hermit)

    raw_rows: list[dict[str, str]] = []
    scorecard_rows: list[dict[str, str]] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="liteinst-backend-parity-") as tmp:
        fixtures = runner.Fixtures(Path(tmp))
        for name in selected:
            p_l1, p_l1_detail, p_stdout, p_status, p_l1_s = run_strict(
                runner, hermit, "ptrace", name, fixtures
            )
            l_l1, l_l1_detail, l_stdout, l_status, l_l1_s = run_strict(
                runner, hermit, "liteinst", name, fixtures
            )
            parity = (
                p_l1
                and l_l1
                and (p_status, p_stdout) == (l_status, l_stdout)
            )
            p_l2_status, p_l2_detail, p_l2_s = run_verify(
                runner, hermit, "ptrace", name, fixtures
            )
            l_l2_status, l_l2_detail, l_l2_s = run_verify(
                runner, hermit, "liteinst", name, fixtures
            )
            p_l2 = p_l2_status == "PASS"
            l_l2 = l_l2_status == "PASS"
            print(
                f"{name}: parity={'PASS' if parity else 'FAIL'} "
                f"ptrace_l2={p_l2_status} liteinst_l2={l_l2_status}",
                flush=True,
            )
            raw_rows.append(
                {
                    "test_name": name,
                    "ptrace_strict": "pass" if p_l1 else "fail",
                    "liteinst_strict": "pass" if l_l1 else "fail",
                    "parity": "1" if parity else "0",
                    "ptrace_l2": "pass" if p_l2 else "fail",
                    "liteinst_l2": "pass" if l_l2 else "fail",
                    "ptrace_stdout_sha256": hashlib.sha256(p_stdout).hexdigest() if p_l1 else "",
                    "liteinst_stdout_sha256": hashlib.sha256(l_stdout).hexdigest() if l_l1 else "",
                    "ptrace_detail": f"{p_l1_detail}; {p_l2_detail}",
                    "liteinst_detail": f"{l_l1_detail}; {l_l2_detail}",
                }
            )
            scorecard_rows.append(
                scorecard_row(
                    run_id=run_id,
                    run_utc=run_utc,
                    hermit_sha=hermit_sha,
                    reverie_sha=reverie_sha,
                    name=name,
                    backend="ptrace",
                    l1_ok=p_l1,
                    l2_ok=p_l2,
                    parity=True,
                    stdout=p_stdout,
                    duration=p_l1_s + p_l2_s,
                    detail=f"{p_l1_detail}; {p_l2_detail}",
                )
            )
            scorecard_rows.append(
                scorecard_row(
                    run_id=run_id,
                    run_utc=run_utc,
                    hermit_sha=hermit_sha,
                    reverie_sha=reverie_sha,
                    name=name,
                    backend="liteinst",
                    l1_ok=l_l1,
                    l2_ok=l_l2,
                    parity=parity,
                    stdout=l_stdout,
                    duration=l_l1_s + l_l2_s,
                    detail=f"{l_l1_detail}; {l_l2_detail}",
                )
            )

    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=list(raw_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(raw_rows)
    with (output_dir / "scorecard-rows.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=SCORECARD_HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(scorecard_rows)
    metadata = {
        "schema": 1,
        "run_id": run_id,
        "run_utc": run_utc,
        "hermit_sha": hermit_sha,
        "reverie_sha": reverie_sha,
        "dirty": False,
        "host": platform.node(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "matrix_rows": len(names),
        "measured_rows": len(selected),
        "scope": "dynamic ELF, single process, single thread",
        "excluded": EXCLUDED,
        "command": "./run.py --repo <slot>/hermit --hermit <slot>/hermit/target/release/hermit --output-dir results",
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
