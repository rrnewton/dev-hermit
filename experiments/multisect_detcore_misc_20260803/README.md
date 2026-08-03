# Multisect: `test.detcore_misc` vfork-reap flip points (2026-08-03)

## Question
Localize **every** flip point (pass↔fail, both directions) of
`test.detcore_misc`'s `vfork::vfork_parent_resumes_after_child_exec` across
hermit commit history — not just one transition. The failure is a
**load-dependent probabilistic deadlock**, so a single sample per commit is
meaningless; classify each commit by its **hang rate**.

## Method
- **Probe = matched-load concurrent stress (trinary).** At each commit, launch
  N single-shot instances of the target test *interleaved with every other
  commit's instances in the same wave*, so all commits share the same
  instantaneous host load. Classify per wave: all-pass = **PASS**, zero-pass =
  **FAIL**, mixed = **FLAKY** (a red condition). A hang = the 20 s `timeout`
  fires (exit 124).
- **Amplification + validity calibrator.** Concurrency (C=32 per label) plus
  ambient fleet load (286–590 on 316 cores) widens the vfork/reap race. The
  known-bad `head` binary is co-scheduled in every wave; a wave counts **only**
  if `head` comes back FLAKY (proves the wave was powered). Calibration: `head`
  30×@load407 → 5/5 waves FLAKY (100 % detection).
- **Builds.** One reflink-/incremental worktree per SHA; `cargo test -p detcore
  --test tests_misc --no-run`. Each binary verified to link its pinned reverie
  rev (`Compiling reverie-ptrace` at build time + distinct md5). `build-seq.sh`
  reuses a single worktree (checkout+incremental build ~20–33 s) to avoid the
  BPFJailer-blocked full-`target/` copy.
- **Signature match** (so we detect *this* bug): `Zombie::reap` →
  `Running::next_state` → `…getevent`, a `PTRACE_GETEVENTMSG`/ESRCH hot repoll,
  a defunct child while the tracee stays ptrace-stopped, one core pinned.

## Result — ONE flip, direction pass→fail
`results.csv` (hangs / samples, matched valid waves):

| hermit | reverie pin | rate | verdict |
|--------|-------------|------|---------|
| 21e91ff3 (pre-#868) | 4c6e9a0b | 0/320 (0.0 %) | CLEAN |
| d495b602 (#868 vfork-serialize) | 4c6e9a0b | 2/320 (0.6 %) | CLEAN |
| 829aab34 … 5e190f7d (pin-bumps #1–#16) | …→22791b2f | 0–1/192 each | CLEAN |
| **9c964fce (#1167)** | **fb2cf7e0** | **69/320 (21.6 %)** | **FLAKY ← FLIP** |
| 1ece0654 | aa6f1283 | 75/320 (23.4 %) | FLAKY |
| 5d92a601 (PR#305) | 26ffc1a6 | 59/320 (18.4 %) | FLAKY (partial mitigation) |
| a14c1878 / c008e014 | … | ~20 % | FLAKY |
| 0ac1c1d7 (head) | d973a85b | 52/320 (16.3 %) | FLAKY |

- **Single pass→fail flip at `9c964fce` "Ratchet SaBRe compiler compatibility
  (#1167)"**, which bumped the reverie pin `22791b2f → fb2cf7e0`. Ancestry:
  `fb2cf7e0` is the **first** window pin containing reverie
  `a8195cfc` (the notifier ~10× `capture_identity` regression); `22791b2f`
  (5e190f7d, last clean) does not. Empirical + causal agreement.
- **No fail→pass flip** anywhere after: every commit `9c964fce`→head stays
  16–23 % FLAKY. reverie **PR #305** (at `5d92a601`) only *partially* lowered
  the rate (23 %→18 %); it did **not** restore CLEAN.
- **Current main is explained.** `66c6b701` (live main) pins the same
  `d973a85b` as `0ac1c1d7`, which does **not** contain the real fix
  (reverie **PR #355**, `820b2b64`). Still ~16 % FLAKY.

## Mechanism (why a SaBRe/compiler pin bump broke a vfork test)
The hang is a **long-standing** reverie `safeptrace` notifier bug:
`decode_status_return` does `?` (returns `Err(Died)` on ESRCH) **before**
`reservation.commit()`, so a status that ESRCH'd (tracee died between the
notifier's `waitid` latch and `PTRACE_GETEVENTMSG`) is never popped from
`pending` and is re-`getevent`'d forever — an ESRCH hot spin that wedges the
guest's `wait4`. It is latent from ~0 % because the death-race window is tiny.
`9c964fce` pulled reverie `a8195cf`, which made the notifier ~10× slower
(always `capture_identity`), widening that window enough to expose the spin at
~20 % under concurrency. The pin bump's *stated* purpose (SaBRe compiler
compat) is unrelated; the regression rode along in the reverie revision range.

## Reproduce
```
# build any commit's test binary into ignored/bins/<sha>
./build-seq.sh ignored/wt/<seed-worktree> <sha>...
# matched-load trinary probe (head co-scheduled as calibrator)
./matched.sh 32 20 10 lbl:ignored/bins/<sha> ... z_HEAD:ignored/bins/head
# a wave counts only if z_HEAD is FLAKY; commit is CLEAN only at ~0 hangs across valid waves
```
Raw per-wave exit-code files: `ignored/matched/<timestamp>/` (gitignored).
See `metadata.json` for exact SHAs, host, and the confirmed live-hang signature.
