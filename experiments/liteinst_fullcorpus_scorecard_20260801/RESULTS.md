# LiteInst full-corpus result

Run `liteinst-fullcorpus-1785621912` measured the 200 manifest tests that
declare a ptrace `verify` cell at Hermit
`464cbd9f9bb43d5505c914783819e1d349630283` and Reverie
`aa6f1283aeee3efd174c57f6dd8198310bd307e1` on
`devbig014` (Linux 6.18.39, x86-64). The checkout was clean.

## Scorecard cells

| Scope | parity vs ptrace | LiteInst DETLOG determinism |
|---|---:|---:|
| Full ptrace-verify denominator | 107/200 (53.5%) | 117/200 (58.5%) |
| Measured dynamic-ELF SP/ST scope | 107/128 (83.6%) | 117/128 (91.4%) |

The 72 unmeasured cells are explicit skips: 50 exceed the single-process or
single-thread envelope, 6 are static ELF, and 16 could not be classified
because the ptrace reference failed. These skips remain in the full-corpus
denominator and are not presented as measured failures.

Parity here means successful exit plus byte-identical stdout against the
ptrace strict reference. Determinism is L2: `--backend liteinst --strict
--verify` completed and emitted the `Determinism verified` witness. This run
does not claim stderr/artifact parity, L3 heap/stack equality, static ELF,
multiprocess, or multithread support.

## Honest maturity assessment

LiteInst remains **B2 (hybrid)**. The raw full-denominator parity cell crosses
50%, but B3 is not earned: B2.4's full declared envelope is incomplete, 72
cells are outside or unclassified, and the parity comparison is exit+stdout
rather than the stronger stderr/artifact/semantic and L3 evidence required by
the maturity model.

## Measured gaps

Eleven in-scope cells failed both parity and DETLOG verification:

- `applications/timed-progress-bar`
- `c-programs/arch-prctl-determinism`
- `c-programs/dbi-execveat-unsupported`
- `c-programs/record-replay-fd-close`
- `c-programs/socket-timestamp-edge-cases`
- `language-runtimes/gawk-random`
- `language-runtimes/python-hash-determinism`
- `language-runtimes/python-random`
- `system-utils/openssl-passwd`
- `system-utils/proc-uptime`
- `system-utils/random-device`

Ten additional cells were L2 self-deterministic but differed from ptrace
stdout/exit: `print-memaddrs`, `proc-fd-link-aliases`, `proc-fdinfo`, the three
socket-cookie probes, the three socket-timestamp probes, `sysinfo`, and
`clock-determinism`.

The dominant diagnosed gaps are the intentionally unsupported post-start
`exec` lifecycle, time/identity virtualization differences, entropy consumers
that cross an interpreter `exec`, and topology/static-ELF exclusions. The 16
ptrace-reference failures are preserved row-by-row in `results/results.csv`
and are not charged to LiteInst.
