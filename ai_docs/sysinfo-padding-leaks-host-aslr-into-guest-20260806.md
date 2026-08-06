# `sysinfo(2)` writes uninitialized detcore padding — including host ASLR bytes — into guest memory

**Task:** `fork-exec-process-tree-determinism` (P1) · **Date:** 2026-08-06 · **Author:** hermit-det1
**Bound to:** hermit `f89c69766371806d3c9b2c3003531df2d59d6118` (clean worktree build, no `-dirty`),
reverie cargo pin `9470712afa9b421c72850ab7955fb335692e43a0`. **Host:** devbig014. **Local, no egress.**
**Full experiment (harness, guests, all CSVs, reproduction):**
`experiments/fork-exec-process-tree-determinism_20260806/`. **Gap filed:** `sysinfo_2_writes_uninitialized`.

This note carries the two findings that are *not* about fork/exec and therefore outlive the sweep that
found them. The process-tree result itself lives in the experiment README.

---

## 1. How it was found, and why that matters for the compat metric

A 14-guest fork/exec + process-tree determinism sweep (ptrace + e9patch, `--strict --detlog-stack
--detlog-heap`, two independent runs per cell) came back clean on process-event ordering in 28/28
cells. Exactly one cell diverged, and it diverged in a way the shipped scorecard cannot see:

| `sh_pipeline` = `echo \| tr \| tr \| sort \| uniq \| wc` | |
| --- | --- |
| stdout run1 vs run2 | **byte-identical** |
| process-event trace run1 vs run2 | **identical** |
| full DETLOG via `hermit log-diff` | **differs** |
| `hermit run --strict --verify` (plain Stripped comparator) | **`verdict: diverged`** |

Because `run_and_hash` in the compat path hashes `out.stdout` only, `collect-envelope.rs` would score
this cell **`parity=1`**. That is a **worked counterexample** to the stdout-only metric — a second one,
independent of the `nondet` cell in `cross-backend-detlog-parity-sweep-20260806.md` §3, and this one
has a root cause that is a genuine product defect rather than a relocation artifact.

## 2. Root cause

First divergence: a `[stack]` content hash for dtid 11 **at identical addresses**, immediately after
`finish syscall #148: sysinfo(…) = Ok(0)`. GNU `sort` calls `sysinfo(2)` to size its merge buffer.

`reverie-syscalls/src/args/sysinfo.rs:48-51` at the pinned rev:

```rust
impl From<SysInfo> for libc::sysinfo {
    fn from(sys_info: SysInfo) -> libc::sysinfo { unsafe { std::mem::transmute(sys_info) } }
}
```

`#[repr(C)] SysInfo` is `u64 × 10`, `u16 procs`, `u64 total_high`, `u64 free_high`, `u32 mem_unit`
(size 112, align 8). Bytes **82..88** — the alignment hole after `procs` — and **108..112** — the tail
padding — are never written by the struct literal in `collect_sysinfo`
(`detcore/src/syscalls/sysinfo.rs`). `transmute` copies all 112 bytes, so
`handle_sysinfo`'s `guest.memory().write_value(info_addr, …)` copies **detcore's own uninitialized
stack padding** across the container boundary into the guest.

Every guest-visible *field* is properly determinized (`uptime` from logical time, `loads_*` pinned to
1, `free_ram` deliberately derived from `statm.size` rather than RSS, with a comment explaining that
exact hazard). The bug is entirely in the bytes nobody assigned.

## 3. Evidence

**Two-sided bracket — same binary, same guest, one variant apart**
(`results-sysinfo-bracket-raw.csv`):

| variant | detlog self-determinism | `--verify` |
| --- | --- | --- |
| `sysinfo(NULL)` → EFAULT, nothing written | **matched** | matched |
| `uname(2)` into a stack buffer (control) | **matched** | matched |
| `sysinfo` → 256 B **stack** buffer | **diverged**, differing offsets exactly `{82,83,84,108}` | diverged |
| `sysinfo` → 256 B **heap** buffer | **diverged**, differing offsets exactly `{83,84,108}` | diverged |
| single-process guest, only `sysinfo` | **diverged** | diverged |
| same guest, call skipped | **matched** | matched |

Bytes 112..256 stay at the `0xAA` guard in both buffer variants, so there is **no over-write past
`sizeof`**. A **single-process** guest with no `fork`, `exec`, or `wait` reproduces it, so this is not
a process-tree defect.

**Quantified — 10/10 distinct** (`results-sysinfo-padding-10samples.txt`). Ten identical runs under
`--strict --detlog-stack --detlog-heap --log info` with a pinned `env -i`:

