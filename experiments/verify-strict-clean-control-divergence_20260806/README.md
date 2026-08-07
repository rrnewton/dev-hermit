# Why `--verify-strict` rejects a trivially deterministic clean control

Task `research-verify-strict-clean-control-divergence` (hermit-w10). **Research only —
no product code was changed.** Exact SHAs, host, toolchain and denominators are in
`metadata.json`; per-run numbers are in `results.csv`; the analyzer is `classify.py`.

## Question

`hermit-w7` found that the obvious remediation for the fake-green scorecard — flipping the
backend-parity probe from the lossy `Stripped` comparator to `--verify-strict` — turns
`clean_ctrl` red. `clean_ctrl.c` in its entirety is:

```c
#include <stdio.h>
int main(void){ printf("constant-output\n"); return 0; }
```

built `gcc -O0 -static`. If a static `printf` of a constant string is nondeterministic under
the parity comparator, the comparator cannot be adopted. So: **what actually diverges, and
is it guest nondeterminism, Hermit/tool instrumentation nondeterminism, or a
comparator-policy defect?**

## Method

Run `hermit-w7`'s literal producer probe (`run_matrix.py`'s `hermit_command`) with
`--verify-strict` added, at hermit `f89c69766371806d3c9b2c3003531df2d59d6118` /
reverie `9470712afa9b421c72850ab7955fb335692e43a0`, ptrace backend:

```
hermit/target/debug/hermit run --strict --verify --verify-allow both --verify-strict \
    --no-virtualize-cpuid --verify-json=<path> \
    --base-env=minimal --max-timeslice=disabled --tmp=/tmp -- <guest>
```

On a divergence `--verify` retains both runs' logs (`/tmp/run{1,2}_log_*`). `classify.py`
strips the real wall-clock prefix (the one normalization the `Canonical` policy also applies),
compares line-for-line, and buckets each differing line into the classes below. Copies of the
three most load-bearing log pairs are in `logs/`.

## Result 1 — reproduced, and stable

`rc=1`, `verdict=diverged`, `bitwise_parity=false`, `compared_log_messages 273|273`, on
**6/6** clean-control double-runs. `comparison` in the `--verify-json` is
`{strictness: canonical, strip_lines: false, canonicalize_addresses: true, full_trace: true,
exact_remainder: true, stripped_prefixes: [real-wall-clock-prefix/v1],
canonicalizations: [host-address-to-first-appearance-ordinal/v1]}`. w7's cell reproduces exactly.

## Result 2 — the headline: **every divergence is DEBUG-level**

The retained logs are 290 lines each (the comparator counts 273 messages). Partitioned by
log level, on the canonical pair (`logs/clean_ctrl_strict_run{1,2}.txt`):

| level | lines | differing |
|---|---|---|
| INFO | 56 | **0** |
| DEBUG | 217 | **18** |
| other (COMMIT / continuation / summary) | 17 | 0 |

DETLOG lines specifically: 163 total, 2 differing — and **both of those 2 are DEBUG-level
DETLOG**. INFO-level DETLOG: 41 lines, **0 differing**.

**0 INFO divergences on 9/9 clean-control double-runs** (6 with the producer probe, 3 with
CPUID virtualized). The clean control is bitwise-identical across the entire INFO log — the
level the determinism contract is actually written against.

This is *not* a property of the comparator being weak at INFO: see Result 5.

## Result 3 — the 18 divergences are five distinct causes

Source citations re-read at the exact build pin via `git show 9470712a:<path>`.

