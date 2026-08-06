# hermit's vDSO strategy: original intent, and whether it survives across backends

**Task:** `vdso-strategy-original-intent-and-cross-backend-viability` · **Agent:** hermit-rand
(`[impl agent, claude-opus-5]`) · **2026-08-06** · research only, no code changed.

## Answers up front

1. **Original intent: CONFIRMED.** The strategy was *disable the vDSO and force guests through
   the real syscall*, and it is still live today.
2. **Repair-and-route: NOT VIABLE as a uniform strategy.** Two backends cannot do it at all.
3. **Cross-backend: the constraint is ALREADY VIOLATED.** Three backends implement three
   *different* vDSO strategies with three *different* coverage sets. That is the parity break;
   the `__vdso_getrandom` divergence is a symptom of it, not the disease.

---

## (1) Original intent — file and commit evidence

`reverie-ptrace/src/vdso.rs`, module doc, first line:

> `//! Provides APIs to disable VDSOs at runtime.`

Added in **`15d2f61`, 2021-12-29, "Initial commit"** — about **four and a half years before**
the SaBRe revival (`90970ac`, 2026-07-24). Nothing since has changed its strategy; the only
revival-era commits touching it are `d7424fd` (ptrace error diagnostics), `afbdd68` (CI
warnings) and `9e7af7d` (LiteInst stats) — all incidental.

The mechanism is literal: each vDSO entry is overwritten with a stub that performs the real
syscall.

```
const clock_gettime_code = [ 0xb8, 0xe4, 0x00, 0x00, 0x00,   // mov $228, %eax
                             0x0f, 0x05,                     // syscall
                             0xc3 ]                          // ret
```

**Measured, guest-visible, under `hermit run --strict` (ptrace):**

| symbol | native first 8 bytes | under hermit | |
| --- | --- | --- | --- |
| `__vdso_gettimeofday` | `55 48 89 e5 …` | `b8 60 00 00 00 0f 05 c3` | neutered → sysno 96 |
| `__vdso_time` | `55 48 89 e5 …` | `b8 c9 00 00 00 0f 05 c3` | neutered → 201 |
| `__vdso_clock_getres` | `55 48 89 e5 …` | `b8 e5 00 00 00 0f 05 c3` | neutered → 229 |
| `__vdso_clock_gettime` | `55 48 89 e5 …` | `b8 e4 00 00 00 0f 05 c3` | neutered → 228 |
| `__vdso_getcpu` | `55 48 89 e5 …` | `b8 35 01 00 00 0f 05 c3` | neutered → 309 |
| **`__vdso_getrandom`** | `55 48 89 e5 …` | **`55 48 89 e5 …`** | **UNPATCHED** |
| **`__vdso_sgx_enter_enclave`** | `55 48 89 e5 …` | **`55 48 89 e5 …`** | **UNPATCHED** |

So the owner's recollection is exactly right, the code is not dead, and the defect is narrower
and more mechanical than "the strategy was abandoned":

**The patch set is a static allowlist frozen in 2021.** `vdso.rs` maps five x86-64 names
(`__vdso_time`, `__vdso_clock_gettime`, `__vdso_getcpu`, `__vdso_gettimeofday`,
`__vdso_clock_getres`). `__vdso_getrandom` arrived in Linux 6.11 (2024) and was never added.
The gate that decides whether to patch at all, `is_patch_required`, keys on the *same* six
syscalls — so a tool subscribing only to `getrandom` would get no vDSO patching whatsoever.

**The allowlist is fail-open.** An unknown `__vdso_*` symbol is silently left executable rather
than refused. Same failure direction as the `flock` no-op found earlier today: a determinism
tool defaulting to "let it through".

---

## (2) and (3) Per-backend feasibility

Two candidate strategies:

* **A — DISABLE-AND-ROUTE** (the original): make the vDSO entry unusable so the guest falls
  through to a real syscall the tool already intercepts.
* **B — REPAIR-AND-ROUTE**: leave the vDSO callable but make it return a determinized value,
  computed in user space without a syscall.

| backend | does A today? | **can** it do A? | **can** it do B? | evidence |
| --- | --- | --- | --- | --- |
| **ptrace** | **yes**, 5 symbols | yes | **NO** | measured above. A pure-userspace vDSO call traps nothing, so ptrace cannot observe it; the only way it could "repair" is to rewrite the vDSO to jump to injected code — which is A with extra steps |
| **dbi (DynamoRIO)** | **yes**, byte-identical to ptrace | yes | yes, in principle — DR instruments any code including the vDSO | measured: `clock_gettime` `b8 e4 …`, `getrandom` unpatched |
| **e9patch** | **yes**, byte-identical to ptrace | yes (it *is* the ptrace runtime) | **NO** — e9patch rewrites the main ELF ahead of time; the vDSO is kernel-supplied at run time and is not in the file | measured; identical bytes to ptrace |
| **kvm** | **yes, and more completely** | yes | n/a — it authors the image | code: `write_vdso` synthesizes a vDSO with **no dynamic section**, so "glibc resolves no vDSO symbols and every real syscall still flows through the executor" |
| **sabre** | **no — does B instead** | probably (it loads the guest in-process and could mprotect+patch), unverified | **yes, today** | `experimental/reverie-sabre/src/vdso.rs` + `handle_vdso` in `callbacks.rs`: replaces the vDSO **function pointer** for 4 symbols |
| **liteinst** | not determined | likely inherits the ptrace host hybrid | plausible (it is a patching backend) | no `vdso` reference anywhere in `reverie-liteinst/src`; runtime DSO not stageable here |

