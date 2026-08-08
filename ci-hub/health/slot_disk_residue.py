#!/usr/bin/env python3
"""Ask the DISK question about worktree slots, never the AGENT question.

WHY THIS EXISTS, AND WHY IT REPLACES A LIVENESS CHECK. Huge worktree slots hang
around under `worktrees/` long after anything was working in them, and nothing
reclaims them. That is a real disk problem and it must keep being detected.

Its predecessor (`scripts/worktree_liveness.py --fail-on-new-dead`, wired as the
`worktree_new_dead_owner` tick-hub gate) detected it by asking *"is the agent that
owns this slot dead?"* -- and that is the wrong question asked of the wrong
authority. ORC owns the fleet and answers agent liveness definitively; a second
observer guessing at it from a registry file only manufactures disagreement. On
2026-08-08 that disagreement produced a false "agent died" alarm naming the agent
holding the *freshest* activity timestamp on the box. Under the coordinator's
autonomous-replacement mandate, acting on it would have destroyed a live agent
mid-P0.

The blast radii are not symmetric. A wrong answer to "is this directory idle?"
costs a directory being listed for a human to look at. A wrong answer to "is this
agent alive?" costs an agent. So this tool asks only the first, and its finding
authorizes nothing:

    a slot is RECLAIMABLE iff it exists on disk, consumes real space, and no live
    process has had its cwd inside it for a sustained period.

No agent names. No ORC fleet census. No TaskGraph owner. No registry `status`
field. Those are all somebody else's authority, and none of them is the question.

DETECT ONLY. Nothing here removes, releases, resets, cleans, or prunes anything.
A flagged slot is very often holding the only copy of somebody's uncommitted work
-- reclaiming is a coordinator decision made with a recovery SHA in hand, and this
tool deliberately gives the coordinator no mechanism to skip that step.

THREE MEASUREMENT HONESTIES, because a number without them is not evidence:

1. OCCUPANCY IS /proc cwd. It cannot be forged by editing a file and nobody can
   forget to update it. Its blind spot is a live process whose `cwd` we cannot
   read: measured on this box, 5 of 792 same-uid processes (0.6%) -- the other 96
   unreadable ones are zombies, which have no cwd and cannot be working anywhere.
   `blind_pids` is reported on every run so the occupancy count always travels
   with its own uncertainty.

2. IDLENESS IS SUSTAINED, NOT INSTANTANEOUS. A slot must read idle continuously
   for `--idle-hours` before it can qualify. This is what makes the 0.6% blind
   spot tolerable: a permission blip does not survive a day of consecutive ticks.

3. SIZE IS ACTUAL DISK BLOCKS, NOT APPARENT SIZE. Slots are seeded with
   `cp -a --reflink=auto`, so extents are shared between them. A slot reported at
   8 GiB may free far less than 8 GiB when removed, because its neighbours hold
   references to the same extents. The number is an upper bound on what reclaim
   yields, and `summary=` says so rather than implying a clean total.

PAGES ON THE DELTA, REPORTS THE STANDING SET. `--gate` exits 1 only when a slot
NEWLY becomes reclaimable, because a gate that fires every tick forever gets muted
within a day -- a failure this repo has already had. The standing backlog is
carried in the captured fields of every tick instead, so it stays visible without
being a page.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

WORKTREES_DIR = "worktrees"
DEFAULT_STATE = Path(".tick-hub") / "slot-disk-residue-state.json"

# Defaults. A slot must be BOTH sustained-idle and materially large: a 40 MiB
# idle directory is not a disk problem and paging about it is noise.
DEFAULT_IDLE_HOURS = 24.0
DEFAULT_MIN_GIB = 1.0
DEFAULT_REFRESH_HOURS = 12.0
DEFAULT_DU_TIMEOUT = 120
DEFAULT_MAX_MEASURE = 60

GIB = 1024.0 ** 3

OK = "ok"
RECLAIMABLE = "reclaimable"
UNKNOWN = "unknown"


class ResidueUnavailable(RuntimeError):
    """An input could not be read. Silence is not a clean result."""


def repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__).resolve().parent).resolve()
    for cand in [here, *here.parents]:
        if (cand / WORKTREES_DIR).is_dir():
            return cand
    raise ResidueUnavailable(f"no {WORKTREES_DIR}/ directory found above {here}")


def slots_on_disk(root: Path) -> set[str]:
    """Slot directories that actually exist and therefore actually cost space.

    The FILESYSTEM is the authority here, not `worktree-state.json`. A registry
    can claim a slot that was deleted (costing nothing) and can omit a slot that
    exists (costing plenty); only one of those two is a disk problem, and it is
    the one the registry cannot see.
    """
    wt = root / WORKTREES_DIR
    try:
        return {p.name for p in wt.iterdir() if p.is_dir() and not p.is_symlink()}
    except OSError as exc:
        raise ResidueUnavailable(f"cannot enumerate {wt}: {exc}") from exc


@dataclass
class Occupancy:
    counts: dict[str, int] = field(default_factory=dict)
    blind_pids: int = 0
    zombie_pids: int = 0
    deleted_cwd_pids: int = 0


def _proc_state(pid: str) -> str:
    """Scheduler state letter from /proc/<pid>/stat, tolerant of ')' in comm."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return "?"


