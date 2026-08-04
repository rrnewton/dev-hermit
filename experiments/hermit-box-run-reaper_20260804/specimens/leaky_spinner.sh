#!/bin/bash
# OUTER: launches an INNER cpu-spinner in its OWN new session (setsid), then waits on it.
# Mimics the hermit outer(session-leader, pipe-wait) + inner(supervisor, own pgid, burns core)
# two-process tree. Killing the OUTER's pgid does NOT reach the inner's separate pgid.
setsid bash -c 'while :; do :; done' &
inner=$!
echo "INNER_PID=$inner"          # so the harness can observe/clean the escapee
wait "$inner"
