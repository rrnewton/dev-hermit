#!/usr/bin/env python3
"""Qualify the backend-parity-c RED cells that are disabled for LACK OF EVIDENCE.

The manifest disables a (fixture, backend) cell for two very different reasons:

  * a KNOWN limitation  -- "KVM ElfExecutor returns ENOSYS for sendfile(2)".
    Out of scope: the cell is red because the backend genuinely cannot do it.
  * NO EVIDENCE         -- "Not evaluated in the source backend-parity matrix"
    or "the L2 --verify witness was not recorded". These are unknown, not
    unsupported, and they are the burndown surface.

This runs each unknown cell INDIVIDUALLY against the ptrace golden and records
why it diverges, so a flip is backed by a measurement rather than a bulk edit.

Verdicts are deliberately not collapsed into pass/fail:
  L2-PASS        --verify succeeded AND stdout+exit match the ptrace golden
  L1-ONLY        plain --strict matches the golden, but --verify did not hold
  DIVERGES       stdout or exit differs from the golden (the real finding)
  GOLDEN-FAIL    ptrace itself failed, so there is no golden to compare against
                 -- a NO-RESULT for the backend, never a pass (#319)
  BUILD-FAIL     the fixture does not compile; the cell cannot be enabled at all
  TIMEOUT        no verdict within the contract
"""
from __future__ import annotations

import concurrent.futures as futures
import os
import re
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

HERMIT_REPO = Path("/home/newton/work/dev-hermit/hermit")
MANIFEST = HERMIT_REPO / "tests/e2e/manifests/backend-parity-c.toml"
OUT = Path("/home/newton/work/dev-hermit/ignored/w10-cells")
BINARY = Path("/home/newton/work/dev-hermit/worktrees/audit/hermit/target/release/hermit")
# Must be run IN PLACE: hermit discovers the DBI runtime as a SIBLING of the
# binary, so copying it elsewhere silently disables the backend -- which then
# reads as 141 "divergent" cells. Guarded by a before/after digest instead.
BUILD = OUT / "build"
TIMEOUT = 60

# A reason is "no evidence" iff it says so. Anything else is a known limitation
# and is left alone -- matching on the reason text is what keeps this from
# silently re-litigating a documented gap.
NO_EVIDENCE = re.compile(
    r"not evaluated in the source backend-parity matrix"
    r"|the L2 --verify witness was not recorded"
    r"|preserves the established ptrace baseline",
    re.IGNORECASE,
)


def run(cmd: list[str], timeout: int = TIMEOUT):
    """Run in its own session so a hung guest is reaped by PGID.

    Invariant 15: only ever signal a process group this function created.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.communicate()
        return None, b"", b"timeout"


def engagement(backend: str, guest: Path, name: str) -> tuple[bool, str]:
    """Prove the BACKEND actually ran the guest, not just that the guest ran.

    Without this a "pass" only says some backend produced the golden's output --
    and both backends can degrade to something that looks exactly like ptrace.
    The observable is different per backend, so each gets its own signal:

      dbi   -- `reverie-dbi: ... rewritten=N`; N>0 means DynamoRIO rewrote code.
      sabre -- `hermit::sabre::fallback: ... guest_rpc_observed=true`; the guest
               made coordinator RPC calls THROUGH the SaBRe plugin, which a
               silent ptrace fallback cannot produce. `ptrace_fallback_sites`
               is reported alongside it.

    A pass whose engagement cannot be observed is NOT counted as a pass.
    """
    cmd = [str(BINARY), "-l", "info", "run", "--backend", backend, "--strict",
           "--base-env=minimal", "--max-timeslice=disabled", "--tmp=/tmp", "--", str(guest)]
    rc, _, err = run(cmd)
    text = err.decode(errors="replace") if err else ""
    if backend == "dbi":
        m = re.search(r"reverie-dbi:.*?rewritten=(\d+)", text)
        if m and int(m.group(1)) > 0:
            return True, f"dbi rewritten={m.group(1)}"
        return False, "no reverie-dbi rewritten>0 line: DynamoRIO engagement unproven"
    m = re.search(r"guest_rpc_observed=(\w+).*", text)
    fb = re.search(r"ptrace_fallback_sites=(\d+)", text)
    if m and m.group(1) == "true":
        return True, f"sabre guest_rpc_observed=true ptrace_fallback_sites={fb.group(1) if fb else '?'}"
    return False, "no guest_rpc_observed=true: cannot exclude a silent ptrace fallback"


def hermit_cmd(backend: str, guest: Path, name: str, verify: bool) -> list[str]:
    """Exactly the matrix driver's command shape (run_matrix.py::hermit_command)."""
    cmd = [str(BINARY), "run"]
    if backend != "ptrace":
        cmd += ["--backend", backend]
    cmd.append("--strict")
    if verify:
        cmd += ["--verify", "--verify-allow", "both"]
    cmd += ["--base-env=minimal", "--max-timeslice=disabled", "--tmp=/tmp"]
    if backend == "ptrace" and name != "cpuid_policy":
        cmd.append("--no-virtualize-cpuid")
    cmd += ["--", str(guest)]
    return cmd


