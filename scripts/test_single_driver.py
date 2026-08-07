#!/usr/bin/env python3
"""Bracket the single-driver guard from BOTH sides, and prove the dead-owner case.

The verify clause this answers, one test each:

  A  a second driver launched against a LIVE run REFUSES with a clear reason
  B  the first driver completes normally regardless
  C  a driver whose process is GONE does not block a legitimate run
  D  a stale owner sidecar naming a dead/reused pid does not block either
  E  the guard actually prevents the corruption it exists for -- unguarded
     concurrent drivers duplicate rows; guarded ones do not

E matters most. A and C only show the lock behaves like a lock; E shows the lock
is attached to the right thing. Without it this file would pass even if the
guard were wired to a ledger nobody writes.

Run: python3 scripts/test_single_driver.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARD = HERE / "single-driver.py"

FAILURES: list[str] = []
CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)


def guard(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD), *args],
                          capture_output=True, text=True, **kw)


def wait_for(pred, timeout=10.0, interval=0.05) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    ledger = tmp / "results.tsv"
    ledger.write_text("guest\trc\n")

    # ------------------------------------------------------------------ A + B
    # A long-lived first driver, then a second one against the same ledger.
    started = tmp / "started"
    first = subprocess.Popen(
        [sys.executable, str(GUARD), "run", "--ledger", str(ledger),
         "--name", "driver-one", "--",
         "bash", "-c", f"touch {started}; sleep 6; echo done > {tmp}/first-finished"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check(wait_for(started.exists), "first driver never started")

    second = guard("run", "--ledger", str(ledger), "--name", "driver-two",
                   "--", "bash", "-c", f"echo SHOULD-NOT-RUN > {tmp}/second-ran")
    check(second.returncode == 3,
          f"A: second driver did not refuse with 3 (got {second.returncode})")
    check("REFUSED" in second.stderr, "A: refusal did not say REFUSED")
    check("driver-one" in second.stderr,
          f"A: refusal did not name the live owner: {second.stderr[:200]}")
    check(not (tmp / "second-ran").exists(), "A: the refused driver ran anyway")

    # `status` must agree with the refusal while the run is live.
    st = guard("status", "--ledger", str(ledger))
    check(st.returncode == 3, f"A: status said free while held (rc={st.returncode})")
    check("HELD" in st.stdout, "A: status did not report HELD")

    rc_first = first.wait(timeout=30)
    check(rc_first == 0, f"B: first driver did not finish cleanly (rc={rc_first})")
    check((tmp / "first-finished").exists(), "B: first driver did not complete its work")

    # The lock is gone the moment the holder exits -- nothing to release.
    check(guard("status", "--ledger", str(ledger)).returncode == 0,
          "B: lock still held after the owner exited normally")

    # ---------------------------------------------------------------------- C
    # THE DEAD-OWNER CASE. Kill the holder with SIGKILL -- no handler runs, no
    # cleanup happens, the owner sidecar is left behind pointing at a dead pid.
    # A PID-file guard would now be stuck exactly like the land-lock. An flock
    # is already free.
    started2 = tmp / "started2"
    victim = subprocess.Popen(
        [sys.executable, str(GUARD), "run", "--ledger", str(ledger),
         "--name", "driver-doomed", "--",
         "bash", "-c", f"touch {started2}; sleep 300"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check(wait_for(started2.exists), "C: victim driver never started")
    check(guard("check", "--ledger", str(ledger)).returncode == 3,
          "C: lock not held before the kill (test would prove nothing)")

    os.kill(victim.pid, signal.SIGKILL)   # our own child, by pid
    victim.wait(timeout=30)

    owner_file = ledger.with_name(ledger.name + ".driver-owner.json")
    check(owner_file.exists(),
          "C: no stale owner sidecar survived the kill — the test needs one to be meaningful")

    check(wait_for(lambda: guard("check", "--ledger", str(ledger)).returncode == 0, 15),
          "C: DEAD-OWNER TRAP RECREATED — lock still held after SIGKILL")

    ran = tmp / "after-kill-ran"
    after = guard("run", "--ledger", str(ledger), "--name", "driver-three",
                  "--", "bash", "-c", f"echo ok > {ran}")
    check(after.returncode == 0,
          f"C: a legitimate run was blocked by a dead owner (rc={after.returncode}, "
          f"{after.stderr[:200]})")
    check(ran.exists(), "C: the post-kill driver did not actually run")

    # ---------------------------------------------------------------------- D
    # A stale sidecar naming a pid that cannot be ours: still must not block,
    # and `status` should say so out loud rather than hedging.
    owner_file.write_text('{"name":"ghost","pid":999999,"start_ticks":1,'
                          '"boot_id":"stale","host":"nowhere","cmd":"gone",'
                          '"since_epoch":0}\n')
    st = guard("status", "--ledger", str(ledger))
    check(st.returncode == 0, "D: a stale sidecar made status report HELD")
    check("stale owner sidecar" in st.stdout,
          f"D: status did not call out the stale record: {st.stdout[:200]}")
    ran_d = tmp / "stale-ok"
    check(guard("run", "--ledger", str(ledger), "--name", "d", "--",
                "bash", "-c", f"echo ok > {ran_d}").returncode == 0,
          "D: a stale sidecar blocked a legitimate run")
    check(ran_d.exists(), "D: the run after a stale sidecar did not execute")

    # ---------------------------------------------------------------------- E
    # THE CORRUPTION THE GUARD EXISTS FOR. Reproduce the real driver's shape:
    #   grep -q "^label\t" results.tsv && continue     <- check
    #   ... measure ...                                <- window
    #   printf 'label\t...' >> results.tsv             <- append
    # Two drivers racing that window both append the same label.
    APPENDER = r'''
for i in $(seq 1 12); do
  lbl="guest$i"
  grep -q "^$lbl	" "$1" && continue
  sleep 0.02                      # the measure window the real runner has
  printf '%s\t0\n' "$lbl" >> "$1"
done
'''
    def race(led: Path, guarded: bool) -> int:
        led.write_text("guest\trc\n")
        procs = []
        for _ in range(2):
            argv = ["bash", "-c", APPENDER, "_", str(led)]
            if guarded:
                argv = [sys.executable, str(GUARD), "run", "--ledger", str(led),
                        "--name", "racer", "--"] + argv
            procs.append(subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL))
        for p in procs:
            p.wait(timeout=60)
        rows = [l for l in led.read_text().splitlines()[1:] if l.strip()]
        labels = [r.split("\t")[0] for r in rows]
        return len(labels) - len(set(labels))   # duplicate count

    unguarded_dupes = race(tmp / "race-unguarded.tsv", guarded=False)
    check(unguarded_dupes > 0,
          "E: the unguarded race produced NO duplicates, so this test cannot show "
          "the guard prevents anything (widen the measure window and retry)")

    # Guarded: the loser is REFUSED (exit 3) rather than queued, so it contributes
    # nothing. Exactly one driver writes, so no label can appear twice.
    guarded_dupes = race(tmp / "race-guarded.tsv", guarded=True)
    check(guarded_dupes == 0,
          f"E: the guard did not prevent duplicate rows ({guarded_dupes} dupes)")

# ------------------------------------------------------------------- VERDICT
# Keep this last: an assertion placed above later checks records their failures
# and never looks at them again.
if FAILURES:
    print(f"FAIL: {len(FAILURES)} of {CHECKS} checks failed")
    for f in FAILURES:
        print(f"  - {f}")
    raise SystemExit(1)
print(f"ok: {CHECKS} checks passed — live driver refused, dead owner does NOT block "
      f"(SIGKILL'd holder released instantly), stale sidecar ignored, and the guard "
      f"removed {unguarded_dupes} duplicate row(s) the unguarded race produced")
