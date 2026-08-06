# The measurements index: what a number must carry to survive contact with a second number

**Lane:** (c) measurements index · hermit-clone (opus-5), 2026-08-06 · **no-commit** (parent artifact)

## The failure this fixes

Two numbers that measure "the same thing" disagree, and **nobody can tell whether it is a
regression, a different population, or a different instrument** — so the disagreement is filed as
noise and both numbers keep circulating. Today: `40/53` vs `9/32`, and `~193` vs `106`, unreconcilable
purely because populations were never recorded.

The cost is asymmetric. An unrecorded population does not make a number *wrong*; it makes it
**unfalsifiable**, which is worse, because nothing can ever retire it.

## The worked example that shows the schema is sufficient

This morning, item 2.3: a recorded finding said ptrace returns **100** for a SIGTRAP-killed guest;
I measured **0**. Same claim, different values.

That was reconcilable *only* because the producer had committed the fixture:
`experiments/exit_termination_determinism_20260806/guest_exit.c:29` is
`if(!strcmp(m,"raise_trap")) raise(SIGTRAP);` — semantically identical to my probe. Fixture
identity is what let me **refute my own first hypothesis** (that they had used
`__builtin_trap`/`int3`; that is their separate `ill` row at `:26`) and conclude the difference was
the *binary*, not the probe — i.e. **a regression from 100 to 0**, which is a severity increase
because 0 is indistinguishable from success.

Had the fixture not been recorded, the honest outcome would have been "two numbers, unknown
relationship" — exactly the pile this index exists to prevent. **The schema below is the
generalisation of what made that case work.**

## Required fields

A measurement is publishable when it carries all of these. Anything missing is recorded as
`UNKNOWN`, never omitted — an absent field and a field known to be inapplicable are different, and
collapsing them is how a population silently becomes a guess.

| field | why it is load-bearing |
|---|---|
| `measurement` | the quantity, in words, including its **unit** — a count is not a rate; a load average is not a utilisation |
| `value` | the number, with the unit repeated |
| `denominator` | **the population, enumerated or a query that re-derives it.** `40/53` is not a measurement; "40 of the 53 PRs open at 09:00 with a non-draft state" is |
| `date_utc` | absolute, never relative. A population is live; "today" is unresolvable in a week |
| `producer` | agent + model, so a systematic instrument bias is attributable |
| `commit` | the exact SHA(s) the number is bound to — **never a branch name** |
| `fixture` | the guest/probe/input, by committed path. *This is the field that saved the SIGTRAP case* |
| `instrument` | how it was obtained: external clock, in-guest clock, cgroup peak, polled aggregate, log grep. **A polled aggregate is not a cgroup-recorded peak** |
| `control` | what demonstrates the instrument could have produced a *different* answer. Absent control ⇒ the number is not evidence, only an observation |
| `re_derive` | the exact command line, runnable |

## The two fields people skip, and what skipping them costs

**`denominator`.** A ratio without an enumerated population cannot be compared to any other ratio.
I produced this defect myself today: my `tg_landed` reporter classified **40 tasks as 51** because
`tg sql` renders one row per line and multi-line notes parsed as extra rows. It was caught only by a
sum-to-population assertion. A denominator you did not enumerate is a denominator you do not have.

**`control`.** Without it a negative result is unfalsifiable. Two from today:

- I reported per-syscall costs from an **in-guest clock** under `--strict`. Detcore virtualizes the
  clock, so those were virtual time, not cost — the whole table was retracted. The tell was that
  repeated runs returned *identical* values to three decimals; the only varying row was the native
  control. **The control is what exposed the instrument.**
- Socket determinism: 3 of 4 dimensions showed no divergence under hermit — but the *native* control
  also showed none (`0 1 2 3` every run, even with four racing threads). So those cells cannot
  distinguish "determinized" from "never nondeterministic," and I recorded them as **unproven rather
  than passing**. Same number, opposite meaning, decided entirely by the control.

## Seed entries (today's measurements, in schema)

| measurement | value | denominator | fixture | instrument | control | commit |
|---|---|---|---|---|---|---|
| ptrace exit status, SIGTRAP-killed guest | `0` (native `133`) | n/a (single fixture, N=1 per backend) | `scratch/trapdie.c`; cf. `experiments/exit_termination_determinism_20260806/guest_exit.c:29` | process exit status | native = 133 ✓ discriminates | hermit release @ `4c70658e7` |
| same, prior run | `100` | as above | `guest_exit.c:29` | process exit status | native = 133 | earlier SHA (unrecorded — **this is the gap**) |
| per-syscall cost, ptrace | ~34 µs | marginal over N=2 000→12 000, 3 pairs | `scratch/ptpath/churn3.c` | **external** wall clock, differenced | no-syscall loop ⇒ ~0 ±1 µs ✓ | hermit `b64d893a` |
| per-syscall cost, e9patch | ~34 µs (indistinguishable) | as above | `churn3.c`, `mapped_sites=1` | external | as above | `b64d893a` |
| per-syscall cost, sabre | ~157 µs | as above | `churn3.c` | external | as above | `b64d893a` |
| SaBRe `patched_sites` | `0` on every guest tried | `/bin/echo`, `/bin/true`, `churn3` (3 of 3) | those binaries | `--log-file` grep | e9patch rewrites `churn3` (`mapped_sites=1`) ⇒ 0 is real, not absent instrumentation | release + debug |
| detlog reproducibility, file-writing guest | 2 differing lines → **0** | 3 guests (pwrite64, sendfile, python3) | `scratch/inodedet/{fw,sf}.c` | detlog diff, wall-clock prefix stripped (`BitwiseInfoV1`) | mutation: revert fix ⇒ fails ✓ | `9cf96a4d9` |
| `DetInode` conflation sites | 11 lib + 3 test | all `DetInode` uses, 7 files | n/a | **compiler** after newtype | pre-newtype: 0 detectable ✓ | `9cf96a4d9` |
| `DetTid`/`DetPid` split size | 443 + 156 mentions, 30 files | whole hermit tree excl. `target/` | n/a | `grep -rn --include=*.rs` | 1.1 measured the same way: 27 / 7 files | `4c70658e7` |

The second row is deliberately included **with its gap visible**: it is the one measurement here that
cannot be re-derived, because its SHA was never recorded. That is the whole argument for the index,
stated against my own corpus rather than someone else's.

## How to adopt without a migration

Do not backfill. Require the schema at the point a number is **published** (task note, artifact,
scorecard row), and let the corpus accrete. Two mechanical rules carry most of the value:

1. **Never write a bare ratio.** `40/53` → `40 of 53 <enumerated population> @ <date>`.
2. **State the instrument and the control in the same sentence as the number.** If there is no
   control, say so — "no control; observation only" is a publishable, honest status, and it is what
   stops an unfalsifiable number from being cited later as a result.

## Relationship to lanes (a) and (b)

Lane (a) is this schema applied to one consumer: a scorecard cell carrying SHA + flags + comparator
+ ref-hash *is* `commit` + `instrument` + `denominator` for that row. Lane (b) is a specific
`instrument` distinction — wall vs CPU per node — that today had to be reconstructed by hand for
three hangs. Both are instances; fixing them individually leaves the next consumer to re-derive the
rule, which is why the index is worth having as its own artifact.
