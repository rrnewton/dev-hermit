# e9patch compat "last cells" — honest root-cause of every parity/determinism gap

> **⚠️ SUPERSEDED (2026-08-02).** The "SATURATED / 7 inherent gaps" conclusion
> below was derived from the `82a8e853` scorecard + source-reading, not a direct
> re-measurement. A featured re-measurement at current main `e8ddd925` shows **6
> of the 7 flagged cells flipped to parity-green + L2-deterministic** (via
> `ee746bde` stdio-inode determinization + backend-independent virtual clock);
> only `rcx-canonicalization` is genuinely inherent. See
> `experiments/e9patch_compat_ratchet_recheck_20260802/`. The **method** here is
> sound; only the SATURATED conclusion is stale.

## Question

The full-corpus compat-envelope scorecard puts e9patch at **parity 96%,
determinism 99%** of the ptrace-green cells. What are the exact residual gap
cells, and for each, is the divergence a **fixable defect**, an **inherent
consequence of e9patch's architecture**, or a **shared failure unrelated to
e9patch**? The goal is an honest disposition — not to inflate the ratchet by
papering over structural limits or by adding corpus guests that dodge the gaps.

## Method

Static join of `compat-envelope/fullcorpus-scorecard.csv` (hermit
`82a8e853`, reverie `a4f33d69`, 316-core devbig, `hermit run --strict --verify`
L2 on each backend), keyed on `test_id`, comparing the **e9patch** column to the
**ptrace reference**:

- `deterministic` (det) = backend `--strict --verify` exits 0 (self-verified
  bitwise-reproducible across two runs of the *same* backend).
- `parity` = backend stdout hash == ptrace stdout hash.

Then each gap cell's test source was read to attribute the divergence to a
concrete line of code. Classes: **(a)** inherent to e9patch instruction
relocation; **(b)** backend-sensitive *value* emitted by the test, shifted by
e9patch's structure; **(c)** shared failure unrelated to e9patch.

## Results

**Denominator (measured, not asserted):**

| metric | value |
|---|---|
| full-corpus cells | 200 |
| ptrace-green cells | 179 |
| e9patch det-green on ptrace-green | **178 / 179 = 99.4%** |
| e9patch parity-green on ptrace-green | **172 / 179 = 96.1%** |

The 7 gaps = **1 determinism failure** + **6 parity-only** (det ok, stdout value
differs). Per-cell root cause (file:line evidence in
`tests/` under the hermit checkout):

| cell | class | root cause | evidence |
|---|---|---|---|
| `c-programs/rcx-canonicalization` | **(a) inherent** | Only guest with a raw **in-ELF `SYSCALL` site** that e9tool rewrites. Test asserts SYSRET `%rcx` (return RIP) == lexically-adjacent `leaq 1f(%rip)` label; e9patch relocates the `syscall` into a trampoline so the return RIP no longer equals the inline address → invariant fails, guest exits 1. | `tests/c/rcx_canonicalization.c:46-62` |
| `c-programs/print-memaddrs` | **(b) inherent value** | Prints absolute stack/heap addresses (`%p`). e9patch runs the rewritten cache-artifact ELF with a different segment layout + e9loader-reserved mmaps → load addresses legitimately shift. Exits 0. | `tests/c/print_memaddrs.c:16,22` |
| `c-programs/proc-fd-link-aliases` | **(b) inherent value** | `readlink`s fd 1; under pipe capture it resolves to `pipe:[<virtual-inode>]`. The e9loader prologue performs extra file opens that shift detcore's virtual-inode allocation → emitted inode differs. Same mechanism as the documented SaBRe `proc_fdinfo` disable (`c-programs.toml:4335`). | `tests/c/proc_fd_link_aliases.c:36-53` |
| `system-utils/date-nanoseconds` | **(b) inherent value** | `date +…_%N` reads detcore's virtual clock; the e9loader prologue's syscalls advance virtual time before `_start`, so the deterministic nanosecond field shifts cross-backend. | `examples/date.sh:9` |
| `language-runtimes/bash-loop-pipe-time` | **(b) inherent value** | `version/bytes/sha256/values` match; only `wall_ns` (virtual-clock read) differs by the fixed e9loader-prologue offset. | `bash-loop-pipe-time.sh:36-38` |
| `language-runtimes/perl-io-subprocess-time` | **(b) inherent value** | `version/bytes/sha256/child` match; only `wall_ns`/`monotonic_ns` differ (virtual-clock offset). | `perl-io-subprocess-time.sh:56-59` |
| `language-runtimes/python-io-subprocess-time` | **(b) inherent value** | `version/bytes/sha256/child` match; only `time.time_ns()`/`monotonic_ns()` differ (virtual-clock offset). | `python-io-subprocess-time.sh:50-51` |

