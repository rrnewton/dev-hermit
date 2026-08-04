# LiteInst cache-line straddler rate across a real corpus

**Task:** `liteinst-straddler-wait-calibration` / `liteinst-lane-restaffed-ratchet-toward-ptrace-envelope`
**Date:** 2026-08-04 · **Host:** Linux x86-64, 64-byte cache line

## Question

The LiteInst WordPatch++ straddler protocol used a hard-coded 3000-TSC staleness
wait, which violates PLDI16's `wait > Tmax` premise on machines where 3000 ticks
is below the instruction-fetch staleness bound. A single real L2 probe reported
**ZERO straddlers**, raising the hypothesis: *if straddlers barely occur, the
wait guards a case that almost never fires, so bail-to-ptrace is simply the right
default and calibration is moot.*

**Establish the real straddler rate across a corpus before calibrating a constant
for it** — and, first, establish *which* straddler quantity the "zero" measured.

## Headline

- The reported **"zero straddlers" measured the wrong quantity.** Two different
  straddler notions live in the code and differ by ~9x:
  - **word8 (operative):** the 8-byte publication word crosses a line
    (`(vaddr mod 64) ∈ 57..63`) → `classify_word_patch` returns `GuardedSplit`.
    **This is the rule that drives bail-to-ptrace / needs-calibrated-wait.**
  - **prefix2 (reported):** the 2-byte `syscall` instruction itself crosses a
    line (offset 63 only) → the end-of-run `cacheline_straddlers` stat.
- **Measured word8 straddler rate = 119/944 = 12.6%** across 26 binaries (libc
  alone 78/510 = **15.3%**). Offsets are ~uniform mod 64, so this is a structural
  alignment property (uniform baseline = 7/64 = 10.9%), not a corpus fluke.
- **prefix2 rate = 13/944 = 1.4%** (libc 3/510 = 0.6%) — consistent with a small
  probe reporting "0". **The premise "straddlers barely occur" is REFUTED for the
  operative (word8) definition: straddlers are common, ~1 in 8 syscall sites.**

## Does the rate warrant calibration? No — for a stronger reason than "rate ≈ 0".

1. **Hermit's product path never uses the staleness wait at all.** Hermit
   instruments through the **ptrace-host hybrid**, which installs patches on a
   **stopped tracee** via `PatchPublication::Quiescent`
   (`reverie_liteinst_install_site_for_ptrace`, `activate_quiescent`). That path
   sets `staleness = None` and **never calls `classify_word_patch`/`budget_for_patch`**
   (`runtime.rs:1066-1072`). A quiescent target lets the 8-byte word publish
   safely across a line with **no wait, no bail — regardless of the 12.6% rate.**
   So no per-machine calibration is needed for Hermit whether the rate is 0% or
   50%.
2. **The staleness budget matters only on the standalone in-process (Concurrent)
   SIGSYS path** (`runtime.rs:1623,1671`), where other threads may fetch the site
   mid-publication. There the 12.6% rate *is* material — cross-line publication
   is a common case, not a corner case.
3. **A single calibrated constant cannot be safe across machines** (`wait > Tmax`
   is machine-specific). The hard-coded 3000 is already **removed** and replaced
   by an explicit opt-in, `REVERIE_LITEINST_STRADDLER_STALENESS_TICKS`
   (`straddler.rs`), landed on reverie main (PR #321 / `1f0dfdd`). **Default =
   disabled → the Concurrent path bails cross-line sites to ptrace.** That is the
   correct design: opt-in with an operator-supplied, machine-calibrated budget,
   never a baked-in number.

**Verdict:** calibration is **not warranted as a default**, and the current
code (opt-in explicit calibration, quiescent publication for Hermit, bail-to-
ptrace otherwise) is correct. The justification is *not* "straddlers are rare"
(they are not, ~12.6%); it is that Hermit's quiescent path sidesteps the protocol
and a single constant is unsound across machines.

## Method

Static and decoder-independent. For each decoded `syscall` (`0f 05`) instruction
in the corpus, `(vaddr mod 64)` is computed. ELF load bases are page-aligned
(`4096 % 64 == 0`), so `(runtime_addr mod 64) == (vaddr mod 64)`: **ASLR does not
change the classification**, and the static vaddr equals the runtime patch-site
offset LiteInst classifies.

- **Corpus:** 1459 unique-realpath ELF files from `/usr/bin`, `/bin`,
  `/usr/lib64`, `/lib64`. 26 contain `syscall` instructions (the rest reach the
  kernel through libc). Coverage spans the libc path (libc, ld.so, liburing,
  libasan, libgomp) **and** inlined-syscall binaries (buildah, git-lfs, btrd,
  wprof).
- **Caps (honest coverage):** 30 files > 40 MiB skipped (giant Go/internal
  monoliths, e.g. `devfeature` 1.08 GB, `libLLVM`), per-`objdump` timeout 25 s.
  See `skipped.csv`. Because the mod-64 distribution is uniform, the rate
  generalizes to the skipped inlined-syscall binaries (no alignment mechanism
  distinguishes them).

## Reproduction

```bash
# from the parent workspace
bash scan.sh corpus.txt out
cat out/totals.txt
```

`scan.sh` decodes each binary with `objdump -d`, extracts the address of every
`syscall` mnemonic, and bins `addr mod 64`. Outputs: `per_binary.csv`,
`offset_histogram.csv`, `skipped.csv`, `totals.txt`.

## Files

- `scan.sh` — the analyzer (reproduction recipe).
- `corpus.txt` — the 1459 scanned files.
- `per_binary.csv` — `path,syscall_sites,word8_straddlers,prefix2_straddlers`.
- `offset_histogram.csv` — `offset_mod64,count` (0..63); shows ~uniformity.
- `skipped.csv` — files excluded by size cap / timeout, with reason and bytes.
- `totals.txt` — aggregate counts and rates.
- `metadata.json` — SHAs, source anchors, host, results.
