#!/usr/bin/env python3
"""Integration tests for ownership-safe agent Podman reconciliation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "agent-podman.rs"


FAKE_PODMAN = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_PODMAN_STATE"])
log_path = Path(os.environ["FAKE_PODMAN_LOG"])
containers = json.loads(state_path.read_text())
args = sys.argv[1:]

def save():
    state_path.write_text(json.dumps(containers))

def find(name):
    return next((item for item in containers if item["Id"] == name or name in item["Names"]), None)

if args[:3] == ["ps", "-a", "--format"]:
    print(json.dumps(containers))
elif args and args[0] == "top":
    item = find(args[1])
    print("HPID STATE")
    for offset in range(item.get("Zombies", 0)):
        print(f"{1000 + offset} Z")
elif args and args[0] == "inspect":
    item = find(args[-1])
    if item is None:
        sys.exit(1)
    template = args[args.index("--format") + 1]
    if template == "{{json .Config.Labels}}":
        print(json.dumps(item.get("Labels", {})))
    elif template == "{{.Id}}":
        print(item["Id"])
    elif template == "{{.State.Pid}}":
        print("0")
    else:
        sys.exit(2)
elif args and args[0] == "stop":
    item = find(args[-1])
    item["State"] = "stopped"
    with log_path.open("a") as log:
        log.write(f"stop {item['Id']}\n")
    save()
    if os.environ.get("FAKE_PODMAN_STOP_FAIL_AFTER_STOP") == "1":
        print("stop command timed out after container exited", file=sys.stderr)
        sys.exit(124)
elif args and args[0] == "rm":
    item = find(args[-1])
    with log_path.open("a") as log:
        log.write(f"rm {item['Id']}\n")
    containers.remove(item)
    save()
elif args[:2] == ["container", "exists"]:
    sys.exit(0 if find(args[2]) else 1)
else:
    print(f"unsupported fake podman args: {args}", file=sys.stderr)
    sys.exit(2)
"""


def labels(agent: str, invocation: str, lifetime: str = "agent") -> dict[str, str]:
    return {
        "io.dev-hermit.agent-podman": "v1",
        "io.dev-hermit.owner-agent": agent,
        "io.dev-hermit.owner-invocation": invocation,
        "io.dev-hermit.owner-pane": "%1",
        "io.dev-hermit.owner-task": "fixture-task",
        "io.dev-hermit.lifetime": lifetime,
    }


def container(
    container_id: str,
    name: str,
    container_labels: dict[str, str] | None,
    *,
    zombies: int = 0,
) -> dict[str, Any]:
    return {
        "Id": container_id,
        "Names": [name],
        "State": "running",
        "Labels": container_labels,
        "Zombies": zombies,
    }


class AgentPodmanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-podman-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.fake_podman = self.directory / "podman"
        self.fake_podman.write_text(textwrap.dedent(FAKE_PODMAN))
        self.fake_podman.chmod(0o755)
        self.podman_state = self.directory / "podman.json"
        self.podman_log = self.directory / "podman.log"
        self.podman_log.write_text("")
        self.agents = self.directory / "agents.json"
        self.invocations = self.directory / "invocations.json"
        self.registry = self.directory / "ownership.json"
        self.environment = os.environ | {
            "AGENT_PODMAN_BIN": str(self.fake_podman),
            "FAKE_PODMAN_STATE": str(self.podman_state),
            "FAKE_PODMAN_LOG": str(self.podman_log),
            "DEV_HERMIT_CONTAINER_STATE": str(self.registry),
            "DG_AGENT_NAME": "hermit-new",
            "META_3PAI_INVOCATION_ID": "inv-new",
            "TMUX_PANE": "%9",
        }

    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["rust-script", str(TOOL), *arguments],
            cwd=ROOT,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )

    def write_live_state(
        self, agents: list[dict[str, str]], invocations: dict[str, list[str]]
    ) -> None:
        self.agents.write_text(json.dumps(agents))
        self.invocations.write_text(json.dumps(invocations))

    def reconcile(self, *, apply: bool = True) -> subprocess.CompletedProcess[str]:
        arguments = [
            "reconcile",
            "--agents-json",
            str(self.agents),
            "--live-invocations",
            str(self.invocations),
            "--json",
        ]
        if apply:
            arguments.append("--apply")
        return self.run_tool(*arguments)

    def test_retired_agent_fixture_is_gracefully_reclaimed(self) -> None:
        self.podman_state.write_text(
            json.dumps([container("a" * 64, "retired", labels("hermit-old", "inv-old"), zombies=2)])
        )
        self.write_live_state([], {})

        result = self.reconcile()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["reclaimed"], 1)
        self.assertEqual(report["zombies"], 2)
        self.assertEqual(
            self.podman_log.read_text().splitlines(),
            [f"stop {'a' * 64}", f"rm {'a' * 64}"],
        )
        self.assertEqual(json.loads(self.podman_state.read_text()), [])

    def test_remove_continues_when_stop_times_out_after_container_exits(self) -> None:
        container_id = "e" * 64
        self.podman_state.write_text(
            json.dumps(
                [container(container_id, "slow-stop", labels("hermit-old", "inv-old"))]
            )
        )
        self.write_live_state([], {})
        self.environment["FAKE_PODMAN_STOP_FAIL_AFTER_STOP"] = "1"

        result = self.reconcile()

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["reclaimed"], 1)
        self.assertEqual(
            self.podman_log.read_text().splitlines(),
            [f"stop {container_id}", f"rm {container_id}"],
        )
        self.assertEqual(json.loads(self.podman_state.read_text()), [])

    def test_task_retained_fixture_requires_explicit_transfer(self) -> None:
        container_id = "b" * 64
        self.podman_state.write_text(
            json.dumps([container(container_id, "retained", labels("hermit-old", "inv-old", "task"))])
        )
        self.write_live_state([], {})

        before = self.reconcile()

        self.assertEqual(before.returncode, 1)
        self.assertEqual(json.loads(before.stdout)["transfer_required"], 1)
        self.assertEqual(self.podman_log.read_text(), "")

        transfer = self.run_tool(
            "transfer", "retained", "--task", "fixture-task", "--lifetime", "agent"
        )
        self.assertEqual(transfer.returncode, 0, transfer.stderr + transfer.stdout)
        self.write_live_state(
            [{"name": "hermit-new", "status": "busy", "tmux_pane_id": "%9"}],
            {"hermit-new": ["inv-new"]},
        )

        after = self.reconcile()

        self.assertEqual(after.returncode, 0, after.stderr + after.stdout)
        report = json.loads(after.stdout)
        self.assertEqual(report["live"], 1)
        self.assertEqual(report["reclaimed"], 0)
        self.assertEqual(self.podman_log.read_text(), "")

    def test_live_and_unmanaged_containers_are_never_removed(self) -> None:
        self.podman_state.write_text(
            json.dumps(
                [
                    container("c" * 64, "live", labels("hermit-new", "inv-new")),
                    container("d" * 64, "legacy", None, zombies=3),
                ]
            )
        )
        self.write_live_state(
            [{"name": "hermit-new", "status": "busy", "tmux_pane_id": "%9"}],
            {"hermit-new": ["inv-new"]},
        )

        result = self.reconcile()

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["live"], 1)
        self.assertEqual(report["unmanaged"], 1)
        self.assertEqual(report["reclaimed"], 0)
        self.assertEqual(self.podman_log.read_text(), "")
        self.assertEqual(len(json.loads(self.podman_state.read_text())), 2)

    def test_quickstart_is_pure_without_podman_or_state(self) -> None:
        self.environment["AGENT_PODMAN_BIN"] = str(self.directory / "missing-podman")
        result = self.run_tool("quickstart")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("never automatic deletion targets", result.stdout)
        self.assertFalse(self.registry.exists())


if __name__ == "__main__":
    unittest.main()