| id | n | line | cause | classification |
|---|---|---|---|---|
| **C1** | 4 | `DEBUG tracee.attach{pid=3}: reverie_ptrace::timer: Setting precise_ip to false for cpu CpuId {…}` | Differs **only** in `initial_local_apic_id` (27 vs 255) and `x2apic_id` (283 vs 255). `reverie-ptrace/src/timer.rs:334-341 has_precise_ip()` debug-logs the whole `raw_cpuid::CpuId` — i.e. which physical CPU the **tracer** landed on. | instrumentation |
| **C2** | 6–10 | `DEBUG …reverie_ptrace::vdso: 3 patched __vdso_<sym>@<addr>` | Same 5 symbols, **same per-symbol address**, emitted in a **different order**. run1: `gettimeofday, clock_getres, clock_gettime, getcpu, time`; run2: `clock_gettime, time, getcpu, clock_getres, gettimeofday`. Root cause is exact: `reverie-ptrace/src/vdso.rs:175` declares `type VdsoPatchInfo = HashMap<&'static str, …>` and `:293` iterates it with `.iter()`, logging at `:303`. Rust `HashMap` iteration order is randomized per process. The same permutation appears in both patch blocks of a run — one map, iterated twice. | instrumentation |
| **C3** | 1–2 | `DEBUG reverie_ptrace::task: [tool] (tid 3) beginning inject of syscall: execveat, args SyscallArgs{…}` | `arg0`/`arg1` identical; `arg2` 93888356606752 vs 93888356617504, `arg3` 93888356616688 vs 93888356615424. Not a uniform ASLR shift (arg1 is unchanged) ⇒ allocation placement inside the **Hermit tracer process**, not the guest. | instrumentation |
| **C4** | 2 | `DEBUG detcore: DETLOG (pre\|post) registers [dtid 3][rcbs 0]. rax 0x7 rbx 0x4eff0800 …` vs `rbx 0xffff0800` | Every other register byte-identical. `0x4eff0800`/`0xffff0800` decode as CPUID leaf-1 EBX = `initial_APIC_id<<24 \| max_logical<<16 \| clflush<<8 \| brand_index`, i.e. APIC id 78 vs 255. **The host APIC id is reaching a guest register.** | **genuine guest-visible nondeterminism — but probe-induced (see Result 4)** |
| **C5** | 1 | `DEBUG detcore::tool_global: Nondeterministic realtime elapsed: 16.144915ms` vs `19.79095ms` | `detcore/src/tool_global.rs:541`. The producer **literally names it "Nondeterministic"**. A declared wall-clock measurement, compared exactly. | **comparator-policy defect** |

C1/C3/C4/C5 counts are identical on all 6 clean-control double-runs. C2 varies 6–10 because a
random permutation sometimes leaves a symbol at the same index.

## Result 4 — control B: C4 is caused by the probe's own `--no-virtualize-cpuid` flag

Dropping `--no-virtualize-cpuid` and changing nothing else:

| | C1 | C2 | C3 | **C4** | C5 | INFO diffs |
|---|---|---|---|---|---|---|
| producer probe (`--no-virtualize-cpuid`), n=6 | 4 | 6–10 | 1 | **2, 2, 2, 2, 2, 2** | 1 | 0 |
| CPUID virtualized, n=3 | 4 | 6–8 | 1 | **0, 0, 0** | 1 | 0 |

**6/6 vs 0/3.** C4 vanishes; C1 (a *tracer*-side CPUID read, unaffected by guest CPUID
virtualization) persists, which is the expected asymmetry and confirms the control is not just
suppressing everything. So the one genuine guest-state divergence in the clean control is
**manufactured by the backend-parity probe's own flag choice**, not by Hermit's determinization.

## Result 5 — the bracket: INFO-restricted comparison passes the control **and** still
refuses all five planted defects

`info_restricted_verdict` in `results.csv` is the verdict a comparator that kept `Canonical`
strictness but compared only INFO-level messages would return.

**Positive control (must fire, i.e. must NOT be inert) — all 5 of w7's planted defects:**

All five mutants share w7's `bump()` state-file helper, so all five diverge at the same
`lseek` line; `+` marks the divergence specific to that mutant's planted defect.

| guest | planted defect | INFO-level divergence(s) |
|---|---|---|
| `mut_stdout` | stdout counter | `finish syscall #19: lseek(3, 0, SEEK_END) = Ok(1)` vs `Ok(2)` |
| `mut_exit` | exit status | + `inbound syscall: exit_group(1) = ?` vs `exit_group(2)` |
| `mut_detlog_only` | read() return length | + `finish syscall #22: read(3, 0x7fffffffdb80, 4096) = Ok(1)` vs `Ok(2)` |
| `mut_addr` | pointer arg to a 0-length write | + `inbound syscall: write(1, 0x4bf300, 0)` vs `write(1, 0x4bf340, 0)` |
| `mut_path` | openat path arg | + `openat(-100, … -> "/tmp/w7mutpath_1", …)` vs `"…_2"` |

