# DBI long-running microbenchmarks (2026-07-22)

This recovered experiment compares native execution, Hermit's DynamoRIO DBI
backend, and Hermit's ptrace backend on small CPU-bound programs. The host was
under unusually high load, so the measurements establish feasibility rather
than stable performance ratios.

The original textual output is in `bench.out`. Rebuild the omitted binaries
from source before rerunning:

```bash
for source in loop3s.c loop10s.c mixed.c loop_sm.c; do
  cc -O2 "$source" -o "${source%.c}"
done
```

Set `HERMIT_DBI_CLIENT` to the built Reverie DBI client. `DYNAMORIO_HOME`,
`HERMIT_DRRUN`, `HDBI`, and `HPT` can be overridden when their defaults do not
match the local checkout. Then run `./bench.sh` from this directory.

Original observations under the loaded host:

- DBI completed the 3-second loops in about 62-64 seconds.
- ptrace crashed on the shorter loops and exceeded 400 seconds on `loop10s`.
- DBI client stderr reported zero instruction and branch counts.

The transient source worktrees and generated build outputs were not retained.
