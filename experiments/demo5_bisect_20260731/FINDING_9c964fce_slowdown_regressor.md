# demo5 ~8x SLOWDOWN localized: regressor = hermit 9c964fce (reverie a8195cf)

Task: `demo5-localize-slowdown-commit` (owner hermit-231; this = interior-bisect
complement by coordinator/opus-4.8). Date: 2026-08-01. Host: 316-core devbig.

## Result

The demo5 boot-time regression (green ~47s → HEAD ~345s under the shipped
rcb-armed config) steps up at a **single hermit commit: `9c964fce` "Ratchet
SaBRe compiler compatibility (#1167)"**, whose *only* runtime-affecting change is
a **reverie git-pin bump `22791b2f → fb2cf7e0`** (Cargo.lock delta is
reverie-pin-only; the detcore-*/Cargo.toml bumps are internal 0.x versions and
validate.sh is CI-only).

That pin bump pulls in reverie commit **`a8195cf` "reverie-liteinst: add
ptrace-owned hybrid runtime (#270)"**, which — despite its liteinst-focused title
— is a large rewrite of the **shared ptrace path**: `safeptrace/src/notifier.rs`
(+7860), `reverie-ptrace/src/tracer.rs` (+2550), `task.rs` (+1715),
`safeptrace/src/lib.rs` (+498), adding a per-stop `current_or_new` /
identity-capture path. QEMU TCG boot is ptrace-stop-dense, so per-stop capture
overhead multiplies the whole boot ~7-8x. This is the same regression recorded in
memory `reverie-a8195cfc-notifier-10x-ptrace-regression` (~10x on a 100k-getpid
microbench). The fix `8323c4e` (reverie PR#305, pidfd-liveness fast path) is
**NOT** in reverie `fb2cf7e0` nor in HEAD's reverie pin `aa6f1283`, so HEAD is
still slow.

## Evidence — MIN-of-N boot wall (out-of-container enforcer, shipped rcb-armed
config `--strict --target-timeslice 100000 --max-timeslice 2000000000`, PASS =
`2022-01-01T` RTC marker in serial, cutoff 600s; MIN-of-N is the load-robust
intrinsic estimator since `--pin-threads` forbids cpuset isolation).

Window `(1663138d, 84a65a3b]`, old→new, with reverie pin and class:

| hermit commit | reverie pin | boot wall | class | source |
|---|---|---|---|---|
| 1663138d | 9c22e2fb | 47.0–47.5s | FAST | interior + boundary1 |
| ba0adf58 (#test-only) | 9c22e2fb | 47.0s | FAST | boundary1 |
| 37ea6bce (SaBRe-gated) | 9c22e2fb | 46.5–47.0s | FAST | interior + boundary1 |
| **5e190f7d** (bump→22791b2f) | 22791b2f | **47.5s** | **FAST** | **interior (decisive)** |
| f6c836b1 (=5e190f7d binary) | 22791b2f | (FAST) | FAST | inferred (validate.sh-only) |
| **9c964fce** (bump→fb2cf7e0) | **fb2cf7e0** | **SLOW** | **SLOW** | **interior (decisive)** |
| 84a65a3b (#test-only) | fb2cf7e0 | 356–368s | SLOW | interior + boundary1 + ladder1 |
| …HEAD 2f3689bd | aa6f1283 | ~345s | SLOW | ladder1 |

Last FAST = **5e190f7d** (reverie 22791b2f, WITHOUT a8195cf).
First SLOW = **9c964fce** (reverie fb2cf7e0, WITH a8195cf).

## Why not the other window commits (ruled out by measurement + diff)

- `37ea6bce` "Normalize SaBRe inherited pipe verification (#1158)" touches
  `scheduler.rs`/`files.rs` but is **provably inert on ptrace**: its tagging is
  gated on `should_tag_sabre_internal_pipe_io(discover_live_file_metadata, …)` and
  `config.discover_live_file_metadata = backend == Backend::Sabre`
  (`hermit-cli/src/lib.rs:1452`). Measured FAST 46.5s.
- `5e190f7d` (first reverie bump 9c22e2fb→22791b2f) — that reverie range is all
  SaBRe/e9patch-local commits, none touch safeptrace. Measured FAST 47.5s.
- `ba0adf58`, `84a65a3b` — test-only. `f6c836b1` — validate.sh-only (binary ==
  5e190f7d).

## Fix — EMPIRICALLY PROVEN (2026-08-01)

The fix is reverie **PR #305** "safeptrace: fix ~10x per-ptrace-stop perf
regression from #270 (notifier handoff)" — **MERGED to reverie main** (both
`Regular tests` and `Host-dependent tests` green). It is two safeptrace commits
on top of the current hermit pin `aa6f1283`:

- `043cfaf` safeptrace: lock-free steady-state fast path in `claim_notifier_wait`
- `8323c4e` safeptrace: skip redundant per-stop identity capture in `current_or_new`

**A/B proof.** Built hermit@origin/main (`2ddb7798`) with the reverie pin bumped
`aa6f1283 → 8323c4e` (same-line, still crates 0.1.0 — the tightest possible
isolation: identical hermit source, identical reverie except the 2 fix commits):

| pin | demo5 boot wall | class |
|---|---|---|
| aa6f1283 (HEAD baseline) | ~345–372 s | SLOW |
| **8323c4e (+#305 fix)** | **47.0 s, 47.5 s (2/2)** | **FAST** |

~8x restored, back to the pre-regression FAST class. Repro:
`ignored/fairness-val/build_fix_and_time.sh`; results in
`ignored/fairness-val/timing/fix/`.

**Recommended fix PR (hermit).** Bump hermit's reverie pin `aa6f1283` → reverie
main (currently `26ffc1a`). Note reverie `e212517` published the crates at
**0.2.0**, so the hermit manifests' `version = "0.1.0"` on the reverie deps must
move to `0.2.0` in the same PR (the isolated `8323c4e` validation used 0.1.0 to
avoid conflating the fix with the version bump). This is a routine cross-repo pin
bump — **not** a DetCore/scheduler change, so no `post-facto-human-review`
trigger applies.

## Reproduction

```
python3 ignored/fairness-val/interior_timing.py 2   # times 1663138d,37ea6bce,5e190f7d,9c964fce,84a65a3b
# binaries: ignored/demo5-multisect/bin/hermit-<sha>   (built via mswt/build_one.sh)
# CSV: ignored/fairness-val/timing/interior/interior_results.csv
```
