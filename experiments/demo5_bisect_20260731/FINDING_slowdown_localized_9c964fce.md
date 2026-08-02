# demo5 ~4-8x SLOWDOWN localized to hermit 9c964fce (reverie-pin bump → a8195cfc)

Task: `demo5-localize-slowdown-commit` (owner, P0).
Date: 2026-07-31. Host: 316-core devbig (quiet, load ~65).

## Reframing that made this tractable

Earlier "byte-identical wedge" verdicts were **too-short-cutoff artifacts**: the
config-reverted `demos/05-qemu-boot.py` (`hermit run --strict --target-timeslice
100000 --max-timeslice 2000000000`, RCB armed) *boots* full-linux demo5, it is
just **~4-8x slower than the green era** (~50s → ~370s). With a generous 600s
cutoff (slow ≠ wedge) the discriminator becomes **boot wall-time**, not a binary
pass/wedge.

## Method — timing multisect, min-of-N

`ignored/fairness-val/timing_multisect.py`. Key ideas:

- The canonical config implies `--pin-threads` → reverie pins the guest to a
  **random absolute host core** (`hermit-cli/.../container.rs apply_affinity`,
  `rand::random_range(0..num_cpus)`). A restricted cpuset therefore makes
  reverie's `sched_setaffinity` return **EINVAL** and crashes the boot. So we do
  **not** pin a cpuset. Instead:
- `boot_wall = intrinsic_TCG_work + contention_delay(≥0)` ⇒ **MIN over N reps**
  is a load-robust estimator of intrinsic boot time (contention only adds).
- Each boot runs in a `systemd-run --user --scope` with `MemoryMax` (OOM safety,
  no cpuset) + out-of-container wall timeout + pgid SIGKILL reaping. PASS = the
  `2022-01-01T` RTC epoch reaches the serial log (booted to shell).

Binaries built at each rung (all distinct sha256, in
`ignored/demo5-multisect/bin/hermit-<sha>`), `+N` = commits behind HEAD.

## Result

Coarse ladder (`timing/ladder1`): last FAST = 1663138d+57 (47.5s); first SLOW
= 84a65a3b+51 (368s). Window (`timing/window4`, decisive):

| commit         | +N  | reverie pin | boot wall | verdict |
|----------------|-----|-------------|-----------|---------|
| 37ea6bce       | +55 | 9c22e2fb    | 63.5s     | FAST    |
| 5e190f7d       | +54 | 22791b2f    | 58.5s     | FAST    |
| **f6c836b1**   | +53 | 22791b2f    | **47.0s** | FAST    |
| **9c964fce**   | +52 | fb2cf7e0    | **373.6s**| SLOW    |
| 84a65a3b       | +51 | fb2cf7e0    | ~370s     | SLOW    |

The adjacent pair **f6c836b1 (47s) / 9c964fce (373.6s)** differs **only in the
reverie Cargo.lock pin** (22791b2f → fb2cf7e0). No detcore/scheduler source
change is involved.

## Regressor + mechanism

**hermit `9c964fce` "Ratchet SaBRe compiler compatibility (#1167)"** bumps the
reverie pin `22791b2f → fb2cf7e0`, which pulls in reverie **`a8195cfc`
"reverie-liteinst: add ptrace-owned hybrid runtime (#270)"** — the **~10x
ptrace-notifier hot-path regression** (`current_or_new` always calls
`capture_identity` before the registry fast-return; see memory
`reverie-a8195cfc-notifier-10x-ptrace-regression`). demo5 boot issues ~910k host
syscalls, each routed through the notifier → ~7x boot slowdown.

Reverie ancestry confirmed: `a8195cfc` is **absent** from 9c22e2fb and 22791b2f,
**present** in fb2cf7e0. HEAD's pin (`aa6f1283`, after the reverie→reverie-core
rename via hermit `1ece0654`) is a descendant of fb2cf7e0: it still **contains**
`a8195cfc` and **lacks** the fix `8323c4e` (reverie PR #305 pidfd-liveness fast
path).

## What this refutes

- **Guest-clock cluster** 3ac51e11(+23)/cc3730fd(+22): REFUTED — slowdown is
  already present at +51/+52, upstream of the cluster.
- **1663138d N+K**: previously REFUTED (see `FINDING_1663138d_refuted.md`).
- **#1386 fairness overlay** (226's fix): B=1000 = 380s ≈ HEAD → does **NOT**
  restore ~60s, and mechanistically cannot: the overlay is a detcore scheduler
  change, orthogonal to a reverie ptrace-notifier hot path.

## Fix

Bump hermit's reverie pin forward to a reverie SHA containing `8323c4e` (PR #305).
Tracked by task `demo5-fix-bump-reverie-pin-305`.

## Data

- `ignored/fairness-val/timing/{ladder1,boundary1,window4}/results.csv` + `summary.json`
- Binaries: `ignored/demo5-multisect/bin/hermit-{37ea6bce,5e190f7d,f6c836b1,9c964fce,84a65a3b,...}`
- Driver: `ignored/fairness-val/timing_multisect.py`
