---
name: core-memory-record-requires-sequentialization
description: "hermit record/replay is fundamentally coupled to sequentialize_threads=true; QEMU can't be recorded because it can't boot sequentialized (CORE-MEMORY mirror of memory/record-requires-sequentialization.md)"
---

# CORE-MEMORY: record-requires-sequentialization

<!-- GENERATED MIRROR of core memory `record-requires-sequentialization`. Source of truth is the memory
     file `record-requires-sequentialization.md`. Regenerate: scripts/sync-memory-skill.rs. Verify in
     sync: scripts/lint-memory-skill-sync.rs. Do NOT hand-edit inside the
     markers — edit the memory and re-run sync. -->

<!-- BEGIN CORE-MEMORY-MIRROR (source: record-requires-sequentialization.md) -->
hermit record/replay is FUNDAMENTALLY coupled to `sequentialize_threads=true`, not a conservative default (investigated 2026-07-23, task impl-qemu-recording-exploration).

- `hermit-cli/src/metadata.rs:145-156` `record_or_replay_config()` hardcodes `sequentialize_threads:true`; comment: record & replay must use the EXACT SAME config or replay diverges.
- DEFINITIONAL: `detcore-model/src/config.rs:415` `should_trace_schedevent() = sequentialize_threads && !debug_externalize_sockets`. Schedule tracing is *defined* to require sequentialization. Gated at `detcore/src/lib.rs:800,1056,1309`; asserted at `detcore/src/tool_global.rs:842,1469,1590` (`trace_schedevent` starts with `assert!(guest.config().sequentialize_threads)`).
- Model: record = sequentialize + RCB preemption (default `preemption_timeout=200000000` vns). Each `SchedEvent` carries `end_rip` + RCB logical time; that precise total-ordered schedule only EXISTS because the scheduler steps threads one at a time.

Recording a schedule WITHOUT sequentialization is NOT a shortcut:
- REPLAY infeasible for racy programs (QEMU): sub-syscall memory interleaving (data races, lock-free atomics, untrapped futex fast paths) is invisible to hermit; RCB counts unstable across CPU migration. Would desync immediately (`die_on_desync`). Only "works" for race-free programs that already record fine.
- Empirical: `run --no-sequentialize-threads --record-preemptions-to X` → PANIC/SIGSEGV at tool_global.rs:1590. `hermit record start -- qemu…` (forces seq, no --no-seq flag) → HANGS, 0 serial output (QEMU can't boot sequentialized, see [[qemu-linux-boots-under-hermit-config]] which needs --no-sequentialize-threads).

BOTTOM LINE: QEMU can't be recorded today because record REQUIRES sequentialization AND QEMU can't boot under it. The unlock is the SCHEDULER FIX (make hermit sequentialize QEMU without the TCG vCPU starving support threads); once QEMU boots under --sequentialize-threads, record/replay works unchanged. Recording is downstream of, not an alternative to, the scheduler fix. Related: [[strict-mode-frontier-regresses-real-workloads]].
<!-- END CORE-MEMORY-MIRROR -->
