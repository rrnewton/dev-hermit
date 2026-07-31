# KVM vs ptrace: full frozen verify-corpus parity sweep (B3 measurement)

**Question.** How much of the 183-row frozen ptrace `verify`-mode C corpus does
the reverie-kvm backend match today, and which cells can be enabled as a green
manifest increment?

**Method.** For every `verify`-mode C guest in `tests/e2e/manifests/*.toml`:
1. Compile with the cell's declared `cflags`/`extra_sources`.
2. Run `hermit run --strict` (ptrace) and `hermit run --backend kvm --strict`;
   compare exit status + stdout SHA-256.
3. Run `hermit run --backend kvm --strict --verify` (L2 bitwise) under the exact
   e2e harness env (`LC_ALL=C TZ=UTC`, per-cell `HOME`/`XDG_CONFIG_HOME`/
   `E2E_TMPDIR`, `--log=info`).

**Results.** 105/183 (57.4%) raw KVM==ptrace exit+stdout parity; 63.3% excluding
17 rows where ptrace itself fails; ~108/183 (~59%) correcting for 3 guest_args
cells the no-args sweep misfired. Crosses the 50% B3 stdout/exit threshold.

102 cells are parity-clean AND pass KVM L2 but were manifest-disabled for KVM.
The landed increment (PR #1188) enables the 93 `ci=false` ones (KVM verify
coverage 7 -> 100); 93/93 pass faithful-harness L2, real `ci/test_harness.sh`
spot-check 16/16.

**Scope caveat.** This measures the exit+stdout + backend-local-L2 dimension.
A formal B3 claim additionally needs L3 memory determinism
(`--detlog-stack --detlog-heap`) and stderr/semantic cross-backend match.

**Files.** `results.tsv` (per-cell verdicts), `enabled_candidates_ci.tsv`
(the 102 candidates with ci status), `metadata.json` (SHAs, host, tallies).

**Reproduce.** Scripts used live in the worktree at
`worktrees/kvm/hermit/ignored/` (`kvm_b3_par.sh`, `dump_verify_c.rs`,
`kvm_enabled_status.rs`, `enable_kvm_verify.rs`).