### The finding that answers the binding constraint

**There is no single strategy in the tree. There are three.**

1. **ptrace / dbi / e9patch** — byte-overwrite a 5-symbol allowlist from 2021. Fail-open.
2. **sabre** — function-pointer interposition of **4** symbols (`clock_gettime`, `getcpu`,
   `gettimeofday`, `time`; **no `clock_getres`**) inside its in-guest runtime, restored with the
   revival (`90970ac`, 2026-07-24). Also fail-open: `handle_vdso` ends `_ => None`.
3. **kvm** — synthesize a vDSO with no resolvable symbols (`0f17ef2`, 2026-07-29).

Different mechanisms, different symbol sets, different fail behaviour. Under the owner's rule —
*all backends must do the same thing or the parity guarantee is void* — **the guarantee is
already void today**, independently of `getrandom`.

**Repair-and-route is not viable as the uniform strategy**, because ptrace and e9patch cannot
implement it at all. Adopting it would mean the reference backend could not do what the
strategy requires — the definition of a parity break rather than a strategy.

---

## Recommendation

**Standardize on disable-and-route, in KVM's form, and make completeness structural rather than
enumerated.**

KVM already demonstrates the strongest version: if the vDSO exposes **no resolvable symbols**,
every vDSO-able call becomes a syscall, and the result is correct for symbols that do not exist
yet. It needs no list, so it cannot go stale — which is precisely how the 2021 list failed.

Ranked, with tradeoffs:

1. **Empty/undynamic vDSO for every backend** (KVM's model). *Pro:* complete by construction;
   immune to new kernel symbols; one mechanism everywhere. *Con:* loses the vDSO's performance
   benefit for clock reads on every backend — under a determinizing tool those calls are already
   being intercepted, so the loss is smaller than it looks, but it is real and should be
   measured before committing. *Risk:* a guest that hard-requires a vDSO symbol's presence
   (rather than falling back) would break; glibc falls back correctly, but this needs a corpus
   check.
2. **Keep byte-patching, but make the allowlist fail-closed.** Enumerate the vDSO's *actual*
   exported `__vdso_*` symbols at startup and refuse (or loudly warn) on any not in the known
   set, instead of silently leaving it executable. *Pro:* small, surgical, preserves current
   behaviour for known symbols. *Con:* still a list; every new kernel symbol is a new
   fail-closed abort until someone adds a stub — noisy, but honest, and strictly better than
   today's silent pass.
3. **Do nothing / patch only `getrandom`.** Rejected: it fixes the instance and leaves the
   class. `__vdso_sgx_enter_enclave` is already unpatched today, and the next kernel adds the
   one after that.

Whichever is chosen, **SaBRe must converge on it.** Its independent 4-symbol interposition is
both a coverage gap (`clock_getres`) and a second implementation of a contract that is supposed
to be uniform.

---

## What I could NOT determine

Stated so this does not read as a complete survey:

* **SaBRe's guest-visible vDSO bytes were not measured.** Every attempt crashed before the guest
  ran: `failed to connect Detcore SaBRe plugin to coordinator: Decode(InvalidBooleanValue(20))`.
  SaBRe's row above is from **source only**. I therefore cannot confirm whether SaBRe *also*
  leaves the vDSO image unpatched in practice, only that its source contains no image-patching
  and does interposition instead.
* **Whether `handle_vdso` is actually invoked** by the SaBRe loader at run time. The code path
  exists; I did not observe it fire.
* **LiteInst was not measured at all** — `libreverie_liteinst.so` is not staged beside the
  hermit binary in this slot, so the backend refuses to start. Its row is inference from the
  absence of vDSO code in `reverie-liteinst/src`, not a measurement.
* **KVM was not measured at run time** — it livelocks at guest startup on this host, so its row
  is code evidence (`write_vdso`) only.
* **Why DBI's vDSO is patched identically to ptrace.** I measured the *effect* (byte-identical)
  but did not establish the *mechanism* — whether reverie-dbi reuses the ptrace pre-init path or
  duplicates it. That matters for the recommendation, because if DBI inherits ptrace's code it
  also inherits the stale list for free, and if it duplicates it, it is a fourth implementation.
* **The performance cost** of the empty-vDSO recommendation is unquantified. I did not benchmark
  clock-read-heavy guests with and without a usable vDSO.
* I did not search Meta-internal history; all commit evidence is from the public
  `rrnewton/reverie` git history present in this checkout.

## Reproduction

```bash
# vDSO bytes as the guest sees them, native vs each backend
gcc -O1 -o vdsobytes vdsobytes.c      # source in this directory
./vdsobytes                                            # native
hermit run --strict -- ./vdsobytes                     # ptrace
hermit --backend dbi     run --strict -- ./vdsobytes   # dbi
hermit --backend e9patch run --strict -- ./vdsobytes   # e9patch (needs HERMIT_E9TOOL)
```

`vdsobytes` walks `AT_SYSINFO_EHDR`, enumerates the vDSO's own dynamic symbol table, and prints
each `__vdso_*` entry's first eight bytes, flagging the `b8 <sysno> 0f 05 c3` stub shape.