def occupancy(root: Path, proc_root: Path = Path("/proc")) -> Occupancy:
    """Slots containing at least one live process, by /proc/<pid>/cwd.

    Deliberately NOT a match on process names. Name matching is how sibling
    agents get misidentified and, on this shared box, killed; a cwd is an
    unforgeable statement of where a process actually is.

    A cwd rendered by the kernel as `<path> (deleted)` COUNTS AS OCCUPANCY. The
    suffix is appended to the whole path and marks the unlinked *leaf*, which is
    usually a child worktree (`<slot>/hermit`) removed out from under a process
    that is still running there. The slot above it still exists and is still in
    use, so discarding those pids would report a busy slot as idle. A slot that
    has genuinely vanished needs no special case: it is absent from
    `slots_on_disk` and drops out on its own. Counted for visibility, never
    subtracted.
    """
    prefix = str(root / WORKTREES_DIR) + os.sep
    occ = Occupancy()
    uid = os.getuid()
    try:
        entries = [e for e in os.listdir(proc_root) if e.isdigit()]
    except OSError as exc:
        raise ResidueUnavailable(f"cannot enumerate {proc_root}: {exc}") from exc

    for pid in entries:
        try:
            cwd = os.readlink(proc_root / pid / "cwd")
        except OSError:
            # Unreadable. Distinguish the harmless majority (zombies have no cwd
            # and cannot be working in a slot) from the genuine blind spot.
            try:
                same_uid = (proc_root / pid).stat().st_uid == uid
            except OSError:
                continue
            if not same_uid:
                continue
            if _proc_state(pid) == "Z":
                occ.zombie_pids += 1
            else:
                occ.blind_pids += 1
            continue
        if cwd.endswith(" (deleted)"):
            cwd = cwd[: -len(" (deleted)")]
            if cwd.startswith(prefix):
                occ.deleted_cwd_pids += 1
        if not cwd.startswith(prefix):
            continue
        slot = cwd[len(prefix):].split(os.sep)[0]
        if slot:
            occ.counts[slot] = occ.counts.get(slot, 0) + 1
    return occ


