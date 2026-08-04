# P0 Parity-Scorecard Vacuity Audit — KVM slice

Date: 2026-08-04T13:52Z. Author: audit subagent (measurement/enumeration only; no
product source changed, no B-levels re-derived).

ONE QUESTION PER CELL: **does the test FAIL if the KVM backend does NOTHING?**

## Operative definition of "KVM does nothing" (ESTABLISHED)

For the KVM backend, an inert/absent syscall implementation is NOT native
passthrough — it is the reverie-kvm executor's **default fall-through
`negative_errno(ENOSYS)`**. This is established by four independent ratchet
memories that each name the pre-fix behavior as ENOSYS for an unhandled syscall:
`kvm-ptrace-eperm-parity-pr341` ("fell through to default `negative_errno(ENOSYS)`"),
`kvm-seccomp-eopnotsupp-pr344` ("hit the default `ENOSYS`"),
`kvm-close-range-parity-pr340` ("had NO handler -> guest got `ENOSYS`"),
`kvm-so-incoming-cpu-pr345` (unhandled option -> `ENOPROTOOPT`).

So the vacuity discriminator for a KVM parity cell is sharp:
- a cell asserting **ENOSYS** passes against an inert KVM (default == ENOSYS) -> VACUOUS
- a cell asserting **any other errno / real output** makes the self-checking probe
  exit non-zero under inert KVM (ENOSYS != asserted) -> the cell FAILS -> GENUINE

## How KVM parity is actually measured (ESTABLISHED)

Source: `experiments/kvm_fullcorpus_scorecard_20260801/sweep.sh:43-72`.
`parity=1` iff **ptrace exit 0 AND kvm exit 0 AND sha256(kvm stdout) ==
sha256(ptrace stdout)**. Notes that shape vacuity:
- **stdout only** — stderr is diverted to `*.err` and never hashed (sweep.sh:43-48).
- exit STATUS is not compared beyond "both are 0".
- comparison base is ptrace-under-Detcore, NOT native. Syscall *semantics* are
  Detcore's (shared by both backends); the cell therefore tests KVM's
  transport/marshaling + any executor-specific arm, not syscall semantics.

The fixtures are **self-checking probes**: on the expected errno they `puts()` a
fixed string to stdout and `return 0`; on a wrong errno they `fprintf(stderr,...)`
and `return 1` (read directly: `hermit/tests/c/{add_key_enosys,bpf_enosys,
acct_refusal_probe,copy_file_range_refusal_probe,process_vm_readv_refusal_probe,
ptrace_attach_eperm}.c`). So a wrong errno -> non-zero exit -> parity fails. This
is exactly why the ENOSYS-vs-other discriminator is decisive.

Data source: `compat-envelope/scorecard.csv` (KVM DATA column, run
`kvm-fullcorpus-scorecard`, hermit 82a8e853, reverie a4f33d69, devbig014 real
/dev/kvm). 200 KVM rows; **112 parity-win (parity=1)**.

---

## CONFIRMED INSTANCE: #338 exit-stats collector is PER-INSTANCE — VERDICT: CONFIRMED

Verified against the live PR #338 diff (branch `codex/kvm-backend-stats-provider`
@ `b4bea502bdb8006f98608686f33858b958d85adb`, `gh pr diff 338 --repo
rrnewton/reverie`). File:line citations are into the post-patch reverie-kvm tree
that PR introduces.

- **Per-instance collector field:** `reverie-kvm/src/vm.rs` — `pub(crate)
  exit_collector: KvmExitCollector` added to `struct KvmBackend` (new fields
  block, `stats_request` + `exit_collector`). Doc string: "Per-backend KVM
  vCPU-exit-reason counters."
- **Collection points (8):** `Self::record_exit(self.stats_request, &mut
  self.exit_collector, &vcpu_exit)` after each `self.vcpu.run()` —
  `reverie-kvm/src/runtime.rs:1052` (`run_with_tool`, the real `hermit run
  --backend kvm` Detcore loop) and `runtime.rs:1410` (`run_static_elf_with_tool`),
  plus 6 sites in `reverie-kvm/src/vm.rs` (fork-park, thread-clone x2, pre-exec,
  personality loop, static loop). Every increment targets **`self.exit_collector`**
  — the calling instance's collector.