**Class tally: (a) 1, (b) 6, (c) 0.**

## Interpretation

**Every one of the 7 residual gaps is structural to e9patch, not a fixable
defect, and none is a shared/e9patch-unrelated failure.** They reduce to two
intrinsic properties of the backend, both already documented in
`tests/backend-parity/README.md:177-196` ("byte-identical DETLOG parity is
impossible by construction for e9patch"):

1. **Instruction relocation** (1 cell). e9tool moves the guest's in-ELF `syscall`
   into a trampoline. A guest that *observes its own return RIP*
   (`rcx-canonicalization`) is the single case that can detect this; it is a
   genuine correctness divergence but an inherent one — no backend that relocates
   instructions can pass it.

2. **The deterministic e9loader prologue** (6 cells). Before the guest's
   `_start`, the e9patch image runs a fixed
   `readlink → open → arch_prctl → N×mmap → close` prologue. Those extra syscalls
   deterministically (a) **advance detcore's virtual clock** (the 4 time cells)
   and (b) **shift virtual-inode allocation** (`proc-fd-link-aliases`), and the
   rewritten image's segment layout shifts **absolute addresses**
   (`print-memaddrs`). Each backend remains **internally L2-deterministic**
   (det=1); only the *cross-backend value* differs, by a fixed offset.

**Consequences for the ratchet (the honest disposition):**

- **These are not corpus-addition opportunities.** Adding freestanding guests
  cannot move these cells; the divergence is in the loader prologue / relocation,
  not in per-syscall coverage.
- **These are not (mostly) harness path-invariance bugs.** An earlier hypothesis
  held that the 6 parity cells were per-backend stdout *path* artifacts fixable by
  a path-invariant harness. Source inspection refutes that for 5 of 6: the
  divergent bytes are virtual-clock/virtual-inode/address *values* the guest
  itself emits, not harness-injected paths. (Only if a future harness pointed fd
  1 at a plain file would `proc-fd-link-aliases` stop diverging — but that would
  mask, not fix, the inode shift.)
- **The correct action is documentation, not code.** Record all 7 as e9patch's
  known inherent limits (relocation + e9loader-prologue offset), so the 96%/99%
  is understood as **saturated at the achievable bar**, not a backlog. A guest
  that prints an absolute address, a raw virtual timestamp, a `/proc/self/fd`
  pipe inode, or its own return RIP will always be e9patch-parity-divergent by
  construction while remaining internally deterministic.
- **Optional scorecard refinement (non-blocking):** the renderer could tag these
  7 as `e9patch-inherent (loader-prologue|relocation)` so the column reports
  "172/179 parity + 7 inherent-structural" rather than an undifferentiated gap.
  This is a reporting nicety; it changes no product behavior and is not required
  to call the ratchet complete.

## Reproduction

```bash
cd ~/work/dev-hermit
# Denominator + per-cell det/parity join (e9patch vs ptrace reference):
awk -F',' '
  NR>1 { k=$8"/"$9
    if($11=="ptrace"){pdet[k]=$14}
    if($11=="e9patch"){edet[k]=$14; epar[k]=$15} }
  END { for(k in pdet) if(pdet[k]=="1"){ pg++;
          if(edet[k]=="1")egd++; if(epar[k]=="1")egp++ }
        printf "ptrace_green=%d  e9patch_det=%d(%.1f%%)  e9patch_parity=%d(%.1f%%)\n",
          pg, egd, 100*egd/pg, egp, 100*egp/pg }' \
  compat-envelope/fullcorpus-scorecard.csv
# expect: ptrace_green=179  e9patch_det=178(99.4%)  e9patch_parity=172(96.1%)
```

`results.csv` in this directory holds the 7 gap cells × {ptrace, e9patch} rows
extracted from the scorecard.

## Provenance

- Data: `compat-envelope/fullcorpus-scorecard.csv` + `REPORT.md` at hermit
  `82a8e853`, reverie `a4f33d69`.
- Source evidence read from the e9patch worktree hermit checkout
  (`worktrees/e9patch/hermit`), files/lines cited in the table.
- Related: `tests/backend-parity/README.md:177-196` (e9loader prologue,
  "impossible by construction"); the SaBRe `proc_fdinfo` disable
  (`tests/e2e/manifests/c-programs.toml:4335`) documents the same virtual-inode
  mechanism for a different backend.
