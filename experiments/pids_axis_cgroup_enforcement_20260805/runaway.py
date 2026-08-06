#!/usr/bin/env python3
"""Runaway holder: fork K children that each hold a task slot forever.

Used to prove that a breaching/runaway cgroup can actually be KILLED, not
merely denied further forks. Parent and children all block in signal.pause()
so nothing exits on its own -- any death observed is caused by the killer.
"""
import os
import signal
import sys

k = int(sys.argv[1])
for _ in range(k):
    if os.fork() == 0:
        try:
            signal.pause()
        except Exception:
            pass
        os._exit(0)
sys.stdout.write("holder-up pid=%d children=%d\n" % (os.getpid(), k))
sys.stdout.flush()
try:
    signal.pause()
except Exception:
    pass