def _cc(source: Path, out: Path, cflags: list[str]):
    cmd = ["cc", "-O2", "-g", "-std=c11", "-Wall", "-Wextra", "-Werror",
           *cflags, str(source), "-o", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def compile_fixture(source: Path, out: Path, cflags: list[str]):
    """Compile with the MANIFEST's flags, then retry with the legacy driver's.

    Returns (error_or_None, manifest_flags_sufficient).

    The legacy run_matrix.py carries a hardcoded per-fixture flag map
    (-D_GNU_SOURCE, -pthread) that the schema-v2 manifest did NOT inherit: it
    records `build.cflags` for only 3 of 78 tests. Compiling with the manifest's
    flags alone therefore fails on any fixture using a GNU extension
    (e.g. mkdtemp under -std=c11), which would be scored as a red cell when the
    real defect is a MISSING BUILD FLAG IN THE MANIFEST. The retry keeps that
    from manufacturing false reds, and the flag is reported so the manifest gap
    is visible rather than papered over.
    """
    res = _cc(source, out, cflags)
    if res.returncode == 0:
        return None, True
    first = ""
    for line in (res.stderr or res.stdout).split("\n"):
        if "error:" in line:
            first = line.strip()[:150]
            break
    retry = _cc(source, out, list(cflags) + ["-D_GNU_SOURCE", "-pthread"])
    if retry.returncode == 0:
        return None, False
    for line in (retry.stderr or retry.stdout).split("\n"):
        if "error:" in line:
            return line.strip()[:150], False
    return first or "compile failed", False


def load_cells():
    doc = tomllib.loads(MANIFEST.read_text())
    cells = []
    for test in doc["test"]:
        verify = test.get("modes", {}).get("verify")
        if not verify:
            continue
        disabled = verify.get("backends_disabled", {})
        unknown = {
            b: r for b, r in disabled.items()
            if b in ("dbi", "sabre") and NO_EVIDENCE.search(r)
        }
        if not unknown:
            continue
        cells.append({
            "id": test["id"],
            "program": test.get("program"),
            "cflags": test.get("build", {}).get("cflags", []),
            "unknown": unknown,
        })
    return cells


def qualify(cell) -> list[dict]:
    name = cell["id"].split("/")[-1]
    rows = []
    src = HERMIT_REPO / cell["program"]
    binary = BUILD / name.replace("/", "_")

    if not src.is_file():
        return [dict(cell_id=cell["id"], backend=b, verdict="BUILD-FAIL",
                     detail=f"source missing: {cell['program']}", golden_rc="", backend_rc="")
                for b in cell["unknown"]]

    err, manifest_ok = compile_fixture(src, binary, cell["cflags"])
    if err:
        return [dict(cell_id=cell["id"], backend=b, verdict="BUILD-FAIL",
                     detail=err, golden_rc="", backend_rc="")
                for b in cell["unknown"]]

    flag_note = "" if manifest_ok else " [manifest build.cflags incomplete: needed -D_GNU_SOURCE/-pthread]"
    grc, gout, _ = run(hermit_cmd("ptrace", binary, name, verify=False))
    for backend in sorted(cell["unknown"]):
        if grc is None:
            rows.append(dict(cell_id=cell["id"], backend=backend, verdict="GOLDEN-FAIL",
                             detail="ptrace golden timed out; no baseline to compare",
                             golden_rc="timeout", backend_rc=""))
            continue
        brc, bout, berr = run(hermit_cmd(backend, binary, name, verify=False))
        if brc is None:
            rows.append(dict(cell_id=cell["id"], backend=backend, verdict="TIMEOUT",
                             detail=f"no verdict in {TIMEOUT}s under --strict",
                             golden_rc=grc, backend_rc="timeout"))
            continue
        tail = berr.decode(errors="replace")
        if "is unavailable" in tail or "was not built beside" in tail:
            # NOT a divergence: the backend never ran. Scoring this as red is
            # the #319 error one level up -- a no-result reported as a finding.
            rows.append(dict(cell_id=cell["id"], backend=backend, verdict="UNAVAILABLE",
                             detail="backend not available to this binary: "
                                    + tail.strip().split("\n")[-1][:130],
                             golden_rc=grc, backend_rc=brc))
            continue
        if brc != grc:
            rows.append(dict(cell_id=cell["id"], backend=backend, verdict="DIVERGES",
                             detail=f"exit {brc} vs golden {grc}: "
                                    + berr.decode(errors="replace").strip().split("\n")[-1][:140],
                             golden_rc=grc, backend_rc=brc))
            continue
        if bout != gout:
            rows.append(dict(cell_id=cell["id"], backend=backend, verdict="DIVERGES",
                             detail=f"stdout differs ({len(bout)}B vs golden {len(gout)}B)",
                             golden_rc=grc, backend_rc=brc))
            continue
        vrc, _, verr = run(hermit_cmd(backend, binary, name, verify=True))
        if vrc == 0:
            engaged, how = engagement(backend, binary, name)
            verdict = "L2-PASS" if engaged else "UNVERIFIED-ENGAGEMENT"
            rows.append(dict(cell_id=cell["id"], backend=backend, verdict=verdict,
                             detail=("--verify held, stdout+exit match the ptrace golden; " + how)
                                    + flag_note,
                             golden_rc=grc, backend_rc=brc))
        else:
            why = "timeout" if vrc is None else verr.decode(errors="replace").strip().split("\n")[-1][:140]
            rows.append(dict(cell_id=cell["id"], backend=backend, verdict="L1-ONLY",
                             detail=f"strict matches golden but --verify rc={vrc}: {why}" + flag_note,
                             golden_rc=grc, backend_rc=brc))
    return rows


def main() -> int:
    BUILD.mkdir(parents=True, exist_ok=True)
    cells = load_cells()
    total = sum(len(c["unknown"]) for c in cells)
    print(f"qualifying {total} no-evidence cells across {len(cells)} fixtures "
          f"(dbi+sabre; kvm excluded: livelocks and ignores SIGTERM)", file=sys.stderr)

    rows = []
    # Modest pool: this is a 316-core box shared with ~15 other agents.
    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        for done, result in enumerate(pool.map(qualify, cells), 1):
            rows.extend(result)
            if done % 10 == 0:
                print(f"  {done}/{len(cells)} fixtures", file=sys.stderr)

    rows.sort(key=lambda r: (r["backend"], r["verdict"], r["cell_id"]))
    csv = OUT / "cell-qualification.csv"
    with csv.open("w") as handle:
        handle.write("cell_id,backend,verdict,golden_rc,backend_rc,detail\n")
        for r in rows:
            detail = str(r["detail"]).replace('"', "'").replace("\n", " ")
            handle.write(f'{r["cell_id"]},{r["backend"]},{r["verdict"]},'
                         f'{r["golden_rc"]},{r["backend_rc"]},"{detail}"\n')
    print(f"wrote {csv} ({len(rows)} rows)", file=sys.stderr)

    from collections import Counter
    for backend in ("dbi", "sabre"):
        tally = Counter(r["verdict"] for r in rows if r["backend"] == backend)
        print(f"{backend}: " + "  ".join(f"{k}={v}" for k, v in tally.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
