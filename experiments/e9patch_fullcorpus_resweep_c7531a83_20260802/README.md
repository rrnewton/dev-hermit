# e9patch full-corpus re-sweep @ c7531a83 — authoritative new column (183/184 = 99.46%)

## Question

Last cycle's re-check (`e9patch_compat_ratchet_recheck_20260802`) retracted the
"7 inherent gaps / SATURATED" claim by re-measuring only the 7 previously-flagged
cells at `e8ddd925`, and explicitly left open a **scope caveat**: the full 200-cell
e9patch column in `compat-envelope/REPORT.md` was still frozen at `82a8e853` and
needed a fresh whole-corpus sweep for an authoritative new total. This artifact
runs that sweep at current main and answers: **what is the real e9patch
parity/determinism column now?**

## Method

Built a featured hermit at current main `c7531a83` (`--features e9patch`, external
`CARGO_TARGET_DIR`, zero mutation of the primary checkout), then ran the canonical
whole-corpus gate for ptrace + e9patch:

```bash
HERMIT_BIN=scratch/featured-c7531a83-target/release/hermit \
HERMIT_E9TOOL=worktrees/e9patch/reverie/third-party/e9patch/e9tool \
HERMIT_E9PATCH_BACKEND=worktrees/e9patch/reverie/third-party/e9patch/e9patch \
  compat-envelope/collect-fullcorpus.sh --backends ptrace,e9patch \
    --no-assert --par 24 --out results.csv
```

- **parity** = e9patch plain-`--strict` stdout == ptrace plain-`--strict` stdout
  (byte-compare). The parity reference is plain `--strict` (NOT `--verify`, which
  double-runs and emits no parent stdout).
- **det** = `<backend> --strict --verify` exit 0 (L2 DETLOG bitwise self-verify).
- Portable-lane flags `--no-virtualize-cpuid --max-timeslice=disabled`, applied by
  the gate. Guest build tree under `hermit/ignored/` (gitignored, not host `/tmp`);
  all paths `realpath`'d so e9patch's no-`..` rule holds.
- Corpus: the 235-cell e2e UNION corpus (grown from the 200-cell corpus the
  `82a8e853` column used). Of these, **184 are ptrace-green** (ptrace itself
  produces a valid `--strict` reference and passes `--verify`); the rest are cells
  where ptrace itself errors and parity is therefore unmeasured for every
  non-ptrace backend.

Sweep wall-clock: 14m25s on a shared 316-core box; gate result: **GREEN**.

## Results

Provenance: hermit `c7531a83`, reverie `ef5ffebc`.

| denominator | ptrace | e9patch | note |
|---|---|---|---|
| **ptrace-green cells (184)** | 184/184 det | **183/184 det (99.46%)**, **183/184 parity (99.46%)** | 0 parity-unmeasured on green |
| raw measurable (205) | 184 det | 183 det, 183 parity, 1 parity=0, 21 parity-unmeasured | unmeasured = ptrace ref failed |

**The single cell where ptrace passes det but e9patch does not:**

| cell | ptrace det | e9 det | e9 parity | class |
|---|---|---|---|---|
| `c-programs/rcx-canonicalization` | 1 | 0 | 0 | **inherent** (instruction relocation) |

There are **zero** parity-only gaps (no cell where both backends are det but their
stdout diverges). Every other non-green cell in the corpus is one where **ptrace
itself fails** — e9patch is neither credited nor blamed there (parity unmeasured).

## Interpretation

- **The authoritative e9patch column at `c7531a83` is 183/184 (99.46%) parity AND
  det on the ptrace-green denominator.** This supersedes the stale `82a8e853`
  REPORT.md figures (det 179/200, parity 173/200; on ptrace-green 178/179 det,
  172/179 parity).
- **The parity gap collapsed from 7 → 1** across the intervening 40-odd commits,
  confirming last cycle's retraction with a whole-corpus measurement rather than a
  7-cell spot-check. The flips came from landed detcore determinization
  (`ee746bde` stdio-inode identity; `c7531a83` proc-fd alias fixture; the
  backend-independent virtual clock), which e9patch inherits for free because its
  runtime *is* ptrace.
- **`rcx-canonicalization` is the sole residual e9patch-specific gap and it is
  genuinely inherent**: e9tool must relocate the in-ELF `syscall` into a
  trampoline to intercept it, so the guest's own SYSRET `%rcx` (return RIP) no
  longer equals its lexically adjacent `leaq 1f(%rip)` label; the guest exits 1.
  No interception backend that relocates the syscall can pass this cell.
- **e9patch faithfully mirrors ptrace's own failures.** The 21 parity-unmeasured
  cells (qemu-*, thread-contention, ipc/signal/mmap-determinism, shell-pipeline,
  pmu-skid, …) are cells ptrace itself does not pass under strict-verify; e9patch
  neither improves nor regresses them, exactly as expected for a ptrace-runtime
  backend.

## Consequences

- **No e9patch product PR is warranted.** The only e9patch-specific gap
  (`rcx-canonicalization`) is unfixable without abandoning syscall interception;
  every other cell already tracks ptrace. The product-side compat ratchet is
  genuinely at its achievable bar — this time confirmed by whole-corpus
  measurement, not inference.
- **Further "ratchet" is corpus expansion, not defect-fixing.** Adding more
  freestanding raw-syscall guests (the `e9patch_corpus` rounds lineage) widens
  coverage but does not close a parity defect, because none remains.
- **REPORT.md updated** in the same parent commit to carry the `c7531a83`
  183/184 figure with this artifact cited.

## Provenance

- hermit `c7531a83` (origin/main), reverie `ef5ffebc`; featured build
  `--features e9patch` into `scratch/featured-c7531a83-target`.
- e9tool/e9patch AOT binaries from
  `worktrees/e9patch/reverie/third-party/e9patch/`.
- Host: shared 316-core devbig, release hermit binary. Guests under
  `hermit/ignored/` (never host `/tmp`).
- `results.csv` = the full 410-row sweep (ptrace + e9patch × 205 measurable cells).
- Supersedes the column-total scope caveat left by
  `experiments/e9patch_compat_ratchet_recheck_20260802/` (which re-measured only
  7 cells); this is the whole-corpus follow-through.