**5/5 UNEQUAL.** Note `mut_addr` and `mut_path` are exactly the two defects the `Stripped`
comparator is blind to by design (`unsafe-numeric-address-and-path-normalization/v1`), and
both are caught here.

**Incidental finding — a hole no strictness closes.** `mut_stdout`'s actual planted defect
(`counter=1` vs `counter=2`) is **invisible in the DETLOG at every strictness**. Both runs log
byte-identical `inbound syscall: write(1, 0x4c68f0, 10) = ?` and
`finish syscall #22: write(1, 0x4c68f0, 10) = Ok(10)` — the DETLOG records the buffer
*pointer and length*, never its *contents*, and the two strings are the same length. It is
caught here only by the separate stdout comparison, plus incidentally by the shared `bump()`
`lseek`. A guest that writes differing same-length bytes to a **file** rather than stdout would
therefore be caught by nothing. This is the same shape as the already-known
`Syscall::Fstat(_) => syscall.display(memory)` hole (`detcore/src/lib.rs`, FIXME T136880615),
which logs `fstat` without its output struct. Both need producer changes; neither is a
comparator-strictness question. Record as known-uncovered so the certification table does not
imply coverage it lacks.

**Negative control (must not fire): 9/9 clean-control double-runs EQUAL.**

So the divergence classes C1–C5 and the planted-defect channel are cleanly separated by log
level, in both directions, with counts on both sides.

## Result 6 — `--log=info` cannot be used to get there from the CLI

`verify.rs:557` (`setup_double_run`) does `global.log = Some(LevelFilter::DEBUG);`
**unconditionally**, overwriting the user's level; `validate_log_level` (`:518`) only rejects
levels *below* INFO. Control C confirms this at runtime: `hermit --log=info run … --verify-strict`
produced the same 290-line DEBUG logs, 273 compared messages, and the same 16 DEBUG divergences.
An INFO-restricted comparison is therefore a **code change**, not a flag.

## Interpretation

**The clean control is not nondeterministic. The comparison is over-broad.**
`LogCompareStrictness::Canonical` sets `full_trace = true`, and `FullTrace` compares *every*
message in a log that `--verify` has forcibly set to DEBUG. That sweeps in tracer-internal
diagnostics (C1, C2, C3) and one line the producer explicitly labels nondeterministic (C5),
none of which are part of any determinism claim. Of the 18 divergences, **17 are outside the
determinism contract and 1 (C4) is induced by the probe's own flag.**

Restated as a Proxy Binding failure: `full_trace` is being used as a proxy for *"compare
everything the determinism contract covers."* Its actual binding is *"compare every line the
tracing subsystem happened to emit at DEBUG,"* and the tracing subsystem's DEBUG output is not
a determinism-contract surface. w7's C5 line is the proof in one record — a value whose own
text says it is nondeterministic, inside a comparison whose verdict claims bitwise determinism.

**Consequences for the strict-certification correction:**

1. Flipping the parity probe to `--verify-strict` as-is is **not safe** — it reds every cell
   on this box for reasons unrelated to the guest. That confirms w7's caution and refutes
   w11's stated next step in its current form.
2. Three of the five causes are cheap producer-side fixes, and they are the right kind of fix
   (producer-side avoidance beats comparator-side erasure):
   - **C2** — `HashMap` → `BTreeMap` for `VDSO_PATCH_INFO` (`reverie-ptrace/src/vdso.rs:175`),
     or sort before logging. This is a real nondeterminism in Reverie worth removing on its
     own merits, independent of this comparator question.
   - **C5** — `detcore/src/tool_global.rs:541` emits a self-declared nondeterministic value
     via `debug!`. Compare the already-established precedent at
     `detcore/src/scheduler.rs:2965-2972`, where the internal IO-polling retry time-advance
     is emitted as `trace!` — with the comment *"Advance time (needed for timeout enforcement)
     but keep it off the DETLOG"* — while the ordinary scheduler turn next to it uses
     `detlog_debug!`. That is exactly the demotion C5 needs.
   - **C1/C3** — tracer-internal diagnostics; demote to `trace!` or drop the host-identifying
     fields.
