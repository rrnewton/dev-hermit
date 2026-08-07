# DBI's DETLOG column: one unvirtualised scalar, and DBI is not the weak backend it reads as

**Agent:** hermit-w7 (`[impl agent, opus-5]`) · **2026-08-07** · hermit `723d19ad5d10` (= `origin/main`,
freshly fetched, 0 commits after), release, not `-dirty`, Reverie pin `038e9939`.

## 0. This is a RE-DERIVATION, not a discovery — credit where it belongs

**hermit-w2 already found and localised this** in task
`dbi-build-at-current-main-then-virtualise-dtid` (now CLOSED). Their note is the primary
analysis: they identified `reverie-dbi/src/lib.rs:280-296`, established that the accessor is
identical to ptrace's and that the real difference is the **absence of a PID namespace** under
DBI, framed the fix as an architecture fork, and deliberately declined to pick a branch
drive-by. All of that is theirs and this page does not restate it as new.

I arrived at the same place from a different direction — a 30-run *self-determinism* sweep
rather than a parity probe — which makes this independent corroboration at a newer binary
(they built at `590fcc9e`). What is added here is three measurements they did not take.

## 1. The observation

| backend | dtid, three consecutive runs | guest-visible `getpid()` |
| --- | --- | --- |
| ptrace | 3, 3, 3 | `Ok(3)` |
| kvm | 3, 3, 3 | `Ok(3)` |
| sabre | 3, 3, 3 | `Ok(3)` |
| e9patch | 3, 3, 3 | `Ok(3)` |
| liteinst | 3, 3, 3 | `Ok(3)` |
| **dbi** | **2477904, 2478010, 2481848** | `Ok(3)` |

Note the last column. **Guest-visible `getpid()` under DBI is already `Ok(3)`** — the
host→deterministic mapping exists and works. DETLOG's `dtid` simply does not use it. Under
ptrace the two coincide (both 3) because the guest runs in a PID namespace, which is why this
is invisible on the golden path.

## 2. NEW: `dtid` is 100 % of DBI's DETLOG self-nondeterminism

hermit-w2 measured *parity* residue. This is the *self-determinism* consequence, 30 runs per
cell across all 7 matrix guests:

| | raw | `dtid`-normalised |
| --- | --- | --- |
| DBI, every one of the 7 guests | **30 distinct outcome classes / 30 runs** — no run ever repeats | **1 class / 30 runs** |
| ptrace (control) | 1 class / 30 | 1 class / 30 |
| liteinst `detlog_syscalls` (control) | 2 classes / 30 | **2 classes / 30** — unchanged, a different defect |

Record counts are perfectly stable across all 30 runs (94, 68, 334 …), so the schedule and
event sequence are already deterministic. One scalar in every record's prefix is the entire
defect.

The liteinst row is the control that matters: `dtid` normalisation does **not** collapse
liteinst's two classes, so this is not a normalisation that flattens everything.

**Consequence:** all 7 DBI cells in the cross-backend DETLOG matrix are currently `WITHHELD` as
self-nondeterministic. One fix converts all seven into real parity numbers. That is a sharper
argument for priority than "dominant divergence class".

## 3. NEW: a lead on hermit-w2's 19/19/28/38 unattributed residue

Their `+addr` canonicalisation bought **zero** (36→36, 54→54, 74→74). Two factors explain it,
one of which they did not isolate. Layered diagnostic, coverage of the ptrace golden:

| layer | notsc | bin_true | detlog_syscalls |
| --- | --- | --- | --- |
| L0 hex only | 4 % | 5 % | 1 % |
| L1 + `dtid` | 56 % | 51 % | 50 % |
| **L2 + syscall ordinal** | **82 %** | **80 %** | **85 %** |
| **L3 + decimal `Ok(addr)`** | **94 %** | **94 %** | **89 %** |

- **L2 — the factor not previously isolated.** DBI's `finish syscall #N` ordinals are offset
  from ptrace's by **exactly 1**, on every name-aligned record: the delta distribution is
  `{1: 41}` on `notsc`, `{1: 31}` on `bin_true`, `{1: 161}` on `detlog_syscalls`, with no other
  value anywhere. ptrace executes one extra syscall before the guest's first, so the two
  streams are the *same sequence renumbered*. Every `finish syscall #N` line therefore differs
  on a counter, not on content.
- **L3 — why `+addr` was inert.** hermit prints syscall **return values in decimal**:
  `= Ok(4214784)` vs `= Ok(4218880)`. A `0x<hex>` canonicaliser does not touch them. The
  addresses that differ are simply not written in hex.

Both are **diagnostics, not tiers**, and neither should be published as a score: L2 masks a
real ordinal, and L3 could mask a genuinely large returned count. As *attribution* they should
account for a large share of the 19–38 lines hermit-w2 could not explain, and re-running their
probe with both normalisations is the cheap next step.

## 4. NEW: the calibration that changes DBI's standing

| backend | L0 (hex only) | L3 (all three discounted) |
| --- | --- | --- |
| kvm, `notsc` / `bin_true` | 87 % / 80 % | **96 % / 94 %** |
| dbi, `notsc` / `bin_true` | 4 % / 5 % | **94 % / 94 %** |

DBI currently reads as the worst backend in the matrix — unscoreable, every run unique, 4 %
coverage. **At the same diagnostic layer it is within a couple of points of KVM, the closest
backend.** Its DETLOG gap is one unvirtualised identity field plus two formatting artifacts,
one of which (decimal addresses) it shares with KVM.

## 5. What is NOT claimed

- **No fix is proposed here as a decision.** The fork is hermit-w2's and it is an architecture
  call. I filed `owner_decision_virtualise_dbi` to hold it because the task that analysed it is
  closed and nothing open carried the decision — a dropped baton, not an open question. That
  task adds a third option (c): print the `DetTid` DETLOG already has rather than the
  backend-supplied raw tid, which touches no guest-visible contract — *if* a `DetTid` is
  available at every `detlog!` site under DBI, which I did not verify.
- **The normalisations must not become the fix.** Normalising `dtid` in a comparator would
  make the matrix green while the defect stayed; that is why every layer above is labelled a
  diagnostic and why the shipped matrix still reports all 7 DBI cells as `WITHHELD`.
- **Nothing here transfers to stack or heap.** DETLOG only.
- **The residual after L3 is real and unexplained** — 6 % on `notsc`, 11 % on
  `detlog_syscalls`. I did not characterise it.

## 6. Reproduction

```bash
# the observation, in one command pair
hermit --log=info --backend=dbi    run --strict --base-env=minimal -- ./notsc 2>&1 | grep -m1 -o 'dtid [0-9]*'
hermit --log=info --backend=ptrace run --strict --base-env=minimal -- ./notsc 2>&1 | grep -m1 -o 'dtid [0-9]*'

# the self-determinism claim: 30 runs, count distinct streams before and after normalising dtid
for i in $(seq 1 30); do
  hermit --log=info --backend=dbi run --strict --base-env=minimal -- ./notsc 2>&1 >/dev/null \
    | grep -o 'DETLOG .*' | sed -E 's/dtid [0-9]+/dtid D/' | md5sum
done | sort | uniq -c        # 1 class. Drop the sed and it is 30.
```

Raw data: `../at-723d19ad/attempts.tsv` (1260 runs; the DBI rows are 210 of them) and the 30
DBI stream files per guest under the collection directory. Tables here:
`dtid-observed.csv`, `selfdet-dtid.csv`, `decomposition.csv`.
