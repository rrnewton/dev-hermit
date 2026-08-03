# E9patch corpus power-to-weight — adversarial review (cross-model, claude)

**Reviews:** `ai_docs/e9patch-corpus-power-to-weight-selection_20260803.md`
(hermit-ptw codex proposal, parent commit `272367e`).

**Reviewer:** hermit-coord (claude, opus-4.8), cross-model adversarial gate.

**Method:** opened the proposal's actual selection artifact, then verified every
claim against ground truth — the shared schema-v2 manifests
(`tests/e2e/manifests/*.toml`) and the current program sources on `hermit`
primary `main` (`f356148f`) — rather than trusting the proposal's assertions.
`clock_getres`/`flock`/`sched_getaffinity` "gaps" were checked with a
whole-tree grep of `tests/**/*.{c,rs}`, not just the five target files. Backend
cell sets were read directly from the manifests. I did **not** rebuild the
#1516 corpus binaries, so the absolute five-run medians are accepted as
plausible, not independently reproduced (see Check 3).

## Verdict summary

| Case | Proposal | Verdict | Basis |
|---|---|---|---|
| KEEP-1 clock_getres | promote (1 contract) | **DISAGREE — drop** | already called & L2-verified in a shared program |
| KEEP-2 scheduler queries | promote (6 contracts / 8 entry pts) | **AGREE** (conditional) | genuinely uncovered surface |
| KEEP-3 process identity | promote (4 / 6) | **AGREE** (conditional) | genuinely uncovered surface |
| KEEP-4 metadata agreement | promote (4 / 7) | **AGREE** (conditional) | genuinely uncovered surface |
| KEEP-5 pidfd operations | promote (2 / 2) | **AGREE** (conditional) | genuinely uncovered surface |
| 347 rejects | reject | **AGREE** | taxonomy holds on spot-check |

**Agreed promotable subset: KEEPs 2–5 = 16 contracts / 23 entry points**
(not 17 / 24), at ~0.70 ptrace-first validate seconds — subject to the
multi-backend condition below.

## Check 1 — do the KEEPs cover surface the shared suite actually lacks?

Whole-tree grep of every KEEP syscall against `tests/**/*.{c,rs}` on `main`:

- **KEEP-1 clock_getres — gap claim is FALSE.** The proposal states "No other
  shared manifest program explicitly calls `clock_getres`." But
  `tests/rust/clock_gettime.rs:42` calls `clock_getres(*clockid).unwrap()` for
  all 10 clock IDs, prints `res` into stdout (so it is already bitwise-verified
  under `--verify`), and branches its monotonicity assertions on it. That
  program is a shared manifest row (`system-utils.toml`, and
  `clock_total_order.rs` references it too). `clock_getres` is therefore already
  an asserted, L2-verified contract in the shared suite. KEEP-1 adds no new
  contract; at most a marginal C/LiteInst-cell echo of an existing one. **Drop
  it** — its inclusion also inflates the headline to 17/24.

- **KEEP-2 scheduler queries — gap real.** `sched_getparam`,
  `sched_getscheduler`, `sched_getattr`, `sched_get_priority_min/max`,
  `sched_rr_get_interval`, `getpriority` = 0 hits. `sched_getaffinity`'s only
  hit is a strace-output *comment* in `bind_connect_race.rs:74`, not a live
  call. Existing `scheduler_policy_queries.c` only exercises `getitimer`,
  `ioprio_get`, `sched_setattr`. The cross-check (legacy scheduler APIs agree
  with `sched_getattr`) is a genuine, strong contract. **Agree.**

- **KEEP-3 process identity — gap real.** `geteuid`, `getegid`, `getgroups`,
  `getpgid`, `getpgrp`, `getsid` = 0 hits. Current `pid_probe.c` prints only
  `getpid`. **Agree.**

- **KEEP-4 metadata agreement — gap real.** `statx`, `newfstatat`, `statfs`,
  `fstatfs`, `faccessat2`, `utimensat` = 0 hits. The `flock` grep hit is a
  FALSE POSITIVE — `proc_locks.c` uses `struct flock` with
  `fcntl(F_SETLK)`/`F_OFD_SETLK` (POSIX/OFD record locks), which is a distinct
  lock space from `flock(2)` BSD advisory locks; `flock(2)` is genuinely
  absent. **Agree.**

