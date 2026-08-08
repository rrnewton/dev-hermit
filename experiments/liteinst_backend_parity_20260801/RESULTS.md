# Result

Run `liteinst-spst-1785620995` measured landed Hermit
`464cbd9f9bb43d5505c914783819e1d349630283` with pinned Reverie
`aa6f1283aeee3efd174c57f6dd8198310bd307e1` on
`devbig014` (Linux 6.18.39, x86-64). The Hermit checkout was
clean.

| scope | denominator | LiteInst parity | LiteInst DETLOG determinism |
| --- | ---: | ---: | ---: |
| dynamic ELF, single process, single thread | 20 | 18/20 (90.0%) | 19/20 (95.0%) |

All 20 ptrace reference cells passed L2. LiteInst ran all 20 cells; parity was
measured against ptrace for all 20.

Confirmed non-green cells:

- `heap_growth`: LiteInst returned `sbrk: Cannot allocate memory`, so its guest
  exit did not match the ptrace reference and the cell does not reach L2.
- `virtual_clock`: LiteInst is self-deterministic at full DETLOG L2, but its
  strict stdout hash differs from ptrace, so parity is false.

The three excluded matrix rows are outside the stated scope, not failures:
`pthread_lifecycle`, `process_wait_accounting`, and
`process_wait_lifecycle`. `random_sources` uses the matrix's `--root-only`
single-thread contract.

The canonical renderer confirms the appended Hermit B2 scorecard cell:

```text
bucket                   ptrace  liteinst_parity_pct  liteinst_det_pct  liteinst_parity_measured  liteinst_ran
backend-parity-spst      20      90.0                 95.0              20/20                     20/20
```

An auxiliary `validate.sh --liteinst-compat-only` run built both release
artifacts successfully, then was stopped after the unrelated utility-corpus
`fold` case remained live for more than two minutes. The bounded backend-parity
measurement above completed independently and is the evidence used for the
scorecard; the interrupted auxiliary run is not reported as a green gate.
