# Parity is bitwise-DETLOG, not stdout — audit re-examination (P0, owner correction)

- **Date:** 2026-08-04 (UTC)
- **Task:** `parity-definition-is-wrong-stdout-not-bitwise` (supersedes the framing in
  `parity-metric-has-no-negative-side-by-construction` and
  `parity-scorecard-cells-may-pass-on-tests-that-cannot-fail`).
- **Scope:** AUDIT / MEASUREMENT + verification of one landed tooling change. No estimates on
  box-blocked cells. All code claims cite file:line read this session.
- **Hermit origin/main HEAD at audit:** `f5523c09` (fresh `with-proxy git fetch origin main`).

## Owner correction (the definition)

Full deterministic-execution **parity is BITWISE DETERMINISM**: the **INFO logs completely
match** between backends — which *implies* syscall inputs and outputs are identical — **with
identical virtual-time timestamps**; stack/heap hashing on spot checks goes deeper still.
**Stdout matching is NOT parity** (not even a weak version): it is a different, much weaker
channel with no negative side. The prior report ("parity = stdout-hash equality, under-enforced")
understated it — the *definition in the measurement code is wrong*, not merely under-enforced.

## Deliverable (a) — the cheating-vector flag: RENAMED and LANDED (premise refuted)

Owner premise as handed: "`hermit log diff --strip-lines` CONFIRMED STILL PRESENT — the unsafe
rename never landed. ANY CELL USING IT HAS A FAKE PASS." **This premise is now FALSE.**

- The predecessor's note (14:41 UTC) correctly said it was *not yet* landed.
- It **landed at 14:45:32 UTC today** (minutes after the owner's message) as hermit commit
  **`f5523c09` "tooling: mark log normalization explicitly unsafe"**, verified on `origin/main`
  by fresh fetch + `git merge-base --is-ancestor f5523c09 origin/main` (rc=0).
- Contents (source-verified at `detcore/src/logdiff.rs`): the CLI flag is now
  `#[clap(long = "unsafe-strip-lines")]` (:44); the doc comment (:39–43) warns "erases timestamps
  and syscall values that bitwise parity exists to compare … doing so is cheating"; a landed
  regression test `unsafe_strip_lines_cli_name_and_warning_are_explicit` (:695–712) asserts the
  bare `--strip-lines` spelling now **errors**, `--unsafe-strip-lines` parses, and the help text
  carries the warning. Two skill docs updated to match.

**Cheating-vector exploitation check (does any cell USE it?):** grep for `strip-lines` /
`strip_lines` across `hermit ci-hub scripts reverie compat-envelope` (excluding `/target/` and
`experiments/**/ignored/`) finds **zero command-line callers** of the flag — only skill docs (now
updated) and vendored `experiments/demo5_bisect_.../ignored/` snapshots. **No scorecard cell has a
fake pass via the CLI flag.** The Rust *field* `strip_lines` is unchanged, so internal callers
still compile. **BUT see the deeper hazard below: the equivalent normalization is default-ON
inside `hermit run --verify` with no flag at all.**

## Deliverable (b) — what the CURRENT parity checks actually compare (from source)

There are **two distinct measurements**, and **neither is bitwise INFO-log parity with matching
timestamps.**

### 1. Cross-backend `parity` (the "108/200", "18–22 bracketed" numbers) = stdout-SHA-256 only
`compat-envelope/collect-envelope.rs`:
- `capture_parity` (:435–445): `parity = (ref_hash == this_hash)` where each hash is
  `run_and_hash(..., "ptrace", ...)` vs `run_and_hash(..., backend, ...)`.
- `run_and_hash` (:578–597): runs `hermit run --backend <b> --strict -- <guest>` — **a single
  run, no `--verify`** — and returns `Sha256(out.stdout)` (:594–595). It hashes **guest stdout
  only**. It never reads INFO logs, DETLOG, detlog-stack, detlog-heap, or any timestamp.

⇒ Cross-backend parity is **stdout byte-hash equality between ptrace and the backend**. It is
blind to the entire INFO-log stream and to virtual time. "No negative side by construction": two
backends emitting the same constant/empty string score parity=1.

### 2. `deterministic` col + `matrix.tsv` "detlog" L2 = `--verify`, stripped by default
`hermit-cli/src/bin/hermit/verify.rs::compare_two_runs` (:111):
- stdout / stderr / exit-status: exact byte/value equality, always (:126,:138,:184).
- INFO/DETLOG stream (:150–177): compared with `logdiff::LogDiffOpts { strip_lines: true, .. }`
  **by default** (:158). Only when `options.verbose` is set does it flip to
  `strip_lines=false` + `LogComparisonMode::FullTrace` (:162–165).
- Callers all leave verbose OFF for the recurring gates: `run --verify` uses
  `verbose: self.verify_verbose` (run.rs:2700), whose flag defaults false; `record start
  --verify` passes `verbose: false` (record_start.rs:414); the `verify` subcommand passes
  `verbose: false` (verify.rs:296). `run_matrix.py` runs `--verify --verify-allow both` (:383)
  and **never** passes `--verify-verbose`; `--verify-allow` only widens which exit statuses are
  accepted (run.rs:447–452), it does not touch stripping.

What `strip_lines=true` erases before comparison — `detcore/src/logdiff.rs::strip_log_entry`
(:177–210): **every numeric literal → `<NUM>`** (RE1 :190–191,207, comment "terrible overkill"),
hex → `<ADDR>` (RE0 :182,206), durations → `<NANOSECONDS>` (RE4 :202–204), `/proc/<PID>/`,
`/tmp/<somewhere>`. Separately, `extract_log_messages` (:215–219) strips the leading log-line
timestamp regardless of the flag.

⇒ The default `--verify` verdict — the thing "L2 bitwise-identical" and the matrix "detlog" kind
claim to establish — is a **stripped Deterministic-DETLOG compare**: syscall numeric arguments,
return values, and **virtual-time timestamps are all normalized away**. Two runs whose virtual
clocks diverged compare EQUAL. This is a within-backend run1==run2 check, not a cross-backend
comparison. `VerificationReport` records `{verified, verdict, guest_exit_code, guest_signal}` but
**not the comparison mode**, so a consumer cannot distinguish stripped-parity from bitwise-parity
(Proxy Binding failure). See memory `verify-verdict-bound-to-stripped-compare-not-bitwise`.

## Re-examination — of the "18–22 genuinely bracketed" liteinst cells, how many compare full INFO logs with matching timestamps?

**ANSWER: ZERO.** This is not an estimate; it follows directly from (b.1): the liteinst `parity`
metric is `Sha256(stdout)` equality and reads no logs and no timestamps at all. Every liteinst
parity cell — all 108, and the tightened firm subset — lives on the stdout channel.

To be precise about the two different questions:
- **Bracketed on the OWNER's bitwise definition** (full INFO-log match + identical virtual-time
  timestamps): **0 cells.** No liteinst cell (and no cell in the whole compat-envelope system)
  compares INFO logs cross-backend; the only log-comparing path (`--verify`) is within-backend
  and strips timestamps/numbers by default.
