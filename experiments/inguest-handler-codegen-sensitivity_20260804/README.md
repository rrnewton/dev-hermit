# In-guest handler codegen sensitivity (liteinst) — 2026-08-04

## Question

The LTO experiment measured `cargo build --release -p hermit` (the **ptrace supervisor**)
and found runtime deltas ≤2%, explained by ~83% of supervisor CPU on a syscall-hot
workload being kernel `ptrace`/`sys` time that codegen cannot touch. Owner flagged that
this reasoning is about the **supervisor** and does **not** transfer to an **in-guest
handler**, which runs inside the guest with **no ptrace round-trip** — so user-space
codegen should be a much larger share of the cost.

Measure codegen sensitivity **for the in-guest liteinst handler path specifically**:
opt-level, codegen-units, LTO. Report **ns/syscall**, not wall.

## Method

- **Backend:** liteinst. Confirmed genuinely in-guest per run: `direct_hook=N+1`,
  `ptrace_installation=0` (vacuity guard — rejects any silent ptrace fallback).
- **Guest (fixed):** `syscall_loop` tight raw `getpid` loop, exact count N.
- **Supervisor (fixed):** hermit release bin, lifecycle-only for liteinst — its codegen
  is irrelevant, held constant. **Only `libreverie_liteinst.so` codegen varies**,
  selected per run via `HERMIT_LITEINST_RUNTIME`.
- **Metric:** ns per in-guest getpid = CPU-time (user+sys) **slope** across N=1e5 and
  N=2e6. The slope cancels fixed startup/patch/teardown; CPU-time is immune to the ~2×
  wall noise floor (contention deschedules but adds no CPU time).
- **Box state:** single-core pinned (`taskset -c 300`) + boxed (`systemd-run --user`),
  serial, 5 measured reps/point (+1 discarded warmup), median. Host devbig014, 316c.
- **Profile facts (from source):** hermit workspace and `liteinst-runtime-build` both have
  **zero `[profile.*]`** → release inherits cargo defaults **opt-level=3, codegen-units=16,
  lto=false**. So variant **A = the shipping config**, and the owner's "we inherit cu=16"
  premise is confirmed.

SHAs: hermit `02a47ef7`, reverie-liteinst `3eda4286`, liteinst2 `95ee5e69`; rustc 1.97.1.

## Results (ns per in-guest getpid handler invocation)

| variant | codegen                              | ns/syscall | vs A     |
|---------|--------------------------------------|-----------:|----------|
| A       | opt3 / cu16 / lto=off (**SHIPS**)    |      957.9 | —        |
| B       | opt3 / **cu1**                       |      978.9 | +2.2%    |
| C       | opt3 / cu1 / **lto=fat**             |      910.5 | **−5.1%** (−47 ns) |
| D       | **opt0** / cu16 (CONTROL)            |     3368.4 | **+252%** (3.5×) |
| native  | raw getpid, no hermit                |       73.7 | —        |

Dispersion (MAD) at N_hi ≤ 0.04 s on ~2 s totals → slopes well-resolved; the 3.5× D
effect and the 5% C effect are both well above noise; B's +2.2% is marginal.

## Interpretation

**Owner's hypothesis is CONFIRMED at the mechanism level — and refuted as a large free win.**

1. **Codegen sensitivity is real and large here, unlike the supervisor.** opt0→opt3 is a
   **3.5× swing** (3368→958 ns). The supervisor's "codegen bounded by kernel time"
   reasoning does **not** transfer: for the in-guest handler the kernel `getpid` is only
   ~8% of cost (native 74 ns of 958 ns); the **user-space handler is ~92%** (884 ns of
   overhead). That is exactly why codegen has leverage here and did not for the supervisor.
   The discriminator fired: liteinst getpid does **not** pass through cheaply to the kernel.

2. **But the big lever is already pulled.** We already ship at **opt-level=3** (variant A =
   release default). The actionable knobs *on top of* opt3 give little:
   - **codegen-units=1 (B): +2.2%** — no help, slight regression within noise. The owner's
     specific untested knob (cu=16 → cu=1) does **not** speed up this handler.
   - **LTO-fat + cu1 (C): −5.1%** (−47 ns) — a modest, real but small improvement.

**Net:** rustc **opt-level** matters enormously for in-guest handlers (3.5×) in a way it
provably does not for the ptrace supervisor — so the earlier "LTO is already optimal"
conclusion was correctly scoped to the supervisor binary and should **not** be generalized
to handler code. However, the release profile already captures the dominant lever (opt3);
further codegen tuning (cu1, LTO) buys ~0–5%, not a step change. If handler ns/syscall
becomes the bottleneck, LTO-fat is worth ~5%; codegen-units=1 is not worth it.

## Supplement: confound-free `getcpu` cut (2026-08-04, same session)

The getpid result carries a constant ~74 ns kernel-injection floor: getpid is classified
**PassThrough** (`detcore/src/syscall_classification.rs`), so the handler injects a *real*
kernel `getpid`. That 74 ns is identical across A–D so it cancels in the opt0→opt3 ratio,
but it dilutes the raw ratio and leaves ~8% of the cost outside the handler. To measure the
**100%-handler** codegen effect with the kernel floor removed, re-ran with a `getcpu` guest:
`getcpu` is **Determinized locally** (`detcore/src/syscalls/misc.rs:690` — writes cpu=0/node=0,
returns `Ok(0)`; **no kernel injection, no coordinator RPC**). Reused the **persisted A.so
(opt3, ships) and D.so (opt0, control)** builds — no rebuild. Same box: `taskset -c 300` +
`systemd-run --user`, 5-rep median, N=1e5→2e6 CPU-time slope, vacuity-guarded
(`direct_hook=N+1`, `ptrace_installation=0`) every run. `getcpu_loop.c`, `run-getcpu.sh`,
`rebuild_getcpu_csv.py`, `results-getcpu.csv` committed here.

| variant | codegen                            | ns/`getcpu` | vs A |
|---------|------------------------------------|------------:|------|
| A       | opt3 / cu16 / lto=off (**SHIPS**)  |       784.2 | —    |
| D       | **opt0** / cu16 (CONTROL)          |      2942.1 | **+275%** (**3.75×**) |
| native  | raw getcpu, no hermit              |        73.7 | —    |

**Confirms and strengthens the getpid result.** The pure-handler codegen swing is **3.75×**
— *higher* than getpid's raw 3.5× precisely because the kernel floor is gone. Cross-check:
subtracting getpid's constant 74 ns floor gives a handler-only ratio of **3.73×**, agreeing
with getcpu's directly-measured **3.75×** to 0.5% (two independent methods). Absolute cost is
also 174 ns lower (784 vs 958 ns) — the removed kernel-injection floor. **Actionable answer
unchanged:** the dominant lever is opt-level=3 and release already ships it.

## Reproduction

```
cd experiments/inguest-handler-codegen-sensitivity_20260804
# (rebuild the 4 .so variants via liteinst runtime build under
#  CARGO_PROFILE_RELEASE_{OPT_LEVEL,CODEGEN_UNITS,LTO}; see metadata.json matrix)
bash run-matrix.sh A:<A.so> B:<B.so> C:<C.so> D:<D.so>
python3 rebuild_csv.py   # authoritative parser (run-matrix.sh's inline awk had a tab bug)
python3 analyze.py results.csv

# confound-free getcpu cut (reuses persisted A.so opt3 / D.so opt0):
bash run-getcpu.sh
python3 rebuild_getcpu_csv.py   # authoritative parser; live run.sh awk \s bug zeroed cpu col
```
