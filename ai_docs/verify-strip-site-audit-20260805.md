# Verify/compat-envelope strip-site audit — every place output is stripped or normalized before comparison

**Task:** `verify-tightening-high-confidence-compat-scorecard`
**Date:** 2026-08-05
**Scope of this pass (owner-narrowed, egress-free):** enumerate *every* site where the
detlog/log/output comparison **strips or normalizes** before comparing, and for each
give a verdict — **LEGITIMATE** (filtering injected or genuinely irreproducible noise)
or **MASKING-A-HOLE** (destroying the ability to detect a real divergence) — with the
**denominator** each site applies to.

**Deferred (not done here, by directive):** the corrected full-corpus scorecard run.
`validate` was reported blocked by the `detcore_misc` livelock at concurrency
(attributed by the dispatching coordinator to reverie#355 — premise not independently
verified here), and egress was down box-wide. No fetch/push/land was performed.

---

## Evidence anchors

| Thing | Exact value |
| --- | --- |
| Hermit source read | `rrnewton/hermit` primary checkout, `main` @ `b64d893ae9ea6404472eae9cb86102d91ec642ef` |
| Reverie source read | primary checkout @ `025d37800d347c32711038bd0a3889e8e4774c2b` |
| Scorecard anchor CSV | `compat-envelope/fullcorpus-scorecard.csv`, hermit `82a8e853357584a3a567fd80812e015572a607c7`, reverie `a4f33d69a56ed4233a53b218c39d93807ffc8cd0`, `run_utc=@1785621409` |
| Live log sample | `hermit --log=debug --log-file=… run --strict -- /bin/echo hello`, run boxed via `scripts/hermit-box-run` (cgroup-v2 scope ACTIVE), binary `hermit/target/debug/hermit` built 2026-08-03 (**pre-#1595**, see Limitations) |

### Denominators used throughout

Derived directly from the anchor CSV (commands in *Reproduction* below):

- **1,200 rows** = 200 test IDs × 6 backends.
- **ptrace reference outcome:** 179 `pass`, 20 `diverge`, 1 `timeout`.
- **895 comparable non-ptrace cells** = 179 ptrace-passing tests × 5 non-ptrace backends.
- **672** non-ptrace rows carry `parity=1`; **669** of those sit on a ptrace-*passing*
  test. (An earlier task note attributed the 672→669 delta to a stale-append/`run_utc`
  ordering fix. That attribution is **wrong**: independently reproduced here, the delta
  is exactly the 3 cells that claim `parity=1` on a test whose ptrace reference did not
  pass. The corrected number 669 stands; the stated cause does not.)
- Per-backend `parity=1` restricted to ptrace-pass tests: e9patch 172, sabre 141,
  dbi 136, kvm 112, liteinst 108.
- **Lane split:** 1,194 rows `portable`, 6 rows `privileged`.

---

## Verdict key

- **LEGITIMATE** — discards something that is genuinely not reproducible (real wall
  clock) or is instrumentation-injected, *and* cannot hide a guest-observable
  divergence.
- **MASKING-A-HOLE** — a real divergence in the compared population can survive this
  site and still report equal. A green produced through it verifies strictly less than
  it claims.
- **LEGITIMATE-BUT-UNRECORDED** — defensible discard, but the verdict/row does not
  carry the fact, so a consumer must *infer* rather than *observe* what was compared.

---

## Summary table — 25 sites (F1/F2 detailed separately below)

| # | Site | File:line | Applies to | Verdict |
| --- | --- | --- | --- | --- |
| A1 | wall-clock prefix strip | `detcore/src/logdiff.rs:309-327` | every log comparison | **LEGITIMATE** |
| A2 | `RE0` every `0x…` → `<ADDR>` | `logdiff.rs:210,234` | `Stripped` mode only | **MASKING-A-HOLE** |
| A3 | `RE1` every decimal/duration → `<NUM>` | `logdiff.rs:218-219,235` | `Stripped` mode only | **MASKING-A-HOLE** |
| A4 | `RE4` `\b[\d][\d_.]*s\b` → `<NANOSECONDS>` | `logdiff.rs:230,232` | `Stripped` mode only | **MASKING-A-HOLE** |
| A5 | `RE3` `/proc/<pid>/` → `/proc/<PID>/` | `logdiff.rs:226,233` | `Stripped` mode only | **MASKING-A-HOLE** |
| A6 | `RE2` `/tmp/.*"` → `/tmp/<somewhere>` (**greedy**) | `logdiff.rs:223,236` | `Stripped` mode only | **MASKING-A-HOLE (worse than documented)** |
| A7 | `<hostaddr 0x…>` → `<addrN>` ordinal | `logdiff.rs:279-304` | `Canonical` mode | **LEGITIMATE** (but INERT — 0 producers) |
| A8 | `Deterministic` mode selects only DETLOG+COMMIT | `logdiff.rs:139-151,823-826` | `Stripped` mode | **MASKING-A-HOLE** (36.0% of messages never looked at) |
| A9 | `InternalIOPolling`/SaBRe-marker COMMIT drop | `logdiff.rs:362-367` | `Deterministic` only — **absent in FullTrace** | **LEGITIMATE**, but its absence under `Canonical` is an open contract gap |
| A10 | `advancing committed_time` drop | `logdiff.rs:377-379` | `Deterministic` only — **absent in FullTrace** | same as A9 |
| A11 | `--ignore-lines` substring drop | `logdiff.rs:410-423` | caller-set | **LEGITIMATE** in `--verify` (empty, asserted); **MASKING-A-HOLE** in chaos lanes (F2) |
| A12 | `--skip-commit` / `--skip-detlog` / `--include-detlogs` | `logdiff.rs:113-137` | caller-set | **LEGITIMATE** in `--verify` (off, asserted); **MASKING-A-HOLE** in hermit-verify lanes (F1/F2) |
| A13 | `--git-diff` path runs `git diff -w` | `logdiff.rs:656-689` | opt-in, no live caller | **MASKING-A-HOLE** (whitespace-blind; applies *neither* strip nor canonicalization) |
| B1 | **default `--verify` strictness = `Stripped`** | `run.rs:2779-2783`, `record_start.rs:465-469` | every bare `--verify` invocation | **MASKING-A-HOLE — highest reach** |
| B2 | KVM `compare_logs=false` output-only | `run.rs:2784`, `verify.rs:686-690` | 200/200 KVM rows | **LEGITIMATE-BUT-UNRECORDED** in the CSV |
| B3 | `extract_sabre_detlogs` removes matching lines from guest **stderr** | `run.rs:75-98` | SaBRe rows | **LEGITIMATE-BUT-UNRECORDED** (see detail) |
| B4 | `Fstat` logged **without** its output struct | `detcore/src/lib.rs:773` | all backends, all strictness | **MASKING-A-HOLE** (self-declared FIXME T136880615) |
| B5 | `Ivar` Display prints `<ivar NoWaiter/HasWaiter/value>`, never the host pointer | `detcore/src/ivar.rs` (test `display_excludes_host_address:193`) | scheduler ivar log lines | **LEGITIMATE — and the right pattern** (don't emit the host pointer rather than erase it later) |
| C1 | detlog memory covers only `[stack]`/`[heap]`, flags default OFF | `detcore/src/lib.rs:718-765` | 1,200/1,200 rows have **zero** memory evidence | **MASKING-A-HOLE (coverage)** |
| C2 | no injected-region provenance projection | — (absent) | e9patch/SaBRe/LiteInst | blocker for enabling C1 on patching backends |
| D1 | **cross-backend "parity" = sha256 of guest stdout only** | `collect-fullcorpus.sh:142,168,172-176` | 895/895 comparable cells | **MASKING-A-HOLE — dominant** |
| D2 | `deterministic` column comes from bare `--verify` (=`Stripped`), documented in-file as "L2 DETLOG-bitwise self-verify" | `collect-fullcorpus.sh:15,144,166` | 1,200/1,200 rows | **MASKING-A-HOLE** (+ same L2 mislabel as E1) |
| D3 | portable lane adds `--no-virtualize-cpuid --max-timeslice=disabled` | `collect-fullcorpus.sh:129` | 1,194/1,200 rows | **LEGITIMATE-BUT-UNRECORDED** |
| D4 | `check_parity` = same stdout-hash construction | `collect-envelope.rs:432-443` | envelope collector | **MASKING-A-HOLE** |
| E1 | `strict_compatibility_probe` hardcodes `assurance=L2` before comparing, invokes bare `--verify` | `hermit/validate.sh:2915,2919` | **168 call-site lines** | **MASKING-A-HOLE (mislabel)** |

Plus three non-defects worth naming so they are not re-reported: **D5** the collector
correctly refuses parity when the ptrace reference itself failed (`ptv.fail` marker →
`parity=""`, not a false match — `collect-fullcorpus.sh:149-157,172`); **E3**
`rr_compatibility_probe` is already migrated onto typed `bitwise_parity` with a
7-fixture two-directional consumer bracket; **F3** `strip_times` on schedule events is
correct for RCB-based schedule replay.

---

## Site detail

### A1 — wall-clock prefix strip (LEGITIMATE)

`extract_log_messages` splits on the timestamp regex and discards it. This is the one
genuinely irreproducible datum. Bracketed both directions by
`canonical_wall_clock_prefix_difference_compares_equal` (`logdiff.rs:1443-1462`): two
runs differing *only* in prefix (and even in prefix *format*) compare EQUAL, while the
sibling tests show non-prefix differences still diverge.

### A2–A6 — `strip_log_entry`'s five erasure classes (all MASKING-A-HOLE)

Reachable only when `strip_lines = true`, i.e. the `Stripped` strictness — which is the
**default** for every bare `--verify` (see B1). What each erases:

- **A2 `RE0`** `\b0[xX][A-Fa-f0-9]+\b` → `<ADDR>`. Cannot distinguish a varying host
  pointer from a reproducible hex value: syscall arguments printed `{:#x}` (`flock`
  `operation=0x2` vs `0x6`), guest memory ranges, content digests, cpuid leaves all
  collapse to one token. Because *every* address maps to the same token, an
  **allocation-order** or **aliasing** change compares EQUAL — proven by
  `canonical_allocation_order_difference_compares_unequal` (`logdiff.rs:1319-1355`),
  whose second half asserts the stripped comparator *does* hide the case.
- **A3 `RE1`** `\b[\d][\d_]*(?:\.[\d][\d_]*)?(?:ns|us|µs|ms)?\b` → `<NUM>`. The source's
  own comment is `// This one is terrible overkill.` It erases virtual-time timestamps,
  syscall inputs and results, fds, tids, turn numbers, counts, sizes, and numeric flags.
  End-to-end bracket already in-tree:
  `stripped_matches_but_bitwise_diverges_on_numeric_only_log_difference`
  (`verify.rs:1012-1045`) — logs differing only in a numeric DETLOG value give
  `Stripped` = *matched/verified* while `Canonical` = *diverged*.
- **A4 `RE4`** matches any digits-then-`s` token, not only durations.
- **A5 `RE3`** collapses all `/proc/<pid>/` to one token, losing pid identity and
  aliasing.
- **A6 `RE2`** is `/tmp/.*"` — **greedy, and `.` matches everything but newline**, so it
  eats from the first `/tmp/` to the **last** double-quote on the line, not to the end
  of the path. Demonstrated:

  ```
  IN : DETLOG [syscall] openat(AT_FDCWD, "/tmp/x", O_RDONLY) = Ok(7) size=4096 flags="O_CLOEXEC"
  OUT: DETLOG [syscall] openat(AT_FDCWD, "/tmp/<somewhere>
  ```

  The syscall's flags, result, and size are erased along with the path. This over-reach
  is not described in the code comment and is materially worse than "strip tmp paths".

The CLI spelling is already `--unsafe-strip-lines` with an anti-cheat doc and a
regression test rejecting bare `--strip-lines` (`logdiff.rs:38-46,885-903`). That is
good hygiene for the *flag*; it does not touch the **internal default** (B1).

### A7 — address canonicalization (LEGITIMATE, but currently INERT)

The `Canonical` policy's tier-2 step rewrites only explicitly-marked
`<hostaddr 0x…>` values to `<addrN>` ordinals by first appearance, per run, in a
pre-pass over the whole ordered message list (`logdiff.rs:555-575`). This preserves
identity, order, and aliasing — exactly what A2 throws away — and is bracketed four
ways (`logdiff.rs:1280-1437`): address-only/ASLR shift EQUAL (with a raw positive
control proving non-vacuity), allocation-order UNEQUAL, aliasing 1,1-vs-1,2 UNEQUAL,
bare syscall-arg hex `0x2`/`0x6` UNEQUAL, virtual-time decimal UNEQUAL.

**Caveat that matters:** the doc at `logdiff.rs:246-251` states no detcore producer
currently emits the marker. Confirmed by grep — `host_addr()` has **zero production
call sites**. So on real logs today the `Canonical` policy reduces to *"strip the
wall-clock prefix, compare everything else exactly"*. That is a **stricter** state than
advertised, not a weaker one, so it is not a hole — but the ordinal machinery is
unexercised outside unit tests, and the first real `<hostaddr>` producer will be its
first live coverage.

*Why there are no producers* is itself the good news, and it is B5: detcore's log
producers already avoid emitting host pointers. `Ivar`'s `Display` prints
`<ivar NoWaiter>` / `<ivar HasWaiter>` / `<ivar 7>` rather than its address, pinned by
`display_excludes_host_address` (`detcore/src/ivar.rs:193`). Producer-side avoidance is
strictly better than comparator-side erasure, and it is the pattern any future
host-pointer print should follow before reaching for `host_addr()`.

### A8 — the selection strip (MASKING-A-HOLE), **measured**

In `Deterministic` mode the compared population is only DETLOG + scheduler COMMIT
messages. Measured on the captured `/bin/echo hello` log by faithfully re-implementing
`extract_log_messages`, `filter_detcore`, and `filter_deterministic`:

| Quantity | Value |
| --- | --- |
| total log messages | 1,009 |
| detcore-tagged messages | 814 |
| selected by `Deterministic` | **646 (64.0%)** |
| **dropped by selection alone** | **363 (36.0%)** |
| selected messages **altered** by `strip_log_entry` | **646 / 646 (100.0%)** |
| distinct originals → distinct stripped forms | **613 → 181** |
| selected messages sharing a stripped form with ≥1 other | 494 / 646 |

The last two rows are the sharpest statement of the A2–A6 defect: on a *trivial* guest,
stripping collapses 613 distinct messages into 181 equivalence classes. 494 of 646
compared messages are no longer individually distinguishable from some other compared
message. That is the resolution at which a bare `--verify` green is asserted.

### A9/A10 — poll-retry bookkeeping exclusions, and the FullTrace gap

`is_internal_io_poll_commit` and `is_scheduler_committed_time` drop scheduler
bookkeeping whose *count* is host-timing dependent (how many times a thread re-polls
before a peer becomes ready). The rationale is sound and documented at length
(`logdiff.rs:342-379`), it is bracketed three ways (`logdiff.rs:1152-1234`), and the
scheduler additionally suppresses the per-retry time-advance line at the producer
(`detcore/src/scheduler.rs:2811-2816`, emitted as `trace!` rather than `detlog_debug!`).
Verdict on the filter itself: **LEGITIMATE**.

**But these filters live in `filter_deterministic`, which `FullTrace` does not call**
(`logdiff.rs:823-826`: `FullTrace` compares `all_a`/`all_b`). So the `Canonical`
strictness that `--verify-strict` selects — the one the whole high-confidence contract
rests on — **re-exposes the very host-timing-dependent lines the project already
concluded are not comparable**, at DEBUG level where both the `InternalIOPolling`
COMMIT turns (INFO) and the `advancing committed_time` DETLOG lines (DEBUG) are present.

Exposure surface on the trivial guest: 0 `InternalIOPolling` lines but **42
`advancing committed_time` lines** out of 41 total COMMIT and 647 DETLOG messages — the
class is present even where no polling occurs.

**Consequence to plan for:** the corrected corpus run under `Canonical` should be
expected to produce a class of **false reds** on any guest doing nonblocking I/O.
Before Phase 2 runs, the BitwiseInfoV1 contract must decide explicitly between (a)
suppressing these lines at the producer (as already done for the retry time-advance),
or (b) admitting a *declared, receipt-carried* exclusion. Silently reusing
`filter_deterministic` under FullTrace would be re-widening the comparison and is not
acceptable. This is a **contract gap, not yet an observed failure** — see Limitations.

### A11/A12 — caller-set line and event-class filters

In the `--verify` path these are provably off: `ComparisonSpec` records
`ignore_lines/skip_commit/skip_detlog = false` and `compare_two_runs` threads them into
`LogDiffOpts`, with a `debug_assert` binding the spec's claim to the engine's real
default (`verify.rs:210-221,660-672`) plus a test
(`default_log_diff_opts_apply_no_line_filters`). That is a correct **carry-the-condition
-with-the-value** construction and should be the model elsewhere. Verdict there:
**LEGITIMATE**. In hermit-verify's chaos lanes they are set — see F1/F2.

### A13 — `--git-diff` (MASKING-A-HOLE, no live caller)

`git_diff` shells out to `git diff --color --color-words -w`. `-w` ignores whitespace
changes; more importantly the path applies **neither** `strip_log_entry` **nor**
canonicalization, and derives its boolean purely from git's exit code. Opt-in only, and
no caller in the verify path sets it, so reach is 0 today — but it is a second,
divergent comparator behind the same function.

### B1 — the default strictness (MASKING-A-HOLE, highest reach)

`run.rs:2779-2783` and `record_start.rs:465-469`:
`if self.verify_verbose || self.verify_strict { Canonical } else { Stripped }`.
So **every** bare `--verify` — which is what the compat-envelope collectors and 168
`validate.sh` call sites use — runs the A2–A6 + A8 comparator. Everything above is
gated behind this one default. `#1595` (merged as `9b642f6d3`) did the hard part: it
made `Canonical` selectable *quietly* via `--verify-strict`, carried `ComparisonSpec`
beside the verdict, and exposed `bitwise_parity` in `--verify-json`. What remains is
purely that **no consumer has been re-keyed onto it**.

### B2 — KVM output-only (LEGITIMATE-BUT-UNRECORDED)

`compare_logs=false` for KVM; the run prints an explicit banner and
`is_bitwise_parity()` returns false. Correct per `AGENTS.md`. The hole is downstream:
the scorecard's 19-column row has no field distinguishing "log comparison ran and
matched" from "log comparison was never attempted", so KVM's 112 `parity=1` cells look
identical in the CSV to the other backends'.

### B3 — SaBRe stderr extraction (LEGITIMATE-BUT-UNRECORDED)

`extract_sabre_detlogs` walks the captured guest **stderr**, moves every line beginning
`INFO detcore` and containing ` DETLOG ` into the log file (prefixed with a constant
synthetic timestamp), and rewrites `out.stderr` to the remainder. Necessary — SaBRe
emits detcore's log on stderr — and it is guarded by a nonzero-record check that errors
out when either run captured zero syscall DETLOGs (`run.rs:2745-2757`), which is a good
no-result refusal. Two residues: a guest that itself printed such a line would have it
silently removed from the compared stderr, and the synthetic timestamp is constant
(harmless, since A1 discards prefixes anyway). Neither fact is recorded in the verdict.

### B4 — `Fstat` display (MASKING-A-HOLE)

```rust
Syscall::Fstat(_) => syscall.display(memory), //FIXME: T136880615 - fstat structure isn't fully deterministic yet
_ => syscall.display_with_outputs(memory),
```

Every other syscall is logged with its output values; `fstat` is logged without them.
A divergence in the returned `struct stat` is therefore invisible to **every** log
comparison at **every** strictness — including `Canonical`. This is the one site on
this list that a stricter comparator cannot fix; it needs the producer to emit the
data (or to emit a determinized projection of it).

### C1/C2 — memory determinism is unmeasured

`detlog_memory_maps` returns immediately unless `--detlog-stack` or `--detlog-heap` is
set (both default off), and even when set it hashes only the regions typed `[stack]` and
`[heap]` — anonymous mmaps, data/bss, and injected-code regions are never hashed.
Neither flag appears anywhere in `collect-fullcorpus.sh` or `collect-envelope.rs`.
**Denominator: 0 of 1,200 anchor rows carry any memory evidence**, so the L3 tier of the
assurance ladder is entirely unmeasured across the envelope. Turning it on for
e9patch/SaBRe/LiteInst additionally requires the backend-neutral injected-region
provenance projection (C2), which does not exist.

### D1/D4 — the cross-backend metric is stdout only (dominant hole)

`collect-fullcorpus.sh measure()` produces the parity verdict as
`sha256(ptrace stdout) == sha256(backend stdout)`. Discarded entirely: stderr, the INFO
log, virtual-time timestamps, the syscall trace, and memory. `collect-envelope.rs:432-443`
uses the identical construction.

**Denominator: 895/895 comparable cells. All 669 claimed parity wins are stdout-hash-only** —
a divergence confined to logs, virtual time, or syscall values cannot make any of them fail.
Independently reproduced here from the raw CSV; agrees with the prior task-note figure.

Two documentation defects sit in the same file's header. Line 16 states
`parity = <backend> --strict stdout == ptrace --strict --verify stdout`, but the code
uses the plain `--strict` capture (`ptv.out`) as the reference — the NB comment at
133-137 explains exactly why it must, so the header contradicts the implementation it
describes. Line 15 calls the `det` column an "L2 DETLOG-bitwise self-verify" when the
invocation is bare `--verify`, the same mislabel as E1.

There is also no cross-backend comparator anywhere in the product to fall back on:
`compare_two_runs` has exactly two live callers (`run.rs:2759`, `record_start.rs:452`),
both same-backend (run-vs-run) or record-vs-replay. Backend-vs-ptrace log comparison
does not exist as code.

### D2 — the determinism column is a Stripped result

The `deterministic` column is `exit == 0` from `hermit run --strict --verify` with no
`--verify-strict`. Denominator 1,200/1,200 rows. Combined with D1, both numeric columns
of the scorecard measure something weaker than their names suggest: `parity` is
stdout-SHA equality, `deterministic` is a Stripped self-compare.

### D3 — portable-lane relaxations (LEGITIMATE-BUT-UNRECORDED)

1,194 of 1,200 rows ran with `--no-virtualize-cpuid --max-timeslice=disabled`. These are
determinism relaxations applied at measurement time. The row records only `lane=portable`;
the actual flag set is not carried, so a consumer must know the script to know what was
relaxed.

### E1 — `validate.sh` labels a Stripped result "L2"

`strict_compatibility_probe` sets `local assurance=L2` at `validate.sh:2915` — before any
comparison runs — and invokes `run_args=(run --strict --verify --)` at 2919. **168
call-site lines** (170 identifier occurrences including the definition and the rr
dispatch). `hermit/AGENTS.md` states in the repo's own words that default `--verify` uses
the lossy `Stripped` comparator and *cannot* establish L2, and defines L2 as
`--verify-strict --verify-json` with `bitwise_parity: true`. So the project's headline
compatibility number is a Stripped result wearing an L2 label, on a blocking gate.

The fix is mechanical and needs no product change: add `--verify-strict --verify-json`
and gate on `rr_report_has_bitwise_parity`, which already exists and is already bracketed
at `validate.sh:2712`. `rr_compatibility_probe` (139-row ratchet) is the model migration
— it consumes the typed field, demotes wrapper exit to diagnostic, and brackets its own
consumer with 7 producer-shaped fixtures in both directions.

**Standing rule for that migration:** re-keying Stripped-plus-exit-status onto
`bitwise_parity` is a **strict tightening**. Rows will flip green→red. Every flip is a
genuine product finding to file — never mask one by widening the comparison back.

### F1/F2 — hermit-verify chaos lanes

`chaos_replay` (`chaos_replay.rs:95-105`): `verify_commits: false` → `--skip-commit`
drops **all** scheduler COMMIT turns; `ignore_lines: ["CHAOSRAND"]`.
`trace_replay` (`trace_replay.rs:120-145`): `verify_commits: false`;
`verify_detlog_others: !split_branches && !chaos`; and under chaos
`ignore_lines: ["CHAOSRAND", "advance global time for scheduler turn",
"inbound syscall: exit_group"]`.

Dropping `CHAOSRAND` and the recording-only register/trap-flag entries is defensible and
documented in-code. Dropping **all COMMIT turns** and **all scheduler time advances** is
not noise filtering — it removes the scheduling evidence these lanes exist to check.
Verdict: **MASKING-A-HOLE** for the COMMIT and time-advance exclusions specifically;
these lanes are outside the compat-envelope denominators but inside the "verify we can
trust" perimeter.

---

## What this means for the scorecard

Three shared pipeline defects explain the entire bitwise-qualification gap, and they are
**not** 895 independent problems:

1. **D1/D4** — the cross-backend metric is stdout-only. Affects 895/895.
2. **B1 + A2–A6 + A8** — the comparison policy is the lossy default, and the row records
   neither the policy nor the counts. Affects 1,200/1,200.
3. **No durable dereferenceable receipt** — the 19-column row carries a stdout hash, not
   an exact-head comparison receipt or artifact references, so nothing is replayable.

A fourth gap is test-semantic rather than pipeline: only 3 test IDs
(`compat-envelope/absolute-oracles.csv`: the three `meminfo-*-v1` oracles) carry a
source-bound absolute assertion with a planted host-negative control — 15 cells across
5 backends, i.e. **15/895**. Fixing 1–3 can qualify coverage broadly; it cannot raise
high-confidence beyond the oracle-backed population.

Adding C1 (memory unmeasured, 0/1,200) and B4 (fstat outputs never logged), the honest
statement of the envelope's current resolution is: **stdout bytes plus a
number-blind, address-blind, 36%-of-messages-unread self-compare.**

---

## Recommended fix order (cheapest first, each independently landable)

1. **Re-key `validate.sh:strict_compatibility_probe` onto `--verify-strict
   --verify-json` + `rr_report_has_bitwise_parity`** (E1). No product change; copy the
   already-bracketed `rr_compatibility_probe`. Expect green→red flips; file each.
2. **Fix `RE2`'s greediness** (A6) or delete `strip_log_entry` outright. If `Stripped`
   is only for diagnostic localization, the greedy regex is a latent misleading-diagnostic
   source too.
3. **Resolve the A9/A10 FullTrace gap** *before* any corrected corpus run — decide
   producer-side suppression vs. a declared receipt-carried exclusion. Bracket both
   directions on a real nonblocking-I/O guest.
4. **Switch the collectors to `--verify-strict --verify-json`** and record
   `bitwise_parity` + `compared_log_messages` per row (D2), with missing fields rendering
   UNQUALIFIED rather than green.
5. **Add a backend-vs-ptrace comparator** (D1). This is the one item requiring genuinely
   new product code; without it bitwise stays fail-closed at 0/895 no matter what else
   lands.
6. **Fix `Fstat` output logging** (B4) — a comparator improvement cannot reach it.
7. **Enable `--detlog-stack --detlog-heap`** in the corrected run (C1), gated on the
   injected-region provenance projection for the patching backends (C2).

---

## Limitations — what this pass did NOT establish

- **No corrected corpus run.** Deferred by directive; `validate` is blocked by the
  `detcore_misc` livelock at concurrency (reverie#355), and egress was down box-wide.
  All scorecard numbers are read from the existing anchor CSV, not freshly measured.
- **The A9/A10 FullTrace re-exposure is code-path confirmed, runtime UNVERIFIED.** The
  only locally-runnable `hermit` binary (built 2026-08-03) predates `#1595` and has no
  `--verify-strict` flag, so the end-to-end demonstration was not run. The settling
  experiment: build at `b64d893a`, run a nonblocking-I/O guest under
  `--strict --verify --verify-strict --verify-json` repeatedly under load, and check
  whether `bitwise_parity` flaps with poll-retry count.
- **The log-composition measurements (A8) come from one trivial guest** (`/bin/echo
  hello`) on a pre-#1595 binary. They characterize the *comparator*, whose regexes and
  filters are unchanged at `b64d893a`, but the 64%/36% split will vary by workload.
- Sites were enumerated by reading `detcore/src/logdiff.rs`, `hermit-cli/src/bin/hermit/{verify,run,record_start}.rs`,
  `detcore/src/lib.rs`, `hermit-verify/src/`, `compat-envelope/{collect-fullcorpus.sh,collect-envelope.rs}`,
  and `hermit/validate.sh`, plus targeted greps for `strip`/`normali`/`ignore`/`skip`/`suppress`
  across `hermit/`. Reverie-side normalization was **not** audited in this pass.
- `hermit/ignored/**` contains stale copies of these files from prior landing work; all
  line references above are to the live tree only.

---

## Reproduction

```bash
cd ~/work/dev-hermit

# Denominators
awk -F, 'NR>1 && $11=="ptrace"{print $13}' compat-envelope/fullcorpus-scorecard.csv | sort | uniq -c
awk -F, 'NR>1 && $11=="ptrace" && $13=="pass"{p[$9]=1}
         NR>1 && $11!="ptrace" && $15=="1" && ($9 in p){n[$11]++; t++}
         END{for(b in n) print b, n[b]; print "TOTAL", t}' compat-envelope/fullcorpus-scorecard.csv
awk -F, 'NR>1{print $7}' compat-envelope/fullcorpus-scorecard.csv | sort | uniq -c

# validate.sh L2 mislabel
sed -n '2902,2925p' hermit/validate.sh
grep -c '^[[:space:]]*strict_compatibility_probe ' hermit/validate.sh   # 168

# Live log capture (boxed)
scripts/hermit-box-run --cpu-budget 120 --wall 300 --label strip-audit-echo -- \
  env LD_LIBRARY_PATH=$PWD/ignored/haskell-drb/hostlibs \
  hermit/target/debug/hermit --log=debug --log-file=$PWD/scratch/verify-strip-audit/echo.log \
  run --strict -- /bin/echo hello

# Canonicalization brackets (in-tree, no egress)
cd hermit && cargo test -p detcore --lib logdiff:: && cargo test -p hermit --bin hermit verify::
```
