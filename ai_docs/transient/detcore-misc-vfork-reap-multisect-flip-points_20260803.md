# Multisect of `test.detcore_misc` vfork-reap hang: the flip points (2026-08-03)

**TL;DR.** The `test.detcore_misc` hang
(`vfork::vfork_parent_resumes_after_child_exec`) has **exactly one flip point**
across hermit history, in the **pass→fail** direction:
**`9c964fce` "Ratchet SaBRe compiler compatibility (#1167)"**, which bumped the
reverie pin `22791b2f → fb2cf7e0`. There is **no fail→pass flip** after it —
every commit from `9c964fce` through current `main` stays 16–23 % flaky under
load. Current `main` is still broken because the real fix (reverie **PR #355**)
is not yet pinned.

Full method, raw data, and reproduction:
`experiments/multisect_detcore_misc_20260803/` (README.md, results.csv,
metadata.json, matched.sh, build-seq.sh).

## Why "multisect", and why rate-based
Plain `git bisect` assumes one monotonic transition and one sample per commit.
This bug is a **load-dependent probabilistic deadlock**, so a single sample is
noise, and there could be several introduce/fix cycles. The probe therefore
measured a **hang rate** per commit and searched the whole rate landscape.

### Probe design (matched-load concurrent stress, trinary)
- At each commit, launch **32 single-shot instances interleaved with every other
  commit's instances in the same wave** → all commits share the same
  instantaneous host load. Per wave: all-pass = PASS, zero-pass = FAIL, mixed =
  **FLAKY** (red).
- Amplification: concurrency × ambient fleet load (286–590 on 316 cores) widens
  the vfork/reap race, raising the per-instance hang probability far above the
  ~2.6 % seen under light load, so ~200–320 samples/commit are decisive.
- **Validity calibrator:** the known-bad `head` binary is co-scheduled every
  wave; a wave is counted only if `head` returns FLAKY. Calibration: `head`
  30×@load407 → 5/5 waves FLAKY (100 % detection). This rejects the failure mode
  the owner warned about (a low-load wave falsely reporting a broken commit as
  clean).

## The rate landscape (hangs / samples, matched valid waves)

| hermit | reverie pin | rate | verdict |
|--------|-------------|------|---------|
| 21e91ff3 (pre-#868 vfork parent) | 4c6e9a0b | 0/320 (0.0 %) | CLEAN |
| d495b602 (#868 "Serialize vfork scheduling") | 4c6e9a0b | 2/320 (0.6 %) | CLEAN |
| 51f18e9d … 5e190f7d (pin-bumps #1–#16) | …→22791b2f | 0–1/192 each | CLEAN |
| **9c964fce (#1167)** | **fb2cf7e0** | **69/320 (21.6 %)** | **FLAKY ← FLIP** |
| 1ece0654 | aa6f1283 | 75/320 (23.4 %) | FLAKY |
| 5d92a601 (reverie PR#305) | 26ffc1a6 | 59/320 (18.4 %) | FLAKY (partial) |
| a14c1878 | bbf6e2ef | 64/320 (20.0 %) | FLAKY |
| c008e014 | 6b8ed64f | 68/320 (21.3 %) | FLAKY |
| 0ac1c1d7 (head at task start) | d973a85b | 52/320 (16.3 %) | FLAKY |

A sub-window sweep of every reverie pin-bump in `(829aab34, 9c964fce]`
(#9→#17) resolved the boundary to a **single commit**: #9–#16 all 0–1/192
(noise floor), #17 `9c964fce` 36/182 (19.8 %).

## Findings
1. **#868 vfork serialization is exonerated.** `d495b602` and its parent are
   90/90 clean at load 450 and ≤0.6 % across 320 matched samples. The hermit-side
   vfork change did not cause the hang.
2. **The single flip is `9c964fce` (#1167), a reverie pin bump.** Ancestry
   confirms the empirical result: `fb2cf7e0` is the **first** pin in the window
   containing reverie `a8195cfc`; the previous pin `22791b2f` (5e190f7d, last
   clean) does not. Empirical bisect and reverie ancestry agree exactly.
3. **No fail→pass flip.** The bug is never fixed in-range; it persists through
   current `main`. reverie **PR #305** (`5d92a601`) only lowered the rate
   ~23 %→18 %, it did not restore CLEAN.
4. **Current `main` (`66c6b701`) is explained.** It pins the same `d973a85b` as
   `0ac1c1d7`; that reverie rev does **not** contain the fix (`820b2b64`, PR
   #355). Expect ~16 % flaky until the pin bumps past PR #355.

## Signature (confirmed on a live head hang)
Matches the reap-deadlock signature exactly, so the counted hangs are *this* bug:
- `131883` `ptrace(PTRACE_GETEVENTMSG, <defunct-cat-pid>)` in ~3 s, `131882`
  returning `-1 ESRCH` — a hot repoll.
- child `cat` = `Z <defunct>`; tracee stays `t` (ptrace-stopped); one thread
  ~105 % CPU while the rest idle.
- stack: `ptrace` → `Stopped::getevent` → `Event::decode_status_return` →
  `WaitFuture::poll` → `Running::next_state` → `Zombie::reap`, under
  `reverie_ptrace::task::handle_internal_error`.

## Root cause (mechanism)
Long-standing reverie `safeptrace` notifier bug: `decode_status_return` runs `?`
(returns `Err(Died)` on ESRCH) **before** `reservation.commit()`, so a status
that ESRCH'd — the tracee died between the notifier's `waitid` latch and
`PTRACE_GETEVENTMSG` (parent's `SIGKILL`; `man 2 ptrace` "Death under ptrace") —
is never popped from `pending` and is re-`getevent`'d forever → ESRCH hot spin →
the guest's `wait4` wedges. Latent (≈0 %) because the death-race window is tiny.
`9c964fce` pulled reverie `a8195cf`, which made the notifier ~10× slower (always
`capture_identity`), widening the window enough to expose the spin at ~20 % under
concurrency. The pin bump's stated purpose (SaBRe compiler compat) is unrelated;
the regression rode along in the reverie revision range.

**Fix path:** bump the hermit reverie pin past **PR #355** (`820b2b64`,
consume-dead-status-on-ESRCH). That removes the spin regardless of notifier
speed; the `a8195cf` slowdown is a separate perf regression (see
`reverie-a8195cfc-notifier-10x-ptrace-regression`).

## Related
- memory `detcore-misc-vfork-flaky-timeout-under-load` (root cause + PR #355)
- memory `demo5-slowdown-regressor-9c964fce-reverie-a8195cf` (independently
  fingered `9c964fce`/`a8195cf` for an ~8× slowdown — same commit, same reverie
  regression, different symptom)
- memory `reverie-a8195cfc-notifier-10x-ptrace-regression`
