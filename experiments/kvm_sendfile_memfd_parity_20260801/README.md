# KVM sendfile / memfd_create parity batch — 2026-08-01

## Question

Can the KVM backend match the ptrace golden reference on the `c-programs`
corpus cells that exercise `memfd_create(2)` and `sendfile(2)`? Before this
batch these cells returned `EBADF` under KVM while passing under ptrace.

## Change under test

reverie PR https://github.com/rrnewton/reverie/pull/322
(branch `codex/kvm-sendfile-memfd-parity`, reverie SHA
`59bb618aaf22d246094a3ec193cbfce92c8f9803`), consuming Hermit built via a
TEST-ONLY local cargo patch (reverted after measurement). See PR for the
three-part change: `memfd_create` handler, `sendfile` handler with a
stdout-mediated path, and the `mutates_file_table` shared-table write-back
root-cause fix.

## Method

Backend: `--backend=kvm --no-virtualize-cpuid --max-timeslice=disabled`,
`LC_ALL=C TZ=UTC`, hermit debug binary. Golden reference: ptrace. Each cell run
under `--strict --verify`. KVM `--verify` reports "Success: KVM guest output and
exit status matched" (guest-visible L2); ptrace `--verify` reports byte-identical
DETLOG (L2).

## Results

| corpus cell | ptrace --verify | KVM --verify (before) | KVM --verify (after) |
| --- | --- | --- | --- |
| `c-programs/record-replay-fd-close` | pass | fail (EBADF, exit1) | **pass** |
| `c-programs/remap-file-pages-memfd-enosys` | pass | fail (EBADF, exit1) | **pass** |
| `c-programs/record-replay-file-state` | diverge (ptrace-verify-fail-exit1) | fail | still fail (FICLONE gap) |
| `c-programs/syscall-quick-wins` | pass | fail | still fail (close_range ENOSYS) |

Supplemental synthetic (`min_sendfile`, regular-file → STDOUT, explicit + NULL
offset): native, ptrace `--strict`, KVM `--strict` all emit identical
`payload\npayload`; KVM `--strict --verify` = Success.

## Interpretation

Two KVM corpus cells flip to parity (both ptrace-green, so both count toward the
parity denominator). Against the last recorded KVM full-corpus baseline
(det 130/200 = 65%, parity 112/184 = 61%):

- det: 130 → **132/200 = 66%** (+1.0 pt)
- parity: 112 → **114/184 = 62%** (+1.1 pt)

Root cause was NOT a missing handler alone: the KVM executor discarded the
newly-allocated descriptor because `SYS_memfd_create` was absent from
`mutates_file_table`, so detcore's injected `fstat` could not resolve the fd.

Honest remaining gaps in this bucket (out of scope, not regressions):
- `syscall-quick-wins`: `close_range(2)` still `ENOSYS` under KVM — separate syscall.
- `record-replay-file-state`: `FICLONE` ioctl unimplemented under KVM (guest
  prints `clone unsupported`); this cell also fails ptrace `--verify` in the
  corpus, so it is not parity-eligible regardless.

## Reproduction

Test cells live in `hermit/tests/` (built into the corpus). The scorecard
authoritative rows will reflect these flips once PR #322 lands, the Hermit
reverie pin bumps to the landed SHA, and `compat-envelope/collect-fullcorpus.sh`
re-runs the KVM lane (coordinated with the manifest-cli/235 lane). Do not
hand-edit the tracked scorecard CSV at a stale reverie SHA; the CSV binds each
cell to exact `hermit_sha`/`reverie_sha`.
