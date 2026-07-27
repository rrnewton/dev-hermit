#!/usr/bin/env python3
"""Drive QEMU HMP savevm/loadvm while QEMU runs under Hermit's KVM backend."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def wait_for_marker(path: Path, marker: str, process: subprocess.Popen[str], timeout: float) -> float:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if path.exists() and marker in path.read_text(errors="replace"):
            return time.monotonic() - start
        if process.poll() is not None:
            raise RuntimeError(f"Hermit exited before marker with status {process.returncode}")
        time.sleep(0.05)
    raise TimeoutError(f"did not observe {marker!r} in {path} after {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermit", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--initramfs", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--marker-timeout", type=float, default=120)
    args = parser.parse_args()

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    disk = args.artifact_dir / "snapshot-state.qcow2"
    serial = args.artifact_dir / "snapshot-serial.log"
    monitor_log = args.artifact_dir / "snapshot-monitor.log"
    hermit_log = args.artifact_dir / "snapshot-hermit.stderr"
    for path in (disk, serial, monitor_log, hermit_log):
        path.unlink(missing_ok=True)

    subprocess.run(
        ["qemu-img", "create", "-q", "-f", "qcow2", str(disk), "256M"],
        check=True,
    )
    command = [
        str(args.hermit),
        "--log", "warn",
        "run", "--backend", "kvm", "--strict", "--",
        "/usr/local/bin/qemu-system-x86_64",
        "-m", "128M",
        "-accel", "tcg,thread=single",
        "-smp", "1",
        "-icount", "shift=0,sleep=off",
        "-kernel", str(args.kernel),
        "-initrd", str(args.initramfs),
        "-drive", f"file={disk},if=virtio,format=qcow2",
        "-display", "none",
        "-serial", f"file:{serial}",
        "-monitor", "stdio",
        "-no-reboot",
        "-append", "console=ttyS0 panic=-1 rdinit=/init",
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        boot_seconds = wait_for_marker(
            serial, "HERMIT-QEMU-SNAPSHOT-READY", process, args.marker_timeout
        )
        assert process.stdin is not None
        process.stdin.write("savevm hermit-ready\n")
        process.stdin.flush()
        time.sleep(2)
        process.stdin.write("info snapshots\n")
        process.stdin.flush()
        time.sleep(1)
        before_load = time.monotonic()
        process.stdin.write("loadvm hermit-ready\n")
        process.stdin.flush()
        time.sleep(2)
        load_seconds = time.monotonic() - before_load
        process.stdin.write("info status\nquit\n")
        process.stdin.flush()
        stdout, stderr = process.communicate(timeout=30)
    except Exception as error:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=10)
        monitor_log.write_text(stdout)
        hermit_log.write_text(stderr)
        result = {
            "command": command,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": str(error),
            "exit_status": process.returncode,
            "serial_bytes": serial.stat().st_size if serial.exists() else 0,
            "snapshot_listed": False,
            "running_after_load": False,
        }
        (args.artifact_dir / "snapshot-result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    monitor_log.write_text(stdout)
    hermit_log.write_text(stderr)
    monitor_normalized = stdout.replace("\r", "")
    result = {
        "command": command,
        "exit_status": process.returncode,
        "boot_to_marker_seconds": round(boot_seconds, 3),
        "load_wait_seconds": round(load_seconds, 3),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "snapshot_listed": "hermit-ready" in monitor_normalized,
        "running_after_load": "VM status: running" in monitor_normalized,
        "serial_marker": "HERMIT-QEMU-SNAPSHOT-READY" in serial.read_text(errors="replace"),
        "disk_bytes": disk.stat().st_size,
    }
    (args.artifact_dir / "snapshot-result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if all(
        [
            result["exit_status"] == 0,
            result["snapshot_listed"],
            result["running_after_load"],
            result["serial_marker"],
        ]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
