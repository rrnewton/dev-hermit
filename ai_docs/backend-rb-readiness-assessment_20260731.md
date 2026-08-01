# Non-ptrace backend readiness for reproducible builds (RB perf graduation)

- **Task:** `backend-rb-readiness-assessment` (recurring research, ungated, #126)
- **Agent:** opus-4.8, research-only (no code changes)
- **Date:** 2026-07-31
- **Ground:** hermit `main` @ `c4b7b1a6` ("test: make LiteInst sum assertion
  host-independent"); reverie `main` @ `aa6f128`.
- **Question:** which non-ptrace backend is the leading candidate to *try* on a
  simple reproducible build, to graduate the RB perf path off ptrace (ptrace is
  the slow sequentialization path)? Track KVM (flagship), DBI, and the patching
  backends (SaBRe / LiteInst / e9patch) as perf-leader candidates.

## TL;DR — go/no-go

**Leading candidate: SaBRe (GO to try).** It is the only non-ptrace backend that
supports the full RB process model (fork + execve + process trees + pthread
lifecycle + orderly `exit_group` + signal forwarding), it already runs
`rustc`/`cargo` guests under `--strict --verify` (L2), and it is the
systrap-analog in-guest-syscall-rewriting design the owner flagged as the likely
perf leader. Remaining gaps are compat-envelope (execveat, record/replay, chaos,
a few clock/random determinism items), not process-model blockers.

| Backend | Is it a real backend? | Latest measured envelope | RB process model (fork/exec/wait/SIGCHLD/threads) | RB go/no-go |
|---|---|---|---|---|
| **SaBRe** | Yes (`Detcore<SabreGuest>`, in-guest syscall rewriting) | **131/194 (67.5%) at `--strict --verify` (L2)**, PR #1214 draft; 4 non-racy examples L2; runs rustc/cargo | **Full**: fork+execve, process trees, pthread waves, orderly exit_group, signal forwarding. Gap: **execveat unsupported** | **GO** — try RB now |
| **KVM (flagship)** | Yes (`Detcore<KvmGuest>`) | matches ptrace **105/183 (63.3%)** B3 sweep (#1188); shared parity matrix 22/23 | Partial: wait4/waitid serialized, but **no synthesized guest SIGCHLD delivery**; process-creation chain still blocked (python3 vfork/CLONE_THREAD); waitid ECHILD bug | **NO-GO yet** — process-creation + SIGCHLD gaps |
| **DBI (DynamoRIO)** | Yes (`Detcore<DbiGuest>`) | shared parity matrix 22/23; DBI L2 corpus 29/35; rdtsc determinized (#1171) | Partial: pthread-startup stall before Detcore readiness; **exit_group multi-thread teardown** contract fails under DynamoRIO threads; preemption ceiling | **NO-GO yet** — pthread/exit_group teardown |
| **LiteInst** | Yes (in-place patching) | 65 single-process commands (#1211 draft) | **None by design**: corpus *deliberately excludes* clone/fork/thread/SIGCHLD | **NO-GO** — cannot model a multi-process build |
| **e9patch** | **No** — AOT binary-rewriting *preprocessing for the ptrace backend* | 12 static raw-syscall guests (#1216); no-op on dynamically-linked libc | N/A — executes under ptrace | **N/A** — not an off-ptrace path |

## The RB-critical discriminator is the process model, not the utility corpus %

A reproducible build is `shell → make/cargo → cc/rustc → as → ld`, each spawned
and reaped. The dimension that decides RB-readiness is therefore
**fork/exec/clone/wait/SIGCHLD/process-tree support**, *not* the single-process
utility corpus percentage that most backend ratchets report. A backend can score
70% on system utilities and still be unable to run a two-process build if it
cannot fork-exec-and-reap a child. This reframes the ranking:

- **SaBRe** is the only non-ptrace backend whose capability sheet asserts the
  whole chain. `reverie/experimental/reverie-sabre/CAPABILITIES.md`:
  forked children lazily construct the same Tool with new process-local state;
  `execve` re-enters the pinned SaBRe loader so the plugin survives the new
  image; pthread create/return/join waves are gated by conformance;
  `exit_group` requests orderly exit from tracked threads then issues a real
  kernel `exit_group`; `signal_forwarding` installs handlers, forks, and waits;
  counter2 aggregates forked process trees. **Only `execveat` is unsupported.**
- **KVM** records serialized child exits and implements wait4/waitid but "does
  not synthesize guest SIGCHLD handler delivery" (matrix.tsv gap), and the
  process-creation chain (vfork panic / folly EBADF / CLONE_THREAD-bypasses-
  Detcore) is still open; a build's `make` that relies on SIGCHLD reaping is at
  risk.
- **DBI** stalls during native pthread startup before Detcore readiness
  (matrix.tsv pthread_lifecycle gap) and its `exit_group` multi-thread teardown
  ("trust kernel to kill siblings") fails under DynamoRIO-managed threads —
  both directly in the build teardown path.
- **LiteInst's** #1211 corpus explicitly excludes clone/fork/thread/SIGCHLD, so
  it structurally cannot host a multi-process build today.
- **e9patch** is preprocessing over ptrace, so it offers no perf escape from
  ptrace at all.

## There is already a minimal in-tree RB regression — and it is ptrace-only

`hermit/tests/reproducible-builds/run.sh` is the concrete "simple reproducible
build" the task targets. It:

1. compiles a leaf crate with `rustc -Z threads=1 --emit=obj` that expands the
   `build_time_utc!` proc-macro (fixture `build-time-0.1.3`), embedding a
   timestamp — so **native** `native-one.o` vs `native-two.o` differ;
2. runs the same `rustc` under `hermit run --strict` twice and asserts
   `hermit-one.o == hermit-two.o` (reproducible);
3. runs it once more under `hermit run --strict --verify` (L2 bitwise repeat).

The final line is literally `PASS: native artifacts differ; strict **ptrace**
Hermit artifacts match.` The backend is hardcoded to the default (ptrace); there
is no `--backend` override. This is the exact artifact to graduate off ptrace.

**Note on scope:** this fixture is a *single-process* `rustc -Z threads=1`
compile with no linker child, so it is a gentler test than a full `make`/`cargo`
build. It is a good first rung for SaBRe (and even a stretch attempt for KVM/DBI
on the single-process axis), but it does **not** exercise fork-exec-reap. A true
RB graduation must follow with a multi-process build (a `cargo build` or a small
`make`) where the process-model differences above become decisive — and there
SaBRe is the only current candidate.

## Concrete graduation experiment (recommended, not yet run — needs approval)

Minimal, low-risk, and directly answers go/no-go with evidence:

1. **SaBRe on the existing single-process fixture.** Re-run
   `tests/reproducible-builds/run.sh` with the two `--strict`/`--strict --verify`
   invocations carrying `--backend=sabre --sabre-loader <loader>`. Expected:
   PASS at L2 (rustc is already in SaBRe's verified corpus). If it passes, that
   is the first off-ptrace RB green.
2. **SaBRe on a two-process build** (`bash -c 'rustc … && ld …'`, or a trivial
   two-target `make`) under `--strict --verify`. This is the real test of
   fork/execve/reap. Watch for the `execveat` gap (modern coreutils/shells
   occasionally use it) and the date.sh-class continuous-clock trajectory.
3. **Perf comparison** vs ptrace on the same build (wall-clock), to quantify the
   graduation payoff — this is the whole point (ptrace's >23-min sequentialized
   path is the baseline to beat).

KVM/DBI/LiteInst are not worth a build attempt this cycle: KVM and DBI fail on
the process-model axis a build needs, and LiteInst excludes it by construction.

## Perf rationale (why SaBRe is also the perf-leader candidate)

The owner's 2026-07-30 note anticipated the perf leader being a patching backend
(SaBRe / LiteInst / e9patch) analogous to gVisor's SYSTRAP platform — in-guest
syscall rewriting avoids the ptrace context-switch per syscall. SaBRe is exactly
that: it rewrites syscall sites in-process and dispatches to the Detcore tool
without a ptrace stop. So SaBRe is simultaneously (a) the most RB-capable
non-ptrace backend and (b) the design most likely to *beat* ptrace on build
wall-clock. That convergence is why it is the single recommended try.

## Readiness note (this cycle)

- **Leading backend:** SaBRe. **B-level/corpus:** 131/194 = 67.5% at
  `--strict --verify` (L2), PR #1214 (draft) @ hermit `main` `c4b7b1a6` /
  reverie `aa6f128`; runs rustc/cargo guests; full fork/exec/thread/exit_group/
  signal process model (execveat excepted).
- **Can it run a simple build under `--strict`?** Yes for single-process rustc
  (already in the verified corpus); a fork-exec build is the untested next rung.
- **Go/no-go on trying RB off ptrace:** **GO on SaBRe** — run the graduation
  experiment above. **NO-GO** on KVM (SIGCHLD/process-creation), DBI
  (pthread-startup/exit_group teardown), LiteInst (no process model), and e9patch
  (not an off-ptrace path).

## Sources

- `hermit/tests/reproducible-builds/run.sh`, `.../build-time-0.1.3/` (in-tree RB
  regression, ptrace-only).
- `hermit/hermit-cli/tests/sabre_examples.rs` (`assert_backend_parity_and_sabre_verify`
  = ptrace parity + SaBRe `--strict --verify`; NON_RACY_EXAMPLES + clock-progress).
- `reverie/experimental/reverie-sabre/CAPABILITIES.md` (fork/exec/threads/
  exit_group/signal envelope; execveat unsupported).
- `hermit/tests/backend-parity/matrix.tsv` (ptrace 23/23; DBI 22/23 pthread gap;
  KVM 22/23 SIGCHLD gap).
- PRs: #1214 (SaBRe 131/194 corpus), #1173 (SaBRe rustc/cargo required rows),
  #1211 (LiteInst 65 single-process, excludes fork/clone/thread), #1216 (e9patch
  preprocessing harness), #1188 (KVM B3 sweep 105/183), #1171 (DBI rdtsc),
  #1218 (DBI report-fd hygiene).
- Memory: `dbi-l2-corpus-baseline`, `kvm-b3-corpus-sweep-and-increment`,
  `kvm-python3-blocker-chain`, `dbi-corpus-hangs-preemption-ceiling-exit-group-teardown`,
  `third-party-backends-cargo-feature-flag`, `nix-minimum-hermit-dose`.
