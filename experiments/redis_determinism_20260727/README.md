# Redis deterministic under hermit --strict --verify

**Date:** 2026-07-27 · **Task:** `frontier-app-redis-determinism` · **Agent:** hermit-274
**Hermit:** `8568173b` (`codex/main-ci-status-script`, release binary built 09:56) · **Backend:** ptrace
**Redis:** `redis-server v6.2.22` (jemalloc-5.1.0), stock `/usr/bin/redis-server`

## Question

Can a real Redis **server + client**, with a **client-server TCP network inside the
container**, run deterministically under `hermit run --strict --verify`?

## Method

Redis is a long-running server, but `--verify` needs a clean-exiting guest, so the
workload is a self-terminating shell script that runs the whole client-server session
inside one hermit invocation:

1. `redis-server` on TCP loopback `127.0.0.1:6399`, foreground, persistence disabled
   (`--save '' --appendonly no`), rundir in the (private) `/tmp`.
2. Poll `redis-cli ping` until `PONG` (readiness; deterministic under hermit's
   scheduler + virtual time).
3. `redis-cli` issues a battery of commands (see workloads).
4. `redis-cli shutdown nosave`, `wait` the server, exit.

Two workloads: `redis_test.sh` (basic) and `redis_test_full.sh` (expanded — many
data types plus `INFO`, which surfaces entropy/host-state fields like `run_id`,
`process_id`, `uptime_in_seconds`).

## Results — deterministic, L2 (→ effectively L4)

| Check | Result |
| --- | --- |
| L1 `--strict` (both workloads) | **PASS**, exit 0 |
| L2 `--strict --verify` basic | **PASS** — "Determinism verified"; 66905 DETLOG+COMMIT msgs identical run1 vs run2 |
| L2 `--strict --verify` expanded (incl. `INFO`) | **PASS** — "no substantive differences found" |
| L4 stress: 5× `--strict --verify` | **5/5 verified** |
| Guest stdout across 3 independent `--strict` runs | **byte-identical** (md5 `ab8735d5c2195d115b6fdef5e3cf5349`) |
| redis `run_id` (40-hex RNG) across 3 independent runs | **identical** `13735d9863f6007b1b1f0ac329e6f0c3085d1d1d` |

Observed virtualization making Redis deterministic (no code change needed):

- **PID** → `9` (redis banner + `INFO process_id`).
- **Wall time** → hermit epoch (`31 Dec 2021`); `INFO uptime_in_seconds` → virtual.
- **Entropy** → redis's `run_id` (from redis's RNG seeded via `getrandom`) is
  byte-identical across runs, i.e. `getrandom` is determinized.
- **Scheduling** → the client/server socket rendezvous over TCP loopback is
  serialized deterministically; the full syscall trace matches across runs.

**Redis is deterministic under hermit's *existing* virtualization** — no detcore
change was required. The determinism engine already covers the sources Redis
touches (getrandom / PID / time / scheduling / loopback sockets).

## The one gotcha (harness, not determinism)

hermit gives the guest a **private `/tmp`**, so a guest script placed in the host
`/tmp` is invisible inside the container (`openat(... redis_test.sh) = ENOENT`,
exit 127). Fix: keep the script **outside** `/tmp` (here, in `scratch/`); the
private `/tmp` is in fact ideal for redis's rundir (clean per run → deterministic).
Separately, `hermit --log-file <path>` panics in the container clone callback
("Failed to open log file"); use `2> stderr` redirection for the trace instead
(the guest's own output goes to stdout, so the streams stay separate).

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/274/hermit
SC=<dir-outside-/tmp>            # e.g. ~/work/dev-hermit/scratch/redis-det-274
cp redis_test_full.sh "$SC"/
./target/release/hermit --log warn run --strict --verify -- /bin/sh "$SC/redis_test_full.sh"
#  => ":: Success: deterministic. Determinism verified."
```

## Files

- `redis_test.sh` — basic client-server workload.
- `redis_test_full.sh` — expanded workload (data types + `INFO` entropy probe).
- `basic_output.txt` — deterministic guest stdout of the basic workload.
- `full_verify.log` — hermit `--verify` verdict tail for the expanded workload.
- `metadata.json` — SHAs, host facts, exact results.