def disk_bytes(path: Path, timeout: int = DEFAULT_DU_TIMEOUT) -> int | None:
    """Actual disk blocks used, not apparent size.

    `--block-size=1` without `--apparent-size` reports allocated blocks, which is
    what "consuming space" means. `--one-file-system` keeps a stray bind mount or
    nested mount from being attributed to the slot. Returns None -- never 0 -- if
    the measurement could not be taken, so an unmeasurable slot cannot silently
    read as empty and get flagged.
    """
    try:
        out = subprocess.run(
            ["du", "-s", "--block-size=1", "--one-file-system", str(path)],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 and not out.stdout.strip():
        return None
    try:
        return int(out.stdout.split(None, 1)[0])
    except (ValueError, IndexError):
        return None


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_ts(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass
class SlotResidue:
    slot: str
    procs: int
    idle_since: str | None
    idle_hours: float | None
    size_bytes: int | None
    reclaimable: bool
    reason: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def evaluate(
    root: Path,
    prior: dict,
    now: datetime,
    idle_hours: float,
    min_gib: float,
    refresh_hours: float,
    max_measure: int,
    du_timeout: int,
    measure: bool = True,
) -> tuple[list[SlotResidue], dict, Occupancy]:
    """Classify every on-disk slot. Pure disk + process facts, nothing else."""
    on_disk = slots_on_disk(root)
    occ = occupancy(root)
    prior_slots = prior.get("slots") if isinstance(prior.get("slots"), dict) else {}
    min_bytes = min_gib * GIB

    next_slots: dict[str, dict] = {}
    idle_names: list[str] = []
    for slot in sorted(on_disk):
        procs = occ.counts.get(slot, 0)
        rec = prior_slots.get(slot) if isinstance(prior_slots.get(slot), dict) else {}
        if procs > 0:
            # Any live process resets the clock outright. Positive evidence of use
            # always wins; the cost of a false "idle" is somebody's work listed
            # for deletion, the cost of a false "busy" is one more cycle of disk.
            next_slots[slot] = {"size_bytes": rec.get("size_bytes"),
                                "sized_at": rec.get("sized_at")}
            continue
        since = parse_ts(rec.get("idle_since")) or now
        next_slots[slot] = {
            "idle_since": fmt_ts(since),
            "size_bytes": rec.get("size_bytes"),
            "sized_at": rec.get("sized_at"),
        }
        idle_names.append(slot)

    # Measure only idle slots, and only when the cached size is missing or stale.
    # An occupied slot's size is nobody's business: it is not a reclaim candidate.
    if measure:
        stale = []
        for slot in idle_names:
            rec = next_slots[slot]
            sized_at = parse_ts(rec.get("sized_at"))
            age_h = (now - sized_at).total_seconds() / 3600.0 if sized_at else None
            if rec.get("size_bytes") is None or age_h is None or age_h >= refresh_hours:
                stale.append(slot)
        for slot in stale[:max_measure]:
            size = disk_bytes(root / WORKTREES_DIR / slot, timeout=du_timeout)
            if size is not None:
                next_slots[slot]["size_bytes"] = size
                next_slots[slot]["sized_at"] = fmt_ts(now)

    results: list[SlotResidue] = []
    for slot in sorted(on_disk):
        rec = next_slots[slot]
        procs = occ.counts.get(slot, 0)
        size = rec.get("size_bytes")
        size = size if isinstance(size, int) else None
        since = parse_ts(rec.get("idle_since"))
        age_h = (now - since).total_seconds() / 3600.0 if since else None

        if procs > 0:
            reclaimable, reason = False, f"{procs} live process(es) with cwd in slot"
        elif age_h is None or age_h < idle_hours:
            shown = 0.0 if age_h is None else age_h
            reclaimable = False
            reason = f"idle {shown:.1f}h (< {idle_hours}h threshold)"
        elif size is None:
            reclaimable, reason = False, "size could not be measured; not flagged"
        elif size < min_bytes:
            reclaimable = False
            reason = f"idle {age_h:.1f}h but only {size / GIB:.2f} GiB (< {min_gib} GiB)"
        else:
            reclaimable = True
            reason = f"idle {age_h:.1f}h, {size / GIB:.2f} GiB on disk, no process inside"

        results.append(SlotResidue(
            slot=slot, procs=procs,
            idle_since=rec.get("idle_since"),
            idle_hours=round(age_h, 2) if age_h is not None else None,
            size_bytes=size, reclaimable=reclaimable, reason=reason,
        ))

    return results, {"slots": next_slots}, occ


def summarize(
    results: list[SlotResidue], occ: Occupancy, fresh: set[str], min_gib: float
) -> tuple[str, str, dict]:
    flagged = [r for r in results if r.reclaimable]
    total = sum(r.size_bytes or 0 for r in flagged)
    fields = {
        "slots_on_disk": len(results),
        "slots_occupied": sum(1 for r in results if r.procs > 0),
        "slots_idle": sum(1 for r in results if r.procs == 0),
        "reclaimable_slots": len(flagged),
        "reclaimable_gib": round(total / GIB, 2),
        "new_reclaimable_slots": len(fresh),
        "blind_pids": occ.blind_pids,
        "zombie_pids": occ.zombie_pids,
        "deleted_cwd_pids": occ.deleted_cwd_pids,
    }
    state = RECLAIMABLE if fresh else OK
    if fresh:
        named = ", ".join(
            f"{r.slot} ({(r.size_bytes or 0) / GIB:.1f} GiB)"
            for r in flagged if r.slot in fresh
        )
        summary = (
            f"{len(fresh)} slot(s) newly idle >= threshold and >= {min_gib} GiB: {named}; "
            f"{len(flagged)} reclaimable in total holding up to {total / GIB:.1f} GiB "
            f"(upper bound: reflink-shared extents are counted per-slot). "
            f"DETECT ONLY -- no slot was touched; reclaim needs a recovery SHA."
        )
    else:
        summary = (
            f"no newly reclaimable slot; {len(flagged)} standing reclaimable slot(s) "
            f"holding up to {total / GIB:.1f} GiB of {fields['slots_on_disk']} on disk "
            f"({fields['slots_occupied']} occupied). Standing backlog, not paged."
        )
    if occ.blind_pids:
        summary += f" occupancy blind spot: {occ.blind_pids} same-uid pid(s) with unreadable cwd."
    return state, summary, fields


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", help="repo root (default: discovered from this script)")
    p.add_argument("--json", action="store_true", help="emit the complete typed report")
    p.add_argument("--gate", action="store_true",
                   help="exit 1 when a slot became reclaimable since the last run")
    p.add_argument("--idle-hours", type=float, default=DEFAULT_IDLE_HOURS)
    p.add_argument("--min-gib", type=float, default=DEFAULT_MIN_GIB)
    p.add_argument("--refresh-hours", type=float, default=DEFAULT_REFRESH_HOURS)
    p.add_argument("--max-measure", type=int, default=DEFAULT_MAX_MEASURE)
    p.add_argument("--du-timeout", type=int, default=DEFAULT_DU_TIMEOUT)
    p.add_argument("--no-measure", action="store_true",
                   help="skip du entirely and use only cached sizes")
    p.add_argument("--state", default=None, help=f"state file (default {DEFAULT_STATE})")
    p.add_argument("--dry-run", action="store_true",
                   help="do not persist state (idle clocks and baseline stay unchanged)")
    args = p.parse_args(argv)

    try:
        root = repo_root(Path(args.root) if args.root else None)
        state_path = Path(args.state) if args.state else root / DEFAULT_STATE
        prior = read_state(state_path)
        now = datetime.now(timezone.utc)
        results, next_state, occ = evaluate(
            root, prior, now,
            idle_hours=args.idle_hours, min_gib=args.min_gib,
            refresh_hours=args.refresh_hours, max_measure=args.max_measure,
            du_timeout=args.du_timeout, measure=not args.no_measure,
        )
    except ResidueUnavailable as exc:
        # Never silent: an unreadable input is `unknown`, which is not `ok`.
        print(f"state={UNKNOWN}")
        print(f"summary=slot disk residue unavailable: {exc}")
        return 2

    current = {r.slot for r in results if r.reclaimable}
    reported = prior.get("reported")
    if isinstance(reported, list):
        # Only slots that were ALREADY reclaimable are suppressed. A slot that
        # gets used again and later goes idle again must page again, so this is
        # the current set, never a cumulative union.
        fresh = current - set(reported)
    else:
        # First run adopts the baseline WITHOUT paging: the standing backlog did
        # not happen at this tick and must not be reported as if it had.
        fresh = set()
    next_state["reported"] = sorted(current)

    if not args.dry_run:
        write_state(state_path, next_state)

    state, summary, fields = summarize(results, occ, fresh, args.min_gib)

    if args.json:
        print(json.dumps({
            "state": state, "summary": summary, **fields,
            "new_reclaimable": sorted(fresh),
            "slots": [r.to_dict() for r in results],
        }, indent=2, sort_keys=True))
    else:
        print(f"state={state}")
        print(f"summary={summary}")
        for key, value in fields.items():
            print(f"{key}={value}")
        for r in results:
            if r.reclaimable or r.procs == 0:
                mark = "RECLAIMABLE" if r.reclaimable else "idle       "
                new = " NEW" if r.slot in fresh else ""
                print(f"slot={r.slot} {mark}{new} {r.reason}")

    return 1 if (args.gate and fresh) else 0


if __name__ == "__main__":
    raise SystemExit(main())
