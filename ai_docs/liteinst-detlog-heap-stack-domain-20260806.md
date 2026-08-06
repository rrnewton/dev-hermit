# LiteInst detlog heap/stack parity: the injected-bytes question, answered without the backend

- **Task:** `liteinst-detlog-heap-stack-parity` (north star #89; provenance carried from #268).
- **Author:** impl agent, claude-opus-5. Local only, no egress, no concurrent validate.
- **Tree:** slot `worktrees/oci/hermit` @ `5562161a4`, base hermit main `b64d893ae9ea`.
- **Predecessor:** `ai_docs/liteinst-strict-parity-ratchet-blocked-20260806.md` — LiteInst does not
  activate on current main (regression), so **no corpus parity run was possible here either.**

The task's one sub-question that does **not** require a running backend is *"ensure liteinst-injected
bytes aren't hashed."* That is answerable from the domain definition plus host-native probes, and it
is answered below. **Answer: they are not hashed, by construction and by measurement.**

---

## 1. The shipped domain excludes injected bytes by construction

`detcore/src/lib.rs:718-765` (`detlog_memory_maps`), verified at `b64d893a`. When the backend does
not supply regions, it filters `/proc/<pid>/maps` to exactly:

```rust
procmaps::MMapPath::Stack if self.cfg.detlog_stack => true,
procmaps::MMapPath::Heap  if self.cfg.detlog_heap  => true,
_ => false,
```

`MMapPath::{Stack,Heap}` are the **kernel `[stack]` / `[heap]` labels**. A preloaded DSO's text and
data are file-backed or plain anonymous mappings and can never carry those labels. So LiteInst's
injected bytes were **never in the hashed domain** — which is precisely the owner's stated principle
(*define the domain so patched bytes were never in it; never maintain an exclusion list*). No
exclusion work is required for the shipped hash.

## 2. Measured confirmation, and the preload's actual footprint

Host-native probes with **ASLR disabled** (`setarch -R`), comparing a guest run with and without
`LD_PRELOAD=target/release/libreverie_liteinst.so`:

| Probe | Without preload | With preload |
|---|---|---|
| `[heap]` extent (`/bin/sh`, ×3 each) | `55555558c000-5555555d0000` | **identical** |
| `[stack]` extent (`/bin/sh`, ×3 each) | `7ffffffdc000-7ffffffff000` | **identical** |
| `[stack]` extent (`python3`, ×4 each) | `7ffffffdc000-7ffffffff000` | **identical** |
| total mappings (`python3`) | 47 | 52 (**+5**) |
| liteinst DSO mappings labelled `[heap]`/`[stack]` | — | **0** |

So the preload adds **5 mappings**, **none** of which is in the hashed domain, and it does **not move
`[heap]` or `[stack]`**. Both halves of the owner's prediction — *same address*, and *nothing injected
inside the domain* — hold at the mapping level for the shipped hash.

### One unreplicated observation, recorded as such

A single earlier run of a **heap-allocating** python guest (3 MB `bytearray`) showed `[stack]` starting
one page lower with the preload (`7ffffffdb000` vs `7ffffffdc000`, same end). **It did not reproduce**:
4/4 runs of the simpler probe were identical on both sides. I am recording it as **noise / N=1, not a
finding**. If anyone pursues it, repeat the *allocating* variant specifically — that is the shape that
produced it, and the simpler probe does not exercise the same stack depth.

## 3. Why a green result here would still not mean much

Carried forward from the prior measurement of this domain (`[heap]`-only captures **0.2%** of
anonymous non-executable memory — 264 KiB of `[heap]` versus 106,608 KiB of anonymous non-exec on a
probe using the three allocation shapes every real program uses):

**`MMapPath::Heap` is the brk segment only.** Every allocation above glibc's 128 KiB
`M_MMAP_THRESHOLD`, and every non-main-thread arena, is invisible to the hash. A LiteInst-vs-ptrace
heap-parity "match" under the shipped domain is therefore close to vacuous — the same failure shape as
a stdout-only parity metric. **Fixing LiteInst parity against this domain would be optimising against
a nearly-empty oracle.**

The forward risk that this task should actually own: **under the intended domain (Rule A: anonymous ∧
!exec ∧ readable ∧ !stack ∧ !special, or Rule B provenance), the preload's writable data segments
*would* be admitted** — they are anonymous, readable, non-exec, and not stack. Today's "injected bytes
aren't hashed" is a property of the domain being narrow, not of the domain being *right*. Rule B
(provenance: pages the guest itself obtained via its own brk/mmap) is what keeps that true after the
domain is widened, and it is the direct carry of #268.

## 4. Status against the task's asks

| Ask | Status |
|---|---|
| Verify full `--detlog-heap` + `--detlog-stack` parity vs ptrace over the liteinst-passing corpus | **NOT DONE** — backend does not activate (predecessor G1) |
| Ensure liteinst-injected bytes aren't hashed | **DONE — confirmed by construction and measurement (§1, §2)** |
| Fix each divergence toward 100% | **N/A** — no divergence measurable until the backend runs |
| Carry provenance (#268) | **Recorded (§3)** as the mechanism that must precede widening the domain |

## 5. Gaps

| # | Gap |
|---|---|
| H1 | **Blocked on the LiteInst activation regression** (predecessor G1). No parity number can exist until it runs. |
| H2 | **The heap oracle is near-vacuous** (`[heap]` label only, ~0.2% coverage). Widening it must come *before* chasing liteinst heap parity, or the parity result is meaningless. |
| H3 | **Widening the domain re-opens the injected-bytes question.** Rule A admits the preload's anonymous non-exec data; Rule B (provenance, #268) is what keeps injected bytes out. Land B with A as a cross-check and report the delta. |
| H4 | When parity is finally measured, compare **(address-range, digest) pairs**, never bare digests, and treat `region_count == 0` as **NO-RESULT**, never a match. |

## 6. Limitations

- **No hermit execution at all** in this task — every number above is a host-native `/proc/<pid>/maps`
  probe. They characterise the *preload's* effect on the address space, which is the right proxy for
  "does LiteInst perturb the hashed domain", but they are **not** a Detcore-observed heap/stack hash.
- ASLR was disabled for comparability; hermit virtualises layout differently, so absolute addresses
  here are not the addresses a hermit run would see. The **equality between the two arms** is the
  claim, not the addresses themselves.
- Only two guests (`/bin/sh`, `python3`) and one preload profile (release) were probed.
- The §2 table shows the preload loaded by the *normal loader*. Under hermit the DSO is injected in a
  ptrace-supervised container; that path is unexercised because the backend does not activate.
