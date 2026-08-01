# e9patch corpus ratchet — round 37 (more socket options, prctl gets [dropped], device lseek)

## Question

Round 37 of the standing e9patch corpus ratchet. Can freestanding raw-syscall
x86-64 guests for six inert query/no-op probes on previously uncovered axes —
two more `getsockopt` options (`SO_REUSEADDR`, `SO_BROADCAST`), three new `prctl`
gets (`PR_GET_CHILD_SUBREAPER`, `PR_GET_TSC`, `PR_MCE_KILL_GET`), and an `lseek`
`SEEK_END` on the `/dev/null` character device — reach L2 parity across the golden
ptrace backend and the e9patch-rewritten ptrace path?

**Answer: partly.** The three socket/lseek probes are clean; the three `prctl`
gets are DROPPED because golden hermit ptrace returns an error for those prctl
subcommands where native returns a value — a real golden-vs-native divergence, so
admitting them would be false parity (hermit issue #152).

## Method

Each candidate is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Each was native-tested, then golden-hermit-ptrace
L2-tested (`--strict --verify`), then e9patch L2-tested (`--backend e9patch`:
candidate_sites>0, mapped==candidate, no SIGILL fallback `b0==0`, DETLOG
tail-match with the deterministic e9loader prologue removed). A candidate is KEPT
only if native, golden, and e9 all pass AND agree; any guest whose golden output
diverges from native is DROPPED (no false parity, hermit issue #152).

**Two-layer verification (the round-37 lesson).** The scorecard collector
(`compat-envelope/collect-e9patch-compat.rs`) checks only the *e9-vs-golden*
relationship: it reported `det=1 par=1` for all six candidates, including the
three prctl gets, because e9patch and golden both return the *same* (erroneous)
result deterministically — parity and L2 both hold between them. The authoritative
corpus harness (`e9patch_corpus.py`) additionally checks *golden-vs-native* by
comparing golden stdout to the recorded native expected value, and it caught the
three prctl gets: golden printed `subreaper=err` / `tsc=err` / `mcekill=err` while
native printed `subreaper=0` / `tsc=1` / `mcekill=2`. **Always run the full corpus
harness, not just the scorecard collector, before keeping a guest.**

## Kept (3)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| getsockopt_reuseaddr | getsockopt(55) SO_REUSEADDR | option unset on fresh endpoint | `reuseaddr=0` |
| getsockopt_broadcast | getsockopt(55) SO_BROADCAST | option unset on fresh endpoint | `broadcast=0` |
| lseek_devnull_end | lseek(8) /dev/null SEEK_END | device seek is a no-op reporting 0 | `devnullend=0` |

`getsockopt_reuseaddr` and `getsockopt_broadcast` read two more socket OPTIONS on
a fresh `AF_UNIX` socketpair endpoint (both unset → 0), beyond the
`SO_TYPE`/`SO_DOMAIN`/`SO_PROTOCOL`/`SO_ERROR`/`SO_ACCEPTCONN` options already
covered. `lseek_devnull_end` seeks `/dev/null` to `SEEK_END`: a character device
whose seek is a no-op that always reports offset 0, a distinct lseek target from
`lseek_pipe`'s `ESPIPE` and the memfd seekable-file positioning guests.

## Dropped (3)

| guest | syscall | native | golden hermit ptrace | reason |
|-------|---------|--------|----------------------|--------|
| prctl_child_subreaper | prctl(157) PR_GET_CHILD_SUBREAPER=37 | `subreaper=0` | `subreaper=err` | Detcore prctl handler returns an error for this subcommand |
| prctl_tsc | prctl(157) PR_GET_TSC=25 | `tsc=1` | `tsc=err` | Detcore prctl handler returns an error for this subcommand |
| prctl_mce_kill_get | prctl(157) PR_MCE_KILL_GET=34 | `mcekill=2` | `mcekill=err` | Detcore prctl handler returns an error for this subcommand |

These three prctl *get* subcommands succeed natively but hermit's Detcore prctl
handler does not support them, returning an error under the golden ptrace
backend. Keeping them would assert e9patch preprocessing invariance over an
already-broken golden path — false parity. They are excluded and recorded as
UNSUPPORTED so future rounds do not re-probe them.

## Results

- native: 6/6 exit 0 (`reuseaddr=0`, `broadcast=0`, `subreaper=0`, `tsc=1`,
  `mcekill=2`, `devnullend=0`).
- golden ptrace: 3/6 L2 with native-matching stdout (the three kept guests); the
  three prctl gets returned an error (`...=err`) and were dropped.
- e9patch: the 3 kept guests PASS_L2 exit=0, sites c/1 m/1 b0/0, prologue=8,
  tail_match=yes; scorecard collector `det=1 par=1` for the kept arms.
- full corpus (after dropping the 3): **242/242 PASS_L2**.
- corpus size: 239 → 242 (net +3 after 3 drops).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0 (620 files, 275
  guest-fixtures).

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