- **Bracketed on the STDOUT-hash channel** (has a negative witness so an inert host-passthrough
  backend would diverge on stdout): **17 firm cells** — the predecessor's committed slice
  `../liteinst-ptrace-frontier-gap_20260804/genuinely-bracketed-parity-cells.md` (14 value-
  emitting + 3 `meminfo-*` gate; 18 when #1397 arch-prctl lands; ≤23 if 6 weak checksum/boolean
  cells are counted). I read that slice; its per-cell fixture classification is internally
  consistent. But this is stdout-channel bracketing, **a strictly weaker property than the owner's
  parity**, so it does not answer "compares info logs with timestamps."

So the owner is right that 18–22 was overstated *for the bitwise definition*: the bitwise-parity
coverage 0.2 can claim on liteinst is **0**. The 17 are real only as stdout-differential brackets.

**43 error-canonicalization cells stay BOX-BLOCKED — NOT estimated** (per-syscall native host
errno is unavailable in the agent sandbox). 4 const-string signal cells (`hello-alarm`,
`hello-signals`, `sigpipe-siginfo`, `dbi-self-sigqueue`) remain firmly vacuous. Carried forward
verbatim from the predecessor's slices; no new estimate applied.

## Deliverable (c) — what it would take to make the scorecard measure BITWISE parity

The generative cure (per Proxy Binding, "carry the condition with the value"):

