#!/usr/bin/env python3
"""Record and query exact ownership of a Hermit run domain.

This module is deliberately separate from process selection.  A process name,
PPID, age, CPU use, command line, or agent-shaped cgroup is diagnostic evidence;
none proves which agent, slot, and task launched it.  A receipt does.

There are two receipt sources:

* ``record-live`` records a newly-created, unique child cgroup while its exact
  owner process is alive.  It refuses a child in the owner's shared cgroup.
* ``attest-existing`` is the explicit migration path for pre-receipt orphans.
  It requires the cgroup's embedded launcher PID to be absent, an exact
  acknowledgement, and an explicit finite PID set.  Only the captured
  ``(boot_id, pid, start_ticks, cgroup)`` identities are covered.

Querying always dereferences the receipt against current ``/proc`` and cgroup
state.  Missing, malformed, duplicate, stale, or mismatched evidence is
``unproven``.  It is never silently interpreted as an orphan.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Iterable


SCHEMA = "hermit-run-provenance/v1"
SOURCE_LIVE = "live-domain"
SOURCE_ATTESTED = "coordinator-attested-existing"
LIVE_OWNER = "live_owner"
PROVEN_ORPHAN = "proven_orphan"
UNPROVEN = "unproven"
ATTEST_CONFIRMATION = "attest-existing-orphan-domain"


class ProvenanceError(RuntimeError):
    """A receipt could not be safely created or interpreted."""


class ProcessMissing(ProvenanceError):
    """The exact process is absent."""


@dataclasses.dataclass(frozen=True)
class ProcessIdentity:
    boot_id: str
    pid: int
    start_ticks: int
    cgroup: str


@dataclasses.dataclass(frozen=True)
class OwnerIdentity:
    boot_id: str
    pid: int
    start_ticks: int | None
    cgroup: str


@dataclasses.dataclass(frozen=True)
class DomainIdentity:
    boot_id: str
    cgroup: str
    inode: int


@dataclasses.dataclass(frozen=True)
class Receipt:
    schema: str
    receipt_id: str
    source: str
    created_at: str
    agent: str
    slot: str
    task: str
    invocation_id: str | None
    domain: DomainIdentity
    owner: OwnerIdentity
    seed_process: ProcessIdentity
    attested_members: tuple[ProcessIdentity, ...]
    attested_by: str | None


@dataclasses.dataclass(frozen=True)
class QueryResult:
    classification: str
    reason: str
    process: ProcessIdentity | None = None
    receipt: Receipt | None = None


def default_receipt_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime else Path(f"/tmp/dev-hermit-{os.getuid()}-runtime")
    return base / "dev-hermit" / "hermit-run-provenance"


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except (FileNotFoundError, ProcessLookupError) as exc:
        raise ProcessMissing(f"missing {path}") from exc
    except OSError as exc:
        raise ProvenanceError(f"cannot read {path}: {exc}") from exc


def _boot_id(proc_root: Path) -> str:
    value = _read_text(proc_root / "sys/kernel/random/boot_id").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]+", value):
        raise ProvenanceError("invalid boot_id")
    return value


def _start_ticks(raw: str) -> int:
    right = raw.rfind(")")
    if right < 0:
        raise ProvenanceError("malformed /proc stat: missing command terminator")
    fields = raw[right + 2 :].split()
    if len(fields) < 20:
        raise ProvenanceError("malformed /proc stat: too few fields")
    try:
        value = int(fields[19])
    except (ValueError, IndexError) as exc:
        raise ProvenanceError("malformed process start_ticks") from exc
    if value < 0:
        raise ProvenanceError("negative process start_ticks")
    return value


def _cgroup(raw: str) -> str:
    values = [line.split("::", 1)[1] for line in raw.splitlines() if line.startswith("0::")]
    if len(values) != 1:
        raise ProvenanceError("missing or ambiguous unified cgroup")
    value = values[0]
    if not _normalized_cgroup(value):
        raise ProvenanceError(f"invalid unified cgroup {value!r}")
    return value


def _normalized_cgroup(value: str) -> bool:
    if not value.startswith("/") or value == "/" or "\0" in value:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/")[1:])


def read_process_identity(proc_root: Path, pid: int) -> ProcessIdentity:
    if pid <= 0:
        raise ProvenanceError("pid must be positive")
    base = proc_root / str(pid)
    return ProcessIdentity(
        boot_id=_boot_id(proc_root),
        pid=pid,
        start_ticks=_start_ticks(_read_text(base / "stat")),
        cgroup=_cgroup(_read_text(base / "cgroup")),
    )


def _domain(identity: ProcessIdentity, cgroup_root: Path) -> DomainIdentity:
    path = cgroup_root / identity.cgroup.lstrip("/")
    try:
        inode = path.stat().st_ino
    except FileNotFoundError as exc:
        raise ProvenanceError(f"cgroup domain is absent: {identity.cgroup}") from exc
    except OSError as exc:
        raise ProvenanceError(f"cannot stat cgroup domain {identity.cgroup}: {exc}") from exc
    return DomainIdentity(identity.boot_id, identity.cgroup, inode)


def _field(label: str, value: str) -> str:
    value = value.strip()
    if not value or len(value) > 512 or any(ord(char) < 32 for char in value):
        raise ProvenanceError(f"invalid {label}")
    return value


def _identity_dict(identity: ProcessIdentity | OwnerIdentity) -> dict[str, Any]:
    return dataclasses.asdict(identity)


def _receipt_digest(payload: dict[str, Any]) -> str:
    stable = dict(payload)
    stable.pop("created_at", None)
    stable.pop("receipt_id", None)
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _receipt_payload(
    *,
    source: str,
    agent: str,
    slot: str,
    task: str,
    invocation_id: str | None,
    domain: DomainIdentity,
    owner: OwnerIdentity,
    seed_process: ProcessIdentity,
    attested_members: Iterable[ProcessIdentity] = (),
    attested_by: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source": source,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "agent": _field("agent", agent),
        "slot": _field("slot", slot),
        "task": _field("task", task),
        "invocation_id": _field("invocation_id", invocation_id) if invocation_id else None,
        "domain": dataclasses.asdict(domain),
        "owner": _identity_dict(owner),
        "seed_process": _identity_dict(seed_process),
        "attested_members": [
            _identity_dict(item)
            for item in sorted(attested_members, key=lambda item: (item.pid, item.start_ticks))
        ],
        "attested_by": _field("attested_by", attested_by) if attested_by else None,
    }
    payload["receipt_id"] = _receipt_digest(payload)
    return payload


def _write_receipt(receipt_dir: Path, payload: dict[str, Any]) -> Path:
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        receipt_dir.chmod(0o700)
    except OSError as exc:
        raise ProvenanceError(f"cannot protect receipt directory: {exc}") from exc
    body = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    path = receipt_dir / f"{payload['receipt_id']}.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ProvenanceError(f"existing receipt is unreadable: {path}: {exc}") from exc
        comparison = dict(existing)
        candidate = dict(payload)
        comparison.pop("created_at", None)
        candidate.pop("created_at", None)
        if comparison != candidate:
            raise ProvenanceError(f"receipt id collision at {path}")
        return path

    descriptor, temporary_name = tempfile.mkstemp(prefix=".receipt-", dir=receipt_dir)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ProvenanceError(f"receipt appeared concurrently: {path}") from exc
        directory_fd = os.open(receipt_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def record_live_domain(
    *,
    pid: int,
    owner_pid: int,
    agent: str,
    slot: str,
    task: str,
    invocation_id: str | None,
    receipt_dir: Path,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> Path:
    process = read_process_identity(proc_root, pid)
    owner_process = read_process_identity(proc_root, owner_pid)
    if process.boot_id != owner_process.boot_id:
        raise ProvenanceError("process and owner are from different boots")
    if process.cgroup == owner_process.cgroup:
        raise ProvenanceError(
            "process shares the owner's cgroup; a task receipt requires a unique child domain"
        )
    domain = _domain(process, cgroup_root)
    payload = _receipt_payload(
        source=SOURCE_LIVE,
        agent=agent,
        slot=slot,
        task=task,
        invocation_id=invocation_id,
        domain=domain,
        owner=OwnerIdentity(
            owner_process.boot_id,
            owner_process.pid,
            owner_process.start_ticks,
            owner_process.cgroup,
        ),
        seed_process=process,
    )
    return _write_receipt(receipt_dir, payload)


def _embedded_launcher(cgroup: str) -> int | None:
    match = re.search(r"(?:^|/)3pai_sandbox\.slice/run-p([0-9]+)(?:-i[^/]+)?\.scope(?:/|$)", cgroup)
    return int(match.group(1)) if match else None


def attest_existing_orphan_domain(
    *,
    pids: Iterable[int],
    agent: str,
    slot: str,
    task: str,
    invocation_id: str | None,
    attested_by: str,
    confirmation: str,
    receipt_dir: Path,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> Path:
    if confirmation != ATTEST_CONFIRMATION:
        raise ProvenanceError(
            f"attest-existing requires confirmation {ATTEST_CONFIRMATION!r}"
        )
    identities = [read_process_identity(proc_root, pid) for pid in sorted(set(pids))]
    if not identities:
        raise ProvenanceError("attest-existing requires at least one explicit pid")
    domains = {(item.boot_id, item.cgroup) for item in identities}
    if len(domains) != 1:
        raise ProvenanceError("attested processes do not share one boot and cgroup")
    seed = identities[0]
    launcher_pid = _embedded_launcher(seed.cgroup)
    if launcher_pid is None:
        raise ProvenanceError("attested cgroup has no embedded 3pai launcher pid")
    try:
        read_process_identity(proc_root, launcher_pid)
    except ProcessMissing:
        pass
    else:
        raise ProvenanceError(f"launcher pid {launcher_pid} is still present")
    domain = _domain(seed, cgroup_root)
    payload = _receipt_payload(
        source=SOURCE_ATTESTED,
        agent=agent,
        slot=slot,
        task=task,
        invocation_id=invocation_id,
        domain=domain,
        owner=OwnerIdentity(seed.boot_id, launcher_pid, None, seed.cgroup),
        seed_process=seed,
        attested_members=identities,
        attested_by=attested_by,
    )
    return _write_receipt(receipt_dir, payload)


def _parse_process(value: Any, *, owner: bool = False) -> ProcessIdentity | OwnerIdentity:
    if not isinstance(value, dict):
        raise ProvenanceError("identity is not an object")
    expected = {"boot_id", "pid", "start_ticks", "cgroup"}
    if set(value) != expected:
        raise ProvenanceError("identity has unexpected fields")
    boot = value["boot_id"]
    pid = value["pid"]
    start = value["start_ticks"]
    cgroup = value["cgroup"]
    if not isinstance(boot, str) or not re.fullmatch(r"[A-Za-z0-9-]+", boot):
        raise ProvenanceError("identity boot_id is invalid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ProvenanceError("identity pid is invalid")
    if owner and start is None:
        pass
    elif not isinstance(start, int) or isinstance(start, bool) or start < 0:
        raise ProvenanceError("identity start_ticks is invalid")
    if not isinstance(cgroup, str) or not _normalized_cgroup(cgroup):
        raise ProvenanceError("identity cgroup is invalid")
    if owner:
        return OwnerIdentity(boot, pid, start, cgroup)
    assert isinstance(start, int)
    return ProcessIdentity(boot, pid, start, cgroup)


def _parse_receipt(value: Any) -> Receipt:
    if not isinstance(value, dict):
        raise ProvenanceError("receipt is not an object")
    required = {
        "schema",
        "receipt_id",
        "source",
        "created_at",
        "agent",
        "slot",
        "task",
        "invocation_id",
        "domain",
        "owner",
        "seed_process",
        "attested_members",
        "attested_by",
    }
    if set(value) != required:
        raise ProvenanceError("receipt has unexpected fields")
    if value["schema"] != SCHEMA:
        raise ProvenanceError("unsupported receipt schema")
    if value["source"] not in {SOURCE_LIVE, SOURCE_ATTESTED}:
        raise ProvenanceError("unsupported receipt source")
    for label in ("receipt_id", "created_at", "agent", "slot", "task"):
        if not isinstance(value[label], str):
            raise ProvenanceError(f"receipt {label} is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", value["receipt_id"]):
        raise ProvenanceError("receipt_id is invalid")
    for label in ("created_at", "agent", "slot", "task"):
        _field(label, value[label])
    invocation = value["invocation_id"]
    if invocation is not None and not isinstance(invocation, str):
        raise ProvenanceError("invocation_id is invalid")
    if invocation is not None:
        _field("invocation_id", invocation)
    domain_value = value["domain"]
    if not isinstance(domain_value, dict) or set(domain_value) != {"boot_id", "cgroup", "inode"}:
        raise ProvenanceError("domain is invalid")
    if (
        not isinstance(domain_value["boot_id"], str)
        or not isinstance(domain_value["cgroup"], str)
        or not _normalized_cgroup(domain_value["cgroup"])
        or not isinstance(domain_value["inode"], int)
        or isinstance(domain_value["inode"], bool)
        or domain_value["inode"] <= 0
    ):
        raise ProvenanceError("domain fields are invalid")
    owner_identity = _parse_process(value["owner"], owner=True)
    seed = _parse_process(value["seed_process"])
    members_value = value["attested_members"]
    if not isinstance(members_value, list):
        raise ProvenanceError("attested_members is not an array")
    members = tuple(_parse_process(item) for item in members_value)
    assert isinstance(owner_identity, OwnerIdentity)
    assert isinstance(seed, ProcessIdentity)
    attested_by = value["attested_by"]
    if attested_by is not None and not isinstance(attested_by, str):
        raise ProvenanceError("attested_by is invalid")
    if attested_by is not None:
        _field("attested_by", attested_by)
    if value["source"] == SOURCE_LIVE:
        if owner_identity.start_ticks is None or members or attested_by is not None:
            raise ProvenanceError("live-domain receipt has attestation-only fields")
    else:
        if owner_identity.start_ticks is not None or not members or not attested_by:
            raise ProvenanceError("attested receipt lacks finite member or attester binding")
    domain = DomainIdentity(**domain_value)
    if seed.boot_id != domain.boot_id or seed.cgroup != domain.cgroup:
        raise ProvenanceError("seed process is not bound to the receipt domain")
    if owner_identity.boot_id != domain.boot_id:
        raise ProvenanceError("owner and domain boot_id differ")
    if value["source"] == SOURCE_LIVE and owner_identity.cgroup == domain.cgroup:
        raise ProvenanceError("live receipt does not have a unique child domain")
    if value["source"] == SOURCE_ATTESTED:
        if owner_identity.cgroup != domain.cgroup:
            raise ProvenanceError("attested owner cgroup differs from its domain")
        if _embedded_launcher(domain.cgroup) != owner_identity.pid:
            raise ProvenanceError("attested owner pid does not match the cgroup launcher")
        if any(
            member.boot_id != domain.boot_id or member.cgroup != domain.cgroup
            for member in members
        ):
            raise ProvenanceError("attested member is outside the receipt domain")
    if value["receipt_id"] != _receipt_digest(value):
        raise ProvenanceError("receipt digest does not match its content")
    return Receipt(
        schema=value["schema"],
        receipt_id=value["receipt_id"],
        source=value["source"],
        created_at=value["created_at"],
        agent=value["agent"],
        slot=value["slot"],
        task=value["task"],
        invocation_id=invocation,
        domain=domain,
        owner=owner_identity,
        seed_process=seed,
        attested_members=members,
        attested_by=attested_by,
    )


def _load_receipts(receipt_dir: Path) -> tuple[list[Receipt], str | None]:
    try:
        directory_stat = receipt_dir.lstat()
    except FileNotFoundError:
        return [], "receipt-directory-missing"
    except OSError as exc:
        return [], f"receipt-directory-unreadable:{exc}"
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.getuid()
        or stat.S_IMODE(directory_stat.st_mode) & 0o077
    ):
        return [], "receipt-directory-is-not-private-and-owned"
    receipts: list[Receipt] = []
    try:
        paths = sorted(receipt_dir.glob("*.json"))
    except OSError as exc:
        return [], f"receipt-directory-unreadable:{exc}"
    for path in paths:
        try:
            path_stat = path.lstat()
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or path_stat.st_uid != os.getuid()
                or stat.S_IMODE(path_stat.st_mode) & 0o077
                or path_stat.st_nlink != 1
            ):
                raise ProvenanceError("receipt is not private, owned, regular, and single-linked")
            receipts.append(_parse_receipt(json.loads(path.read_text())))
        except (OSError, json.JSONDecodeError, ProvenanceError) as exc:
            return [], f"malformed-receipt:{path.name}:{exc}"
    return receipts, None


def query(
    *,
    pid: int,
    receipt_dir: Path,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> QueryResult:
    try:
        process = read_process_identity(proc_root, pid)
        current_domain = _domain(process, cgroup_root)
    except ProvenanceError as exc:
        return QueryResult(UNPROVEN, f"process-or-domain-unreadable:{exc}")
    receipts, error = _load_receipts(receipt_dir)
    if error:
        return QueryResult(UNPROVEN, error, process=process)
    matches = [receipt for receipt in receipts if receipt.domain == current_domain]
    if not matches:
        return QueryResult(UNPROVEN, "no-matching-receipt", process=process)
    if len(matches) != 1:
        return QueryResult(UNPROVEN, "duplicate-domain-receipts", process=process)
    receipt = matches[0]
    if receipt.source == SOURCE_ATTESTED and process not in receipt.attested_members:
        return QueryResult(
            UNPROVEN,
            "process-not-in-attested-member-set",
            process=process,
            receipt=receipt,
        )
    try:
        owner_now = read_process_identity(proc_root, receipt.owner.pid)
    except ProcessMissing:
        return QueryResult(
            PROVEN_ORPHAN,
            "exact-owner-absent",
            process=process,
            receipt=receipt,
        )
    except ProvenanceError as exc:
        return QueryResult(
            UNPROVEN,
            f"owner-unreadable:{exc}",
            process=process,
            receipt=receipt,
        )
    if receipt.owner.start_ticks is None:
        return QueryResult(
            UNPROVEN,
            "attested-owner-pid-is-present",
            process=process,
            receipt=receipt,
        )
    expected = ProcessIdentity(
        receipt.owner.boot_id,
        receipt.owner.pid,
        receipt.owner.start_ticks,
        receipt.owner.cgroup,
    )
    if owner_now != expected:
        return QueryResult(
            UNPROVEN,
            "owner-identity-mismatch",
            process=process,
            receipt=receipt,
        )
    return QueryResult(
        LIVE_OWNER,
        "exact-owner-live",
        process=process,
        receipt=receipt,
    )


def _result_dict(result: QueryResult) -> dict[str, Any]:
    return {
        "classification": result.classification,
        "reason": result.reason,
        "process": dataclasses.asdict(result.process) if result.process else None,
        "receipt": dataclasses.asdict(result.receipt) if result.receipt else None,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-dir", type=Path, default=default_receipt_dir())
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"), help=argparse.SUPPRESS)
    parser.add_argument(
        "--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"), help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    live = subparsers.add_parser("record-live", help="record a unique child domain")
    live.add_argument("--pid", type=int, required=True)
    live.add_argument("--owner-pid", type=int, required=True)
    live.add_argument("--agent", required=True)
    live.add_argument("--slot", required=True)
    live.add_argument("--task", required=True)
    live.add_argument("--invocation-id")

    attest = subparsers.add_parser(
        "attest-existing", help="explicitly bind a finite pre-receipt orphan PID set"
    )
    attest.add_argument("--pid", type=int, action="append", required=True)
    attest.add_argument("--agent", required=True)
    attest.add_argument("--slot", required=True)
    attest.add_argument("--task", required=True)
    attest.add_argument("--invocation-id")
    attest.add_argument("--attested-by", required=True)
    attest.add_argument("--confirm", required=True)

    inspect = subparsers.add_parser("query", help="query one exact PID")
    inspect.add_argument("--pid", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "record-live":
            path = record_live_domain(
                pid=args.pid,
                owner_pid=args.owner_pid,
                agent=args.agent,
                slot=args.slot,
                task=args.task,
                invocation_id=args.invocation_id,
                receipt_dir=args.receipt_dir,
                proc_root=args.proc_root,
                cgroup_root=args.cgroup_root,
            )
            print(json.dumps({"recorded": str(path)}, sort_keys=True))
            return 0
        if args.command == "attest-existing":
            path = attest_existing_orphan_domain(
                pids=args.pid,
                agent=args.agent,
                slot=args.slot,
                task=args.task,
                invocation_id=args.invocation_id,
                attested_by=args.attested_by,
                confirmation=args.confirm,
                receipt_dir=args.receipt_dir,
                proc_root=args.proc_root,
                cgroup_root=args.cgroup_root,
            )
            print(json.dumps({"recorded": str(path)}, sort_keys=True))
            return 0
        result = query(
            pid=args.pid,
            receipt_dir=args.receipt_dir,
            proc_root=args.proc_root,
            cgroup_root=args.cgroup_root,
        )
        print(json.dumps(_result_dict(result), sort_keys=True))
        return 0 if result.classification != UNPROVEN else 3
    except ProvenanceError as exc:
        print(f"hermit-run-provenance: REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
