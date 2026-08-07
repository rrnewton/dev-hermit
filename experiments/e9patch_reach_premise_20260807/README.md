# e9patch reach: verifying the "parity sweep is vacuous" premise

**Question.** A task note of 2026-08-06 recorded that an e9patch-vs-ptrace parity
sweep is *vacuous* — that e9patch instruments nothing on ordinary guests, so the
sweep compares ptrace with itself. Is that true at current source?

**Answer: partly. The concern is real, but its stated mechanism is wrong and its
scope was overstated. Two of the three claims below are refuted.**

## Method

`hermit --backend e9patch run --strict -- <guest>` prints a preprocessing banner
carrying `candidate_sites` and `mapped_sites`. Reach is also a *static* property
of the guest ELF, so it can be counted without running anything: `scan.sh`
counts the mnemonics `hermit-cli/src/instruction_map.rs:338-352` treats as
candidate sites. The static count agreed with the observed banner on 5/5
validation points spanning 0, 1, 2 and 3 sites across both populations, so it is
used to obtain a full denominator, with every nonzero cell confirmed by an
actual run.

Two populations exist, compiled differently **in tracked code**. The earlier
note did not separate them, and the separation is what decides the answer.

## Results

| population | compile flags | built | reach > 0 | vacuous |
| --- | --- | ---: | ---: | ---: |
| **A** dedicated e9patch corpus | `-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie` (`collect-e9patch-compat.rs:58`) | 20/20 | **20/20** | 0/20 |
| **B** shared full-corpus | `cc -std=c11 -O2 -g -Wall -Wextra -Werror` (`collect-fullcorpus.sh:202`) | 137/213 | **4/137** | 133/137 |

`candidate_sites == mapped_sites` on every nonzero cell in both populations.

## Three findings

**1. The root cause is incomplete by nine mnemonics — REFUTED.** The note says
reach is zero because "the main ELF contains NO syscall instruction to rewrite".
A candidate site is not a syscall instruction:
`nondeterministic_instruction()` matches **ten** mnemonics — `syscall`, `cpuid`,
`rdrand`, `rdtsc`, `rdtscp`, `rdseed`, `sysenter`, `xbegin`, `xend`, `int 0x80`.

**2. The note's own headline counter-example is wrong — REFUTED.** It cites
`tests/backend-parity/fixtures/cpuid_probe.c` compiled as giving `0`, calling
this "the reachability wall in concrete numbers". Measured today, dynamic,
3 of 3 runs: **0 syscall instructions, 2 `cpuid` instructions,
`candidate_sites=2; mapped_sites=2`, rc=0** — real reach. All four nonzero
B cells are: `pmu_skid` 11 (cpuid), `cpuid_probe` 2 (cpuid),
`arch_prctl_determinism` 1 (cpuid), `rcx_canonicalization` 1 (syscall).

**3. The vacuity risk is real but sits in a different file than the one being
fixed — CONFIRMED, relocated.** The gate exists in the collector that does not
need it and is absent from the one that does:

- `collect-e9patch-compat.rs` sweeps **population A** (reach 20/20) and *has*
  `apply_reach_gate` (:177-207).
- `collect-fullcorpus.sh` sweeps e9patch over **population B** (133/137
  vacuous) and has **no reach gate at all** — no `candidate_sites`,
  `mapped_sites` or `reach` anywhere in it.

The hole is currently **latent, not active**: `compat-envelope/scorecard.csv`
has 618 rows and **0** e9patch rows, because the shared lane's feature probe
drops e9patch when `e9tool` is unavailable (it is absent from `PATH` here).

## Two defects found while testing both directions

**A published 100% pass with no reach evidence.**
`compat-envelope/e9patch-scorecard.csv` is tracked and committed: **454 rows,
227 distinct `test_id`, outcome `pass` 454/454, `deterministic=1` 454/454** — and
**19 columns, none of them a reach column.** The collector that owns it declares
**22**, adding `stdout_parity, candidate_sites, mapped_sites, reach_state`; the
CSV also spells column 15 `parity` where the collector says `stdout_parity`. So
the committed artifact predates its own gate: the 227/227 green cannot be
qualified from the artifact. My independent measurement says the underlying
population really does have 20/20 reach, so this is very likely a *real* green —
but the artifact does not carry what it verified, which is the exact failure the
gate was written to prevent. The source comment still reads
"Shared 19-column contract" above a 22-field constant.

**Both banner parsers are blind to the non-ELF banner shape.** There are two
banner shapes on this path:

```
ELF     :: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=0; mapped_sites=0; ...
non-ELF :: Backend: e9patch preprocessing + ptrace runtime; mapped_sites=0; main_executable=non-ELF; preprocessing=not-applicable
```

The second has `mapped_sites=0` but **no `candidate_sites=`**. Both parsers key
on `candidate_sites` first — `ci-hub/validate/e9patch_reach.py:73` and
`collect-e9patch-compat.rs:162` — so both return `unknown-no-banner`,
"cannot tell whether the backend did anything", for a banner that plainly says
zero. Fail-closed on the verdict, wrong on the reason.

## Both-direction gate test (real captured banners, not fixtures)

| banner | expected | `e9patch_reach.py` | rc |
| --- | --- | --- | ---: |
| `candidate_sites=0; mapped_sites=0` | refuse | `vacuous-ptrace-passthrough` | 1 ✓ |
| `candidate_sites=3; mapped_sites=3` | accept | `e9patch-exercised` | 0 ✓ |
| non-ELF, `mapped_sites=0`, no `candidate_sites` | refuse **as vacuous** | `unknown-no-banner` | 1 ✗ reason |

## Reproduction

```bash
cc -nostdlib -static -ffreestanding -O0 -fno-pie -no-pie \
   hermit/tests/backend-parity/e9patch_corpus/multi_site.c -o /tmp/A
cc -std=c11 -O2 -g -Wall -Wextra -Werror \
   hermit/tests/backend-parity/fixtures/cpuid_probe.c -o /tmp/B
hermit/target/release/hermit --backend e9patch run --strict -- /tmp/A   # 3; 3
hermit/target/release/hermit --backend e9patch run --strict -- /tmp/B   # 2; 2
```

`results.csv` carries every guest measured, its instruction mix, its static
candidate count, and its banner values where run.
