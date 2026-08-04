# DBI/DBT backend — honest B-level over the ptrace `--strict --verify` corpus

**Date:** 2026-08-03 · **Agent:** hermit-dbi (opus-4.8)
**Hermit:** `9761a4ac` (branch `work/fix-1200-codex-review`, PR #1200) ·
**Reverie pin:** `d973a85b` · **Host:** devbig014 (kernel 6.18.39, 316 cores)

## Question

Standing owner directive: *"measure REAL DBI coverage honestly and state the
B-level accurately."* The unrelaxed bar is that **PASSING means
`hermit run --strict --verify` produces bitwise-deterministic output** — anything
with relaxation flags does not count.

Per `backend-reality-reviewer` scoring, **B3 = passes ≥50% of the ptrace
strict-verify corpus; B4 = 100%.** So the honest B-level is a measured ratio
whose **denominator is the set of programs ptrace itself passes `--strict
--verify`**, not the manifest's DBI-enabled claim.

DynamoRIO is dynamic binary **translation (DBT)**; "DBI" is the current backend
name (a `dbi -> dbt` rename is planned but gated on zero open PRs — not started).

## Method

Corpus: `tests/e2e/manifests/c-programs.toml` — 159 C guests, all with
`verify` mode `ptrace`-enabled. For every program, ran `verify` mode under both
`ptrace` and `dbi` via `ci/test_harness.sh` (manifest-enabled cells with
`--include-manual`; the 115 manifest-DBI-disabled cells forced with
`--probe-disabled`, so the DBI ceiling is measured beyond the manifest's claim).
`PASS` = harness verdict PASS = two runs bitwise-identical and observations match.
Full driver: `sweep.py`; per-cell raw logs/jsonl retained in the slot's
`ignored/dbi-b3-sweep_20260803/`.

## Result

| Metric | Value |
|---|---|
| c-programs total | 159 |
| **ptrace `--strict --verify` PASS (B3 denominator)** | **152 / 159** |
| DBI PASS within the ptrace-passing corpus | **130 / 152** |
| **DBI B-level (B3 ratio)** | **85.5 %** |
| Manifest DBI-enabled cells passing | **44 / 44 (100 %)** |
| DBI PASS on programs ptrace does *not* pass | 2 (`dbi-pid-virtualization`, `dbi-wait-lifecycle`) |

**DBI is a solid, real B3 at 85.5 %** — well above the 50 % B3 floor, 22
programs short of B4. It is **not a mockup and not merely B2**: `/bin/true` and
130 real C guests run through the shared `Detcore<DbiGuest>` tool in-process
(`reverie-dbi: tool=Detcore ... memory_hash=... | ...` matched across two runs).
See the companion note refuting the "B1 mockup / pseudo-Detcore" premise.

Two honesty checks that matter:
- **The manifest does not overclaim.** All 44 DBI-enabled cells pass, and *every
  one* of the 22 gaps is a program the manifest currently marks DBI-**disabled**.
  The manifest's DBI coverage is conservative and accurate as of this SHA.
- **Zero hangs under the outer 100 s wrapper.** This binary carries PR #1200's
  always-defer admission fix, which removes the tentative-pop poison hang on
  async backends. (Four gaps still hit the harness's own internal timeout — see
  below — so "no async-poison hang" is not "nothing is slow".)

## The 22 B3 → B4 gaps (all manifest-DBI-disabled)

Classified by DBI verify exit status:

| Class | Count | Programs |
|---|---|---|
| Timeout / hang-class (status 124) | 4 | `fp-reduction-nondeterminism`, `pselect6-simulation`, `sigtimedwait-no-timeout`, `writev-determinism` |
| Fail-closed negative test (status 101) — **expected** | 1 | `dbi-unsupported-syscall` |
| Execution error (status 2) | 4 | `epoll-determinism`, `mmap-determinism`, `record-replay-lseek-seek-cur`, `thread-sync-determinism` |
| Verify divergence / observation mismatch (status 1, 40) | 13 | `arch-prctl-determinism` (40); `get-robust-list-child`, `pidfd-waitid-child`, `proc-locks`, `ptrace-attach-eperm`, `ptrace-seize-eperm`, `resource-determinism`, `sigpipe-siginfo`, `so-incoming-cpu-tcp4`, `so-incoming-cpu-tcp6`, `tcp-info-accept4`, `tcp-info-accept6`, `tcp-info-client4` (1) |

Structurally-inapplicable-to-DBI subset (fold out for an "applicable ceiling"):
`dbi-unsupported-syscall` (fail-closed by design) and `record-replay-lseek-seek-cur`
(record/replay is unsupported by DBI). Excluding those two, the applicable
ceiling is **130 / 150 = 86.7 %**.

Notable clusters for a future ratchet (only after the `factor-thirdparty-backends`
packaging change lands — **not ratcheted here per owner directive**):
- **KVM-parity socket syscalls** (5): `so-incoming-cpu-tcp4/6`, `tcp-info-accept4/6`,
  `tcp-info-client4` — canonicalization landed for KVM (#345/#350) but DBI lacks it.
- **ptrace-on-guest** (2): `ptrace-attach-eperm`, `ptrace-seize-eperm`.
- **Slow/preemption-sensitive** (4 × status 124) — consistent with the DBI
  in-process preemption ceiling documented elsewhere.

## The 7 ptrace non-passes (excluded from the B3 denominator)

`dbi-pid-virtualization`, `dbi-wait-lifecycle` (DBI-specific tests ptrace is not
expected to pass — DBI passes both), plus baseline ptrace gaps
`ipc-determinism`, `liteinst-advanced`, `nanosleep-threads-simple`,
`signal-determinism`, `socket-ioctl-timestamp`. These bound the ptrace baseline
at 152/159 on this host — the ptrace corpus is not itself 100 %.

## Interpretation

- **State the B-level as B3 (85.5 % of the measured ptrace strict-verify
  corpus), not B4, and not a bare "132 pass".** The denominator is the
  ptrace-passing set (152), measured here, not the manifest claim.
- Coverage ratcheting is deliberately **withheld**: the DBT compat expansion is
  sequenced *after* `factor-thirdparty-backends-into-separate-packages`
  (hermit-247). This experiment measures; it changes no manifest and opens no
  coverage PR.