- **KEEP-5 pidfd operations — gap real.** `pidfd_getfd`, `pidfd_send_signal` =
  0 hits. Existing pidfd rows cover only `pidfd_open`, poll, and
  `waitid(P_PIDFD)`. **Agree.**

## Check 2 — reject spot-check (esp. the 83 "unique-but-weak")

- "Already represented (102)" sample all have real shared witnesses: `getppid`
  (3 files), `uname` (2), `getrandom` (4), `sysinfo` (3), `getrusage` (1).
  Rejections justified.
- "Unique-but-weak (83)" sample: `gettid` is in fact witnessed
  (`tkill.rs`); `getcwd`, `getdents`, `mlock`, `mlockall` are genuinely absent
  from the shared suite but the #1516 versions are success-only/no-op probes,
  so deferring the weak version is defensible (the proposal explicitly keeps
  them reconsiderable with a stronger oracle). `socket_*` variants are heavily
  covered (29 files). No high-value gap was found wrongly rejected. **Agree
  with the reject taxonomy.** Note for follow-up: `getcwd` is genuinely
  uncovered surface and a *virtualized-cwd determinism* oracle would be worth a
  future, properly-costed test.

## Check 3 — is +0.71 s sound?

Arithmetic is internally consistent (0.16 + 0.17 + 0.20 + 0.17 = 0.70, + ≤0.01
for the already-running clock cell). The methodology is correct: full-cell cost
for the four *new* ptrace verify rows, incremental-only for the clock row that
validate already runs; budgets round the five-run medians up. I did not rebuild
the corpus to reproduce the medians — the ptrace figures are plausible and
conservative. **But the total is understated on the backend axis — see Check
4.** With KEEP-1 dropped, the ptrace-first figure is ~0.70 s.

## Check 4 — collapse into 5 files / "ptrace-first" feasibility

- **Additive collapse is sound in principle** but unverifiable today: this is a
  proposal with no diff. At implementation, each of the five files must be
  *extended* (assertions added, none removed); a reviewer must confirm every
  pre-existing assertion in `clock_determinism.c`,
  `scheduler_policy_queries.c`, `pid_probe.c`, `syscall_file_metadata.c`,
  `pidfd_open_self.c` survives.

- **MATERIAL FINDING — "non-ptrace cells are gaps → zero blocking seconds" is
  false for these five rows.** Read from the manifests:
  - `scheduler_policy_queries`, `syscall_file_metadata`, `pidfd_open_self`:
    verify `backends_enabled = ["ptrace", "sabre"]`.
  - `pid_probe`: verify `backends_enabled = ["ptrace", "sabre", "liteinst"]`.
  - `clock_determinism`: verify `backends_enabled = ["ptrace", "liteinst"]`,
    and it is the only KEEP with `ci = true` (CI-gated).

  Extending these programs immediately changes the already-enabled SaBRe (and
  LiteInst) verify cells too — they run the same binary. So promotion cannot be
  cleanly "ptrace-first, non-ptrace = zero cost": either (a) the SaBRe/LiteInst
  cells also gain the new syscalls, adding real time (SaBRe ~0.43 s, LiteInst
  ~0.90 s per cell) **and** risking regression if those backends don't
  determinize `sched_getattr`, `statx`, `getgroups`, `pidfd_getfd`,
  `pidfd_send_signal`, etc. identically; or (b) the implementer narrows the
  enabled backend set, which is a coverage regression. This must be resolved —
  measured or explicitly re-gated — before any of KEEPs 2–5 land.

## Promotion instruction

Promote **KEEPs 2, 3, 4, 5 only** (16 contracts / 23 entry points), via the
shared schema-v2 TOML manifest, by additively extending the five existing
program sources. For each row: establish ptrace L2 first with JSONL duration
evidence; then for every currently-enabled non-ptrace cell (SaBRe on 2/4/5;
SaBRe+LiteInst on pid_probe; LiteInst on clock), either attach a fresh L2
measurement proving parity or add an explicit per-cell gap reason — do not
assume zero. Preserve all existing assertions in the five files. Do **not**
promote KEEP-1. Do not merge or edit PR #1516.
