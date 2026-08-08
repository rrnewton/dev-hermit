# The re-baselined envelope: old and new, side by side

**Task:** `corpus-falsifiability-followup-after-verify-strict-rerun` · agent `hermit-w6` · 2026-08-07

## The definition change, stated first

| | OLD | NEW |
|---|---|---|
| probe | bare `--verify` (**Stripped** comparator) | `--verify-strict` at `log_scope=info` |
| green predicate | `deterministic == 1` | `bitwise_parity == true` **AND** `compared_log_messages` nonzero **on both sides** |
| what it compares | DETLOG subset, wall-clock prefix stripped, unsafe numeric-address and path normalisation, **no parity assertion** | every INFO message, exactly |
| falsifiable? | **No** — planted defects went undetected | **Yes** — by construction |

The old number was never a strictness claim. That is the point of the exercise, and it is why a lower new
number would have been a re-baseline rather than a regression.

## Headline: both counts, and why they are not a subtraction

> **OLD: 346 green / 618 rows** (all backends, stripped probe) — measured.
> **NEW: 128 green / 133 rows** (ptrace only, strict predicate) — measured.
>
> **These are different populations. `346 → 128` is not a drop and must not be published as one.**

Of the 346 old green rows, the strict run re-measured **8**. That is **2.3%**. Setting the two headlines
beside each other compares a 618-row all-backend scorecard against a 133-row ptrace-only run whose overlap
with the old green set is one fortieth.

## The comparison that *is* like-for-like

Joined on `(test_id, backend)` against `origin/main`'s scorecard, last-writer-wins. All 133 strict-run guests
exist in the old scorecard — but on **ptrace, only 8 do**. The old coverage for these guests is kvm 133 and
liteinst 133.

### Partition A — ptrace present in BOTH (8 cells)

| | count |
|---|---|
| OLD green (`deterministic=1`, stripped) | **8 / 8** |
| NEW green (`bitwise_parity` + nonzero counts) | **8 / 8** |
| **drop** | **0** |

| cell | old `deterministic` | new | compared |
|---|---|---|---|
| `c-programs/add-key-enosys` | 1 | GREEN | 58\|58 |
| `c-programs/cachestat-enosys` | 1 | GREEN | 58\|58 |
| `c-programs/futex-waitv-enosys` | 1 | GREEN | 58\|58 |
| `c-programs/get-robust-list-self` | 1 | GREEN | 58\|58 |
| `c-programs/ioctl-fioclex` | 1 | GREEN | 86\|86 |
| `c-programs/kcmp-eperm` | 1 | GREEN | 60\|60 |
| `c-programs/keyctl-enosys` | 1 | GREEN | 58\|58 |
| `c-programs/listmount-enosys` | 1 | GREEN | 58\|58 |

**The expected drop did not materialise on the only population where it could be measured.** All eight
survive the tightening, now at a named tier.

### Partition B — new ptrace coverage (125 cells)

| | count |
|---|---|
| OLD green | **N/A** — ptrace was never measured for these guests |
| NEW green | **120 / 125 (96.0%)** |

The old scorecard held these guests on kvm (87 green) and liteinst (89 green). Comparing NEW ptrace 120
against OLD kvm 87 or liteinst 89 would be a **cross-backend** comparison, not a definition change. Partition
B is new coverage, not a re-baseline.

## Per-cell tier — the actual achievement

| | tier recorded |
|---|---|
| OLD scorecard (`parity_tier`) | **0 / 535** deduped cells — `UNKNOWN` (blank) on every one |
| NEW run (`log_scope`) | **132 / 133** = `info`; the 1 exception is the `no_result`, correctly `None` |

Not one old cell recorded which comparison earned it. (Independently consistent with the envelope audit
earlier today: 0/127 enabled cells carried a tier.)

> **So the re-baseline's real content is not a count moving — on the only comparable population it did not
> move. It is that cells now carry a tier at all: 0/535 → 132/133.**

## The five non-green cells, each falsifiable rather than silent

| cell | verdict | bitwise | compared |
|---|---|---|---|
| `c-programs/record-replay-file-state` | diverged | false | 196\|196 |
| `c-programs/sigpipe-siginfo` | diverged | false | 142\|142 |
| `c-programs/syscall-file-metadata` | diverged | false | 141\|141 |
| `c-programs/syscall-quick-wins` | diverged | false | 125\|125 |
| `bin-c/robust-futex-test` | **no_result** | false | **0\|0** |

The last one is the vacuity guard earning its place on the first run: a `bitwise_parity`-only rule would have
counted a cell that compared **nothing** as green. It is refused, not counted.

Three of the four divergences are guests the old stripped probe scored `deterministic=1`
(`syscall-file-metadata` on kvm *and* liteinst; `sigpipe-siginfo` and `syscall-quick-wins` on liteinst).
**Caveat that bounds the claim:** those old greens are on kvm/liteinst and these runs are ptrace, so this is
**not a same-cell flip**. It is strong evidence the guests are genuinely nondeterministic and the stripped
probe could not see it; confirming the flip per cell needs those backends under the same predicate.

## Exact SHA and measurement conditions

| | |
|---|---|
| hermit binary | built at `origin/main` **`590fcc9eeb0339c5cf23f72b84394a63333e88ff`**, isolated worktree |
| old scorecard | `compat-envelope/scorecard.csv` as committed on `origin/main` |
| probe | `--verify-strict`, `log_scope=info` (Canonical without `--verify-verbose`) |
| execution | detached `systemd-run --user --unit=w7-corpus-strict-rerun`; every guest takes `ci-hub validate-lock` for its own run |
| backends | **ptrace only.** kvm does not complete on this box; dbi needs its own worktree build |
| coverage | **133 measured / 171 compiled / 214 in `corpus-c.tsv`** |

### Unknowns, reported as unknown rather than assumed

- **38 compiled guests unmeasured** (171 − 133). Cause: per-guest validate-lock churn — each release leaves a
  brief quarantine and the next acquire is *refused* (exit 3) rather than queued. Not a probe defect; a
  serialisation-tuning one. Fix: longer inter-guest backoff, or hold the lock for a ~20-guest batch.
- **43 corpus guests never compiled** (214 − 171). Not measured, not counted either way.
- **kvm and dbi: entirely unmeasured** under the new predicate. Their 130 and 8 old green rows are neither
  confirmed nor refuted.
- **`parity_tier` on the old scorecard: unknown on all 535 cells.** Reported as unknown, never treated as green.

## What must not happen next

The drop this task was written to absorb did not appear. That makes the fake-green move *less* tempting, not
more — but the shape to watch is unchanged: if a later, wider run does drop the number, the fix is not to
loosen the predicate back toward Stripped. The Stripped probe's own record is that it scored three
now-diverging guests as deterministic.

## Honest residue

1. The like-for-like population is **8 cells**. That is a thin basis for "no drop", and it is stated as 8, not
   dressed up. A wider like-for-like needs ptrace rows for guests the old scorecard only covered on kvm/liteinst.
2. The 128/133 figure is **ptrace-only** and says so everywhere it appears.
3. Nothing here re-runs kvm or liteinst, so the three suspected old-green flips remain *suspected*.
