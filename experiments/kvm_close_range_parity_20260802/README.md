# KVM close_range(2) parity ratchet — 2026-08-02

## Question

Can the KVM backend match the ptrace golden on the `c-programs/syscall-quick-wins`
corpus cell? Before this batch that cell passed under ptrace but failed under
KVM because `close_range(2)` returned `ENOSYS`.

## Change under test

reverie PR https://github.com/rrnewton/reverie/pull/340
(branch `codex/kvm-close-range-parity`, reverie SHA
`d740edd5e8baa9ddb971cc072d901a6ad1cb9f6d`, base origin/main
`b9a7fa777e902ef937766e2db7c1a333b1e15e31`).

Adds a `close_range(first, last, flags)` handler to the reverie-kvm executor:
closes every open guest descriptor in `[first, last]`, validates `first<=last`
and the flag set (`EINVAL` otherwise), honors `CLOSE_RANGE_CLOEXEC` (mark
cloexec, do not close), accepts `CLOSE_RANGE_UNSHARE` as an observationally-
equivalent precondition, bounds the scan to actually-open descriptors (so
`last==U32::MAX` is safe), and registers `SYS_close_range` in
`mutates_file_table` for shared-table write-back (same root-cause class as
reverie#322 memfd_create).

## Method

- Fast KVM-guest-path evidence: `cargo test -p reverie-kvm` on real `/dev/kvm`
  (see `kvm-fullstack-debug-boot-unusably-slow` memory for why full-stack
  `hermit run --backend kvm` on a debug binary is not used for spot-checks).
- Golden reference: ptrace (the corpus cell is ptrace-`--verify`-green).

## Results

| corpus cell | ptrace --verify | KVM (before) | KVM (after) |
| --- | --- | --- | --- |
| `c-programs/syscall-quick-wins` | pass | fail (close_range ENOSYS, exit 1) | executor handler present; unit-verified |

- `cargo test -p reverie-kvm`: 190/190 pass (152 lib incl. 2 new close_range
  tests + strace/counter/vmcall/static_elf suites). fmt + clippy clean.
- New unit tests: `close_range_closes_the_inclusive_descriptor_span`,
  `close_range_validates_bounds_and_flags`.

Assurance: L0 (reverie-only). The corpus-cell parity flip is measured once #340
lands and the Hermit reverie pin bumps; full-stack KVM verify on a debug hermit
binary is environment-limited (debug KVM boot pathologically slow).

## Interpretation

`close_range` ENOSYS was the sole KVM divergence on this ptrace-green cell (per
`experiments/kvm_sendfile_memfd_parity_20260801`). With the handler + write-back,
the guest-visible semantics the cell probes (span closed, EBADF on a re-probe of
a closed fd) are satisfied. Expected effect on the KVM full-corpus parity
denominator once landed+pinned: +1 cell (det and parity), from the last recorded
baseline det 132/200, parity 114/184.

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/kvm/reverie
git switch codex/kvm-close-range-parity   # @ d740edd
cargo test -p reverie-kvm close_range
cargo test -p reverie-kvm
```

Corpus cell source: `hermit/tests/c/syscall_quick_wins.c` (close_range block).
