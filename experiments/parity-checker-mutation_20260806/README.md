# Mutation-testing the shipped parity checker — kill score, both directions

**Task:** `parity-checker-mutation-test` (#231/#260) · **Date:** 2026-08-06 · Local, no egress, no validate run.

*A parity check that never fails is worthless.* This plants known divergences into one side
of a known-clean log pair and asks whether the **shipped** checker catches them — and
whether it correctly tolerates the one thing it is designed to discard.

**Target:** `hermit log-diff <A> <B>` — the shipped comparator, not a reimplementation of
it. Default policy (`LogDiffOpts::default`): no stripping, no address canonicalization,
Deterministic message set (DETLOG + scheduler COMMIT). `[memory]` stack/heap records are
DETLOG, so they are in scope.

## Kill score

```
DEFAULT MODE          : 6/6 mutants killed · 2/2 controls correctly not flagged · 0 N/A
--unsafe-strip-lines  : 4/6 mutants killed · same controls
```

| mutant | default | `--unsafe-strip-lines` |
| --- | --- | --- |
| heap-record content hash (1 hex digit) | **KILLED** | KILLED |
| stack-record content hash (1 hex digit) | **KILLED** | KILLED |
| drop one `[memory]` record | **KILLED** | KILLED |
| swap two `[memory]` records (ordering) | **KILLED** | KILLED |
| **syscall numeric result** (`Ok(N)` → `Ok(N+1)`) | **KILLED** | **SURVIVED — blind** |
| **syscall hex argument** (1 hex digit) | **KILLED** | **SURVIVED — blind** |
| *control:* wall-clock prefix only | correctly tolerated | correctly tolerated |
| *control:* unmutated clean pair | correctly not flagged | — |

## The positive control is real, not vacuous

```
Done processing logs, no substantive differences found (172 | 172 DETLOG messages compared).
```

**172 messages compared on each side, not zero.** A "match" over an empty selection is a
no-result, and this is not that. Baseline is two ptrace runs of `/bin/echo hello` with
`--strict --detlog-stack --detlog-heap`, boxed, under a **pinned environment**.

Pinning is non-negotiable and is the reason a clean baseline exists at all: an unpinned
environment puts `INVOCATION_ID` / systemd scope names into the guest's `envp` and hence
its initial stack, and every stack hash then differs run-to-run under *every* backend
(measured 2026-08-05: 3/3 distinct on ptrace alone). Without pinning there is nothing clean
to mutate and every mutant would "die" for the wrong reason.

## Verdict on the checker

**The shipped comparator is sound in its default mode.** It catches a single flipped hex
digit inside a heap hash, inside a stack hash, a dropped record, a reordering, a changed
syscall result, and a changed syscall argument — while tolerating a pure wall-clock-prefix
change. That is exactly the behaviour the canonical policy specifies, demonstrated rather
than asserted.

**The defect is not the checker — it is which mode gets selected.** Bare `--verify`
selects `Stripped` (`run.rs:2779-2783`), and this measures what that costs: **the kill
score drops 6/6 → 4/6, and the two it goes blind on are precisely the syscall-value
class** — a numeric result and a hex argument. Those are the values parity exists to
compare.

Worth noting *why* the four survivors of stripping still die: a 64-char content hash is not
matched by `strip_log_entry`'s `\b0[xX][A-Fa-f0-9]+\b` (no `0x` prefix) nor collapsed by
its decimal rule, so **memory content hashes remain compared even under stripping**. So
`Stripped` is not uniformly blind — it is blind *specifically* to numbers and `0x` values,
which is the sharpest possible statement of the defect.

## Files

- `mutate.py` — the harness (produces the baseline, plants each mutant, scores)
- `results.csv` — portable default/unsafe-strip outcome matrix
- `results.json` — per-case verdicts + the score object
- `runs/runA.log`, `runs/runB.log` — the clean baseline pair
- `runs/mutant-*.log` — every planted mutant, retained so each verdict is re-checkable

## Reproduction

```bash
python3 experiments/parity-checker-mutation_20260806/mutate.py
# and the stripped-mode column:
hermit log-diff --unsafe-strip-lines runs/runA.log runs/mutant-syscall-numeric-result.log
```

## Limitations

- **One guest** (`/bin/echo hello`, ptrace, 172 DETLOG messages). The mutation classes are
  structural, so they generalise better than a single-workload performance number would —
  but this is not a corpus sweep.
- **Same-backend baseline.** Both sides are ptrace. This tests the *comparator*, which is
  backend-agnostic; it does not exercise the cross-backend path (which does not exist as
  product code — see `ratchet-dbi-strict-parity`).
- **`Deterministic` message set only.** The `FullTrace` mode that `--verify-strict`
  selects was not mutation-tested here; it compares a superset, so it should be at least as
  sensitive, but "should be" is not a measurement.
- Mutations are single-point edits to the log text. They test the comparator's sensitivity,
  not a producer's ability to emit a faithful log in the first place.
- Binary is `worktrees/covnode/hermit` @ `fc49593ac`, not current main.
