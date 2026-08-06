[impl agent, claude-opus-5]

Implements Part-B **Stack 1.4** — work item (1) of `dbi-detlog-heap-stack-parity`.

## Summary

`--detlog-heap` produced **zero `[heap]` records on the DBI backend** while ptrace
produced five for the same guest, so heap parity between them was unmeasurable.
Worse, it was unmeasurable in a way that reads like success: downstream, a
zero-record heap comparison looks *compared and matched* rather than *never
measured*.

The cause is neither a determinism bug nor a missing mapping. Measured with a
guest that grows its break and then dumps its own maps:

```
ptrace: brk 0x405000 -> 0x447000    00405000-00447000 rw-p ... [heap]
dbi:    brk 0x406000 -> 0x448000    00406000-00448000 rw-p ...
```

The region **exists under DBI with the same shape and permissions** — the kernel
simply does not label it. `brk` behaves identically on both backends (8 `brk`
and 16 `mmap` calls each; returns matching step for step). The kernel tags
`[heap]` only for `[mm->start_brk, mm->brk)`, and under a backend that loads the
guest itself (DynamoRIO) that break belongs to the loader, so the guest's heap is
an ordinary anonymous mapping.

`detlog_memory_maps` filtered on `MMapPath::Heap` — a **proxy** for "this is the
guest heap" that only holds where the kernel's break is the guest's.

## What changed

Detcore can observe the real thing. `brk` is PassThrough, but both `brk(NULL)`
and `brk(addr)` return the break in effect afterwards, so every successful return
is recorded into `MemoryMetadata` — already shared per address space with the
correct `CLONE_VM` and fork semantics, so no new sharing discipline was invented.

When no labelled `[heap]` mapping is found, the heap record is emitted from that
observed range, reported *through the enclosing anonymous mapping* so it carries
real procfs columns and is textually comparable with the labelled record another
backend produces for the same guest.

## The result — the question this was blocking is now answered

DBI emits four heap records whose hashes are **byte-identical to ptrace's, in
order**:

| # | ptrace range | DBI range | hash (both) |
| --- | --- | --- | --- |
| 1 | `0x405000-0x426000` | `0x406000-0x427000` | `74518f204d46de66…` |
| 2 | `0x405000-0x447000` | `0x406000-0x448000` | `014487333b1ba27d…` |
| 3 | `0x405000-0x447000` | `0x406000-0x448000` | `b303416ba72e1cc7…` |
| 4 | `0x405000-0x447000` | `0x406000-0x448000` | `f36017bb0433443e…` |

ptrace emits five because it repeats `b303416b…` once (two consecutive syscalls
with an unchanged heap); the **distinct content sequence matches exactly**, at a
base one page higher. So DBI's heap contents agree with ptrace's — previously a
NO-RESULT.

## Determinism

- The heap range is derived from `brk` return values Detcore already observes,
  not from host state. Two runs of the patched binary produce byte-identical
  `[memory]` output (65/65 records on ptrace; identical heap-hash stream on DBI
  across two runs).
- `observe_brk` is monotone in the sense that matters: the first observation
  fixes the base and later ones only move the end, so the record's identity does
  not depend on *when* the emitter happens to run. A repeated `brk(NULL)` query
  cannot move the base, and returning to the base yields an empty heap rather
  than an inverted range (both unit-tested).
- A `brk` return of 0 is ignored; recording it would set the base to 0 and hash a
  wild range.
- **ptrace is provably unaffected**: it always finds a labelled mapping, so the
  fallback never executes. Verified rather than assumed — its heap hash sequence
  is unchanged across the pre-change and post-change builds, and record counts
  are identical (65 total, 5 heap, 60 stack).

## Linux Semantics

No guest-visible change; this only affects Detcore's diagnostic memory log. The
reported region is exactly the interval Linux itself would label `[heap]` — the
program break — so the record means the same thing on every backend rather than
meaning "whatever the kernel chose to tag". The enclosing anonymous mapping
supplies the real permissions, offset, device, and inode columns.

## Validation

**Head:** `9001bb3c8e2da9e3958900a717858c7a29ab1cfe`
**Base:** `origin/main` `4c70658e785834737cbe1524f77330c781a6f5ea` (0 behind, 1 ahead)
**Backends:** ptrace and DBI (`--features third-party-backends`)
**Flags:** `--strict --no-virtualize-cpuid --max-timeslice=disabled --detlog-heap --detlog-stack`

| Check | Result |
| --- | --- |
| DBI heap records | **0 → 4** |
| DBI heap hashes vs ptrace | **4/4 byte-identical, in order** |
| ptrace records unchanged | 65 total / 5 heap / 60 stack, heap hash sequence identical |
| ptrace same-binary determinism control | run A vs run B **identical, 65/65** |
| DBI determinism | 2 runs, identical heap-hash stream |
| `cargo test -p hermit-detcore --lib` | **388 passed, 0 failed** (2 new tests) |
| `cargo fmt --all -- --check`, clippy (both feature sets) | clean |

**Observed but not caused by this change, and worth its own look:** ptrace
`[stack]` hashes differ between two *different hermit builds* of the same guest,
while heap hashes do not. Same-binary runs are identical, so this is cross-build
variance in stack content, pre-existing and outside this change (the fallback
never runs on ptrace). Flagging rather than folding it in.

**Not claimed.** This fixes work item (1) only. Work item (2), stack region
normalization, is still open — I re-measured the one-page difference (ptrace
stack `0x7ffffffdc000-0x7ffffffff000` vs DBI `0x7ffffffdb000-0x7ffffffff000`), so
`[stack]` hashes still cannot be compared as-is and "0 shared stack hashes"
still carries no content information. The dtid confound (DBI reports the raw
host TID) is unchanged and owned by
`detlog-record-framing-standardize-all-backends`. One single-threaded guest;
SaBRe/LiteInst not exercised; KVM untestable on this box.

## Blocker

**No validate receipt.** `ci-hub validate-run` refuses at admission:
`preflight_validate.py` shells out to `with-proxy git fetch`, 403 from an agent
shell; the only working egress (`herdr-run`) refuses `ci-hub` (allowlist
`cargo, gh, git`). Admission predicate computed locally: moving-base PASS,
fixed-floor PASS.
