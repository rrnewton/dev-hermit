# Full-corpus ptrace/LiteInst L2 scorecard sweep (agent hermit-235)

**Question.** What is the *true* full-manifest ptrace-verify denominator (the
"200", not the "28" portable-CI subset), and how do the backends available in the
default hermit binary (ptrace, KVM, LiteInst) compare over that whole corpus at
L2?

**Method.** For every one of the 200 verify-mode e2e manifest cells (184 compiled
C guests + 16 shell/interpreter cells; enumerated by the companion
`../kvm_fullcorpus_scorecard_20260801/corpus{,-nonc}.tsv`), run
`hermit run --strict --verify` (L2, DETLOG-bitwise self-verify) per backend and
record pass/fail + parity vs the ptrace `--strict` reference. Guests are reused
from `hermit/target/kvm-fullcorpus/` (compiled by the KVM sweep). Uniform lane
flags (portable → `--no-virtualize-cpuid --max-timeslice=disabled`) make the
KVM/LiteInst columns apples-to-apples with ptrace. The sweep bypasses manifest
`ci`/`enabled` gating — it measures what each backend *can* verify.

- `sweep.sh` — ptrace L2. Default: uniform flags → `scorecard-ptrace.csv`.
  `NOFLAGS=1 ROWS=… OUTCSV=…` → hermit default flags (preemption on) →
  `scorecard-ptrace-default.csv` (flag-robustness cross-check).
- `sweep-liteinst.sh` — LiteInst L2 det + parity → `scorecard-liteinst.csv`.
- KVM columns come from `../kvm_fullcorpus_scorecard_20260801/scorecard-kvm{,-nonc}.csv`.

**Environment.** hermit `82a8e853357584a3a567fd80812e015572a607c7`, reverie
`a4f33d69a56ed4233a53b218c39d93807ffc8cd0`, release binary
(`HERMIT_BIN=hermit/target/release/hermit`), 316-core devbig, load ~77, PAR=24,
per-cell verify timeout 120s.

**Results.**

| backend | flags | L2 pass / corpus | parity |
|---------|-------|-----------------:|-------:|
| ptrace  | uniform | **179 / 200 (89.5%)** | (reference) |
| ptrace  | default (preempt on) | 178 / 200 (89.0%) | flag-robust |
| KVM     | uniform | det 130 / 200 (65%) | 112 (of 184 measurable) |
| LiteInst| uniform | det 118 / 200 (59%) | 108 |

`shared-futex-c` (0/4) and `data-handling` (0/2) fail under **every** backend and
**both** flag configs → genuine failures (segfaults / divergence), not flag
artifacts. Merged 600-row scorecard published at
`compat-envelope/fullcorpus-scorecard.csv`; rendered in `compat-envelope/REPORT.md`.

**Reproduction.** `HERMIT_BIN=…/target/release/hermit PAR=24 ./sweep.sh` then
`./sweep-liteinst.sh`. Raw per-cell `rows*/` and `*.log` are gitignored
(intermediate); the assembled `scorecard-*.csv` are the durable evidence.
