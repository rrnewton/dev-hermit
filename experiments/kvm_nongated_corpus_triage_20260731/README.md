# KVM non-gated corpus ratchet: re-measure + full root-cause triage (2026-07-31)

Task `kvm-corpus-round-nongated`: find the next **non-gated** corpus batch to
enable toward B3 105/183+, avoiding the owner-gated SIGCHLD/guest-clock/scheduling
families, with **no false parity** and portable fixtures. Work-ahead for the
`/dev/kvm` (claude) lane.

## Question

Since the 2026-07-30 B3 sweep (`experiments/kvm_b3_corpus_sweep_20260730`,
hermit `9cd955f9`) established 105/183 KVM==ptrace exit+stdout parity, has the
frontier moved on current `main`, and is there a non-gated batch an
implementation agent can enable **and land** without touching owner-gated code,
another agent's owned files, or unmerged reverie?

## Method

- Binary: `worktrees/kvm/hermit/target/debug/hermit` @ `be567129` (= PR #1191
  branch: current `main` KVM behavior incl. `cc3730fd` guest-clock + the L3
  detlog memory-region hashing fix). Host: AMD EPYC 9D85, kernel
  6.18.39, `/dev/kvm` 0666.
- Re-ran the exact 183-cell exit+stdout parity sweep (`ignored/kvm_b3_par.sh`):
  compile each verify-mode C guest, `hermit run --strict` (ptrace) vs
  `hermit run --backend kvm --strict`, compare exit + stdout SHA-256.
- Diffed per-cell verdicts and KVM stdout hashes against the 07-30 results to
  detect flips.
- Root-caused every one of the 78 DIFF cells from the captured
  `pt.out`/`kvm.out`/`kvm.err` and bucketed by cause + ownership/blocker
  (`triage78.tsv`).

## Result 1 — the exit+stdout ratchet is EXHAUSTED at 105/183 on current main

