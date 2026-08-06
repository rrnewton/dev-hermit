# Full-depth cross-backend parity: the metric is shallower than the claim, and deeper is partly unattainable

**Task:** `cross-backend-detlog-parity-sweep` (P1) · **Date:** 2026-08-06 · **Author:** hermit-design
**Bound to:** hermit `f89c69766371806d3c9b2c3003531df2d59d6118` (clean worktree build, no `-dirty`),
reverie `9470712afa9b421c72850ab7955fb335692e43a0` (Cargo.lock), e9patch artifact
`eeec34aa130e3511…` · **Host:** devbig014 (316 cores, shared) · **Local only**, no egress.
**Harness + logs:** `ignored/detlog-parity/` (run-cell.sh, sweep.sh, results.csv, every log).

---

## 1. What was asked, and what the ceiling turned out to be

Verify FULL inter-backend parity — `--log INFO`, `--detlog-stack`, `--detlog-heap` — across
ptrace/dbi/kvm/liteinst/sabre, rather than the stdout-only equivalence the scorecard ships.

**Only two backends can run on this host today**, so the sweep is ptrace (reference) + e9patch:

| backend | state | reason |
| --- | --- | --- |
| **ptrace** | ✅ runs | — |
| **e9patch** | ✅ runs | prebuilt `e9tool` in-tree, via `HERMIT_E9TOOL` |
| **kvm** | ❌ livelock | `/bin/true` **non-strict** rc=124 at 120 s, boxed |
| **liteinst** | ❌ blocked | preload handshake: *tracee terminated … (phase Waiting)* |
| **sabre** | ❌ unbuildable | `CMakeLists.txt`, and `cmake` is not installed |
| **dbi** | ❌ unbuildable | needs DynamoRIO via cmake |

Two of these correct the record:

* **KVM is worse than believed.** The standing note says *"non-strict KVM does complete"*. At
  `f89c69766` **non-strict hangs too** — both `/bin/true` and the exact cited
  `sh -c 'ls -1 /dev'` case returned rc=124 at 120 s. This is the **6th** confirmation of the
  livelock, and it **answers the open question** that a rebuild at `f89c69766` (whose top commit
  *"Reconstruct deterministic run-queue exec handoff linearly"* touches `scheduler`/`tool_global`)
  might fix it: **it does not.**
* `cmake` absent is the same host-provisioning decay that removed qemu/busybox — it now costs two
  backends.

---

## 2. The harness, and the control that makes it trustworthy

`--detlog-stack` compares the guest's initial stack, and **envp lands on that stack**. An
unpinned comparison measures the launcher, not the backend. So every cell runs under `env -i`
with one fixed variable set — *including* `HERMIT_E9TOOL` and `LD_LIBRARY_PATH`, exported even
for backends that ignore them, precisely so envp does not vary by backend.

**Positive control (ptrace vs ptrace, same pinned env):** clean on **every** guest in the sweep —
`0` syscall delta, identical size sequences, and `log-diff` reporting *no substantive differences*
(e.g. 167/167 on `/bin/true`, 699/699 on the threaded heap guest). The harness measures the
backend, not the environment.

---

## 3. The depth gap, proven on a concrete cell

8 guests × {ptrace ref, ptrace control, e9patch}.

| metric | result |
| --- | --- |
| **stdout parity (what the scorecard ships)** | **8/8 = 100%** |
| **full DETLOG parity** | **7/8 = 87.5%** |

The one divergent cell is `nondet` (4×CPUID + 2×RDTSC + heap traffic) — and its **stdout is
byte-identical to ptrace**, so `collect-envelope.rs` would score it `parity=1`. Confirmed still
true at current main: `run_and_hash` hashes `out.stdout` only, and a grep of the parity path finds
**zero** occurrences of `--log`, `--detlog-stack`, or `--detlog-heap`.

**That is a worked counterexample to the shipped metric**, not merely an argument that one exists.

---

## 4. Mechanism: two independent components

### (a) e9patch's runtime adds 9 syscalls — a real sequence divergence

On the divergent cell (`candidate_sites=3; mapped_sites=3`):

```
ptrace  56 finish-syscalls        e9patch  65        delta +9
+5 mmap   +1 open   +1 readlink   +1 arch_prctl   +1 close
```

