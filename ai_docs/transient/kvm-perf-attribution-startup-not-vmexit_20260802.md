# KVM backend perf attribution: it's startup/teardown, not VM-exit overhead

**Date:** 2026-08-02 · **Author:** hermit-kvm (opus-4.8) · **Backend:** KVM vs ptrace golden

## TL;DR

The headline "KVM ~13x ptrace" is **fixed startup/teardown cost, not per-syscall
VM-exit overhead**. Steady-state, KVM is only ~1.3x ptrace. The dominant term is
host-kernel VM destruction (`close(KVM VM fd)` for the 1 GiB memslot), already
root-caused and instrumented by the closed task `fix-kvm-startup-latency` (PR
#1127). There is no "dumb" per-exit hot-path waste to remove; the only remaining
lever is guest-memory lifecycle, which is owner-gated (QEMU needs the 1 GiB).

## Measurements (release build, `/dev/kvm` host, `--strict`, no logging)

| workload | syscalls | ptrace | kvm | ratio |
| --- | --- | --- | --- | --- |
| `/bin/true` | ~minimal | 0.01 s | 0.92 s | ~90x |
| `/bin/echo hello` | ~minimal | 0.02 s | 0.64 s | ~32x |
| `/bin/ls -laR /usr/include` | thousands | 0.68 s | 0.91 s | **~1.3x** |

The ratio collapses toward 1.3x as syscall count grows — the extra KVM cost is a
**fixed per-invocation constant (~0.6 s)**, not proportional to guest work. If
VM-exit dispatch were the bottleneck, the syscall-heavy workload would show the
*largest* ratio; it shows the smallest.

## Where the fixed cost goes (from `fix-kvm-startup-latency`, PR #1127)

INFO lifecycle phase timing for KVM `/bin/echo` (that task's instrumentation):

```
prepare=466us  setup=1643us  execution=19470us  cleanup=68us  teardown=250563us
```

- **teardown = 250 ms** (up to 577 ms under host load) dominates.
- strace corroborates: total `KVM_RUN` = 2.5 ms; representative `close(VM fd)` =
  143 ms. VM destruction of the 1 GiB memslot is a **host-kernel** cost.
- 1 GiB is required for nested QEMU (`-m 256M`); a global 512 MiB fails ENOMEM,
  and adaptive sizing was withdrawn as measurement-order bias (see task notes).

## Steady-state VM-exit path is healthy

On `ls -laR` (thousands of guest syscalls) the marginal KVM cost over ptrace is
~230 ms across thousands of exits — i.e. tens of microseconds per syscall
round-trip, comparable to ptrace's own `PTRACE_*`/context-switch cost. No
evidence of per-exit allocation, redundant full register syncs dominating, or
broad single-stepping on the syscall path. (A `kvm_backend_stats_provider`
reporting VM-exit-by-reason counts will let us confirm exit composition
quantitatively — see below.)

## Recommendations

1. **Do not chase VM-exit micro-optimization** for the ~13x number — it is not
   there. The premise ("VM-exit overhead?") is answered: no.
2. **Teardown is the only real lever** and it is host-kernel + memory-contract
   bound. A *potential* future optimization (not attempted here, owner-gated):
   back the 1 GiB guest memslot with memory that is never prefaulted and
   `MADV_DONTNEED`/`MADV_FREE` before `close`, so VM-fd teardown only frees
   touched pages. This touches the reverie-kvm memory model (Backend contract) —
   discuss with owner before implementing; it does not belong in routine
   backend-parity work.
3. **Amortize startup for batch/corpus runs**: the fixed ~0.6 s per invocation is
   why KVM corpus sweeps are slow. If a persistent/pooled VM were ever in scope
   it would erase the constant, but that is a large architectural change.
4. **Ship `kvm_backend_stats_provider`** for VM-exit-by-reason observability so
   this attribution is reproducible from counters rather than strace.

## Cross-references

- Closed task `fix-kvm-startup-latency` / PR #1127 (teardown root cause + INFO timing).
- `[[kvm-detpid-fixed-residual-is-address-space]]` (separate KVM parity work).
