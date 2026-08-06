# SaBRe's 7 "red" cells are not red — they carry a stale, misattributed availability verdict

**Task:** `sabre-close-remaining-cells` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Outcome first

**I closed zero cells, and the reason is that there were no red cells to close.** The task asks to
"root-cause+fix SaBRe's remaining RED cells". Root-causing them shows they are not reds: all 7 are
`outcome=unavailable`, which is an **absence produced by a gate at collection time**, and that gate no
longer holds — SaBRe runs clean on this host right now.

## What the scorecard records

7 enabled SaBRe cells, **0 passing**, `deterministic` blank, `parity` blank, `outcome=unavailable`,
**unchanged across both scorecard snapshots** (Aug 3 → Aug 5):

`applications/timed-progress-bar` · `c-programs/get-robust-list-self` · `c-programs/ioctl-fioclex` ·
`c-programs/kcmp-eperm` · `data-handling/archive-roundtrip` · `system-utils/date-nanoseconds` ·
`system-utils/random-device`

## Root cause: the availability gate, and its reason string is wrong

`collect-envelope.rs:241-242` records `unavailable` whenever `backend_available()` is false, with a
**hardcoded** reason:

```rust
("unavailable".to_string(), 0i64, "backend binary not present in this checkout".to_string())
```

But `backend_available()` (`:492-512`) does **not** check for a binary. It **runs a probe**:

```rust
timeout 60s <hermit> run --backend <backend> -- /bin/true
```

So the reason string is a **misattribution**: any probe failure — crash, timeout, missing shared
library, host contention — is recorded as "binary not present". Here it is simply false:

| check | result |
| --- | --- |
| is the sabre binary present? | **yes** — `target/install_pkg/rsrcs/sabre`, 94,096 B, 2026-08-03 20:45 |
| does the probe pass now? | **yes** — `rc=0`, clean stdout and stderr |

**The recorded cause is not the actual cause, and the actual condition no longer holds.**

Note this is the same defect class I reported on the provenance task (#268): a value that does not
carry the condition it was computed under. Here the *reason* field actively asserts a condition that
was never checked.

## SaBRe passes the cells' own mode

The 7 cells are all `test_mode=verify`, i.e. `--strict --verify`. Run on this host, portable profile:

| guest | rc | verdict |
| --- | ---: | --- |
| `/bin/true` | 0 | Determinism verified |
| `/bin/echo hi` | 0 | Determinism verified |
| `/bin/date +%N` — **nanosecond clock** | 0 | Determinism verified |
| `sh -c 'head -c 16 /dev/urandom \| od -An -tx1'` — **entropy** | 0 | Determinism verified |

The last two are the determinism-riskiest shapes in the set, and they map onto two of the actual cells
(`system-utils/date-nanoseconds`, `system-utils/random-device`). The urandom guest's stdout is
Detcore's virtualized stream (`29 72 bb 04 4d 96 df 28 71 ba 03 4c 95 de 27 70`), not host entropy —
so SaBRe is determinizing, not merely running.

## What I have NOT shown

**I did not run the 7 specific cells**, so I am not claiming they pass:

* three are C fixtures that are **not built** on this host (`get_robust_list_self`, `ioctl_fioclex`,
  `kcmp_eperm`);
* `data-handling/archive-roundtrip` and `applications/timed-progress-bar` I did not attempt.

The claim is narrower and sufficient: **the blanket unavailability gate is wrong, so these cells would
be *measured* rather than skipped.** Whether each then passes is the next question, not this one.

Also note the prior `close-top-gap-cells-toward-100` note recorded "sabre … UNBUILDABLE here:
CMakeLists.txt and cmake is not installed". That is true for *rebuilding* SaBRe — and it is also why
the present binary matters: it was built on 2026-08-03, before cmake went missing, and it still works.
**Absence of a build toolchain is not absence of the artifact.**

## Recommended actions

1. **Fix the reason string** (`collect-envelope.rs:242`): record *why* the probe failed — exit status
   and a stderr excerpt — instead of asserting "binary not present". One field; it converts a
   misleading record into a diagnosable one. This is the root-cause fix, because the misattribution is
   what let a stale gate sit unexamined across two snapshots.
2. **Re-run the collector for SaBRe.** The gate now passes, so the 7 cells will produce real verdicts
   (pass/fail + parity) in place of blanks. This is the actual burndown step and it needs no code
   change.
3. **Build the three C fixtures** so `get-robust-list-self`, `ioctl-fioclex` and `kcmp-eperm` can be
   measured at all.
4. **Stop counting SaBRe's 0/7 as a parity deficit.** It is an availability artifact; carrying it in a
   parity readout implies a backend quality problem the evidence does not support.

## Provenance

* Binary under test: `hermit/target/release/hermit`, built **2026-08-03 20:47** (not current main).
* `LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64` (the libunwind workaround; `/tmp/lu` was cleaned
  earlier today and I re-extracted it durably).
* Flags: `--strict --verify --no-virtualize-cpuid --max-timeslice=disabled --log=info`.
* Scorecard read: `compat-envelope/scorecard.csv` @ `13c791e`.
