#!/usr/bin/env python3
"""THE single writer for dev-hermit parent `main`.

WHY THIS EXISTS, MEASURED RATHER THAN ASSERTED
----------------------------------------------
Parent `main` had multiple concurrent writers and no gate at all. Agents held commit
entries while others pushed `origin/main` from clean slots. Measured 2026-08-07: local
parent `main` stood **13 ahead / 318 behind** `origin/main` with 103 dirty paths, and
`experiments/parent-main-orphan-triage_20260806/genuinely-lost-commits.tsv` records
**45 GENUINELY LOST COMMITS** — including one authored by the repository owner.

Five separate "snapshot blockers" (dirty primaries, untracked artifacts, missing cache
keys, parent HEAD divergence, liteinst2 gitlink divergence) were SYMPTOMS OF THIS ONE
UNSERIALIZED WRITE PATH, which is why fixing each one only revealed the next.

Critically, a survey found NO CALL SITES for a parent-main push anywhere in `scripts/`,
`ci-hub/`, or the `Makefile`. Agents push ad hoc with a bare
`git push origin HEAD:refs/heads/main`. There was no single path to gate, so this file
creates one.

THE THREE PROPERTIES, AND WHY EACH IS SEPARATELY NECESSARY
-----------------------------------------------------------
1. ONE WRITER. An exclusive `flock` on a machine-local lock file. Non-blocking by
   default, so a second concurrent writer is REFUSED IMMEDIATELY AND VISIBLY rather
   than queueing and racing. Serialization alone is not enough: two writers that take
   the lock in sequence can still both build on a stale base.

2. FETCHED-ORIGIN CAS. Before an ordinary shared-main commit, `HEAD` must equal the
   FRESHLY FETCHED `origin/main` tip. This is a compare-and-swap against the real
   remote, not against a cached ref: a `git fetch` runs inside the lock, so the value
   compared is observed, not remembered. Without this, the lock only serializes
   stale-base writes instead of preventing them.

3. AUDITED GITLINK/HOOK PATH. Submodule-pointer and hook commits LEGITIMATELY differ
   from ordinary commits and must not be blocked by the general gate — a guard that
   refuses everything passes a negative test and is useless. This mode keeps the lock
   and the CAS but widens the allowed path set to gitlinks plus `.gitmodules`, and
   writes an audit record for every invocation so the exception is counted, not
   invisible.

VERDICTS ARE THREE-VALUED, NOT TWO
-----------------------------------
`OK` / `REFUSED` / `ERROR` are distinct, and each carries what it examined. A refusal is
the gate working; an error is the gate failing to determine anything. Collapsing them
would reproduce, in the guard itself, the no-result-into-fail defect this workspace has
been auditing. Every verdict prints a `paths=` count so a zero is self-evident rather
than inferred.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

MAIN_REF = "refs/heads/main"
LOCK_REL = "ignored/parent-main-write.lock"
AUDIT_REL = "ignored/parent-main-write-audit.jsonl"

# Exit codes are part of the contract: a caller must be able to tell "the gate said no"
# from "the gate broke" without parsing prose.
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2

#: Paths an AUDITED gitlink/hook commit may touch. Everything else is an ordinary
#: shared-main commit and goes through the general gate.
GITLINK_ALLOWED = (".gitmodules",)
SUBMODULES = ("hermit", "reverie", "liteinst2", "agent-utils")


class Refused(Exception):
    """The gate determined the write must not proceed. This is success for the gate.

    Carries the observed values alongside the prose. A refusal that names the stale SHA
    only inside a sentence forces every consumer to parse English; the machine-readable
    fields must be populated on the refusal path too, not just on success.
    """

    def __init__(self, reason: str, *, observed: str = "", head: str = "") -> None:
        super().__init__(reason)
        self.observed = observed
        self.head = head


@dataclass
class Verdict:
    state: str  # OK | REFUSED | ERROR
    reason: str
    mode: str = ""
    observed_origin: str = ""
    head_before: str = ""
    head_after: str = ""
    paths: tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        return (
            f"VERDICT={self.state}\n"
            f"MODE={self.mode}\n"
            f"REASON={self.reason}\n"
            f"OBSERVED_ORIGIN_MAIN={self.observed_origin or '-'}\n"
            f"HEAD_BEFORE={self.head_before or '-'}\n"
            f"HEAD_AFTER={self.head_after or '-'}\n"
            f"PATHS={len(self.paths)}"
            + (f" [{' '.join(self.paths)}]" if self.paths else "")
        )

    def exit_code(self) -> int:
        return {"OK": EXIT_OK, "REFUSED": EXIT_REFUSED}.get(self.state, EXIT_ERROR)


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed rc={proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return proc.stdout.strip()


class OneWriterLock:
    """Exclusive, non-blocking by default.

    Non-blocking is deliberate. A blocking lock makes a concurrent write WAIT, which
    looks like success and hides the contention; the whole point here is that a second
    concurrent writer be REFUSED VISIBLY so the collision is counted.
    """

    def __init__(self, path: Path, *, wait_seconds: float = 0.0) -> None:
        self.path = path
        self.wait_seconds = wait_seconds
        self._fd: int | None = None

    def __enter__(self) -> "OneWriterLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    holder = ""
                    try:
                        holder = os.pread(self._fd, 200, 0).decode().strip()
                    except OSError:
                        pass
                    os.close(self._fd)
                    self._fd = None
                    raise Refused(
                        "another parent-main writer holds the lock"
                        + (f" ({holder})" if holder else "")
                        + "; refusing rather than racing it"
                    ) from None
                time.sleep(0.05)
        os.ftruncate(self._fd, 0)
        os.pwrite(self._fd, f"pid={os.getpid()} at={time.time():.0f}".encode(), 0)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def observe_origin_main(root: Path, *, remote: str, fetch: bool) -> str:
    """Fetch INSIDE the lock and return the observed tip.

    The fetch is what makes this a compare-and-swap rather than a compare-and-hope: the
    value is read from the remote now, not from a ref someone updated minutes ago.
    """
    if fetch:
        proc = subprocess.run(
            ["git", "-C", str(root), "fetch", "--quiet", remote, "main"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "could not fetch the remote; a CAS against an unobserved origin is not "
                f"a CAS: {(proc.stderr or '').strip()[:200]}"
            )
    return git(root, "rev-parse", f"{remote}/main")


def assert_cas(root: Path, observed: str, *, mode: str) -> str:
    """HEAD must be exactly the observed origin tip before an ordinary commit."""
    head = git(root, "rev-parse", "HEAD")
    if head != observed:
        behind = git(root, "rev-list", "--count", f"HEAD..{observed}", check=False)
        ahead = git(root, "rev-list", "--count", f"{observed}..HEAD", check=False)
        raise Refused(
            f"stale base: HEAD {head[:12]} != freshly fetched origin/main "
            f"{observed[:12]} (ahead={ahead or '?'} behind={behind or '?'}). "
            f"A {mode} built on a stale base is exactly how parent main accumulated "
            f"318 behind / 13 ahead and lost 45 commits. Rebase onto origin/main first.",
            observed=observed,
            head=head,
        )
    return head


def classify_paths(paths: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split requested paths into (gitlink-ish, ordinary)."""
    gitlinkish, ordinary = [], []
    for p in paths:
        norm = p.strip("/")
        if norm in GITLINK_ALLOWED or norm in SUBMODULES:
            gitlinkish.append(p)
        else:
            ordinary.append(p)
    return tuple(gitlinkish), tuple(ordinary)


