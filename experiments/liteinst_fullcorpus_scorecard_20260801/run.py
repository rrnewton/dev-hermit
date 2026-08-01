#!/usr/bin/env python3
"""Measure LiteInst against every current ptrace-verify manifest cell.

The full scorecard denominator is all 200 tests that declare ptrace verify.
LiteInst is only executed for empirically confirmed one-process/one-thread,
dynamic-ELF workloads. Other rows are emitted as honest skips, never failures.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import platform
import re
import signal
import subprocess
import time


HEADER = (
    "run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,"
    "test_id,test_mode,backend,cell_state,outcome,deterministic,parity,"
    "output_hash,duration_ms,max_rss_kb,reason"
).split(",")
RESULT_FIELDS = [
    "bucket",
    "test_id",
    "program",
    "lane",
    "scope_state",
    "ptrace_exit",
    "num_processes",
    "num_threads",
    "liteinst_exit",
    "verify_exit",
    "parity",
    "deterministic",
    "ptrace_stdout_sha256",
    "liteinst_stdout_sha256",
    "duration_ms",
    "reason",
]


def die(message: str) -> None:
    raise SystemExit(message)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def pinned_reverie_sha(repo: Path) -> str:
    lock = (repo / "Cargo.lock").read_text(encoding="utf-8")
    shas = set(re.findall(r"rrnewton/reverie\.git\?rev=([0-9a-f]{40})", lock))
    if len(shas) != 1:
        die(f"expected one pinned Reverie SHA, found {sorted(shas)}")
    return shas.pop()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(test_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", test_id)


def run_bounded(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> tuple[int, bytes, bytes, int]:
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        return proc.returncode, stdout, stderr, round((time.monotonic() - started) * 1000)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        return 124, stdout, stderr, round((time.monotonic() - started) * 1000)


def manifest(repo: Path) -> list[dict]:
    output = subprocess.check_output(
        [
            "cargo",
            "run",
            "--quiet",
            "-p",
            "hermit-manifest-plan",
            "--",
            "--format",
            "harness-json",
        ],
        cwd=repo,
    )
    documents = json.loads(output)
    tests = [test for document in documents for test in document["test"]]
    selected = [
        test
        for test in tests
        if "ptrace" in test["modes"]["verify"]["backends_enabled"]
    ]
    if len(tests) != 202 or len(selected) != 200:
        die(
            f"manifest denominator changed: tests={len(tests)} ptrace_verify={len(selected)}; "
            "review the scorecard scope before rerunning"
        )
    return selected


def build_guest(repo: Path, test: dict, cell: Path) -> tuple[list[str] | None, str]:
    program = test["program"]
    source = repo / program
    if program.endswith(".sh"):
        return [str(source), "--run"], ""
    if not program.endswith(".c"):
        return None, f"unsupported-program-kind:{program}"
    guest = cell / "guest"
    command = [
        "cc",
        "-std=c11",
        "-O2",
        "-g",
        "-Wall",
        "-Wextra",
        "-Werror",
        *test.get("build", {}).get("cflags", []),
        str(source),
        "-o",
        str(guest),
    ]
    result = subprocess.run(command, cwd=repo, capture_output=True, check=False)
    (cell / "compile.stderr").write_bytes(result.stderr)
    if result.returncode != 0:
        return None, f"build-fail-exit{result.returncode}"
    dynamic = subprocess.run(
        ["readelf", "-l", str(guest)], capture_output=True, text=True, check=False
    )
    if "Requesting program interpreter" not in dynamic.stdout:
        return [str(guest)], "static-elf-outside-liteinst-preload-scope"
    return [str(guest)], ""


def environment(cell: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "LC_ALL": "C",
            "TZ": "UTC",
            "HOME": str(cell / "home"),
            "XDG_CONFIG_HOME": str(cell / "xdg-config"),
            "E2E_TMPDIR": "/tmp/hermit-e2e",
            "E2E_FIXTURE_DIR": str(cell / "fixtures"),
        }
    )
    return env


def hermit_command(
    hermit: Path,
    backend: str,
    lane: str,
    guest: list[str],
    *,
    verify: bool = False,
    summary: Path | None = None,
) -> list[str]:
    command = [str(hermit), "--log=info", "run", "--backend", backend, "--strict"]
    if verify:
        command.append("--verify")
    if lane == "portable":
        command.extend(["--no-virtualize-cpuid", "--max-timeslice=disabled"])
    if summary is not None:
        command.append(f"--summary-json={summary}")
    command.extend(["--", *guest])
    return command


def one(
    repo: Path,
    hermit: Path,
    scratch: Path,
    test: dict,
) -> dict[str, str]:
    test_id = test["id"]
    cell = scratch / safe_name(test_id)
    for directory in ("home", "xdg-config", "fixtures"):
        (cell / directory).mkdir(parents=True, exist_ok=True)
    result = {field: "" for field in RESULT_FIELDS}
    result.update(
        {
            "bucket": test_id.split("/", 1)[0],
            "test_id": test_id,
            "program": test["program"],
            "lane": test["lane"],
        }
    )
    guest, build_reason = build_guest(repo, test, cell)
    if guest is None:
        result.update(scope_state="unclassified", reason=build_reason)
        return result
    if build_reason.startswith("static-elf"):
        result.update(scope_state="excluded-static", reason=build_reason)
        return result

    env = environment(cell)
    summary_path = cell / "ptrace-summary.json"
    timeout_run = max(30, min(int(test["timeout_seconds"]), 120))
    timeout_verify = max(60, min(int(test["timeout_seconds"]) * 2, 180))
    ptrace = hermit_command(
        hermit, "ptrace", test["lane"], guest, summary=summary_path
    )
    pe, pout, perr, _ = run_bounded(
        ptrace, cwd=repo, env=env, timeout_seconds=timeout_run
    )
    (cell / "ptrace.stdout").write_bytes(pout)
    (cell / "ptrace.stderr").write_bytes(perr)
    result["ptrace_exit"] = str(pe)
    result["ptrace_stdout_sha256"] = sha256(pout)
    if pe != 0 or not summary_path.is_file():
        result.update(
            scope_state="unclassified",
            reason=f"ptrace-reference-fail-exit{pe}",
        )
        return result
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    processes = int(summary["num_processes"])
    threads = int(summary["num_threads"])
    result["num_processes"] = str(processes)
    result["num_threads"] = str(threads)
    if processes != 1 or threads != 1:
        result.update(
            scope_state="excluded-topology",
            reason=f"outside-scope-processes{processes}-threads{threads}",
        )
        return result

    result["scope_state"] = "measured-spst"
    lite = hermit_command(hermit, "liteinst", test["lane"], guest)
    le, lout, lerr, _ = run_bounded(
        lite, cwd=repo, env=env, timeout_seconds=timeout_run
    )
    (cell / "liteinst.stdout").write_bytes(lout)
    (cell / "liteinst.stderr").write_bytes(lerr)
    verify = hermit_command(
        hermit, "liteinst", test["lane"], guest, verify=True
    )
    ve, vout, verr, duration_ms = run_bounded(
        verify, cwd=repo, env=env, timeout_seconds=timeout_verify
    )
    (cell / "liteinst-verify.stdout").write_bytes(vout)
    (cell / "liteinst-verify.stderr").write_bytes(verr)
    parity = pe == 0 and le == 0 and pout == lout
    deterministic = ve == 0 and b"Determinism verified" in verr
    reasons = []
    if le != 0:
        reasons.append(f"liteinst-run-exit{le}")
    if ve != 0:
        reasons.append(f"liteinst-verify-exit{ve}")
    elif not deterministic:
        reasons.append("verify-missing-detlog-witness")
    if not parity:
        reasons.append("ptrace-exit-stdout-mismatch")
    result.update(
        liteinst_exit=str(le),
        verify_exit=str(ve),
        parity="1" if parity else "0",
        deterministic="1" if deterministic else "0",
        liteinst_stdout_sha256=sha256(lout),
        duration_ms=str(duration_ms),
        reason=";".join(reasons),
    )
    return result


def scorecard_row(meta: dict[str, str], result: dict[str, str], enabled: bool) -> dict[str, str]:
    measured = result["scope_state"] == "measured-spst"
    det = result["deterministic"] if measured else ""
    parity = result["parity"] if measured else ""
    if not measured:
        outcome = "skip"
    elif result["liteinst_exit"] == "124" or result["verify_exit"] == "124":
        outcome = "timeout"
    elif result["liteinst_exit"] == "0" and det == "1":
        outcome = "pass"
    else:
        outcome = "fail"
    reason = (
        f"scope={result['scope_state']}; topology="
        f"{result['num_processes'] or '?'}p/{result['num_threads'] or '?'}t; "
        f"dynamic-elf-only; parity=exit+stdout vs ptrace; "
        f"determinism=full --strict --verify DETLOG; {result['reason']}"
    ).strip()
    return {
        **meta,
        "run_mode": "expansion",
        "lane": result["lane"],
        "bucket": result["bucket"],
        "test_id": result["test_id"],
        "test_mode": "verify",
        "backend": "liteinst",
        "cell_state": "enabled" if enabled else "disabled",
        "outcome": outcome,
        "deterministic": det,
        "parity": parity,
        "output_hash": result["liteinst_stdout_sha256"] if measured else "",
        "duration_ms": result["duration_ms"],
        "max_rss_kb": "",
        "reason": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--hermit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--parallel", type=int, default=8)
    args = parser.parse_args()
    repo = args.repo.resolve()
    hermit = args.hermit.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        die(f"refusing to overwrite existing output directory: {output}")
    if git(repo, "status", "--porcelain"):
        die("refusing to measure a dirty Hermit checkout")
    if not hermit.is_file() or not os.access(hermit, os.X_OK):
        die(f"Hermit binary unavailable: {hermit}")

    tests = manifest(repo)
    epoch = int(time.time())
    run_id = f"liteinst-fullcorpus-{epoch}"
    hermit_sha = git(repo, "rev-parse", "HEAD")
    reverie_sha = pinned_reverie_sha(repo)
    scratch = repo / "target" / run_id
    scratch.mkdir(parents=True)
    output.mkdir(parents=True)
    results: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(one, repo, hermit, scratch, test): test
            for test in tests
        }
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(
                f"[{index:03d}/200] {result['test_id']} "
                f"scope={result['scope_state']} det={result['deterministic'] or '-'} "
                f"par={result['parity'] or '-'}",
                flush=True,
            )
    results.sort(key=lambda row: row["test_id"])
    test_by_id = {test["id"]: test for test in tests}
    meta = {
        "run_id": run_id,
        "run_utc": f"@{epoch}",
        "hermit_sha": hermit_sha,
        "reverie_sha": reverie_sha,
        "dirty": "false",
    }
    rows = [
        scorecard_row(
            meta,
            result,
            "liteinst"
            in test_by_id[result["test_id"]]["modes"]["verify"]["backends_enabled"],
        )
        for result in results
    ]
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(results)
    with (output / "scorecard-liteinst.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    scoped = [result for result in results if result["scope_state"] == "measured-spst"]
    parity = sum(result["parity"] == "1" for result in scoped)
    deterministic = sum(result["deterministic"] == "1" for result in scoped)
    scope_counts: dict[str, int] = {}
    for result in results:
        scope_counts[result["scope_state"]] = scope_counts.get(result["scope_state"], 0) + 1
    metadata = {
        "schema": 1,
        "run_id": run_id,
        "run_utc": f"@{epoch}",
        "hermit_sha": hermit_sha,
        "reverie_sha": reverie_sha,
        "dirty": False,
        "host": platform.node(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "full_ptrace_verify_denominator": len(results),
        "scope_counts": scope_counts,
        "measured_spst": len(scoped),
        "parity_count": parity,
        "determinism_count": deterministic,
        "parity_pct_full": round(100 * parity / len(results), 1),
        "determinism_pct_full": round(100 * deterministic / len(results), 1),
        "parity_pct_spst": round(100 * parity / len(scoped), 1) if scoped else 0,
        "determinism_pct_spst": round(100 * deterministic / len(scoped), 1) if scoped else 0,
        "method": "ptrace strict+summary topology; LiteInst strict exit+stdout parity; LiteInst strict+verify DETLOG",
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
