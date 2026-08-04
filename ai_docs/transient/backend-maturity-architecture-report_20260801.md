# Hermit Backend Maturity & Architecture Report

**Date:** 2026-08-01 (07:00 ET deliverable)
**Author:** hermit-235 (impl agent, opus-4.8)
**Evidence base:** hermit `origin/main` @ `0da50ed8`, reverie `origin/main` @ `a4f33d6`.
Grounded in source (`tests/backend-parity/`, `docs/SABRE_COMPATIBILITY.md`,
`hermit-cli/tests/`, reverie backend crates) and tonight's landed PRs. No
guessed parity numbers; every ratio is tied to a source file or a merged PR.
Follows the anti-false-parity rule (task #152): a pass on one backend is
evidence for that backend and that exact test only.

---

## 0. Executive summary

`B`-level ladder (task #57): **B0** = crate exists; **B1** = partial `Guest`
impl; **B2** = trivial programs run through `Detcore<XxxGuest>`; **B3** = ≥50% of
the ptrace strict-verify corpus; **B4** = 100% parity with the golden ptrace
reference.

| Backend | B-level | Headline evidence | Load path | Determinism reached |
| --- | --- | --- | --- | --- |
| **ptrace** | **B4** (golden reference) | 23/23 parity contracts; full record/replay/chaos; the L0–L4 reference | `Detcore<PtraceGuest>`, seccomp+`PTRACE` | L0–L4 |
| **KVM** (flagship) | **B2** | 22/23 favorable-subset parity contracts (96%); canonical Detcore with per-child callbacks confirmed (21/23 exact-main L2, no relaxation, `2f3689bd`); B3 unearned — needs B2.4 + ≥½ the *full* frozen ptrace corpus, not a curated subset | `KvmGuest<Detcore>`, HW VM-exit hypercall, out-of-process | L2 on the 23-contract subset (`2f3689bd`); full-corpus L2/L3 sweep still pending |
| **DBI / DynamoRIO** | **B3** | 22/23 parity contracts (96%); 70/89 (78.7%) native suite | `Detcore<DbiGuest>`, DynamoRIO JIT rewrite, in-process | L1 in matrix (`--strict`, no `--verify`) |
| **SaBRe** | **B3** (qualified tonight) | 131/194 strict-verify cells = 67.5% (#1214) | `RemoteReverieAdapter<Detcore>` / `SabreGuest`, ELF SYSCALL rewrite, in-process + RPC coordinator | L2 with relaxations |
| **LiteInst** | **B2** (hybrid) | 65+ single-process programs L2; direct path L0-harness-only | **Hybrid**: `reverie_ptrace` host runs Detcore + liteinst patch runtime | L2 (single-process only; caveated) |
| **e9patch** | **N/A — not a backend** | 12-guest AOT preprocessing corpus (#1216) | e9tool AOT-rewrites ELF → runs under **ptrace** | L2 via ptrace |

**One-line state of the union:** ptrace remains the golden B4 reference. KVM
matches 22/23 of the cross-backend matrix (96%) running canonical Detcore with
per-child callbacks (confirmed by the `2f3689bd` L2 audit), but stays **B2**: the
23 contracts are a favorable curated subset, not ≥½ the full frozen ptrace
corpus, and no B2.4 is established. DBI is a solid B3 on the same matrix (96%),
with exactly one well-characterized gap. SaBRe crossed the B3 corpus threshold
**tonight** (67.5% of the strict-verify plan). LiteInst is a B2 hybrid whose
"L2" must be read carefully (Detcore actually runs in a ptrace host). e9patch is
preprocessing, not a backend, now with its own parity corpus.

---

## 1. Authoritative evidence sources (read these, not memory)

1. **`tests/backend-parity/matrix.tsv` + `README.md`** — the executable
   cross-backend ratchet. 23 contracts × {ptrace, DBI, KVM}. This is the single
   source of truth for ptrace/DBI/KVM parity ratios. Runs `--strict` (L1), each
   passing pair 3× byte-identical, **without `--verify`**. So the matrix is L1
   strict-mode evidence, not L2.
2. **`docs/SABRE_COMPATIBILITY.md`** (landed tonight, #1214) — the SaBRe
   strict-verify envelope, gap lists, and reproduction commands. L2.
3. **`tests/backend-parity/e9patch_corpus/README.md` + `e9patch_corpus.py`**
   (#1216) — the e9patch preprocessing parity corpus and why e9patch is not a
   matrix column.
4. **`hermit-cli/tests/liteinst_advanced.rs`** — the LiteInst hybrid corpus and
   the stderr assertions proving Detcore runs in the ptrace host.
5. **`hermit-cli/tests/sabre_examples.rs`** — the SaBRe L2 example ratchet.
6. Reverie backend crates: `reverie-kvm/src/{runtime,vm}.rs`,
   `reverie-dbi/src/lib.rs`, `detcore-sabre/src/lib.rs`,
   `reverie/src/backend.rs` (`Backend`, `Guest`, `ThreadOwnership`).

**Numbers deliberately NOT used** (they are corpus/PR figures not backed by the
current parity source, per #152): KVM "105/183", "5/5 @9cd955f9", "L3 2/102 vs
95/102"; DBI "29/35 L2". The only source-of-truth cross-backend ratios are the
23-contract matrix and the SaBRe 194-cell plan.

---

## 2. ptrace — the golden reference (B4)

**Maturity: B4.** ptrace *is* the reference against which B4 (100% parity) is
defined, so it is 23/23 on the matrix by construction. It is the only backend
supporting the full feature surface: `run`, `record`/`replay`, chaos scheduling,
schedule search, and the L3 (`--detlog-heap`/`--detlog-stack`) and L4 (20×
stress) assurance levels.

**Architecture.** `hermit-cli` builds a `reverie_ptrace::TracerBuilder` and loads
Detcore as `Detcore<PtraceGuest>`. Interception is the classic Linux mechanism:
seccomp-BPF traps guest syscalls to `PTRACE_EVENT_SECCOMP`, Reverie stops the
tracee, and Detcore's `handle_syscall_event` either emulates the syscall or
forwards it and sanitizes the result. Preemption uses the PMU retired-conditional-
branch (RCB) counter for repeatable timeslicing. `tool_local` handles per-task
events; `tool_global` owns shared deterministic state; they communicate by RPC.

**Works.** Everything the project claims as "done" is proven here first: virtual
time, virtual PID, determinized randomness, deterministic scheduling, CPUID/RDTSC
control, signals, fork/exec/thread trees, record/replay, chaos, and memory
determinism.

**Limitations.** Per-syscall ptrace stops are the performance cost that motivates
the other backends. Requires PMU access for RCB preemption (can be unavailable in
restricted VMs/containers — a host limitation, not a product bug).

**Recent progress / gaps.** `clock_getres(NULL)` accepted (#1208, `0ca0dec2`);
`pidfd_send_signal`/`pidfd_getfd` determinized (#1175). ptrace is mature; the open
frontier is not ptrace itself but the lower-overhead backends catching up to it.

---

## 3. KVM — the flagship backend (B2)

**Maturity: B2.** 22/23 cross-backend contracts (96%,
`tests/backend-parity/matrix.tsv`); sole gap: `process_wait_lifecycle`. The
23-contract set is a **favorable curated subset**, not ≥½ the full frozen ptrace
corpus, and no B2.4 is established, so the B3 threshold is **unearned** despite the
high subset ratio. (The earlier "105/183 sweep" was exit-code + stdout only —
no stderr, no semantic/L3 comparison — and does not qualify as corpus-level B3
evidence either.)

**Canonical Detcore, per-child, confirmed.** The `2f3689bd` audit
(`kvm-maturity-integrity-audit`) ran the matrix at L2 (`--strict --verify`,
**no relaxation**) and measured **21/23 exact-main parity**. KVM runs the real
shared Detcore tool with genuine per-child callbacks — the earlier claim that
child/thread syscalls bypass Detcore in a backend-local `ElfExecutor`
personality is **withdrawn as inaccurate**.

**Architecture.** KVM loads the real shared Detcore as a Reverie tool via
`KvmGuest<Detcore>`. `reverie-kvm/src/runtime.rs:343` `run_with_tool` calls
`init_global_state` / `T::new` / subscriptions / `init_thread_state`; syscalls
dispatch through `handle_syscall_event` (`runtime.rs:927`). Interception is a
**hardware VM-exit hypercall, out-of-process**: the guest issues
`VMCALL_SYSCALL_TRANSPORT=12` (`vm.rs:66`), the VM exits to the host VMM, which
routes the syscall into Detcore. The root process enters via
`run_static_elf_with_tool::<Detcore>`.

Thread ownership was refactored this session to a first-class
`ThreadOwnership` enum (reverie `6f88cdb`/`ff2d43d`; default resolves to
Tool-owned, `640c5bc`), consumed by hermit-cli (#, `9cd955f9`). This is the
"Option A" CLONE_THREAD dispatch (`d246396`), letting KVM route thread workers
through the Tool.

**Works.** 22 contracts: hello/args/exit, file read/mutation/metadata,
io_uring→ENOSYS with epoll fallback, listmount refusal, process_vm_readv/writev
EPERM refusal, executable mmap (W→X + call), memory-advice policy (KVM enforces
its documented `ENOSYS` for `MADV_DONTNEED`), heap growth, anonymous + shared
mmap layout, bounded cooperative pthread lifecycle, `wait4`/`waitid` with zeroed
CPU accounting + complete reaping, synthetic CPUID, virtual clock/PID, threaded
random-source. **`waitid` was fixed tonight** (reverie #288, `a4f33d6`):
previously a `waitid` on a not-yet-joined child read an empty `state.children`
and returned spurious `ECHILD`; the fix synchronizes `waitid` like `wait4`. This
supersedes the old "KVM waitid ECHILD" defect note.

**L3 memory determinism newly wired tonight** (reverie #301, `4deb923`): the
`--detlog-heap`/`--detlog-stack` path read `/proc/<pid>/maps` for
`Guest::pid()`, which for KVM is the *host VMM* process — hashing the wrong
memory and faulting with EFAULT ("FATAL: cannot determine kernel version"). The
additive, defaulted `Guest::detlog_memory_regions()` now lets `KvmGuest` report
real guest-address ranges — heap `[heap_base, program_break)` and stack
`[rsp, guest_end)` — readable through `Guest::memory()`; ptrace keeps its
historical `/proc` behavior (`None`). This unblocks KVM L3 logging without the
crash.

**Doesn't (honest).** Sole matrix gap = `process_wait_lifecycle`: KVM records
serialized child exits and implements `wait4`/`waitid`, but **does not yet
synthesize an x86-64 SIGCHLD handler frame** to actually run the guest's signal
handler. The CPUID row validates reverie-kvm's backend-local `KVM_SET_CPUID2`
policy, not Detcore CPUID-event parity. The binding B2→B3 gap is **corpus
breadth, not the execution model**: the 23 contracts and the exit+stdout-only
"105/183" sweep are not ≥½ the full frozen ptrace corpus compared at the L3 /
semantic level, so B3 stays unearned.

**Next gaps (incl. owner-gated).** (1) SIGCHLD signal-frame synthesis for
`process_wait_lifecycle` → B4 on that contract. (2) Broaden the confirmed L2
parity from the 23-contract subset to ≥½ the full frozen ptrace corpus
(stderr + semantic/L3, not exit+stdout) — the real B2→B3 step. Any KVM change
also requires the **Relationship to gVisor** PR section.

---

## 4. DBI / DynamoRIO backend (B3)

**Maturity: B3.** 22/23 cross-backend contracts (96%). Separately, the backend's
own Reverie suite baseline is 70/89 (78.7%) — a different measurement, called out
explicitly in `README.md` so the two are never conflated. Sole matrix gap:
`pthread_lifecycle`.

**Architecture.** `detcore::Detcore` over `reverie_dbi::DbiGuest`
(`backends.rs:11-19`). `DbiGuest<'a, T>` uses `Memory = LocalMemory` and runs
**in-process** (`lib.rs:136,272`): DynamoRIO's JIT rewrites the guest instruction
stream, trapping SYSCALL sites into the shared Detcore tool. Config is passed via
`DETCONFIG_ENV` (`backends.rs:411`).

**Works.** 22 contracts including virtual clock/PID, root-thread random source
(compared byte-for-byte against a ptrace reference), executable memory,
memory-advice, memory layout, file mutation/metadata, io_uring/listmount refusal,
process-memory EPERM refusal, and the full wait lifecycle. `rdtsc`/`rdtscp` are
determinized directly in the DynamoRIO client (reverie #293, `9216e22`).

**Recent progress tonight.** DBI fd-hygiene fix #1218 (`22645303`): the
unsupported-syscall report-fd copy is now kept out of the guest fd range
(`detcore/src/tool_global.rs`) — this was the clean win from the fd-hygiene lane.

**Doesn't (honest).** Sole matrix gap = `pthread_lifecycle`: portable release
DynamoRIO can stall or exit during native pthread startup *before* Detcore
readiness, so it is kept an explicit `gap` rather than a flaky CI pass. In-process
clean-call scheduler-turn preemption is a dead-end (re-enters guest-shared libc);
`set_timer` returns `ENOSYS` (not armed, `lib.rs:370-384`);
`--no-sequentialize-threads` is rejected (`backends.rs:359`); no record/replay or
chaos. Matrix evidence is L1 (`--strict`, no `--verify`).

**Next gaps.** (1) pthread startup stall (DynamoRIO-timing) → close the last
contract. (2) Preemption without re-entrancy hazards (safe-point path) is the
known ceiling; the exit_group teardown contract under DynamoRIO threads is
owner-design (trigger #4-adjacent). (3) L2/`--verify` in the matrix.

---

## 5. SaBRe backend (B3 — qualified tonight)

**Maturity: B3, newly established tonight (#1214, `3bc2ab61`).** This is the
headline advance of the session. From `docs/SABRE_COMPATIBILITY.md`: the ptrace
strict-verify plan has **194 cells**; SaBRe was enabled for 22 (11.3%) before the
ratchet. Evaluating 157 previously disabled C candidates:

| Result | Cells |
| --- | ---: |
| SaBRe L2 **and** ptrace exit/stdout parity → enabled | 109 |
| SaBRe L2 but ptrace output differs → disabled | 18 |
| SaBRe L2 failed or timed out → disabled | 30 |

Resulting plan: **131/194 = 67.5%** (7 blocking-CI cells + 124 manual cells).
This meets the B3 threshold (≥50% of the ptrace strict-verify corpus). It does
**not** establish B4, L3 memory determinism, L4 stress, or whole-subsystem
support. (This source-grounds the "67.5%/131/194" figure my earlier notes
flagged as unverified — it is now real, as of tonight.)

**Architecture.** Detcore runs as the tool `T` through SaBRe adapters:
`ReverieAdapter` (in-proc `GlobalState`) and `RemoteReverieAdapter<Detcore>`
(GlobalTool over reverie-rpc-transport), with `SabreGuest<'state,'inject,T>`
(`reverie_adapter.rs:991`, `impl Guest:1089`) and `SabreMemory` via
`process_vm_readv/writev`. Interception is a **custom ELF loader that rewrites
SYSCALL instructions** in the scanned `.text`, in-process, plus vDSO detours and
static RDTSC rewrite — **not** seccomp/ptrace. Detcore ships as
`libdetcore_sabre.so`; invoked with `--backend sabre` behind the non-default
`third-party-backends` Cargo feature; **fails closed** if the feature/artifacts
are absent (no silent ptrace fallback). Log signature:
`launching Detcore guest through SaBRe with coordinator RPC`.

**Process model — broadest of the non-ptrace backends** (`CAPABILITIES.md`):
fork+execve (children lazily rebuild the Tool; execve re-enters the pinned
loader), pthread (128-cycle conformance gate), orderly `exit_group`, virtualized
`rt_sigaction` signals with waitable SIGCHLD, and vfork rewritten to a private
fork.

**Works.** L2 (`--strict --verify`, portable corpus uses
`--no-virtualize-cpuid --max-timeslice=disabled`; standalone `/bin/echo`,
`/bin/true`, `/bin/cat /dev/null` pass L2 with no relaxations). 131 cells across
`c-programs` (99 new), `determinism-stress-c` (7), `backend-parity-c` (1),
`bin-c` (1), plus the pre-existing example/date/random-device/archive ratchets.
GNU `patch` `getrandom` is now mediated via a libc-function detour (#, `af704c4e`)
and passes 5 consecutive strict probes.

**Doesn't (honest).** 18 cells are deterministic-in-SaBRe but differ from ptrace
output (e.g. `random-sources`, `sysinfo`, `socket-cookie-*`, `wait-on-child`,
`setitimer-determinism`); 30 fail strict-verify or time out (e.g. `clone`,
`vforkexec`, `robust-futex-test`, `hello-nostdlib`, `epoll-determinism`,
`thread-sync-determinism`, the `qemu-*` shared-futex cells, `pmu-skid`).
Backend-wide limits: registers RIP/RSP/RAX/flags are read-only (`set_regs`
`EOPNOTSUPP`); CPUID/RDTSCP not fully intercepted (raw host TSC can leak →
clock-determinism cell disabled); only scanned `.text` is covered (static/no-libc
binaries and JIT can escape, no seccomp fallback in the inspected revision);
signal ABI is not kernel-exact; ptrace and SaBRe clock trajectories are not yet
identical; record/replay and chaos are unsupported; `race.sh` excluded (SaBRe does
not serialize arbitrary instructions between callbacks). No PMU preemption
(`read_clock=Ok(0)`, `set_timer` no-op).

**Next gaps.** (1) The 18 output-divergence cells (clock trajectory unification,
multithreaded random-source parity, socket/sysinfo determinization). (2) The 30
failing cells (raw clone/vfork/robust-futex, static-binary rewrite envelope,
epoll/thread-sync). (3) CPUID/RDTSCP interception → clock-determinism. (4)
Register-write support. (5) Promoting from "work-ahead envelope" to a supported
transparent backend is an **owner-gated** posture decision.

---

## 6. LiteInst backend (B2 — hybrid)

**Maturity: B2 (hybrid).** 65+ single-process programs pass at
`--backend liteinst --strict --verify` (L2), enumerated in
`hermit-cli/tests/liteinst_advanced.rs` (incl. `python3`/`perl`/`awk`/`sqlite3`);
16/16 L2 on a 20-program C corpus. The direct `LiteinstBackend` path
(`impl reverie::Backend`, `LiteinstGuest<T>`) is L0-harness-only (echo/true/cat).

**Architecture — two paths, and the distinction matters for #152:**
- **(a) Direct** `LiteinstBackend` + `LiteinstGuest<T>`: liteinst2 5-byte text
  patch + trampoline, bootstrapped by a reverie-preload `LD_PRELOAD` with
  SIGSYS+seccomp. Exercised only by the L0 harness. Fails closed without runtime
  activation (reverie #291, `5e92f4f`).
- **(b) Hermit `--backend liteinst` = HYBRID:** builds a
  `reverie_ptrace::TracerBuilder` host (`backend.rs:31`). The test asserts
  stderr `LiteInst host hybrid (reverie-liteinst patch runtime + ptrace Detcore
  Tool)` and `traps=1,hooks=31; Detcore Tool active in ptrace host`
  (`liteinst_advanced.rs:171-191`). **Detcore runs in the ptrace host**, with
  liteinst providing the in-process patch/trap runtime. TSC/CPUID policy is
  preserved around liteinst patch helpers (reverie `d96ee81`/`87aa36f`).

**Works.** 65+ single-process, single-thread programs L2 via the hybrid path.
Recent progress tonight: corpus rounds r1/r2 ratcheted the coverage —
`4867b52f` (semantic utility corpus r1), `0c2de18c` (utility corpus r2),
`aa1dca7d` (digest/formatting), `0ff48acf` (fixture isolation), `c4b7b1a6`
(host-independent sum assertion). The ptrace-hosted switch itself landed as #1166
(`7f893d09`).

**Doesn't (honest — important #152 caveat).** Single-process / single-thread
**only**: `clone`/`fork`/`vfork`/thread creation fail closed with `ENOTSUPP`
(tested `liteinst_advanced.rs:678-685`). No timer/clock/PMU; guest signal handlers
unsupported. The source `README.md:172-183` carries the key honesty caveat:
`--verify` proves *run1 == run2*, **not** *run == native* — so a program that
ignores a rejected `fork`/`clone` errno gets a wrong-but-reproducible L2. Treat
any L2 on a program that uses threads or child processes as **suspect**. And
because the productive path is ptrace-hosted, its determinism engine *is* ptrace's
Detcore — this is not independent LiteInst-native backend parity.

**Next gaps.** (1) Native (non-ptrace-hosted) Detcore load reaching beyond the L0
harness — the real B2→B3 step for the direct path. (2) Multi-process / thread
support. (3) Timer/clock/PMU. Advancing the direct backend to load Detcore itself
is a core-model change (owner-gated).

---

## 7. e9patch — NOT a backend (preprocessing)

**Classification: not a backend** (confirmed in source; `README.md` and
`e9patch_corpus/README.md`). e9patch is binary-rewriting **AOT preprocessing** for
the ptrace backend: e9tool rewrites the guest ELF ahead of time to pre-trap its
SYSCALL sites, then Detcore runs the rewritten image **under ptrace**. A CLI
spelling `--backend=e9patch` does not change this. It is deliberately not a column
in the parity matrix.

**Recent progress tonight (#1216, `a08ce33b`).** A dedicated preprocessing-parity
corpus: 12 freestanding, statically linked, raw-`syscall` guests
(`minimal_exit`, `write_stdout`, `getpid_check`, `clock_gettime`, `nanosleep`,
`getrandom`, `multi_site`, `loop_write`, `mmap_anon`, `uname`, `sigmask`,
`compute`). Freestanding is required: e9tool rewrites only the *main* executable,
so a dynamically linked libc guest exposes zero in-ELF SYSCALL sites
(`candidate_sites=0`) and the rewrite is a no-op — which is exactly why the shared
`run_matrix.py` libc guests cannot measure e9patch.

For each guest, `e9patch_corpus.py` enforces: exit-status parity, stdout parity,
golden L2 (`hermit run --strict --verify`), e9patch L2
(`hermit --backend e9patch run --strict --verify`), full direct-AOT coverage
(`mapped_sites == candidate_sites > 0`), no signal fallback (`b0_sites == 0`), and
guest-syscall DETLOG **tail-match** (golden guest-syscall sequence == suffix of
the e9patch sequence). Byte-identical DETLOG is impossible by construction: the
e9patch image runs a fixed deterministic e9loader prologue before `_start`; that
prologue is a pure prefix, so parity is enforced *modulo* the prologue. Reverie
side: Counter1 hosted after startup (#283, `4555a14`), coordinator-channel fd
classifier unit-tested (#292, `0ecdff3`).

**Constraint.** e9tool/e9patch binaries are absent from CI, so the harness needs
`--features e9patch` + `HERMIT_E9TOOL`/`HERMIT_E9PATCH_BACKEND` locally; `--check`
validates the contract without prerequisites. No CPUID/TSC interception path
(irrelevant for AOT preprocessing).

---

## 8. Tonight's landed-PR ledger (all MERGED)

| PR | SHA | Backend | What landed |
| --- | --- | --- | --- |
| hermit #1214 | `3bc2ab61` | SaBRe | Qualify SaBRe B3 corpus (131/194 = 67.5%) + `SABRE_COMPATIBILITY.md` |
| hermit (getrandom) | `af704c4e` | SaBRe | Mediate libc `getrandom` via detour (GNU patch 5× strict) |
| hermit (ci) | `672a95b8` | SaBRe | Stage SaBRe for C corpus checks |
| hermit #1216 | `a08ce33b` | e9patch | e9patch preprocessing parity corpus harness (12 guests) |
| hermit #1217 | `1ece0654` | — | Bump reverie pin to `aa6f1283` across reverie→reverie-core rename |
| hermit #1218 | `22645303` | DBI | Keep unsupported-syscall report-fd copy out of guest fd range |
| hermit (LiteInst r1) | `4867b52f` | LiteInst | Ratchet semantic utility corpus |
| hermit (LiteInst r2) | `0c2de18c` | LiteInst | Round-two utility corpus |
| hermit (LiteInst) | `aa1dca7d` / `0ff48acf` / `c4b7b1a6` | LiteInst | Digest/formatting corpus; fixture isolation; host-independent sum |
| hermit (clock) | `0ca0dec2` | ptrace | Accept NULL res pointer in `clock_getres` (#1208) |
| reverie #288 | `a4f33d6` | KVM | Synchronize `waitid` on pending child processes (fixes spurious ECHILD) |
| reverie #301 | `4deb923` | KVM | `Guest::detlog_memory_regions` — report KVM guest stack/heap (L3 unblock) |
| reverie #293 | `9216e22` | DBI | Determinize rdtsc/rdtscp in the DynamoRIO client |
| reverie #291 | `5e92f4f` | LiteInst | Fail closed without LiteInst runtime activation |
| reverie #283/#292 | `4555a14`/`0ecdff3` | e9patch | Counter1 after startup; fd classifier unit test |

New e2e determinism tests this session (portable, two-sided, draft PRs):
`language-runtimes/python-hash-determinism` (#1238, CPython SipHash channel),
`language-runtimes/bash-random` (#1291, Bash `$RANDOM` PID+time channel),
`determinism-stress-c/producer-consumer` (`e7d80562`, landed).

---

## 9. Cross-cutting honesty notes (#152)

- **Matrix ratios are L1, not L2.** ptrace/DBI/KVM 23/22/22 come from
  `matrix.tsv` which runs `--strict` 3× byte-identical **without `--verify`**.
  Do not present them as L2. SaBRe's 131/194 *is* L2 (`--strict --verify`), but
  with `--no-virtualize-cpuid --max-timeslice=disabled` relaxations on the
  portable corpus.
- **"One shared Detcore" is real for ptrace/KVM/DBI/SaBRe**, and for KVM this now
  includes genuine **per-child Detcore callbacks** (confirmed by the `2f3689bd`
  L2 audit: 21/23 exact-main, no relaxation) — the earlier "KVM child/thread
  syscalls run in a backend-local `ElfExecutor` personality without per-child
  Detcore" caveat is withdrawn. LiteInst's productive path still runs Detcore in
  a **ptrace host**, so that path is not unqualified "Detcore parity."
- **LiteInst L2 can be a false positive** for any program touching
  threads/child-processes (run1==run2 ≠ run==native). Only single-process,
  single-thread LiteInst L2 is trustworthy.
- **e9patch is preprocessing, not a backend** — always report "e9patch
  preprocessing with the ptrace backend."
- **B4 is unclaimed for every non-ptrace backend.** No backend other than the
  golden reference passes 100% of the corpus at L2.

---

## 10. Priority next steps (for the owner)

1. **KVM SIGCHLD signal-frame synthesis** — the single remaining matrix contract
   for KVM. (Per-child Detcore callbacks are confirmed working per the `2f3689bd`
   audit; the real B2→B3 lift is corpus breadth at L2/L3, not the execution
   model.) Any KVM change still needs the gVisor-comparison PR section.
2. **SaBRe divergence cells (18)** — clock-trajectory unification and
   multithreaded random-source parity are the highest-leverage; then the 30
   failing cells (clone/vfork/static-binary envelope).
3. **DBI pthread startup stall** — the one matrix gap; and the owner-gated
   exit_group teardown contract under DynamoRIO threads.
4. **LiteInst native path** — move the direct backend beyond the L0 harness; add
   multi-process support (currently fail-closed).
5. **Lift matrix to L2** — add `--verify` to the parity runner so ptrace/DBI/KVM
   ratios become L2 evidence, not L1.