```
82..88 = 3e15567f0000  7ec27d7f0000  9e6daa7f0000  3ee8c07f0000  9e48747f0000
         5ea23f7f0000  5ebf457f0000  dec7c17f0000  feded77f0000  de30707f0000
108..112 =   567f0000      7d7f0000      aa7f0000      c07f0000      747f0000
             3f7f0000      457f0000      c17f0000      d77f0000      707f0000
```

The trailing `7f0000` is bytes 4..7 of a host userspace pointer `0x00007fXXXXXXXXXX`. **These are host
ASLR address bytes, and the guest can read them.**

**Fidelity, not only determinism.** Native (no hermit) padding is all zeros, 3/3 — Linux
`do_sysinfo()` memsets the whole struct, and glibc's `pad` field at 82..84 is a real reserved field
the kernel zeroes. Under plain `hermit run --strict` (no detlog flags) the padding is
`ffffffffffff`, 5/5 — also never the zeros Linux guarantees. So hermit deviates from Linux here in
*every* configuration; the detlog flags only make the deviation *visible*.

## 4. Impact and fix

* Breaks **L3** (`--detlog-heap`/`--detlog-stack`) and hermit's own **plain `--verify`** for any guest
  calling `sysinfo(2)` — which includes anything running GNU `sort`, and glibc
  `sysconf(_SC_AVPHYS_PAGES)`.
* Invisible to the stdout-only compat scorecard (§1).
* Leaks host ASLR address bytes into the guest — a container-isolation defect independent of
  determinism.

**Fix:** zero-initialize the `libc::sysinfo` and assign fields explicitly (or `memset` before the
transmute) in `rrnewton/reverie`. This is a determinization fix, not a `Tool`/`Guest`/`Backend`/
interception-model change, so it does not by itself trigger `post-facto-human-review`. Add a
regression guest that fills a `sysinfo` buffer with a guard pattern and asserts the padding is zero,
run under `--strict --detlog-stack`.

> **Do not "fix" this by excluding `sysinfo` from the detlog or coarsening the memory hash (#140).**
> The guest-visible bytes really do differ; the hash is reporting a true fact.

**Audit follow-up not done here:** the same write-direction `transmute`-a-partially-initialized-
`#[repr(C)]`-struct pattern should be swept across `reverie-syscalls/src/args/`. The `stat.rs`
transmutes are the *read* direction (a kernel-filled buffer into a Rust struct) and are not obviously
affected; `handle_getrusage` uses `mem::zeroed()` and `handle_times` builds a `libc::tms` whose four
`i64` fields leave no padding. Only `sysinfo` was checked closely.

---

## 5. Second finding: `--verify-strict` is inert on this host, for a different reason than recorded

`--verify-strict` returns `bitwise_parity: false` for `/bin/true` and `/bin/echo` as well as for every
fork guest, so it cannot discriminate anything here and every `--verify` leg of the sweep used the
plain Stripped comparator instead.

This **refines** the standing note (`l2-unattainable-and-kvm-strict-hangs-on-this-box`), which says the
divergent lines "render identically on both sides ⇒ comparator gap". At `f89c69766` that is not what
the lines show. They are **genuinely different host state** — two leaks inside the compared envelope:

1. `DEBUG tracee.attach{pid=3}: reverie_ptrace::timer: Setting precise_ip … CpuId { … initial_local_apic_id: 227 vs 239 … }`
   — the host core the tracee happened to attach on;
2. `DEBUG detcore::tool_global: Nondeterministic realtime elapsed: 38.904157ms vs 37.06689ms`
   (`detcore/src/tool_global.rs:541`) — a host wall clock.

Both are **DEBUG** lines, although `AGENTS.md` defines `--verify-strict`/BitwiseInfoV1 over **INFO**
events. `hermit-cli/src/bin/hermit/verify.rs:517-527,557` documents and implements
"With no explicit level, the verification paths select `DEBUG` internally"
(`global.log = Some(LevelFilter::DEBUG)`), and passing an explicit `--log info` does **not** remove
them — the `reverie_ptrace` DEBUG line still appears and the apic-id mismatch reproduces at compared
message 5.

Bracketed on `/bin/true`, so this is an instrument defect, not any guest's result. It is plausibly
host-shaped: `initial_local_apic_id` is stable only if every run lands on the same core, so a
single-core or pinned CI runner may not see leak (1) — but leak (2) is a wall clock and cannot be
stable anywhere. Both are prior to, and independent of, the `sysinfo` bug above.