1. **Cross-backend INFO-log comparison, not stdout hash.** Replace/augment
   `collect-envelope.rs::capture_parity` so parity = INFO/DETLOG-log equality between the ptrace
   golden and the backend, run with `--log=info`+ (at least DEBUG for DETLOG) and
   `strip_lines=false`, i.e. a **full-trace, unstripped, cross-backend** diff. Today it is
   `Sha256(stdout)` of a single non-`--verify` run.
2. **Make bitwise comparison selectable independent of `verbose`.** In `verify.rs`,
   `strip_lines=false`+`FullTrace` is coupled to `verbose` (which also changes diff *printing*).
   Add a quiet strict-verify mode (e.g. `--verify-bitwise`) that sets `strip_lines=false` without
   the verbose printing, so gates can demand the strong compare.
3. **Carry the comparison mode WITH the verdict.** Extend `VerificationReport` /
   `VerificationOutcome` to record `{strip_lines, comparison}` (or a `bitwise: bool`), and a
   `--verify-json` that emits it. A parity consumer must require the *bitwise-qualified* verdict,
   never a bare `verified` — otherwise re-keying the rr ratchet (#1543) onto `verified` silently
   weakens to a stripped compare (see `validate-sh-rr-ratchet-stdout-only-vs-record-verify`).
4. **Identical virtual-time timestamps must be in-scope of the compare.** Bitwise parity requires
   the DETLOG virtual-time timestamps to match; today RE1/RE4 strip them and
   `extract_log_messages` drops the log-line timestamp. The full-trace path must retain and
   compare the virtual-time field. (Address reuse remains a genuine confound; the standing TODO in
   `strip_log_entry` — a monotonic debug allocator / post-facto address renumbering — is the
   principled way to compare addresses without stripping, rather than blanket `<ADDR>`.)
5. **Spot-check stack/heap hashing** (`--detlog-heap --detlog-stack`, the L3 rung) enriches the
   INFO log on selected cells to check deeper program bits, exactly as the owner asked.

This is a heavy hermit-cli change (owner-gated where it touches the verify/verification-report
contract) and is deferred in the current OOM / DAG-cap window per
`verify-verdict-bound-to-stripped-compare-not-bitwise`. Deliverable (c) here is the *design*; the
owner asked to STATE what it would take, and (a) — the anti-cheating rename — is the piece that
was in-scope-to-land and has landed.

## Bottom line for the owner

- (a) **DONE + LANDED:** `--strip-lines` → `--unsafe-strip-lines` with anti-cheat warning + test
  is on `origin/main` at `f5523c09`. The "never landed" premise was true at 14:41 UTC, false by
  14:45 UTC. No scorecard cell invokes the CLI flag, so there are no CLI-flag fake passes.
- (b) The current cross-backend `parity` = **`Sha256(stdout)`** (reads no logs); the `--verify`
  `deterministic`/detlog verdict = **stripped Deterministic-DETLOG** (numbers + virtual-time
  timestamps normalized away by default). Neither is the owner's bitwise INFO-log parity.
- **Re-examination:** cells comparing full INFO logs with matching timestamps = **0** (bitwise
  coverage 0.2 can claim on liteinst = 0). The "17–22" are stdout-channel brackets only. 43
  error-canon cells stay box-blocked, not estimated.
- (c) Bitwise measurement requires a cross-backend unstripped full-trace INFO-log diff, a
  bitwise verify mode decoupled from `verbose`, the verdict carrying its comparison mode, and
  virtual-time timestamps kept in-scope — heavy, partly owner-gated, deferred.

## Reproduction

```
# (a) landed:
cd hermit && with-proxy git fetch origin main
git merge-base --is-ancestor f5523c09 origin/main; echo rc=$?      # 0 = landed
git show f5523c09 -- detcore/src/logdiff.rs | grep -n 'unsafe-strip-lines\|cheating'
# no CLI caller of the flag:
grep -rn 'strip-lines' hermit ci-hub scripts reverie compat-envelope \
  | grep -v -e /target/ -e /ignored/ -e '.claude/skills'            # empty
# (b.1) stdout-only parity:
sed -n '435,445p;578,597p' compat-envelope/collect-envelope.rs       # capture_parity / run_and_hash
# (b.2) strip default in --verify:
sed -n '150,166p' hermit/hermit-cli/src/bin/hermit/verify.rs
sed -n '177,210p' hermit/detcore/src/logdiff.rs                      # strip_log_entry
```