def append_audit(root: Path, record: dict) -> None:
    path = root / AUDIT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def do_commit(root: Path, args: argparse.Namespace, *, gitlink_mode: bool) -> Verdict:
    mode = "gitlink" if gitlink_mode else "commit"
    observed = observe_origin_main(root, remote=args.remote, fetch=not args.no_fetch)
    head_before = assert_cas(root, observed, mode=mode)

    paths = tuple(args.path or ())
    if not paths:
        raise Refused(
            "no --path given; this gate never stages by wildcard. Name the exact paths "
            "(a `git add -A` on shared main is how unrelated work gets swept in)."
        )

    gitlinkish, ordinary = classify_paths(paths)
    if gitlink_mode and ordinary:
        raise Refused(
            f"gitlink mode may only touch submodule pointers and .gitmodules; "
            f"refused {len(ordinary)} ordinary path(s): {' '.join(ordinary)}"
        )
    if not gitlink_mode and gitlinkish:
        raise Refused(
            f"{len(gitlinkish)} submodule-pointer path(s) requested in ordinary mode: "
            f"{' '.join(gitlinkish)}. Use `gitlink` mode — it is audited, and burying a "
            f"gitlink advance in an ordinary commit is what the parent guide forbids."
        )

    git(root, "add", "--", *paths)
    staged = git(root, "diff", "--cached", "--name-only")
    staged_paths = tuple(x for x in staged.splitlines() if x.strip())
    if not staged_paths:
        raise Refused(
            "nothing staged from the named paths — refusing an empty bookkeeping commit"
        )

    git(root, "commit", "-q", "-m", args.message, "--", *paths)
    head_after = git(root, "rev-parse", "HEAD")

    if gitlink_mode:
        append_audit(
            root,
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "mode": "gitlink",
                "observed_origin_main": observed,
                "head_before": head_before,
                "head_after": head_after,
                "paths": list(staged_paths),
                "message": args.message,
                "pid": os.getpid(),
            },
        )

    return Verdict(
        state="OK",
        mode=mode,
        reason=(
            "committed on the freshly fetched origin/main tip"
            + (" (AUDITED gitlink path)" if gitlink_mode else "")
        ),
        observed_origin=observed,
        head_before=head_before,
        head_after=head_after,
        paths=staged_paths,
    )