- **Aggregation point (the vacuity):** `reverie-kvm/src/vm.rs` —
  ```
  impl BackendStatsSource for KvmBackend {
      type Snapshot = KvmBackendStats;
      fn backend_stats(&self) -> Self::Snapshot { self.exit_collector.snapshot() }
  }
  ```
  It returns **only `self.exit_collector`**. There is NO sum over children, NO
  `Arc<collector>`, NO process-wide total, and NO "children-expected" denominator.
- **Fork:** `reverie-kvm/src/vm.rs::prepare_forked_process` copies only the
  request — `child.stats_request = self.stats_request;` — with the in-code comment
  "each backend keeps its own counters"; the `backend_stats` doc-comment states
  "forked child processes keep their own counters."

**Therefore CONFIRMED:** counting is strictly per-`KvmBackend`-instance with no
cross-instance aggregation and no expected-children denominator. A backend that
DROPS ALL CHILDREN produces a fully self-consistent, "green" root-instance
snapshot (its own exits recorded; zero children expected or summed), so any
consumer that scores "did the backend report stats / does the snapshot have
exits" scores 100% regardless of dropped children. The metric structurally cannot
detect missing children. This is the Proxy-Binding failure "carry the condition
with the value": the count records exits-on-this-vCPU without recording how many
child instances were expected.

