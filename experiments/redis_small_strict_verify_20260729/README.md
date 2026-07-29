# Redis small workload: bitwise-deterministic under hermit --strict --verify (twice)

**Date:** 2026-07-29 · **Task:** `compat-deep-app-redis-small` · **Agent:** hermit-227
**Hermit:** release binary built 2026-07-29 07:37 (md5 `cc0ac0ea4266553c43a7139cc333e86f`);
primary `hermit/` HEAD observed **`103657d4`** at run time (main advanced to
`eb76b3a0` afterward by concurrent activity; the binary was unchanged throughout).
**Backend:** ptrace · **Redis:** `redis-server v6.2.22` build `5d43f02b8d896191`
(jemalloc-5.1.0), `redis-6.2.22-2.el9` · **Host:** devbig014.atn7.facebook.com (nproc 316)

## Question

Does a real Redis **server + small client workload** — with a client/server TCP
loopback network inside the container and redis's epoll event loop + `serverCron`
timer — run **bitwise-deterministically** under `hermit run --strict --verify`,
and does that hold **across two fully independent hermit invocations**?

This is the "deep app, small workload" compatibility probe. It differs from the
earlier `redis_determinism_20260727` baseline (which validated the internal
`--verify` on a larger workload) by explicitly running the **complete
`hermit --strict --verify` command twice** through the flood-safe wrapper
`scripts/detached-verify.rs verify-twice` and comparing the two invocations'
combined output **byte-for-byte** — a cross-invocation determinism check, not
only hermit's single-invocation internal DETLOG comparison.

## Method

The workload (`redis_small.sh`, run as `/bin/sh redis_small.sh`) is a small,
self-terminating client/server session inside one hermit invocation:

1. `redis-server` on TCP loopback `127.0.0.1:6399`, foreground, persistence
   disabled (`--save '' --appendonly no`), rundir in the guest's private `/tmp`.
2. Poll `redis-cli ping` until `PONG` (deterministic readiness).
3. Small client workload: `SET`/`GET`/`INCR`/`INCRBY`.
4. A **small pipeline** via `redis-cli --pipe`
   (`SET p:a/p:b/p:c`, `INCR`, `APPEND`, `MGET` — 6 commands in one batch),
   which exercises epoll readiness batching rather than one round-trip per command.
5. `redis-cli shutdown nosave`, `wait` the server, exit 0.

The script lives **outside `/tmp`** (`scratch/redis-small-227/`, copied here):
hermit gives the guest a private `/tmp`, so a script under the host `/tmp` is
invisible in the container (`openat -> ENOENT`, exit 127). The private `/tmp` is
ideal for redis's rundir (clean per run). `--verify` on this hermit build
requires `--log=info` (rejects `--log=warn`); the wrapper detaches that verbose
DETLOG stream to log files, so nothing floods the agent stream.

Two runs via the wrapper:

```bash
SCRIPT="$PWD/scratch/redis-small-227/redis_small.sh"

# (A) L2 internal verify, run twice, cross-invocation byte compare:
scripts/detached-verify.rs verify-twice --name redis-small -- \
  hermit/target/release/hermit --log info run --strict --verify -- /bin/sh "$SCRIPT"

# (B) L1 strict, run twice, guest-stdout byte compare (guest runs once each):
scripts/detached-verify.rs verify-twice --name redis-small-strict -- \
  hermit/target/release/hermit --log warn run --strict -- /bin/sh "$SCRIPT"
```

## Results — deterministic, L2 (PASS), no code change

| Check | Result |
| --- | --- |
| (A) `--strict --verify`, invocation 1 | exit 0 — "Success: deterministic. Determinism verified." |
| (A) `--strict --verify`, invocation 2 | exit 0 — identical verdict |
| (A) hermit internal DETLOG compare (per invocation) | 88671 total / 77303 detcore / 64103 DETLOG+COMMIT messages, run1==run2, "no substantive differences found" |
| (A) **cross-invocation** raw byte compare | divergent at **one line only** — hermit's random `/tmp/runN_log_*` temp filenames (line 3) |
| (A) cross-invocation **normalized** compare (temp-log names only) | **identical** |
| (B) `--strict` guest stdout, invocation 1 vs 2 | **byte-identical** (1120 bytes each; `comparison: identical`) |

Full evidence: `verify_twice_run1.log` (the (A) verdict) and
`strict_guest_stdout_run1.log` (the (B) guest output). The only raw difference
between the two `--verify` invocations was:

```
< :: Comparing logs... /tmp/run1_log_mwJSQ and /tmp/run2_log_xIkev
> :: Comparing logs... /tmp/run1_log_qJDSs and /tmp/run2_log_uo4c1
```

i.e. hermit's own randomly-named scratch log files — not guest behavior. Every
message count and the verdict itself are byte-identical.

hermit's virtualization is visible in the guest stdout (`strict_guest_stdout_run1.log`),
and identical across both `--strict` invocations:

- **PID** → `9` (redis banner `9:C` / `9:M`, `pid=9`).
- **Wall time** → hermit epoch (`31 Dec 2025 16:00:00`); timestamps fixed.
- **epoll/timer event loop** → readiness converges in a fixed `ready after 1 poll(s)`;
  the pipeline reports `errors: 0, replies: 6`; values (`counter -> 42`,
  `p:a -> 2`, `p:b -> 2XY`, `p:c -> 3`, `dbsize -> 5`) are identical run to run.

**Conclusion.** The redis server + small client workload is bitwise-deterministic
under hermit's *existing* virtualization — **no Hermit or Reverie code change was
required**, so this task produces evidence, not a product PR. hermit's determinism
engine already covers everything this workload touches: PID, wall time, `getrandom`,
the epoll/`serverCron` timer event loop, and the loopback-TCP client/server
rendezvous. This reconfirms and strengthens `redis_determinism_20260727`
(different host, exact-same redis build) by adding a byte-for-byte comparison of
two fully independent `--strict --verify` invocations.

## Reproduction

```bash
cd ~/work/dev-hermit
sudo dnf install -y redis-6.2.22            # redis-server + redis-cli v6.2.22
mkdir -p scratch/redis-small && cp experiments/redis_small_strict_verify_20260729/redis_small.sh scratch/redis-small/
SCRIPT="$PWD/scratch/redis-small/redis_small.sh"
scripts/detached-verify.rs verify-twice --name redis-small -- \
  hermit/target/release/hermit --log info run --strict --verify -- /bin/sh "$SCRIPT"
# => comparison: identical (normalization: hermit-temporary-run-log-paths-only)
#    tail: ":: Success: deterministic. Determinism verified."
```

## Files

- `redis_small.sh` — the small SET/GET/INCR + pipeline client/server workload.
- `verify_twice_run1.log` — hermit `--strict --verify` verdict (invocation 1 of (A)).
- `strict_guest_stdout_run1.log` — deterministic guest stdout (invocation 1 of (B)).
- `metadata.json` — SHAs, host facts, versions, exact commands, results.