def do_push(root: Path, args: argparse.Namespace) -> Verdict:
    observed = observe_origin_main(root, remote=args.remote, fetch=not args.no_fetch)
    head = git(root, "rev-parse", "HEAD")

    if head == observed:
        return Verdict(
            state="OK",
            mode="push",
            reason="nothing to push; HEAD already equals the observed origin/main tip",
            observed_origin=observed,
            head_before=head,
            head_after=head,
        )

    # Fast-forward-ability is the real predicate: the observed tip must be reachable
    # from HEAD. If it is not, someone else advanced main after we based on it.
    ff = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", observed, head],
        capture_output=True,
        check=False,
    )
    if ff.returncode != 0:
        raise Refused(
            f"non-fast-forward: freshly fetched origin/main {observed[:12]} is NOT an "
            f"ancestor of HEAD {head[:12]}. Another writer advanced main. Reconcile by "
            f"merging origin/main in a worktree; never force-push shared main.",
            observed=observed,
            head=head,
        )

    if args.dry_run:
        return Verdict(
            state="OK",
            mode="push",
            reason="DRY RUN: CAS satisfied and push would fast-forward",
            observed_origin=observed,
            head_before=head,
            head_after=head,
        )

    # --force-with-lease pins the CAS at the git protocol layer too, so a writer that
    # slips in between our fetch and our push is rejected by the REMOTE, not merely by
    # our own earlier check.
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "push",
            f"--force-with-lease={MAIN_REF}:{observed}",
            args.remote,
            f"HEAD:{MAIN_REF}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise Refused(
            "remote rejected the compare-and-swap push (someone advanced main between "
            f"our fetch and our push): {(proc.stderr or '').strip()[:300]}"
        )
    return Verdict(
        state="OK",
        mode="push",
        reason="pushed under lock with a fetched-origin compare-and-swap",
        observed_origin=observed,
        head_before=head,
        head_after=head,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--remote", default="origin")
    p.add_argument("--no-fetch", action="store_true", help="tests only; skips the CAS fetch")
    p.add_argument(
        "--lock-wait",
        type=float,
        default=0.0,
        help="seconds to wait for the one-writer lock (default 0 = refuse immediately)",
    )
    sub = p.add_subparsers(dest="mode", required=True)

    c = sub.add_parser("commit", help="ordinary shared-main commit (path-limited)")
    c.add_argument("--path", action="append", required=True)
    c.add_argument("--message", required=True)

    g = sub.add_parser("gitlink", help="AUDITED submodule-pointer / .gitmodules commit")
    g.add_argument("--path", action="append", required=True)
    g.add_argument("--message", required=True)

    s = sub.add_parser("push", help="push HEAD to main under lock + CAS")
    s.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root.resolve()
    verdict: Verdict
    try:
        with OneWriterLock(root / LOCK_REL, wait_seconds=args.lock_wait):
            if args.mode == "commit":
                verdict = do_commit(root, args, gitlink_mode=False)
            elif args.mode == "gitlink":
                verdict = do_commit(root, args, gitlink_mode=True)
            else:
                verdict = do_push(root, args)
    except Refused as exc:
        verdict = Verdict(
            state="REFUSED",
            mode=args.mode,
            reason=str(exc),
            observed_origin=getattr(exc, "observed", ""),
            head_before=getattr(exc, "head", ""),
        )
    except Exception as exc:  # noqa: BLE001 — an ERROR is not a REFUSED
        verdict = Verdict(
            state="ERROR", mode=args.mode, reason=f"{type(exc).__name__}: {exc}"
        )
    print(verdict.render())
    return verdict.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
