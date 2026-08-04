# Backend-parity scorecard vacuity audit — NO-IMPL-CHANGE slice

**Run UTC:** 2026-08-04T13:49:15Z
**Auditor:** claude subagent (measurement/enumeration only; NO product source changed)
**Target ledger:** `hermit/tests/backend-parity/matrix.tsv` (the hand-authored cross-backend
parity CONTRACT — the correct vacuity target; see below re: parent CSVs).
**Question:** did a scorecard cell flip GREEN (gap→pass / new-pass row / L2 gap→detlog/guest)
in a commit that made NO backend-impl change (only tests / matrix / run_matrix.py / fixtures)?

---

## Why matrix.tsv, not the parent compat-envelope CSVs

`dev-hermit/compat-envelope/{scorecard,fullcorpus-scorecard}.csv` ARE git-tracked, but every
commit touching them is a re-measurement run (`95585fa Record...`, `03d5938 ... measured
ptrace/KVM/LiteInst`, `1490bbb populate DBI/SaBRe/e9patch columns`, `54db83f Measure
LiteInst...`). They are collector OUTPUT, regenerated per run — a cell "flip" there is a new
measurement, not a hand-asserted green. So the "flip green with no impl change" signal is
vacuously true for ALL of them by construction and carries no information.

`matrix.tsv` is different: `run_matrix.py::read_matrix()` treats it as the **golden INPUT
contract** (the expected pass/gap per cell); observed results go to a separate `--output` TSV.
A `pass` cell here is an ASSERTION a gate must MEET. This is where a test-only PR can flip a
cell green. **matrix.tsv is the audit target.**

---

## Denominator — green-flip commits in the live matrix.tsv history

`git -C hermit log --oneline --follow -- tests/backend-parity/matrix.tsv` (post-move location;
--follow tracks across `251dba15 Move experiments out of Hermit`). A green-flip = ≥1 cell moved
to pass / a new all/partly-pass row / an L2 cell gap→{detlog,guest}. `967abd99` is EXCLUDED from
the numerator (it is a green→gap **regression**, cited below as evidence).