**Load-bearing scope caveat (precision, not refutation):** #338 is an OPEN DRAFT,
NOT landed — the pinned reverie primary `114b3dfcf5fc5fb8cee10877944b0ed7be529522`
has no `reverie-kvm/src/stats.rs`. Per `kvm-backend-stats-provider-pr338` it also
has NO Hermit-cli `report()` wiring (consumer = separate task #1412). And the
parity scorecard's `parity`/`deterministic` columns are computed by sweep.sh from
stdout-hash + exit code, NOT from `backend_stats()`. So #338's per-instance vacuity
is real **in the stats-API design** but is **not currently a scored scorecard
cell** — it becomes load-bearing only when #1412 wires the collector into a
report/score consumer. Flag it now so the wiring task adds a children-expected
denominator (or a process-wide sum) rather than shipping the per-instance count as
a completeness signal.

---

## KVM parity-win classification (denominator: 112 parity=1 cells)

| class | count | verdict | why |
|---|---|---|---|
| `*-enosys` probes (assert ENOSYS) | 29 | VACUOUSLY-GREEN | inert KVM default == ENOSYS == asserted -> probe exits 0, prints fixed string -> parity=1 with zero KVM syscall code |
| `copy-file-range-refusal-probe` (asserts ENOSYS despite name) | 1 | VACUOUSLY-GREEN | fixture asserts `errno==ENOSYS` (copy_file_range_refusal_probe.c:39); inert KVM matches |
| empty-stdout content-free cells | 7 | VACUOUSLY-GREEN | output_hash == e3b0c442… (SHA-256 of empty); parity collapses to "both exit 0, both empty" — no output content compared |
| non-ENOSYS errno probes | 10 | GENUINELY-COVERED | assert EPERM/EOPNOTSUPP; inert KVM (ENOSYS) -> probe exits non-zero -> parity fails |
| real-output programs | 65 | GENUINELY-COVERED (see caveat) | distinct stdout requires KVM to execute + transport + (where applicable) determinize matching ptrace; inert KVM (no execution / leaked nondet) diverges |

**Denominator result: 75 GENUINELY-COVERED of 112 KVM parity-win cells; 37
VACUOUSLY-GREEN; 0 strictly BOX-BLOCKED (the discriminator was resolved
statically from fixtures + executor-default behavior + sweep mechanics).**

Confidence: the 37 VACUOUS and the 10 non-ENOSYS-errno GENUINE are **ESTABLISHED**
(fixture source read for 6 representative probes — the remaining enosys cells use
the identical `assert ENOSYS -> puts -> return 0` idiom — plus 4 executor-default
ratchet memories + sweep.sh parity mechanics + empty-hash directly in the CSV).
The 65 real-output GENUINE are **HYPOTHESIS-leaning-ESTABLISHED**: they clearly
fail if KVM does not execute at all, but "genuine" here means "fails if KVM fails
to execute+transport," a coarser bar than "exercises the specifically-named
determinization." Some (deterministic-output programs with no nondet content) test
generic KVM execution rather than a named determinization; a per-cell boxed
inert-arm probe would be needed to tighten each to the stronger bar — that residue
is the true BOX-BLOCKED work if a stronger standard is required.

### The 37 VACUOUSLY-GREEN cells

29 enosys-named: add-key, bpf, cachestat, futex-requeue, futex-waitv, futex-wake,
keyctl, listmount, lsm-get-self-attr, lsm-list-modules, lsm-set-self-attr,
map-shadow-stack, memfd-secret, perf-event-hardware, perf-event-open,
perf-event-software, perf-event-watchpoint, process-mrelease,
remap-file-pages-anonymous, remap-file-pages-tmpfile, request-key, splice,
statmount, sysfs, sysv-sem, sysv-shm, tee, ustat, vmsplice.
+ copy-file-range-refusal-probe (asserts ENOSYS).
+ 7 empty-stdout: dbi-execveat-unsupported, hello-nostdlib, just-spin,
memorypress, nanosleep-threads-nocrash, pread64-nostdlib, racewrite-nostdlib.

### Top vacuous families (highest stakes)

- **The `*-enosys` corpus family (30 cells).** Because inert KVM's default IS
  ENOSYS, none of these can fail against a KVM with no syscall-specific code. They
  verify only that KVM boots the VM, runs libc startup, reaches the syscall, and
  the default fall-through fires — NOT that KVM implements the named syscall.
- **The 7 empty-stdout cells.** parity=1 reduces to "kvm exited 0" — the stdout
  hash comparison (empty==empty) verifies no value. Weakest signal in the lane.

### Notable TRUE NEGATIVES (scorecard did NOT vacuously green these)

- The task's 4th named suspect — **sysinfo free_ram=0** (`c-programs/sysinfo`,
  `sysinfo-uptime`) — is honestly recorded as **parity=0** (KVM free_ram=0 vs
  ptrace non-zero; root-caused in `kvm-sysinfo-freeram-supervisor-statm-gap` /
  hermit#1404). It is NOT a parity-win, so it is not a vacuous green — the
  scorecard correctly reports the divergence.

### The task's named errno suspects — RESULT (mostly REFUTED as vacuous)

- **ptrace->EPERM (#341)** — GENUINE. Asserts EPERM; inert KVM (ENOSYS) fails.
  Cells `ptrace-eperm`, `ptrace-traceme-eperm`.
- **seccomp->EOPNOTSUPP (#344)** — GENUINE (asserts EOPNOTSUPP; inert ENOSYS
  fails). (Cell rides `tests/c/syscall_quick_wins.c`; behavior established from
  memory.)
- **credential-no-op (#354)** — VACUOUS re: KVM-specific code, but NOT via the
  errno-suspect mechanism: per `kvm-credential-noop-parity-pr354` the credential
  family is routed to Detcore (Ok(0)) on the default `--strict --verify` path and
  the reverie-kvm credential arm is DEAD code there ("newly passes 0 programs").
  So any credential parity-win passes because the SHARED Detcore path handles it;
  the KVM-specific arm is not exercised. (This is the generic-transport form of
  vacuity, distinct from the ENOSYS-default form.)
- **sysinfo free_ram (#338-adjacent)** — parity=0, true negative (above).

**Net:** the "no-op errno asserted as correct = vacuous" hypothesis is REFUTED for
KVM's non-ENOSYS errno cells (EPERM/EOPNOTSUPP), because inert KVM returns ENOSYS,
not the native/asserted errno. Vacuity for KVM concentrates instead in the
**ENOSYS-asserting family** (inert default == ENOSYS) and the **empty-stdout**
cells.

## Secondary ledger: matrix.tsv (guest-visible parity, separate from scorecard)

`hermit/tests/backend-parity/matrix.tsv` has 23 kvm=pass rows / 5 kvm=gap. The KVM
L2 kind is `guest` (guest-visible = stdout+exit only, explicitly weaker than the
DETLOG-bitwise ptrace/dbi kind — see `backend-parity-matrix-l2-verify-lift`). These
23 are curated real-behavior fixtures (hello_stdout, file_read/mutation/metadata,
virtual_clock, random_sources, virtual_pid, cpuid_policy, scheduler_policy_queries,
etc.) — predominantly GENUINE (distinct real output), with the same "guest-visible
is weaker than detlog" caveat the memory already records. Not re-counted into the
112 denominator (different corpus + comparison kind).