3. **C4 is a finding in its own right and should not be lost in the noise.** The
   backend-parity matrix passes `--no-virtualize-cpuid`, which lets the host APIC id into
   guest `rbx`. Any cell that reads CPUID leaf 1 is running with a host-identity channel
   deliberately left open. Whether that flag belongs in the parity probe at all is a
   separate question this task did not have scope to settle.
4. An INFO-restricted `Canonical` comparison is bracketed green here (9/9 clean, 5/5 defects
   caught) and is the smallest change that makes the flip viable — but see the caveats.

## Caveats — what this does NOT establish

- **1 guest × 1 backend × 1 host.** These are counts (9 clean double-runs, 5 defect
  double-runs), **not a rate over the 618-row scorecard**. `clean_ctrl` is a static,
  single-threaded, no-I/O binary. Nothing here predicts what a real corpus guest does.
- **The known FullTrace/`filter_deterministic` gap was not exercised.** A prior audit
  (`ai_docs/verify-strip-site-audit-20260805.md`) established that `FullTrace` does not call
  `filter_deterministic`, so `Canonical` re-exposes host-timing-dependent poll-retry and
  `advancing committed_time` lines, and predicted a class of false reds on nonblocking-I/O
  guests. `clean_ctrl` does no nonblocking I/O, so **that class did not appear here and
  remains unrefuted**. An INFO restriction would not necessarily fix it — the
  `InternalIOPolling` COMMIT turns are INFO-level. That is the next guest to test.
- **"INFO-restricted" is an offline computation over the retained logs, not a shipped mode.**
  No comparator code was changed. Whether the right mechanism is a level restriction, a
  producer-side demotion of C1/C2/C3/C5, or a declared token-carrying exclusion is a design
  decision, not a measurement — and per the no-worse ratchet, a level restriction would itself
  be a new strip and must register a token if adopted.
- **C4's disappearance is measured at n=3 against n=6.** Consistent and mechanistically
  explained, but not a large sample.

## Reproduction

```bash
cd /home/newton/work/dev-hermit
E=experiments/verify-strict-clean-control-divergence_20260806
M=$PWD/experiments/strict-certification-mutation-sweep_20260806/mutants

# 1. hermit binary at f89c6976 (hermit/target/debug/hermit; `cargo build` in a slot if absent)
# 2. reproduce the clean-control failure
rm -f /tmp/w7mutstate
./hermit/target/debug/hermit run --strict --verify --verify-allow both --verify-strict \
    --no-virtualize-cpuid --verify-json=/tmp/v.json \
    --base-env=minimal --max-timeslice=disabled --tmp=/tmp -- $M/clean_ctrl
# rc=1; stderr names the two retained logs: ":: Respective Logs retained ... /tmp/run1_log_XXXXX /tmp/run2_log_YYYYY"

# 3. classify (add -v for the character-level edit regions)
python3 $E/classify.py /tmp/run1_log_XXXXX /tmp/run2_log_YYYYY clean_ctrl -v

# 4. control B -- drop --no-virtualize-cpuid; C4 must go to 0 while C1 stays at 4
# 5. negative bracket -- repeat step 2 for mut_{stdout,exit,detlog_only,addr,path};
#    each must show info_lines_differing >= 1
```

`logs/` holds the exact pairs behind rows `clean_ctrl_probe_strict_rep1`,
`clean_ctrl_strict_cpuid_virtualized_rep1` and `mut_detlog_only_probe_strict`, so
`classify.py` can be re-run without re-executing hermit.

## Ownership

Read-only with respect to `hermit-w7`'s `experiments/strict-certification-mutation-sweep_20260806/`
(guests reused, nothing written) and to `hermit-w5`'s `feat/parity-mutation-harness` /
`hermit/tests/backend-parity/run_matrix.py` (never opened for write). The hermit primary
checkout was used read-only, for its prebuilt binary only.
