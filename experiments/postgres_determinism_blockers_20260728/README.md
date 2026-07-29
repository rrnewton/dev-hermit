# PostgreSQL under `hermit run --strict --verify` — determinism blockers

- **Task:** `compat-deep-app-postgres` (P1 — PostgreSQL under hermit, deep app).
- **Date:** 2026-07-28
- **Hermit:** `codex/compat-deep-app-postgres` @ `967abd99bdc453e9ab9b6f118faaf5f9195bd12b` (origin/main), `target/release/hermit`.
- **PostgreSQL:** 13.23 (`/usr/bin/postgres`, `/usr/bin/initdb`, `/usr/bin/psql`).
- **Backend:** ptrace (default).

## Question

Can PostgreSQL (initdb + start + simple queries) run bitwise-identically
across `hermit run --strict --verify` runs, like Redis did
(`redis-determinizes-under-hermit`)?

## Result: BLOCKED — cannot reach a determinism measurement

initdb never completes under hermit, so no `--verify` L2 result is reachable.
Three independent, reproducible blockers were found and captured. The primary
wall (SysV shared memory) is architectural and hits **before** any query runs.

### Blocker 3 (PRIMARY, both namespace modes): SysV `shmget` returns ENOSYS

PostgreSQL requires a small SysV shared-memory interlock segment at startup,
even with `shared_memory_type = mmap` (the v13 default). hermit deterministically
fail-closes SysV `shm*`/`sem*` to `ENOSYS` (PR #820), so initdb's bootstrap
(`postgres --boot`) aborts:

```
selecting dynamic shared memory implementation ... posix
FATAL:  could not create shared memory segment: Function not implemented
DETAIL:  Failed system call was shmget(key=78, size=56, 03600).
child process exited with exit code 1
initdb: removing data directory ...
```

This is intentional determinism policy, not a bug: cross-process SysV shared
memory is a determinism-hard primitive. The local command transcript is
gitignored; the observed failure is summarized here.
Repro: `hermit run --no-namespace -- initdb -D <dir> -U postgres -A trust --no-sync`.

### Blocker 1 (deterministic isolated-namespace mode only): root refusal

In the normal isolated container, the guest is mapped to **uid 0 (root)** and
`initdb`/`postgres` hard-refuse to run as root:

```
initdb: error: cannot be run as root
```

The user namespace maps a **single** uid (`0 212630 1`) / gid (`0 100 1`), so
only root exists inside the container. The local namespace command transcripts
are gitignored; the relevant observations are summarized here.
Repro: `hermit run -- initdb -D <dir> -U postgres`.

### Blocker 2: cannot escape root in-guest (nested userns denied)

Dropping privileges to a non-root uid inside the container is impossible with a
single-uid map. `setuid(65534)` no-ops (65534 unmapped) and creating a nested
user namespace to remap root→non-root is denied:

```
unshare: unshare failed: Operation not permitted
```

The local command transcript is gitignored; the denial is summarized here.

### `--no-namespace` is not a clean path

`--no-namespace` exposes the real non-root uid (212630) — clearing Blocker 1 —
but (a) shares host `/tmp`, `/proc`, PID space, and network (hermit warns
`--verify` "may be less deterministic due to shared state"; the guest PID is the
real host PID and the scheduler logs continuous `Nondeterministic external
actions ... jumped in the middle of runnable work`), and (b) still hits
Blocker 3 (`shmget` ENOSYS) during initdb. So it neither runs postgres nor
yields a trustworthy determinism result.

## Conclusion

PostgreSQL cannot currently run under hermit. Reaching an L2 `--verify` result
would require **both**:

1. **SysV shared-memory support in detcore** — emulate `shmget`/`shmat`/`shmdt`/
   `shmctl` (at least for the single-node interlock segment postgres needs)
   deterministically, instead of the current fail-closed ENOSYS. This is the
   primary, harder wall (a real determinism feature, not a shim).
2. **A non-root uid inside the isolated namespace** — an opt-in way to map the
   guest to a non-root uid while keeping private `/tmp` + PID stability, so
   postgres's root refusal is satisfied without `--no-namespace`.

Both are design-level determinism/container changes that require human approval
(they touch determinism policy and the container identity model); they are out
of scope for an autonomous compat-run task. No hermit code change was made.

This confirms and deepens the earlier finding in memory note
`make-driven-builds-determinize` (which recorded only the root refusal); the
SysV `shmget` ENOSYS wall is the deeper, mode-independent blocker.

## Source-level root cause (Blockers 1 & 2)

Verified by read-only code investigation (hermit @ `967abd99`, reverie pin):

- The isolated container calls `map_root()` **unconditionally** —
  `hermit-cli/src/bin/hermit/container.rs:108` (`default_container`) →
  `reverie/reverie-process/src/container.rs:402-405`
  (`map_root` = `map_uid(0, geteuid())` / `map_gid(0, getegid())`). The
  inside-uid `0` is hardcoded; the uid_map written to `/proc/self/uid_map` is
  the single-entry `0 <euid> 1`. No CLI flag, `RunOpts` field, config, or env
  var maps the guest to a non-root uid while keeping the namespace; the only
  switch is `--no-namespace`, which drops the namespace entirely.
- `getuid`/`geteuid`/`getgid`/`getegid` are **PassThrough**
  (`detcore/src/syscall_classification.rs:627-638`) — the `0` the guest sees is
  produced by the kernel *inside the user namespace*, not by a Detcore constant.
- The credential-**setting** family (`setuid`/`setgid`/`setresuid`/… ) is a
  deterministic **no-op returning `Ok(0)`** (`detcore/src/lib.rs:1612-1616`;
  predicate `is_credential_identity_noop_syscall`,
  `syscall_classification.rs:922-935`). This is why `setpriv --reuid=…` inside
  the guest cannot drop privileges — `setuid` never actually changes the uid.

Therefore a non-root guest uid can only originate from the container's initial
map: a new opt-in flag would call `container.map_uid(<n>, geteuid())` /
`map_gid(<n>, getegid())` instead of `map_root()`. That code does not exist
today, and adding it changes the container identity model — a design decision
for the maintainers, not an autonomous compat-task change.

## Files

- `metadata.json` — SHAs, versions, host.
- Local `*.log` command transcripts are retained outside Git. The durable
  failure details and environment provenance are summarized above and in
  `metadata.json`.
