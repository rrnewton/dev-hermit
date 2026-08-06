# DBI detlog heap/stack parity: the decisive experiment is run — and neither result is a content bug

**Task:** `dbi-detlog-heap-stack-parity` · **Agent:** hermit-audit (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress.

## Outcome

The experiment two prior agents specified but could not run is now **run**, with its control:

* **Control passes.** ptrace-vs-ptrace on the same guest produces **identical** canonical record
  streams — so ptrace is a clean oracle here, and any divergence is attributable.
* **heap parity: NO-RESULT.** DBI emits **zero** `[heap]` records for a guest whose brk segment
  demonstrably grows (ptrace emits 4).
* **stack parity: NOT COMPARABLE AS-IS.** DBI's stack region is **one page larger** than ptrace's, so
  the digests cover different byte ranges and cannot match by construction.

**Neither is evidence of a DBI memory-content divergence** — which is exactly what a naive reading of
"0 shared hashes" would have concluded, and exactly what the prior notes warned against.

## First: two instrument bugs, one of which explains the prior dead end

The previous agent reported *"ZERO [memory] records from BOTH backends… MY CAPTURE IS WRONG"*. They
were right, and here is the cause:

1. **The guest was under `/tmp`.** Hermit refuses it outright:
   `Program /tmp/guest_heap is under host /tmp, but Hermit replaces guest /tmp with an isolated
   directory.` The guest never ran. Relocating it to `~/.local/hermit-deps/guests/` fixes it.
2. **`--log-file` under `/tmp` fails differently per backend** — a parity defect in the tooling itself:

   | backend | `--log-file /tmp/…` |
   | --- | --- |
   | ptrace | **panics** — `global_opts.rs:61 Failed to open log file: NotFound`, `rc=1` |
   | dbi | **silently ignored**; DETLOG goes to stderr |

   Same flag, opposite failure modes. (The DBI half is the `--log-file` finding I reported earlier
   today; this adds that ptrace hard-fails on the same input.)

With logs and guest both outside `/tmp`, the gate passes: **ptrace 63 `[memory]` records** (59 stack +
4 heap), **DBI 55** (all stack), guest output `sum=125080` on both.

## The decisive comparison

Method, as specified by the prior notes: canonicalize `dtid` to a per-run ordinal by first appearance
(**canonicalize, don't strip**), then compare records as `(dtid, range, perms, tag, hash)` tuples.

The dtid confound is confirmed: **ptrace `dtid=3` constant; DBI `dtid=1912926`, the raw host TID.**

| comparison | result |
| --- | --- |
| **ptrace p1 vs ptrace p2** (control) | **identical** canonical streams |
| ptrace vs DBI, full tuple | 63 vs 55 records, **0 shared** |
| ptrace vs DBI, **address-normalized** (tag+hash only) | **0 shared** |

Zero shared even after address normalization looks damning — until you look at *why*.

### `[stack]`: the region differs, so the digest cannot match

```
ptrace  0x7ffffffdc000-0x7ffffffff000   (0x23000 bytes)
dbi     0x7ffffffdb000-0x7ffffffff000   (0x24000 bytes)   <- one page lower start
```

DBI's guest stack is **one 4 KiB page larger**. A digest over `0x23000` bytes can never equal a digest
over `0x24000` bytes, whatever the content. So the 0-shared-hashes result on stack is **fully
explained by the region mismatch** and carries no information about content.

**Root cause to fix: the stack region itself**, not the hashes. Either DynamoRIO's presence shifts the
initial stack mapping by a page, or the `[stack]` domain must be defined on a normalized (e.g.
page-aligned, size-matched) window before any cross-backend digest comparison is meaningful.

### `[heap]`: DBI emits nothing

ptrace emits 4 heap records showing the brk segment growing:
`0x405000-0x426000` → `0x405000-0x447000`. **DBI emits 0.**

This *refines* the prior note's explanation. They attributed heap NO-RESULT to *"the small guests have
essentially no brk heap"*. With a guest built specifically to force sub-128 KiB (brk-backed) mallocs,
**ptrace sees the heap and DBI still emits nothing** — so the cause is on the **DBI side**, not the
guest. Heap parity is not diverging; it is **unmeasurable**.

## Why this matters for the north star

Both findings are the same shape as the rest of today's compat work: an **absence or a framing defect
being read as a parity result**. Had this been reported as "DBI stack parity 0/59, heap 0/4", it would
have looked like a serious backend-correctness problem and pointed the fix at memory handling. The
actual work items are:

1. **DBI `[heap]` emission** — find why the brk segment produces no `[memory]` record under DBI. This
   is the blocker for any heap parity claim, and it is an emission bug, not a determinism bug.
2. **Stack region normalization** — reconcile the one-page difference, or define the comparison window
   so the two backends hash the same bytes.
3. **dtid framing** — already owned by `detlog-record-framing-standardize-all-backends`; my canonical
   ordinal is a measurement workaround, not a fix.
4. Only after 1–3 can the question *"do DBI's stack/heap contents match ptrace's?"* be asked at all.

## Provenance (#268)

* Binary: `worktrees/oci/hermit/target/release/hermit`, built 2026-08-06 04:30, `--features third-party-backends`.
* Guest: `~/.local/hermit-deps/guests/guest_heap`, from
  `experiments/dbi-heap-stack-parity_20260806/guest_heap.c`, `gcc -O1`, dynamic (**no static libc on
  this host**), prints `sum=125080`.
* Flags: `--log=info --log-file <outside /tmp> --strict --no-virtualize-cpuid --max-timeslice=disabled --detlog-heap --detlog-stack`.
* `LD_LIBRARY_PATH=~/.local/hermit-deps/lu/usr/lib64`.
* Capture: ptrace from the log file; **DBI from stderr** (it ignores `--log-file`).

## Limits

* **One guest, single-threaded.** The prior carry-in notes a genuine *ptrace-side* `[stack]` divergence
  on a multithreaded `zstd -T4` workload; my control passing here does not generalise to threaded
  guests.
* **No divergence fixed, no code changed.** All three work items above are hermit/reverie product code.
* I did not determine *why* DBI's stack is a page larger or why heap records are absent — those are the
  named next steps, not findings.
