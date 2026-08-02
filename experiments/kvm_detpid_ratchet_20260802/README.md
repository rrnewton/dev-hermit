# KVM detpid ratchet — corpus cells that flip DIFF→PARITY

**Question.** After the KVM root-DetPid fix (PR #1120, `set_root_pid(detcore::ROOT_DETPID)`),
which compat-corpus cells that previously diverged from the ptrace golden reference
under KVM now match it bitwise?

## Method

The pre-fix KVM full-corpus scorecard (`82a8e853`) seeded the KVM root guest at
vpid=1 while ptrace uses `ROOT_DETPID = DetPid(3)`. Any guest whose observable
output embeds its pid/tid (directly, or via `SO_COOKIE`'s high 32 bits =
`detpid << 32 | counter`) therefore printed a KVM-specific value and scored
`parity=diff`.

Re-measured with a binary that has the fix applied: verify branch
`codex/kvm-parity-detpid-verify` @ `f7843024` = `origin/main 065980ea` +
env-parity PR #1432 + PR #1120's `set_root_pid` change, release build, reverie
pin `26ffc1a6` (contains `set_root_pid`).

Per cell: `hermit run --backend {ptrace,kvm} --tmp=/tmp --strict -- <guest>`,
compare SHA-256 of stdout. L1 parity (`--strict`, no `--verify` needed for the
parity question). Guests taken from `hermit/target/kvm-fullcorpus/<cell>/guest`.

## Results

All 5 detpid-signature cells flip `diff → parity` (see `results.csv`):

| cell | pre-fix ptrace / kvm | post-fix (both) |
| --- | --- | --- |
| backend-parity-c/pid-probe | `pid=3` / `pid=1` | `pid=3` |
| c-programs/socket-cookie-tcp | `...893,...894` / `...301,...302` | `12884901888,12884901889` |
| c-programs/socket-cookie-udp | diff | `12884901888,12884901889` |
| c-programs/socket-cookie-unix | diff | `12884901888,12884901889` |
| system-utils/record-getpid | diff | `My pid: 3` |

`socket-cookie` high word: pre-fix ptrace `0x3`, kvm `0x1`; post-fix both `0x3`.
(The low counter word also shifted `5→0` between scorecard runs — run-history
dependent — but both backends agree post-fix, which is what parity requires.)

## Interpretation

The detpid fix is the sole cause of these 5 parity gaps; each becomes bitwise
identical to ptrace. This is a clean +5 KVM parity ratchet.

## Gating / reproduction

These flips are contingent on **PR #1120 landing on main** — current main still
seeds KVM vpid=1. Do NOT set `l1_kvm_parity=parity` for these rows in
`compat-envelope/corpus-manifest.csv` until #1120 is merged; then re-run
`compat-envelope/collect-fullcorpus.sh` (or the 5 cells above) at the landed SHA
and update the manifest + KVM scorecard as a post-landing ratchet commit.

Reproduce (post-landing, on a `/dev/kvm` host):
```
D=hermit/target/kvm-fullcorpus
for c in backend-parity-c_pid-probe c-programs_socket-cookie-tcp \
         c-programs_socket-cookie-udp c-programs_socket-cookie-unix \
         system-utils_record-getpid; do
  p=$(hermit run --backend ptrace --strict -- $D/$c/guest)
  k=$(hermit run --backend kvm    --strict -- $D/$c/guest)
  [ "$p" = "$k" ] && echo "$c PARITY" || echo "$c DIFF ($p | $k)"
done
```