- **105 PARITY / 78 DIFF (57.4%), byte-identical to the 07-30 aggregate.**
- **Zero per-cell verdict flips** in either direction 07-30 → now.
- Only KVM-side stdout drift: `print-memaddrs`, `resource-determinism`,
  `clock-determinism` (all already DIFF or owner-gated). The only KVM-relevant
  `main` commits since the sweep base are the **owner-gated guest-clock family**
  (`cc3730fd`/`3ac51e11`, which *regressed* fine-time parity — see #1212) plus
  DBI-only fd hygiene; neither adds a non-gated corpus cell.

Enabling any of the 78 DIFF cells would be **false parity** (the exact
anti-pattern the task forbids), so no manifest increment is proposed.

## Result 2 — full root-cause decomposition of the 78 DIFF cells

| Bucket | Count | Disposition for a compliant impl agent |
| --- | ---: | --- |
| `B_KVM_SYSCALL_GAP` (reverie-kvm) | 22 | reverie-side; **landing-blocked** by the reverie→reverie-core rename pin-bump |
| `H_PTRACE_SIDE_FAIL` | 17 | ptrace itself non-zero — **not a KVM issue**; excluded from the fair frontier |
| `A_DETPID` | 11 | **owned by another agent** (`fix-kvm-detpid-mismatch`, P1 in_progress) |
| `C_SIGNAL_TIMER` | 8 | **owner-gated** (signal/timer delivery) |
| `F_DETCORE_PROCFS` | 7 | hermit-side, but needs deep backend value-alignment; not a clean byte-parity win (see below) |
| `B_KVM_CHILDSYNC` (reverie-kvm) | 7 | reverie-side; landing-blocked (incl. the `#288` waitid + SIGCHLD family) |
| `G_HANG_sched` | 4 | **owner-gated** (scheduling/preemption; `kvm_exit=124` hangs) |
| `E_INTRINSIC_layout` | 1 | `print-memaddrs` — raw addresses; not cleanly determinizable cross-backend |
| `D_GUESTCLOCK` | 1 | **owner-gated** (`clock-determinism`, #1212) |

Fair frontier (excluding the 17 ptrace-side failures) = **61 cells**. Of those:
**29 reverie-blocked**, **11 owned by another agent**, **13 owner-gated**,
**7 detcore-procfs (not clean parity)**, **1 intrinsic**.

### Representative root causes (evidence)

- **A_DETPID** (`pid-probe`, `record-getpid`, `wait-on-child`, `vforkexec`,
  `debuggee`, `pid-tid`, `socket-cookie-{tcp,udp,unix}`, `dbi-pid-virtualization`,
  `random-sources`): ptrace prints `pid=3`/child 5/parent 3; KVM prints
  `pid=1`/child 2/parent 1. Socket cookies differ only in the high dword
  (`0x3_00000005` vs `0x1_00000005`) — the same DetPid delta. Exactly the
  `DetPid(1) vs DetPid(3)` defect owned by `fix-kvm-detpid-mismatch`.
- **F_DETCORE_PROCFS** (`sysinfo`, `sysinfo-uptime`, `proc-fdinfo`,
  `proc-fd-link-aliases`, `pty-nr-count`, `madvise-determinism`,
  `thread-self-procfs-handoff`): e.g. `sysinfo` free-RAM is `0` under KVM vs
  `997,394,944` under ptrace. Root cause: `detcore/src/syscalls/sysinfo.rs`
  `free_ram()` reads `/proc/<guest.pid()>/statm` field 1 — valid for ptrace
  (guest == host process) but for KVM `guest.pid()` is the VMM process, whose
  virtual size exceeds `total_ram`, so it returns the `used > total → 0` branch.
  A portable fix cannot byte-match ptrace's frozen golden (which encodes
  ptrace's exact 636-page mapping count); making both backends use a
  detcore-model vsize would change ptrace's frozen output and is a
  determinization-strategy change (owner review). **Not a clean impl-agent win.**
- **B_KVM_SYSCALL_GAP** (`clone` "Operation not supported"; `ppoll-simulation`,
  `pselect6-simulation`, `ppoll-readv` "Function not implemented"; `recvmsg`/
  `sendmsg`/`sendfile`/`memfd_create`/`F_GETPIPE_SZ` "not implemented";
  `so-incoming-cpu-*`/`tcp-info-*`/`unix-autobind-*` networking "not
  supported"): reverie-kvm `SyscallExecutor` gaps. reverie-side, and consuming
  any reverie fix into hermit is blocked until the reverie pin moves across the
  rename.

## Interpretation — no compliant non-gated batch exists right now

For an implementation agent bound by "avoid owner-gated families, do not edit
another agent's files, no false parity, land it," the addressable set on
current `main` is **empty**:

1. 29 cells need reverie-kvm changes that **cannot land** until the coordinator
   bumps the hermit reverie pin across the reverie→reverie-core rename (#304).
   hermit `main` still pins `adc1473` (pre-rename).
2. 11 cells are the DetPid family, **owned** by `fix-kvm-detpid-mismatch`.
3. 13 cells are **owner-gated** (signal/timer, scheduling/hang, guest-clock).
4. 7 detcore-procfs cells cannot reach byte-parity without changing the frozen
   ptrace golden (owner-review determinization change).
5. 17 are ptrace-side failures (not KVM) and 1 is intrinsic address layout.

The real path past 105 is therefore **coordinator/owner-gated**, not
impl-agent-gated. The single highest-leverage unlock is landing the
`fix-kvm-detpid-mismatch` fix (11 cells) and the reverie rename pin-bump (29
cells). #1188 (the already-measured 7→100 verify-enable batch) is **revalidated**
by this run — its 93 parity-clean cells are still in the 105 PARITY set with
zero flips — and remains the ready-to-land increment.

## Files

- `triage78.tsv` — every DIFF cell: `id`, `kvm_exit`, `bucket`, signature.
- `kvm_parity_results_20260731.tsv` — full 183-cell re-measurement.
- `metadata.json` — SHAs, host, tallies.

## Reproduce

```bash
cd worktrees/kvm/hermit && cargo build --bin hermit          # be567129
bash ignored/kvm_b3_par.sh                                    # -> 105/183, ignored/kvm-b3-results.tsv
# per-cell evidence is in ignored/kvm-b3-cells/<slug>/{pt.out,kvm.out,kvm.err}
```
