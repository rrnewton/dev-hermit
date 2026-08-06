#!/usr/bin/env python3
"""Extract an ADDRESS-NORMALIZED PROCESS-EVENT TRACE from a hermit DETLOG.

WHY A PROJECTION AND NOT THE RAW LOG
------------------------------------
`hermit log-diff` over a full `--log info --detlog-stack --detlog-heap` log is
the right instrument for SELF-determinism (same backend, run1 vs run2), but it
is structurally unusable as a CROSS-backend metric: `--detlog-heap/-stack` hash
memory CONTENT, and that content holds absolute pointers, so any backend that
loads the guest at a different address diverges on every record while behaving
identically (established in ai_docs/cross-backend-detlog-parity-sweep-20260806.md).

This module computes the projection that the fork/exec/process-tree question
actually needs: the ORDERED SEQUENCE OF PROCESS-LIFECYCLE EVENTS, with every
pointer-valued operand ordinalized away and every value that IS the answer
(virtual pid / dettid / exit code / wait return) retained verbatim. Detcore
hands out virtual pids deterministically, so a pid IS a stable identity here --
normalizing it away would delete the signal.

EVENT KINDS
  clone-in / clone-out      process (or thread) creation, with the returned dettid
  fork-in / fork-out        ditto for fork/vfork
  exec-in / exec-out        execve/execveat
  wait-in / wait-out        wait4/waitid, with the reaped pid or errno
  exit                      exit / exit_group, with the code
  commit                    scheduler COMMIT turn: the resource scenario only
                            (ParentContinue / ChildStart / Exit / ...), which is
                            where the parent<->child handoff ORDER is decided
  kill                      logically_kill (scheduler forgetting a dettid)
  tree                      the final thread-tree shape from the run report

USAGE
  pevents.py LOG              -> print the normalized trace, one event per line
  pevents.py A B              -> diff two traces; exit 0 if equal, 1 if not
"""
import re
import sys

# Syscalls that create, replace, reap, or destroy a process/thread. This set is
# the definition of "process event" used by the sweep; widen it here, not at the
# call sites, so every consumer agrees on the boundary.
PROC_SYSCALLS = {
    "clone", "clone3", "fork", "vfork",
    "execve", "execveat",
    "wait4", "waitid", "waitpid",
    "exit", "exit_group",
    "set_tid_address", "getpid", "getppid", "gettid",
}
# getpid/getppid/gettid are *observations* of process identity: if the tree is
# built differently the guest sees different numbers, so they are part of the
# externally-visible process-event surface even though they create nothing.

RE_SYS_IN = re.compile(
    r"DETLOG \[syscall\]\[detcore, dtid (\d+)\] inbound syscall: (\w+)\((.*)\) = \?"
)
RE_SYS_OUT = re.compile(
    r"DETLOG \[syscall\]\[detcore, dtid (\d+)\] finish syscall #(\d+): (\w+)\((.*)\) = (.*)$"
)
RE_COMMIT = re.compile(r"COMMIT turn (\d+), dettid (\d+) using resources \{(.*)\}: (\w+)\}?")
RE_KILL = re.compile(r"logically_kill: Scheduler removing all knowledge of \[det\]tid (\d+) in pid (\d+)")
RE_TREE = re.compile(r"Final thread-tree was: (.*)$")
RE_GROUPS = re.compile(r"There were (\d+) group leaders of (\d+) thread\(s\) total")
RE_NEWTHREAD = re.compile(r"\[detcore, dtid (\d+)\] New thread given go-ahead")
RE_POSTEXEC = re.compile(r"DETLOG \[post_exec, dtid (\d+)\]")

RE_PTR = re.compile(r"0x[0-9a-fA-F]{4,}")
RE_QUOTED_LIBPATH = re.compile(r'"[^"]*"')


def norm_args(s: str, keep_paths: bool) -> str:
    """Strip pointer literals. Keep string operands only when they are part of
    the answer (exec target), because dynamic-loader probe paths differ by
    LD_LIBRARY_PATH shape and are not process-tree structure."""
    s = RE_PTR.sub("PTR", s)
    if not keep_paths:
        s = RE_QUOTED_LIBPATH.sub('"STR"', s)
    return s


def norm_ret(s: str) -> str:
    return RE_PTR.sub("PTR", s.strip())


def extract(path):
    events = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = RE_SYS_OUT.search(line)
            if m:
                dtid, _ord, name, args, ret = m.groups()
                if name in PROC_SYSCALLS:
                    keep = name.startswith("exec")
                    events.append(
                        f"sys-out dtid={dtid} {name}({norm_args(args, keep)}) = {norm_ret(ret)}"
                    )
                continue
            m = RE_SYS_IN.search(line)
            if m:
                dtid, name, args = m.groups()
                if name in PROC_SYSCALLS:
                    keep = name.startswith("exec")
                    events.append(f"sys-in  dtid={dtid} {name}({norm_args(args, keep)})")
                continue
            m = RE_COMMIT.search(line)
            if m:
                turn, dettid, res, mode = m.groups()
                # Keep only process-structural scenarios; MemAddrSpace / Path /
                # FileMetadata commits are ordinary data resources and would swamp
                # the trace with non-process traffic.
                if any(k in res for k in ("ParentContinue", "ChildStart", "Exit {", "Exit(")):
                    events.append(f"commit  turn={turn} dettid={dettid} {{{res}}}:{mode}")
                continue
            m = RE_NEWTHREAD.search(line)
            if m:
                events.append(f"newthr  dtid={m.group(1)}")
                continue
            m = RE_POSTEXEC.search(line)
            if m:
                events.append(f"postexec dtid={m.group(1)}")
                continue
            m = RE_KILL.search(line)
            if m:
                events.append(f"kill    dtid={m.group(1)} pid={m.group(2)}")
                continue
            m = RE_TREE.search(line)
            if m:
                events.append(f"tree    {m.group(1).strip()}")
                continue
            m = RE_GROUPS.search(line)
            if m:
                events.append(f"groups  leaders={m.group(1)} threads={m.group(2)}")
                continue
    return events


def main():
    if len(sys.argv) == 2:
        for e in extract(sys.argv[1]):
            print(e)
        return 0
    if len(sys.argv) == 3:
        a = extract(sys.argv[1])
        b = extract(sys.argv[2])
        if a == b:
            print(f"EQUAL {len(a)} process-events")
            return 0
        print(f"DIFFER len {len(a)} vs {len(b)}")
        import difflib

        n = 0
        for line in difflib.unified_diff(a, b, "A", "B", lineterm="", n=2):
            print(line)
            n += 1
            if n > 80:
                print("... (truncated)")
                break
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
