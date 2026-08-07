# Strict-certification mutation sweep of the GREEN scorecard cells

**Task:** `strict-certification-mutation-sweep-green-cells` (owner directive #322/#323)
**Agent:** hermit-w7 (opus-5) · **Date:** 2026-08-06 · **Host:** devbig014 (316 cores)
**Hermit:** `f89c69766371806d3c9b2c3003531df2d59d6118` (primary, main); DBI from `worktrees/dbi/hermit` @ `52d56e5ce`

## Question

Owner directive #322 asks for certification that the cells marked green in
`compat-envelope/scorecard.csv` are **absolutely strictly bitwise deterministic**. The method
required (#323) is mutation testing: plant a known-wrong value/behaviour in each green cell and
confirm the cell **fails**. Any cell that still passes under a planted defect is fake-green.

## Denominator first

`compat-envelope/scorecard.csv` has **618 rows**. The set the directive calls "green cells" is
`deterministic=1` → **346 rows** (313 of those also `parity=1`). Cross-tab over all 618 rows:

| | count |
|---|---|
| `deterministic=1` ∧ `verify_compare=stripped` | 346 |
| `deterministic=0` ∧ `verify_compare=""` | 82 |
| `deterministic=""` | 190 |
| **`deterministic=1` ∧ `verify_compare != stripped`** | **0 / 618** |

Green cells by backend: **liteinst 136, kvm 130, ptrace 72, dbi 8** (= 346).

The 346 come from **five different producers**, not one: `kvm-fullcorpus-scorecard` (130),
`liteinst-fullcorpus-*` (117), `liteinst-spst-*` (39), `canonical-release-ptrace-dbi` (36),
`backend-parity-*` (24). Only `collect-envelope.rs` ever *writes* the `verify_compare` column
(line 306); the other producers' headers do not contain that field at all, yet all their rows
read `stripped` in the merged scorecard.

## The instrument, measured before anything was planted

The probe every producer uses is (`hermit/tests/backend-parity/run_matrix.py::hermit_command`):

```
hermit run --strict --verify --verify-allow both --base-env=minimal \
      --max-timeslice=disabled --tmp=/tmp [--no-virtualize-cpuid] -- GUEST
```

Run against a trivial guest with `--verify-json`, that exact command reports:

```json
{"verified":true, "bitwise_parity":false,
 "comparison":{"strictness":"stripped","strip_lines":true,"full_trace":false,
   "canonicalize_addresses":false,"exact_remainder":false,
   "stripped_prefixes":["real-wall-clock-prefix/v1",
                        "unsafe-numeric-address-and-path-normalization/v1"]},
 "compared_log_messages":{"left":239,"right":239}}
```

`rc=0`, banner `:: Success: deterministic. Determinism verified.` — while the instrument's own
`bitwise_parity` is **false**. `run_matrix.py:530-535` itself defines the guest-visible witness as
"strictly weaker; do not report it as DETLOG determinism", yet `:604` labels the result
"L2 DETLOG-bitwise" and `collect-fullcorpus.sh:15` calls it "L2 DETLOG-bitwise self-verify".

Read this correctly: under `stripped`, `bitwise_parity=false` means **"this run did not establish
bitwise parity"**, not "the runs diverged". The right verdict on the 346 is
**UNCERTIFIED-BY-CONSTRUCTION**, not proven-nondeterministic.

## Method

Six guests. Five plant a defect; one is the positive control. Each mutant appends a byte to a state
file and reads back the new size, so invocation A and invocation B differ **by construction** —
verified natively before any hermit run:

| guest | diverges in | visible at stdout+exit? |
|---|---|---|
| `clean_ctrl` | nothing (control) | no |
| `mut_stdout` | stdout | **yes** |
| `mut_exit` | exit status | **yes** |
| `mut_detlog_only` | a `read()` return length | no |
| `mut_addr` | a pointer arg to a 0-length `write` | no |
| `mut_path` | an `openat` path arg | no |

Tiers are measured with two separate `hermit --log=LEVEL --log-file=...` invocations rather than
`--verify`'s internal double-run, because `--verify` intermittently hits the known slow-drain (one
`clean_ctrl --verify-strict` run exceeded 900 s after completing twice in under 240 s).
Cross-checked on `clean_ctrl`/ptrace: internal double-run gave INFO 56\|56 / 0 divergent and
DEBUG 217\|217 / 14 divergent; separate invocations gave INFO 56\|56 / 0 and DEBUG 217\|217 / 17.
The INFO tier agrees exactly.

Every tier cell carries its compared-line count. A comparison with a zero line count is reported
`VACUOUS`, never `missed`.

## Result 1 — the producer probe misses 3 of 5 planted defects

ptrace, the exact producer command, 6 guests, all 6 ran:

| guest | producer probe | note |
|---|---|---|
| `clean_ctrl` | **PASS** | positive control fires — the probe is not inert |
| `mut_stdout` | FAIL | can-fail |
| `mut_exit` | FAIL | can-fail |
| `mut_detlog_only` | **PASS** | ← planted DETLOG divergence survives |
| `mut_addr` | **PASS** | ← planted address divergence survives |
| `mut_path` | **PASS** | ← planted path divergence survives |

The probe's true sensitivity is **guest-visible only** (stdout + exit status). Cause:
`unsafe-numeric-address-and-path-normalization/v1` normalises *numbers generally*, not just
addresses — so a differing `read()` return length, a differing pointer arg and a differing path all
collapse to the same token.

## Result 2 — the tier ladder, and why `--verify-strict` is NOT the drop-in fix

`--verify-strict` **fails the positive control**. Diffing the retained log pair after stripping only
the wall-clock prefix: **INFO 56\|56 lines, 0 divergent; DEBUG 217\|217 lines, 14 divergent.** All 14
divergent lines are DEBUG. First divergence is log message 40. Four classes, 3 of them hermit's own
instrumentation:

| class | lines | kind |
|---|---|---|
| `reverie_ptrace::vdso: 3 patched __vdso_*` | 6 | **instrumentation** — same 3 symbols, *identical addresses*, reversed emission order |
| `reverie_ptrace::timer: Setting precise_ip ... CpuId{...}` | 4 | **instrumentation** — per-CPU enumeration order |
| `reverie_ptrace::task: beginning inject of ... execveat` | 1 | **instrumentation** |
| `detcore: DETLOG (pre/post) registers ... rbx 0xe9ff0800` vs `rbx 0x5ff0800` | 2 | **probe-induced** — CPUID leaf-1 EBX (host APIC id) in a guest register, caused by the probe's own `--no-virtualize-cpuid`. *Corrected: first published as "guest nondeterminism — uninitialised rbx". See below.* |
| `detcore::tool_global: Nondeterministic realtime elapsed: 97.70384ms` vs `15.436131ms` | 1 | **instrumentation** — hermit literally names it "Nondeterministic" |

> **Correction to the table above.** The `rbx` row was first published as
> *"guest nondeterminism — uninitialised `rbx` at first syscall"*. That was wrong.
> It is **probe-induced**: the value is CPUID leaf-1 EBX (the host APIC id) reaching
> a guest register because the backend-parity probe passes `--no-virtualize-cpuid`.
> Re-measured, counting divergent `DETLOG (pre|post) registers` lines over two runs,
> 3 reps per arm: **with** the flag 0, 2, 2; **without** it 0, 0, 0. Removing the flag
> eliminates it. (A parallel investigation reports 6/6 with and 0/3 without; the
> direction agrees, the rate does not, and 3 reps is too few for a rate.)
>
> This makes the conclusion **stronger**, not weaker: with `rbx` reclassified, the
> clean control has **no genuine guest-nondeterminism class at all**. Every one of
> the five classes is either hermit's own instrumentation or an artefact of the
> probe's own flag.

So flipping the matrix to `--verify-strict` as-is would turn green cells red for hermit's own DEBUG
emission order and its own probe flag, not for guest nondeterminism. **The root cause is that no INFO-tier exact comparator
exists:** hermit exposes only `stripped` (too weak — erases numbers) and `canonical/full_trace`
(too strong — includes the three instrumentation classes above).

## Result 3 — certification matrix (backend × tier)

`ctrl` = divergent/compared lines for the positive control (0 = tier is sound).
`caught` = planted defects detected, out of 5.

> **Correction — what "5/5" does and does not mean.** These counts are for the
> tier **as a whole** (log + stdout + exit), which is what the harness compares.
> The **DETLOG channel alone catches 4 of 5**: `mut_stdout`'s planted defect is
> invisible in the DETLOG at *every* strictness, because the DETLOG records a
> `write()` pointer and length, never the buffer contents, and `counter=1` and
> `counter=2` are the same length. It is caught only by the separate stdout
> compare. A guest writing differing same-length bytes to a **file** would be
> caught by nothing — a producer-side hole no comparator strictness closes.

| tier | ptrace (72 cells) | liteinst (136) | dbi (8) | kvm (130) |
|---|---|---|---|---|
| **T-guest** (the producer probe) | ctrl pass, **2/5** | ctrl pass, **2/5** | ctrl pass, **2/5** | not measurable |
| **T-INFO** | ctrl 0/108 ✅ **5/5** | ctrl 0/911 ✅ **5/5** | ctrl 0/78 ✅ᵈ **5/5** | not measurable |
| **T-INFO-DETLOG** | ctrl 0/79 ✅ **5/5** | ctrl 0/821 ✅ **5/5** | ctrl 0/78 ✅ᵈ **5/5** | not measurable |
| **T-HEAP** | ctrl 0/81 ✅ **5/5** | ctrl **315/1175** ❌ | ctrl 0/78 ✅ᵈ **5/5** | not measurable |
| **T-STACK** | ctrl 0/115 ✅ **5/5** | ctrl **297/1228** ❌ | ctrl **36/114** ❌ | not measurable |
| **T-DEBUG** (= `--verify-strict`) | ctrl **17/314** ❌ | ctrl **12/1569** ❌ | ❌ | not measurable |

ᵈ **DBI only after canonicalising `dtid`** — see below. No shipped comparator does this.

### Per-backend root causes found by the sweep

* **liteinst — heap and stack hashes are nondeterministic run-to-run.** For the *trivial control*,
  315 of 1175 DETLOG lines differ under `--detlog-heap`. Every divergent line is
  `[memory][dtid 3] 0x405000-0x426000 ... [heap]->HASH` — **identical address range and
  permissions, different content hash.** This is genuine heap-content nondeterminism, not an
  address artifact. Consequence: #322's stated tier ("short tests stdout+INFO+stack+heap") is
  **not attainable on liteinst**, which is 136 of the 346 green cells.

* **DBI — `dtid` in the DETLOG is the raw host PID.** ptrace emits `dtid 3`; DBI emits
  `dtid 2960008`. That single un-virtualised field makes 74 of 78 DETLOG lines differ every run.
  Decomposition on the control (denominator 78 lines):

  | canonicalisation applied | divergent |
  |---|---|
  | wall-clock prefix only | 74 |
  | + hex addresses | 74 (addresses contribute **nothing**) |
  | + `dtid` | **0** |

  Fix that one field and DBI's DETLOG is bitwise-identical run-to-run. Until then DBI cannot be
  certified at any DETLOG tier by any existing comparator. DBI also **ignores `--log-file`** and
  writes its DETLOG to stderr.

* **KVM — not measurable on this host.** A `--strict` run of the *trivial control* did not complete
  within 120 s, then did not complete within 600 s. Two attempts, one guest. The 130 KVM green cells
  are therefore **unreproducible here**; they are not certified and not de-greened for divergence.

## Certification table (the deliverable)

| cell class | cells | currently certified at | can be certified at | verdict |
|---|---|---|---|---|
| ptrace green | 72 | T-guest only | **T-INFO / T-INFO-DETLOG / T-HEAP / T-STACK** | **UNCERTIFIED-BY-CONSTRUCTION** — probe is sound but too weak; the full #322 tier is reachable today |
| liteinst green | 136 | T-guest only | **T-INFO / T-INFO-DETLOG** | **UNCERTIFIED-BY-CONSTRUCTION**; heap+stack tier **blocked** by real heap-content nondeterminism |
| dbi green | 8 | T-guest only | T-INFO-DETLOG / T-HEAP *only after `dtid` virtualisation* | **UNCERTIFIED — BLOCKED**; no shipped comparator canonicalises `dtid` |
| kvm green | 130 | T-guest only | unknown | **UNCERTIFIED — NOT MEASURABLE on devbig014** (control exceeds 600 s) |
| **total** | **346** | | | **0 of 346 certified at any DETLOG-bitwise tier** |

No cell was de-greened for observed divergence. Every one of the 346 is uncertified because the
evidence class cannot support the claim — which is a different, narrower statement than
"nondeterministic".

## Recommendations (reported, not applied — `run_matrix.py` is owned by hermit-w5)

1. **Do not flip the matrix to `--verify-strict`.** It fails a trivially deterministic control for
   three classes of hermit's own DEBUG instrumentation.
2. **Add an INFO-tier exact comparator** (`strip_lines=false`, `full_trace=false`, wall-clock prefix
   stripped, DEBUG excluded). Measured here to catch **5/5** planted defects with a **clean control
   on ptrace, liteinst and DBI**. This is the missing rung.
3. **Key the verdict on the typed `--verify-json` predicate**, not on `rc==0` and not on scraping the
   stderr banner `"Determinism verified"`. `bitwise_parity ∧ compared_log_messages.{left,right} > 0`
   already exists in `verify.rs` and is exactly the falsifiable record required.
4. **Stop labelling the current result "L2 DETLOG-bitwise"** in `run_matrix.py:604` and
   `collect-fullcorpus.sh:15`. The measured tier is guest-visible.
5. **File as separate defects:** liteinst heap/stack content nondeterminism; DBI un-virtualised
   `dtid`; DBI ignoring `--log-file`; KVM strict non-completion on devbig014.

## Harness self-check

The first tier run scored INFO and DEBUG as `missed` for *every* guest including the mutants. Cause:
the grep filter matched zero lines after prefix-stripping, so two **empty** selections were being
compared and reported as "no divergence". The carried line counts (`0|0`) exposed it. Fixed; every
row in the tables above has both line counts > 0. This is the same "an empty comparison certifies as
a match" failure mode the audit is about — recorded here rather than quietly corrected.

## Reproduction

```bash
cd experiments/strict-certification-mutation-sweep_20260806
bash mutants/gen.sh                      # build the six static guests
(cd . && for f in clean_ctrl mut_stdout mut_exit mut_detlog_only mut_addr mut_path; do \
    gcc -O0 -o mutants-dyn/$f mutants/$f.c; done)   # dynamic variants (liteinst needs these)

./sweep.sh                                          # producer-probe can-fail sweep (ptrace)
MUTDIR=mutants-dyn TAG=-dyn BK=ptrace   ./tier2.sh   # tier ladder, ptrace
MUTDIR=mutants-dyn TAG=-dyn BK=liteinst ./tier2.sh   # tier ladder, liteinst
HERMIT=../../worktrees/dbi/hermit/target/debug/hermit BK=dbi ./tier3.sh   # DBI (stderr fallback)
```

`mutants-dyn` is required for liteinst: a statically linked guest fails its preload handshake
("tracee reached guarded executable entry before the required preload handshake completed").
Run the DBI binary **in place** — copying it out of its worktree breaks DBI.
