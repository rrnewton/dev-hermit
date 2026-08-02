# backend-parity matrix L1 -> L2 lift (add --verify)

## Question

The authoritative cross-backend parity source
`hermit/tests/backend-parity/matrix.tsv` + `run_matrix.py` ran every contract at
**L1 only** (`hermit run --strict`, three times, byte-identical stdout) and, per
the runner's own note, "does not pass `--verify`". The 2026-08-01 backend
maturity report flagged this as the gap keeping the matrix at L1 rather than L2.

This experiment adds a `--verify` mode to the runner, measures which contracts
actually reach L2 (`hermit run --strict --verify`) on each backend, and encodes
the measured L2 status as a second ratchet in `matrix.tsv`.

## Method

- Base: hermit `origin/main` @ `2f3689bd8830ab6b59dacea6cb72951f4d0d899e`,
  branch `codex/backend-parity-matrix-l2-verify`. No reverie change.
- Host: `Linux 6.18.39 x86_64`, gcc 11.5.0, `/dev/kvm` present read-write.
- `run_matrix.py --verify` invokes
  `hermit run --strict --verify --verify-allow both ...` per contract. hermit
  runs the guest twice internally and compares. `--verify-allow both` keeps the
  guest's own exit status (including the deliberate non-zero `exit_status`
  contract) flowing through.
- `--verify` diverts the guest's stdout into per-run temp logs, so the L2 path
  cannot re-check stdout. It instead enforces (a) exit status parity and (b) a
  determinism witness on stderr, keyed by kind.

## Two L2 assurance kinds (not interchangeable)

- **DETLOG-bitwise** (`Determinism verified`): ptrace and DBI. hermit found the
  two normalized DETLOG streams — the full syscall + scheduling trace —
  bitwise-identical. Full L2.
- **guest-visible** (`guest output and exit status matched`): KVM. reverie-kvm
  runs concurrently and declares its internal syscall trace order
  nondeterministic, so `--verify` compares only guest stdout + exit. Strictly
  weaker; KVM is capped at this kind and can never record `detlog`.

## Results

| Backend | L1 | L2 verified | L2 kind | L2 gaps |
| --- | ---: | ---: | --- | --- |
| ptrace | 23/23 | 23/23 | DETLOG-bitwise | none |
| DBI | 22/23 | 21/23 | DETLOG-bitwise | `exit_status`, `pthread_lifecycle` |
| KVM | 22/23 | 21/23 | guest-visible | `process_wait_accounting`, `process_wait_lifecycle` |

All three `--verify` runs exit 0 (the two per-backend gaps are recorded in
`matrix.tsv` with reasons, so they are expected `GAP`s, not failures). Raw
per-contract observations: `results-{ptrace,dbi,kvm}-l2.tsv`.

### Two contracts hold at L1 but not L2 (both new, both honest gaps)

- **`exit_status` on DBI.** Under `--verify-allow both`, hermit runs the DBI
  guest only **once** when the first run exits non-zero — it never starts the
  second run — so the double-run DETLOG comparison never executes for the
  non-zero-exit contract. ptrace performs both runs and reaches `detlog`. This
  is a limitation of the DBI `--verify` path for non-zero exits, not a matrix
  bug. Reproduced directly:
  `hermit run --backend dbi --strict --verify --verify-allow both -- /bin/sh -c 'exit 23'`
  prints only `:: DBI Run1...` (no Run2, no comparison) and exits 23.
- **`process_wait_accounting` on KVM.** The `--verify` concurrent double-run
  races child reaping: `waitid` on the already-reaped child returns `ECHILD`
  ("No child processes"), so the second run exits non-zero and verification
  fails. reverie-kvm synchronizes `wait4` child state but not `waitid`.
  Reproducible 3/3. L1's stdout-only three-run check does not surface it — this
  is exactly the value of the L2 lift (it exposed a real KVM `waitid`
  child-sync nondeterminism the L1 gate missed).

The other two gaps are inherited from pre-existing L1 gaps (an L1 gap cannot be
verified at L2 by definition): DBI `pthread_lifecycle` (DynamoRIO startup stall)
and KVM `process_wait_lifecycle` (no guest SIGCHLD frame synthesis).

## Reproduction

```bash
cd hermit
python3 tests/backend-parity/run_matrix.py --check              # schema + both ratchets
python3 tests/backend-parity/run_matrix.py --backend ptrace --verify --require-backend
python3 tests/backend-parity/run_matrix.py --backend dbi    --verify --require-backend
python3 tests/backend-parity/run_matrix.py --backend kvm    --verify --require-backend
# L1 CI gate unchanged and still green:
python3 tests/backend-parity/run_matrix.py --backend dbi --strict --require-backend
```

## Interpretation

The matrix now carries a machine-checked L2 ratchet alongside its L1 ratchet.
ptrace is fully DETLOG-L2 (23/23); DBI is DETLOG-L2 on 21/23; KVM is
guest-visible-L2 on 21/23. No false parity: KVM's weaker assurance is recorded
as a distinct kind, and the two L1-but-not-L2 contracts are explicit gaps with
reproducible reasons. Follow-ups for a future round: fix the DBI `--verify`
non-zero-exit single-run path (would promote DBI `exit_status` to `detlog`), and
synchronize KVM `waitid` child state (would promote KVM `process_wait_accounting`
to `guest`).
