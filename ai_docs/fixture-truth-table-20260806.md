# Fixture truth table, 2026-08-06: landed · running · proven-able-to-fail

**Task:** `fixture-truth-table-landed-running-canfail` (ratchet metric #4) · **Agent:** hermit-audit
(`[impl agent, opus-5]`) · hermit `origin/main` = `4c70658e785834737cbe1524f77330c781a6f5ea`.
Per-fixture rows: [`fixture-truth-table-20260806.csv`](fixture-truth-table-20260806.csv).

## The ratchet number

> ## **ALL THREE = 1**

One fixture is landed **and** running **and** proven able to fail: `personality_domain.c`.

Column totals, measured independently so a drop is attributable:

| column | count | how it was established |
| --- | --- | --- |
| **written** (population) | **105** | distinct `.c` under `tests/backend-parity/fixtures/` across `origin/main` **and all 946 live branches** |
| **landed** | **82** | present in the `origin/main` tree; adding commit ancestry-confirmed |
| **running** | **1** | a real run emitting a **nonzero executed count** |
| **can-fail** | **7 proven** | planted violation caught **and** clean control still passed |

**There is no denominator.** 105 is what has been *written*, not what is *needed* — no defensible
number exists for "fixtures needed", so these are counts, never X-of-Y.

**Why all three, and not any one:** landed alone would report 82; can-fail alone would report 7;
running alone would report 1. Each overstates differently. The conjunction is 1.

## Column 1 — landed (82)

Present in `origin/main`'s tree at `4c70658e7`. **23 are off-main**, on their own branches
(per-branch attribution in the CSV). Ancestry for the all-three fixture:

```
adding commit 4c70658e785834737cbe1524f77330c781a6f5ea
git merge-base --is-ancestor <add> origin/main  -> ANCESTOR, LANDED CONFIRMED
```

## Column 2 — running (1)

**Three independent sources agree**, which matters because each alone is a proxy:

1. **Manifest config:** of **85** cells in `backend-parity-c.toml`, exactly **1** has any mode with
   `ci = true` — `backend-parity-c/personality-domain` (`verify`). **84 do not run.**
2. **Authoritative plan:** `ci/expected-e2e-plan.json` has **81** rows, of which exactly **1** is
   `backend-parity-c`, the same cell.
3. **A real run** (the running thing, not its config):
   ```
   ./ci/test_harness.sh run --lane portable --category backend-parity-c --ci-only
   PASS  portable  verify  ptrace  backend-parity-c/personality-domain
   -> results.jsonl: 1 line, outcome PASS, duration 245ms, hermit_sha fad50bc75…
   ```
   Re-run **without** `--ci-only`: still **1** executed cell. The other 84 are gated to manual
   invocation (`--include-manual` requires exact `--test` and `--mode`).

### A green with zero executed cells

In the full validate run, the privileged lane reported:

```
[e2e.manifest_backend_parity_c] ✓ PASS   Privileged manifest bucket: backend-parity-c (5s)
```

with `results.jsonl` **0 bytes / 0 lines** and JUnit `tests="0" failures="0"`. The bucket passes
having executed **nothing**. The mechanism is in the DAG command:

```
./ci/test_harness.sh run --lane portable --category backend-parity-c --ci-only --allow-empty …
```

`--allow-empty` converts "selected nothing" into PASS. A reader sees a green bucket named
*backend-parity-c* and infers parity coverage; the executed count is zero.

### The running cell cannot test parity

`backend-parity-c/personality-domain` has `backends_enabled = ["ptrace"]`; `dbi`, `kvm`, `sabre`
and `liteinst` are all disabled with "qualify separately" reasons. **A backend-parity fixture
running on exactly one backend compares nothing.** So even ALL-THREE = 1 overstates *parity*
coverage: it proves self-consistency under ptrace, not agreement between backends. The honest
parity number is **0**.

## Column 3 — can-fail (7 proven)

Bracketed both sides per fixture: compile clean → must PASS; compile with the planted violation →
must be caught. Command form (harness flags, `build.cflags = ["-D_GNU_SOURCE"]`):

```
cc -std=c11 -O2 -g -Wall -Wextra -Werror -D_GNU_SOURCE [-DHERMIT_TEST_ORACLE_NEGATIVE] fixture.c -o prog
hermit --log=info run --backend=ptrace --strict --verify \
       --no-virtualize-cpuid --max-timeslice=disabled <abs-path>/prog
```

**The three libunwind variables are not one setting — the runtime one points somewhere else.**
`ignored/lu-parity/usr/lib64` ships `libunwind-ptrace` as a **static `.a` only**, so putting it on
`LD_LIBRARY_PATH` can fail with `libunwind-ptrace.so.0: cannot open shared object file`, which
reads as a broken build and is not one:

```
PKG_CONFIG_PATH=…/ignored/lu-parity/usr/lib64/pkgconfig   # build script
LIBRARY_PATH=…/ignored/lu-parity/usr/lib64                # link search
LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib   # runtime
```

The bracket above was originally run with lu-parity on `LD_LIBRARY_PATH`. That did **not** affect
these results — the debug `hermit` needs only `libunwind-x86_64.so.8` and `libunwind.so.8`, both of
which lu-parity ships (`ldd` → 0 not-found under either path), and links `libunwind-ptrace`
statically. Confirmed rather than assumed by **replaying the whole bracket** under the corrected
runtime path: **7/7 clean controls passed, 7/7 planted violations caught — identical.**

| fixture | clean control | planted violation | verdict |
| --- | --- | --- | --- |
| `cwd_roundtrip.c` | rc=0 | rc=1 | can-fail |
| `fcntl_owner.c` | rc=0 | rc=1 | can-fail |
| `membarrier_query.c` | rc=0 | rc=1 | can-fail |
| `o_tmpfile_anon.c` | rc=0 | rc=1 | can-fail |
| `personality_domain.c` | rc=0 | rc=1 | can-fail |
| `pipe_capacity.c` | rc=0 | rc=1 | can-fail |
| `record_lock.c` | rc=0 | rc=1 | can-fail |

**7 clean controls passed, 7 planted violations caught, 0 escapes.** `personality_domain` carries
two further hooks beyond the shared one; all three were caught
(`NO_TRANSITION` rc=1, `POST_SET_FAILURE` rc=1, `ORACLE_NEGATIVE` rc=1) against a clean control
printing `pers ok=5` rc=0.

**The remaining 75 landed fixtures are UNPROVEN, not proven-unable.** Only **7** of 82 carry a
`HERMIT_TEST_*` mutation hook, and those 7 are exactly the 7 that landed today. Without a hook a
violation cannot be planted without editing the fixture source, so no claim is made either way.
Unproven must not be counted as can-fail.

### On the `ok=N` blindness

**73 of 82** landed fixtures emit only an `ok=N`-style scalar; **8** print at least one observed
value. But `ok=N` alone does **not** imply unable-to-fail: in the 7 hooked fixtures `N` is a
checksum of relational assertions, and their planted violations *were* caught. What `ok=N` does
cost is a specific blindness — **two backends agreeing on a wrong absolute value** still print the
same `ok=N`. `personality_domain` documents this deliberately ("the starting value is never
printed, which keeps the fixed success oracle portable"), because the absolute host persona is
nondeterministic. So this is a real coverage limit, not a defect in those 7.

## Three landed fixtures have no cell at all

`sigaction_state.c`, `sigaltstack_state.c`, `sigprocmask_state.c` are on main and referenced by
**no** manifest cell. They can never run under any flag. 82 landed − 79 with a cell = 3.

## Method notes (traps that would have changed the answer)

- **Derive from main *and* every branch.** Main alone gives 82 and hides 23. The sweep covered
  **946** branch refs.
- **`--limit` truncation reads exactly like missing work.** Established earlier the same day:
  `gh pr list --limit 400` made three tracked branches look PR-less; at `--limit 900` all three
  resolved.
- **Verify the running thing, not the config.** `ci = true` is a claim; the executed count in
  `results.jsonl` is the evidence. They happened to agree here — but the privileged bucket's
  `✓ PASS` with `tests="0"` is the case where config-shaped reasoning fails outright.
- **A control that fails makes the bracket vacuous.** My first bracket returned rc=1 on *all four*
  variants including clean — the cause was `--log=error` being rejected (`--verify requires
  --log=info`), so nothing ran. A second attempt failed on all rows because hermit isolates guest
  `/tmp`, and a third because I omitted `-D_GNU_SOURCE`. Each would have produced a confident
  "planted violation caught" from a run that never happened. **Always require the clean control to
  pass before reading the planted side.**

## Not done

- Can-fail for the 75 unhooked fixtures. It needs either a mutation hook per fixture or a
  mechanical source-mutation harness; a trivial plant (forcing a nonzero exit) would prove nothing
  about oracle discrimination.
- Cross-backend can-fail. Every parity cell is ptrace-only today, so a *divergence* between
  backends has not been planted or caught anywhere.
- The 23 off-main fixtures were not run; landed=no makes their other columns moot for the ratchet.