Its own loader activity is visible to Detcore and shifts every later syscall ordinal.

### (b) Relocation changes every address-valued field

| | ptrace | e9patch |
| --- | --- | --- |
| heap base | `0x405000` | `0x20e9ea000` |
| heap **size** sequence | `135168 270336 139264 139264` | **identical** |
| stack size | `135168` | **identical** |
| every heap/stack **hash** | — | **differs** |

Identical allocation behaviour, different load address. And **e9patch is self-deterministic at
full depth** (221/221, run1 vs run2), so this is not a determinism defect.

### The correlation is exact

**e9patch diverges iff it rewrites.** 7 of 8 guests had `candidate_sites=0` — the AOT pass is a
no-op and the unmodified binary runs under the ptrace runtime — and were identical on stdout,
DETLOG, syscall count and region sizes. The only cell with `mapped_sites>0` is the only divergence.

> **Consequence for a live number:** e9patch's headline parity **173/200 — the highest of any
> backend** — is inflated by cells where e9patch did no rewriting at all and was effectively plain
> ptrace. Its actual rewriting path is barely exercised by the corpus.

---

## 5. "Ensure injected patch bytes aren't hashed" — answered, and it is not the problem

Checked directly: detlog `[memory]` records carry exactly **two** region labels, `[heap]` and
`[stack]`. No text, no anonymous trampoline mapping. **Injected patch bytes are not hashed**, by
construction — the concern does not apply.

The real issue is one level over: the **heap** is relocated because the rewritten artifact loads
elsewhere, so the heap hash differs even though the heap behaves identically. The stack *record
count* also differs (56→65), but only as a downstream effect of §4(a) — one memory record is
emitted per syscall.

---

## 6. The structural finding: the deeper standard is partly unattainable

`--detlog-heap`/`--detlog-stack` hash memory **content**, and that content holds absolute
pointers. So for **any relocating backend**, raw-hash parity against ptrace is not merely unmet —
it is **structurally unattainable**. `hermit log-diff --unsafe-strip-lines` does not rescue it:
it strips numeric literals from log lines, but the hash is content-derived and cannot be
normalised after the fact.

Taken literally, then, "full detlog-stack/heap parity vs ptrace" would file ~100% **false** gaps
against e9patch. **Proposed fix — measure a relocation-invariant projection instead:**

1. region **size sequences** per label (already computed by `sweep.sh`);
2. syscall sequence with pointer arguments **ordinalised**;
3. keep the raw hash for **self-determinism** (run1 vs run2, same backend) — where it is exactly
   the right instrument, and where it passes.

---

## 7. Gaps filed

| task | substance |
| --- | --- |
| `detlog_heap_stack_hash` | address-sensitive hash unusable cross-backend; proposed invariant projection |
| `e9patch_rewrite_injects_9` | +9 loader syscalls on rewrite; e9patch parity 173/200 inflated |
| `liteinst_preload_handshake_fails` | blocks liteinst; pin drift and missing-deps **ruled out** |

---

## 8. Not established

* **No fix was implemented and nothing was committed.** The proposed comparator in §6 is a design,
  not code. `collect-envelope.rs` is unchanged.
* **Corpus is 8 guests, not the 200-cell corpus.** Enough to prove the depth gap exists and nail
  its mechanism; **not** enough to quantify what fraction of the 200 cells it affects. The honest
  headline is "87.5% on 8 hand-picked guests", not a corpus rate.
* **Only ONE backend pair was measured.** Every §4 conclusion is about e9patch, whose runtime *is*
  ptrace. DBI/KVM/SaBRe/LiteInst could diverge for entirely different reasons, and the
  relocation mechanism may not generalise to them at all.
* **`--log INFO` was not compared as a separate dimension.** `log-diff` compares DETLOG+COMMIT
  messages; I did not do a full INFO-line comparison independent of DETLOG, so the "INFO match"
  leg of the task is only covered insofar as INFO carries the DETLOG records.
* **The 5 extra `mmap`s were not individually attributed** to specific e9patch runtime actions; I
  read the counts, not the mapping targets.
* **`heapwork` never exercised rewriting** either (`candidate_sites=0`), so no multithreaded cell
  tested the rewriting path — the divergent cell is single-threaded.
