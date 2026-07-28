# SaBRe backend: RDTSCP not intercepted (RDTSC is)

**Date:** 2026-07-28 · **Task:** `compat-sabre-rdtsc-debug` (research/debug)
**Backend under test:** sabre · **Comparison:** ptrace
**Hermit:** `worktrees/275/hermit` `target/release/hermit`; reverie pin `9233c0d0`.
**Host:** devbig030, kernel 6.17.13.

## Question

Under `--backend sabre`, does Hermit virtualize `RDTSC` and `RDTSCP`
deterministically (as ptrace does)?

## Result

**RDTSC is virtualized under sabre; RDTSCP is not.** `rustbin_rdtsc`
(`tests/rust/rdtsc.rs`, 16× rdtsc+rdtscp) is **L2 under ptrace** but
**nondeterministic under sabre**: the `rdtsc:` lines are identical across the two
`--verify` runs (virtualized, magnitude `0x18867251ee...`), while the `rdtscp:`
lines diverge every run (raw host TSC, magnitude `0x27ac...`/`0x27b2...`).

Minimal C reproducers bound the gap exactly:

| Program | Instruction | sabre `--strict --verify` |
|---|---|---|
| `tsc_only.c`  | `__rdtsc()` only  | **L2 PASS** |
| `tscp_only.c` | `__rdtscp()` only | **FAIL — nondeterministic** |

## Root cause

SaBRe never traps the `RDTSCP` instruction (`0F 01 F9`, 3 bytes):

- **SaBRe x86_64 rewriter** `reverie/third-party/sabre/arch/x86_64/rewriter.c`
  detects only `0x0F05` (SYSCALL) and `0x0F31` (RDTSC) — lines 117-119 and the
  scan at 883 (`*ptr=='\x0F' && ptr[1]=='\x31'`). No `0F 01 F9` case.
- **SIGILL fallback** `reverie/third-party/sabre/loader/loader.c:102` recognizes
  only `0x0B0F` (RDTSC). (`ld_sc_handler.c:428` even carries
  `TODO(andronat): Do router for rdtsc`.)
- **reverie-sabre** exposes only `Tool::rdtsc(&self) -> u64`
  (`experimental/reverie-sabre/src/tool.rs:123`, default native `_rdtsc()`) and
  FFI `handle_rdtsc_fn` (`ffi/mod.rs:110`). There is **no** rdtscp hook, handler
  slot, or FFI type anywhere in reverie-sabre.

So `RDTSCP` is left un-rewritten and executes natively, leaking the raw host TSC
(and `IA32_TSC_AUX` in ECX) → nondeterministic. The **ptrace** backend handles
both via CR4.TSD faulting → `reverie-ptrace/src/task.rs` `Rdtsc::Tsc`/`Rdtsc::Tscp`
→ `handle_rdtscs` (which also sets ECX/aux for `Tscp`).

`hermit-cli/src/instruction_map.rs` *does* scan for `rdtscp` sites, but that is
the **e9patch** preprocessing path (ptrace runtime), not the sabre backend.

## Fix scope (approval-gated; not landed)

The fix lives entirely in the pinned reverie/SaBRe dependency (`rrnewton/reverie`):

1. `third-party/sabre/arch/x86_64/rewriter.c`: detect the 3-byte `RDTSCP`
   (`0F 01 F9`) and add an `rdtscp_entrypoint`; the handler must set **ECX =
   IA32_TSC_AUX** in addition to EDX:EAX. Update the SIGILL fallback in
   `loader/loader.c` too.
2. `experimental/reverie-sabre`: add `ffi::handle_rdtscp_fn`, wire an
   `rdtscp_handler` (`internal.rs`, `loader/premain.c`), add `Tool::rdtscp()`
   returning `(tsc, aux)`.
3. `detcore-sabre/src/lib.rs` (in Hermit): implement the `rdtscp()` hook →
   coordinator virtual TSC + deterministic aux (detcore already virtualizes
   `getcpu`, so aux = 0).

This is an interception-semantics change to a core Reverie/SaBRe contract, so per
the parent **Reverie API Policy** it needs user approval + a reverie feature
branch + a parent pin bump. Same disposition bucket as the sabre pipe-EAGAIN gap
(hermit#1035) and the RT-signal gap (reverie#207). No Hermit-only change can
virtualize `RDTSCP` under sabre.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/275/hermit && cargo build --release --bin hermit --bin rustbin_rdtsc
H=./target/release/hermit
$H run --backend ptrace --strict --verify -- target/release/rustbin_rdtsc   # L2 PASS
$H run --backend sabre  --strict --verify -- target/release/rustbin_rdtsc   # FAIL (rdtscp diverges)
gcc -O1 tsc_only.c  -o /var/tmp/tsc_only  && $H run --backend sabre --strict --verify -- /var/tmp/tsc_only    # PASS
gcc -O1 tscp_only.c -o /var/tmp/tscp_only && $H run --backend sabre --strict --verify -- /var/tmp/tscp_only   # FAIL
```
