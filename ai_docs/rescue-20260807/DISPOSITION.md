# Disposition — 30 dirty children + 84 parent `rescue/*` branches

## RESCUED into PRs (5)

Per-backend **linear coalescable stack**, not a scatter against master.

| PR | Branch | Base | Unit | Evidence |
| --- | --- | --- | --- | --- |
| hermit **#1916** | `rescue/parity-harness-strict-comparator` | `main` | w11flock: pass `--verify-strict` so the double-run comparison is bitwise | measured `bitwise_parity:true, strictness:canonical, 112\|112`; ptrace 28/28, dbi 27/28 unchanged |
| hermit **#1917** | `rescue/parity-c-instruction-nondeterminism` | #1916 | w21: CPUID/RDTSC/RDRAND/RDSEED parity cell + planted negative | `ci/test_harness.sh validate` EXIT 0 — 315 tests, 83 cells, plan/inventory/DAG in sync |
| hermit **#1918** | `rescue/dbi-fchown-identity` | #1917 | dbi-fchown: determinize chown/fchown/lchown/fchownat via virtual-root mapping | `cargo test -p detcore-dbi --lib` 15/15; **measured** `RATCHET dbi 27/28 (96.4%)`, `PASS dbi/file_metadata 3/3` |
| hermit **#1925** | `rescue/validate-emit-coverage-node` | `main` | re-opened `fc49593ac` (PR #1639 CLOSED, never merged; main still schema 4) | `bash -n` OK; helper emits schema 5 + exact 4-key `CoverageRow` shape, `planned=19` |
| reverie **#407** | `rescue/clone-with-stack-catch-unwind` | `main` | kvm/reverie: contain a cloned child's panic instead of aborting | 1/1 pass; **bracketed** — reverting the block reproduces `died by signal 6 ... status=0x86` |

## PRESERVED on the remote, deliberately NO PR (1)

| Branch | Reason |
| --- | --- |
| `rescue/liteinst-zero-ptracer-consumer` (w25) | Compiler-verified cross-repo block: `error[E0425]: cannot find value PROCESS_FORK_ENV in crate reverie_liteinst`. That symbol is absent from reverie main and lives on reverie **PR #405** (open). This is #405's hermit-side consumer; it cannot compile until #405 lands and the pin advances. Opening a permanently-red PR would add drain burden, so the branch is pushed for durability and the dependency routed instead. Its original Cargo pin churn to `f6a7e724` (NOT an ancestor of reverie main) was excluded. |

## DROPPED with reason (7 units)

| Unit | One-line reason |
| --- | --- |
| `fork330/reverie` | Delta is entirely self-labelled `DIAGNOSTIC-ONLY probe ... Remove before land` writing to a hardcoded `/tmp/probe.log`; its 9 "unpushed" commits are all already contained in three pushed origin branches. |
| `kvmcompat/reverie` | Self-labelled `MEASUREMENT REVERT (kvm-compat-ratchet, NOT for PR)` — comments out a default-flip to measure the KVM startup livelock. |
| `kvmcompat/hermit`, `e9patch/hermit`, `250-delegate/hermit` | Self-labelled `MEASUREMENT-ONLY patch ... NOT for PR` `[patch]` sections repointing reverie at local checkouts. |
| `pcfix/hermit` | Delta is self-labelled `TEMPORARY DIAGNOSTIC -- do not commit`; its 5 real commits are already published on open PR #1757. |
| `kvm/hermit` | The superseded earlier WIP of the coverage-node change; the pushed, reviewed `fc49593ac` was rescued instead as #1925. |
| 8 × `M agent-utils` gitlink drift (`226`, `250`, `codex-reviewer`, `e9bp`, `ghdag`, `lander2`, `liteinst`, `staging-drain`) | Submodule gitlink drift, not authored work. |
| 4 × untracked residue (`regress/reverie` `.herdr-run/`, `perf/hermit` `.hermit-validate-ledger.jsonl`, `egress-probe/hermit` `sb/`, `227b/reverie` `.dynamorio-source.lock`) | Runtime/build residue. |
| 82 sha-named parent `rescue/*` branches | fsck/orphan-commit sweeps of parent history; no product work. |

## NOT TOUCHED — live owner (3)

Verified by file mtime *during* this session; Hard Invariant 5.

| Slot | Evidence |
| --- | --- |
| `envhash/hermit` | Edited **09:52 today**, mid-session; branch SHAs rewritten while the rescue ran; work published on `origin/codex/env-var-hash-info-log-v2` (PR #1559, closed — its owner's to re-open). |
| `val1147/hermit` | Edited **09:48 today**. SaBRe corpus re-qualification with unresolved conflict markers and unmeasured 131→132 corpus claims. |
| `slot01/hermit`, `vselect/hermit` | Stale 3 days but left unrescued — see below. |

## NOT RESCUED, judged not worth it (2)

| Unit | Reason |
| --- | --- |
| `slot01/hermit` | `matrix.tsv` is a static snapshot of parity numbers that `run_matrix.py` computes live; the tree is 122 commits behind with conflict markers, and its numbers directly contradict the measured values in #1918. Publishing a stale duplicate table is deck-chair shuffling. |
| `vselect/hermit` | `validate.sh` +318 novel lines with unresolved `UU` conflict markers, 70 commits behind, on the most-churned file in the repo. Reconstructing it is a rewrite, not a rescue; the protected patch is retained for whoever owns smart-selection. |

## Separate defect found while measuring (reported in #1916, not fixed)

`run_matrix.py --output <path>` crashes on current main:
`ValueError: dict contains fields not in fieldnames: 'evidence'` — `write_results`
uses a fixed `DictWriter` fieldname tuple while the result dict carries an
`evidence` key added at line 1247. Present in `origin/main`; no rescued commit
touches either.