**Classification key:** A = flip accompanied by real backend impl source
(`detcore*/src/**.rs`, or a Reverie pin bump for KVM). B = flip touching ONLY
tests/matrix/README/run_matrix.py/fixtures/validate.sh (no backend determinization code).
`hermit-cli/src/bin/hermit/{run,backends}.rs` verify-path plumbing counts as impl for the
DBI-verify case (#1553) since it changes what the double-run does.

| # | SHA | subject | cell(s) flipped green | class | impl files in commit |
|---|-----|---------|-----------------------|-------|----------------------|
| 1 | 8f656b4d | Thread --verify-allow through DBI verify path (#1553) | exit_status dbi_l2 gap→detlog | **A** | hermit-cli/src/bin/hermit/run.rs, backends.rs |
| 2 | d096c20c | consolidate signal-family fixtures (signal-wave) | signal_disposition, sigaction_state, sigprocmask_state, sigaltstack_state NEW (ptrace+dbi pass; kvm gap) | **B** | none (fixtures/*.c, matrix, README, run_matrix.py) |
| 3 | 54bb966e | add scheduler-policy-queries contract | scheduler_policy_queries NEW all-pass (L2 detlog/detlog/guest) | **B** | none (matrix, README, run_matrix.py) |
| 4 | 82a8e853 | add L2 (--verify) ratchet to matrix | L2 columns added: ~19 `dbi_l2=detlog` + ~17 `kvm_l2=guest` + ptrace_l2 | **B** | none (matrix, README, run_matrix.py) |
| 5 | 93966768 | ratchet KVM metadata and wait accounting | file_metadata **kvm gap→pass**; process_wait_accounting NEW pass | **B** | none (matrix, README, run_matrix.py, tests/c/dbi_wait_lifecycle.c) |
| 6 | 8a7803cf | enforce DBI parity under strict mode | pthread_lifecycle **dbi gap→pass** — LATER REVERTED | **B** | none (matrix, README, run_matrix.py) |
| 7 | b3500a31 | KVM: ratchet Reverie tool compatibility (#1013) | random_sources kvm gap→pass | **A** | detcore/src/syscalls/{memory,threads}.rs, detcore/src/tool_local.rs, detcore-model/src/config.rs |
| 8 | 21e91ff3 | Ratchet listmount compatibility (#968) | listmount_unavailable NEW all-pass | **B** | none |
| 9 | 8813c13d | Ratchet process_vm_writev refusal (#962) | process_vm_writev_refusal NEW all-pass | **B** | none (+fixture .c) |
| 10 | 6a17c3ed | Ratchet process_vm_readv refusal (#953) | process_vm_readv_refusal NEW all-pass | **B** | none (+fixture .c) |
| 11 | 6f639267 | Ratchet io_uring fallback (#947) | io_uring_fallback NEW all-pass | **B** | none |
| 12 | 805d8f47 | Ratchet file metadata (#940) | file_metadata NEW (ptrace/dbi pass; kvm gap) | **B** | none |
| 13 | 1d06012f | Ratchet file mutation (#929) | file_mutation NEW all-pass | **B** | none |
| 14 | 9b509f97 | Ratchet shared anonymous mmap (#920) | shared_anonymous_mmap NEW all-pass | **B** | none |
| 15 | d211d989 | Ratchet anonymous mmap layout (#900) | anonymous_mmap_layout NEW all-pass | **B** | none |
| 16 | 87be9ed9 | Ratchet heap growth (#893) | heap_growth NEW all-pass | **B** | none |
| 17 | 10e43392 | Ratchet memory advice (#885) | memory_advice NEW all-pass | **B** | none |
| 18 | 2d7de5ee | Ratchet executable mmap (#878) | executable_mmap NEW all-pass | **B** | none (+hermit-cli/tests/cli.rs test) |
| 19 | a03f94e8 | Ratchet DBI process wait lifecycle (#871) | process_wait_lifecycle NEW (ptrace/dbi pass; kvm gap) | **B** | none |
| 20 | 42ef2b8b | Determinize root random sources under DBI (#849) | random_sources dbi gap→pass | **A** | detcore-dbi/src/lib.rs |
| 21 | b20ffbce | Raise DBI parity floor to eight contracts (#833) | virtual_clock **dbi gap→pass**, virtual_pid **dbi gap→pass** | **B** | none (matrix, README, validate.sh) |

### DENOMINATOR

**Y = 21 green-flip commits sampled** (full live matrix.tsv history, not a sub-sample).
**n = 18 (86%) flipped ≥1 cell green with NO backend impl change** (type B).
**m = 3 impl-backed** (type A): 8f656b4d (#1553), b3500a31 (#1013), 42ef2b8b (#849).

Genesis commits in the retired `experiments/backend-parity_20260722/matrix.tsv` path
(pre-move): 5b0e085b (seed, B), 53a77aea (#588 KVM, B), c0b1a19a (#693, B), 2b41116e (#743,
**A** — detcore-dbi/src/lib.rs), 0087502f (#835, B). Adding these: 26 total, 22 type-B, 4
type-A. Headline uses the live-location 21 to avoid double-counting the pre-move history.

---

## Confirming question: "does the cell fail if the backend does nothing?"

Two distinct vacuity modes. Cite `run_matrix.py` (current HEAD) + `ci/dag/portable.json`.

### Mode (a) — STRUCTURAL vacuity: green not bound to any always-on gate — ESTABLISHED

The always-on portable CI gate runs backend-parity as **DBI-only, L1-only**:
`ci/dag/portable.json:451` → `run_matrix.py --hermit target/release/hermit --backend dbi
--strict --require-backend` (no `--backend kvm`, no `--verify`). No GitHub workflow anywhere
runs `--backend kvm`. `validate.sh` (line ~1665-1678) adds `--backend kvm` ONLY when
`/dev/kvm` is readable+writable, else `note_backend_skip "KVM"`, and it passes
`--probe-gaps --require-backend` but **never `--verify`**.

`run_matrix.py::backend_block` (lines 358-361) returns `"/dev/kvm is not readable and
writable"` for KVM on a host without it; `main()` (lines 774-776) then prints `BLOCKED kvm`
and only counts it as a failure under `--require-backend`. Portable CI never selects kvm, so
KVM cells are simply never probed there.

**Consequence — two large classes of green cells asserted in matrix.tsv are exercised by NO
standing gate:**
- **ALL `kvm=pass` cells** (~19 rows) — only checkable on a privileged /dev/kvm host, never in
  always-on CI. Test-only KVM flips in this slice: **file_metadata kvm** (93966768, prior reason
  was the impl gap "KVM personality does not implement extended-attribute syscalls").
- **ALL L2 cells** — `dbi_l2=detlog` (~19) and `kvm_l2=guest` (~17), added test-only by
  **82a8e853**. `--verify` (the L2 lift) is invoked by NO standing gate (portable.json is
  `--strict` only; validate.sh omits `--verify`). These L2 greens are pure contract assertions
  validated only in the one-off `experiments/backend_parity_matrix_l2_verify_20260801/` run.

The cells COULD fail if run (see mode (b)); the point is the green is not currently bound to a
recurring gate — a Proxy-Binding gap (green not carrying "which gate verified it").

### Mode (b) — SEMANTIC vacuity ("cannot fail even when run") — NOT FOUND, one PROVEN-UNSTABLE

For cells the gate DOES run (DBI L1), `run_case` (run_matrix.py ~lines 588-655) performs real
checks: exit-status match vs `expected_status`, exact-stdout match vs `expected_stdout` OR a
required marker substring, run1==run2==run3 determinism (`baseline` compare), and for
`random_sources` a ptrace-equality compare of the root random stream. A no-op backend that
produced divergent output WOULD fail. So the exercised DBI cells are **not** cannot-fail-vacuous.

**Empirical proof a test-only DBI flip CAN be wrong:** `8a7803cf` flipped `pthread_lifecycle
dbi gap→pass` with no impl; `967abd99 "tests: keep unstable DBI pthread case as gap"` reverted
it to gap (`-pthread_lifecycle pass pass pass` → `+pthread_lifecycle pass gap pass ...
DynamoRIO can stall or exit during native pthread startup`). The green did not hold — a
concrete instance of a no-impl flip that was not real.

**Suspects that flipped from an impl-gap reason to pass with no in-commit impl** (i.e. the
"mechanism already worked" branch must be affirmatively confirmed, not assumed):
- **b20ffbce** virtual_clock/virtual_pid dbi gap→pass — prior reasons were impl gaps ("DBI ...
  does not route clock calls through Detcore virtual time" / "DBI uses the host process
  identity and has no Detcore PID model"). Likely enabled by the earlier DBI thread-registration
  impl (#743 / 2b41116e, detcore-dbi/src/lib.rs) — plausibly genuine "already worked," but the
  flip commit itself carries zero impl. Fixture (`virtual_clock`) requires marker `clock matrix
  success\n` + run1==run2, so it is not cannot-fail; it IS exercised in the DBI portable gate.
- **93966768** file_metadata kvm gap→pass — prior reason was the impl gap "KVM personality does
  not implement extended-attribute syscalls, so setxattr returns ENOSYS." KVM impl lives in the
  Reverie submodule, not this hermit commit; and KVM is not run in always-on CI (mode a).

---

## Verdict

- **ESTABLISHED (cite: commit stats above + ci/dag/portable.json:451 + run_matrix.py:358-361,
  774-776 + validate.sh:1665-1678):** 18/21 (86%) live matrix.tsv green-flips changed no backend
  impl. The dominant vacuity is STRUCTURAL — all KVM cells and all L2 cells are asserted green in
  the contract but exercised by NO always-on CI gate (portable gate = DBI/L1 only; KVM needs
  /dev/kvm; L2 needs --verify which no gate passes).
- **ESTABLISHED (cite: 8a7803cf + 967abd99 matrix diff):** at least one test-only DBI green flip
  (pthread_lifecycle) did not hold and was reverted — proof no-impl flips are not automatically safe.
- **HYPOTHESIS:** the DBI-exercised no-impl flips (virtual_clock, virtual_pid, and the "Ratchet X
  compatibility" new-contract rows) are legitimate "mechanism already worked" ratchets — the
  fixtures are real comparisons (not cannot-fail), but confirming each requires either a green
  DBI portable-gate run at HEAD or bisecting to the enabling impl commit. Not done here
  (measurement/enumeration only; no B-level re-derivation).

## Reproduction

```
git -C hermit log --oneline --follow -- tests/backend-parity/matrix.tsv
git -C hermit show <sha> -- tests/backend-parity/matrix.tsv      # cell diff
git -C hermit show <sha> --stat                                  # impl vs test-only
sed -n '338,362p;735,830p' hermit/tests/backend-parity/run_matrix.py
grep -n run_matrix hermit/ci/dag/portable.json hermit/validate.sh
```
