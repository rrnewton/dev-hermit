# Expanding the ptrace corpus with HARD programs (`compat-ptrace-expand-corpus`)

**Task:** P1 — find HARD programs (multi-threaded, signals, mmap, epoll/io_uring)
and make them pass `hermit run --strict --verify`.
**Backend:** ptrace. **Base:** `origin/main` @ `6044c2d3`.
**Feature branch:** `codex/ptrace-corpus-hard-programs`.

## Question

Which hard program classes reach **L2** (`hermit run --strict --verify`,
bitwise-identical repeat) on the ptrace backend, and which expose frontier bugs?

## Method

Prototype guests were written and driven directly under the release `hermit`
binary from this out-of-tree experiment dir (hermit isolates guest `/tmp`, so
prototypes live here, not under `/tmp`). Each program self-checks its result and
prints a `<name> success` marker as its final line. Determinism was established
two ways:

- **default multi-run compare** — run the guest 5x under `hermit run` and assert
  byte-identical stdout.
- **L2** — `hermit --log=info run --strict --verify --no-virtualize-cpuid
  --preemption-timeout=disabled -- <guest>`, asserting Hermit prints
  `Determinism verified`.

`--no-virtualize-cpuid` is a host workaround (this host has no usable CPUID
faulting); it is orthogonal to scheduling and clock determinism.

The three surviving programs were promoted into the hermit repo as C-guest +
`.rs`-harness pairs, following the `epoll_determinism.rs` model (a default
multi-run test plus an `#[ignore]`'d `assert ... reaches_strict_verify_l2` test).

## Result — 3 hard guests PASS L2 (ptrace); 1 frontier signals bug found

`results.csv` lists the deterministic witness values (generated from captured
runs, not hand-written). All three added guests reach L2:

| Guest | Category | Witness | L2 |
| --- | --- | --- | --- |
| `mmap_stress_determinism` | mmap-heavy | `checksum=37fee76cdf475203`, `total_pages=2080` | PASS |
| `prodcons_determinism` | multi-threaded (blocking mutex + 2 condvars) | `consumed_sum=300499000` | PASS |
| `io_uring_ring_determinism` | io_uring (deterministic ENOSYS fallback) | `io_uring_setup=unsupported` | PASS |

- **mmap-heavy:** 64 growing anonymous maps with deterministic page-touch,
  `mprotect` RO/RW toggling, `mremap` grow×4 then shrink, a `MAP_SHARED`
  anonymous region + `msync`, and an FNV-1a checksum of all touched bytes.
- **multi-threaded:** a bounded ring (CAP=8), 4 producers × 500 items, 3
  consumers, guarded by a mutex and not-full/not-empty condition variables. The
  threads *block* on condvars (futex), so this exercises deterministic
  mutex/condvar wakeup serialization — distinct from a spin-contention probe.
- **io_uring:** drives a real submission/completion queue with raw syscalls;
  Hermit returns `ENOSYS` for `io_uring_setup` (matching `io_uring_fallback.c`),
  so the guest deterministically takes its "unsupported" branch. It is a
  determinism witness for the fallback path that async runtimes rely on.

Wired into the hermit repo (each default test runs in `cargo test`; each L2 test
is `#[ignore]`'d and run via `--ignored`):

```
tests/c/mmap_stress_determinism.c        hermit-cli/tests/mmap_stress_determinism.rs
tests/c/prodcons_determinism.c           hermit-cli/tests/prodcons_determinism.rs
tests/c/io_uring_ring_determinism.c      hermit-cli/tests/io_uring_ring_determinism.rs
```

All six default + `#[ignore]`'d tests pass through the harness (debug binary):
`cargo test -p hermit --test mmap_stress_determinism --test prodcons_determinism
--test io_uring_ring_determinism` and again with `-- --ignored`.

### Frontier bug found — queued real-time signal delivery (complex signals)

The **complex signals** class did *not* uniformly pass. Delivery/consumption of
a queued real-time signal (`sigqueue` / `rt_sigqueueinfo`) crashes the ptrace
backend with `wait after seccomp resume failed for tracee N: -22` (EINVAL) in
**all** modes (`--strict --verify`, `--strict`, and plain relaxed `run`).

Narrowing ladder (prototypes in `src/`):

| Prototype | Behavior | Result |
| --- | --- | --- |
| `sig_q_noirq.c` | `sigqueue` then block forever (never delivered) | PASS (send path OK) |
| `sig_q_ign.c` | `signal(SIGRTMIN, SIG_IGN); sigqueue(self, SIGRTMIN, {7})` | **FAIL (EINVAL)** |
| regular `kill()` | ordinary signal delivery | PASS |

Minimal repro (`src/sig_q_ign.c`, ~5 lines of body): install `SIG_IGN` for
`SIGRTMIN`, then `sigqueue(getpid(), SIGRTMIN, ...)`. The debug log shows
`rt_sigqueueinfo` injected as `rt_tgsigqueueinfo = Ok(0)`, immediately followed
by the fatal seccomp-resume `EINVAL`. This contradicts the note that PR #812
determinized `rt_sigqueueinfo` at ptrace L2 — #812 covered the send/return path,
not live same-thread delivery of a *queued* RT signal. Filed as a hermit issue
(see the task's `tg` notes / the PR body for the issue link). No core fix was
attempted: signal injection/delivery is a core Reverie contract that requires
human design discussion per the parent workspace policy.

## Reproduce

```bash
cd experiments/ptrace_hard_corpus_20260728
HERMIT=~/work/dev-hermit/worktrees/lander/hermit/target/release/hermit
# prototypes are in src/; corpus copies compiled into corpus-bin/ (gitignored)
for p in mmap_stress_determinism prodcons_determinism io_uring_ring_determinism; do
  "$HERMIT" --log=info run --strict --verify --no-virtualize-cpuid \
    --preemption-timeout=disabled -- ./corpus-bin/$p 2>&1 | grep "Determinism verified"
done
# signals frontier bug:
cc -O0 -g -D_GNU_SOURCE src/sig_q_ign.c -o /tmp/sig_q_ign
"$HERMIT" run -- /tmp/sig_q_ign     # crashes: wait after seccomp resume failed ... -22
```

## Files

- `src/*.c` — prototype guests, including the signal-bug narrowing ladder.
- `corpus-bin/`, `bin/` — compiled prototypes (gitignored; regenerable).
- `results.csv` — per-program deterministic witness values (generated).
- `metadata.json` — SHAs, flags, host facts, result, frontier bug.
- The three promoted guests + harnesses live in the hermit repo, not here.
