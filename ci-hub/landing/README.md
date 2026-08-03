# Landing mutex (`ci-hub land-lock`)

A small, deterministic **shared-file landing mutex** that serializes PR landings
which mutate the same shared manifest registries. Discoverable, not folklore.

## Why it exists

Every backend-parity / e2e-manifest PR mutates the **same two** shared files:

- `hermit/tests/e2e/manifests/backend-parity-c.toml`
- `hermit/tests/e2e/manifests/inventory/test-files.json`

When several landers push + merge concurrently, **each land moves `origin/main`
and DIRTYs every other in-flight PR**, so the pack "serializes" the hard way —
by collision-and-retry, burning the single self-hosted `[gate]` runner and
converging slowly (the documented "mass-parallel drain won't self-heal;
SERIALIZE" failure mode). This mutex turns that scrum into an orderly queue:
**exactly one land is in flight at a time.**

## Contract

Every lander MUST hold the lock around its entire land sequence:

```
acquire  ->  re-union onto fresh origin/main  ->  push  ->  stamp
         ->  merge --rebase  ->  ancestry-verify  ->  release
```

Acquire **before** fetching fresh main (so your re-union sees the final state of
the previous land); release **only after** `git merge-base --is-ancestor <sha>
origin/main` confirms your commit actually landed.

## Design (small + deterministic)

- **`flock(1)`** makes each check-and-set on the lockfile atomic across processes.
- The held state is a **lease with an expiry**, not a held fd — so acquire in one
  shell and release in another Just Work, and **(a) a dead holder cannot wedge
  the pack**: once its lease lapses (`--hold` seconds, default 900), the next
  waiter reclaims it and logs `reclaimed lapsed lease from <agent>`.
- **(b)** The lockfile records holder **agent + PR + host + timestamps** for
  debuggability; `status` prints them.
- **(c)** Waiters enqueue in a **FIFO**, so ordering is deterministic and each
  waiter sees who is ahead of it; `release` frees the lock immediately and names
  the next agent, which then acquires on its next short (3s) poll rather than
  polling blindly.

Runtime state (all machine-local, gitignored):

| file | role |
| --- | --- |
| `~/work/dev-hermit/.landing-lock`        | holder metadata — the lock |
| `~/work/dev-hermit/.landing-lock.guard`  | `flock` target (impl detail) |
| `~/work/dev-hermit/.landing-lock.queue`  | FIFO waiter list |

## Usage

```bash
cd ~/work/dev-hermit

# Inspect
ci-hub/ci-hub land-lock status

# Manual acquire / release around your land sequence
ci-hub/ci-hub land-lock acquire --agent hermit-ci --pr 1533   # blocks until yours
#   ... re-union -> push -> stamp -> merge --rebase -> ancestry-verify ...
ci-hub/ci-hub land-lock release --agent hermit-ci

# Crash-safe wrapper (RECOMMENDED): acquire, run, always release, with a
# background heartbeat that renews the lease so a long land keeps the lock.
ci-hub/ci-hub land-lock run --agent hermit-ci --pr 1533 -- ./my-land-sequence.sh
```

### Subcommands

| command | purpose |
| --- | --- |
| `acquire --agent NAME --pr N [--wait S] [--hold S]` | block until acquired (FIFO); reclaims a lapsed lease |
| `renew --agent NAME [--hold S]` | heartbeat — extend your lease during a long land |
| `release --agent NAME` | free the lock (owner only); signals the next waiter |
| `status` | print holder metadata, seconds left, and the FIFO queue |
| `run --agent NAME --pr N [...] -- CMD...` | acquire → run CMD (auto-heartbeat) → always release |

Defaults: `--wait 1800` (give up after 30 min), `--hold 900` (lease lapses after
15 min so a dead holder self-clears). Poll interval 3s.

Exit codes: `0` ok · `1` wait-timeout · `2` usage · `3` not-owner / internal.

## Notes

- `run` is preferred over bare `acquire`/`release`: it releases even if your land
  script fails or is killed, and its heartbeat prevents a genuinely long (but
  live) land from having its lease reclaimed out from under it.
- The lease is a **safety net**, not a schedule: keep `--hold` comfortably above
  your real land time, and prefer `run` so releases happen promptly.
- Disjoint footprints don't strictly need the lock, but taking it is cheap and
  keeps the single `[gate]` runner from being contended — when in doubt, hold it.
