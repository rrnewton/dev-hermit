# e9patch corpus ratchet — round 49 (SOL_SOCKET option flags)

## Question

Round 49 of the standing e9patch corpus ratchet. Can ten freestanding
raw-syscall x86-64 guests reading the remaining uncovered **SOL_SOCKET(1)**
`getsockopt(55)` option flags reach L2 parity across the golden ptrace backend
and the e9patch-rewritten ptrace path, completing socket-level option coverage?

**Answer: yes, all ten.** Corpus 333 → 343, 343/343 PASS_L2. All ten probed
candidates matched golden; none dropped this round.

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) that opens a fresh
`AF_INET`/`SOCK_STREAM` socket, reads one option default with `getsockopt(55)`
at level `SOL_SOCKET(1)`, and prints only that host-independent constant.
Candidates were first native-probed AND golden-probed (`hermit run --strict`) to
catch divergence before authoring; each authored guest was then native-tested,
golden-hermit-ptrace L2-tested (`--strict --verify`), and e9patch L2-tested
(candidate_sites>0, mapped==candidate, no SIGILL fallback `b0==0`, deterministic
e9loader `prologue=8`, DETLOG tail-match). A candidate is KEPT only if native,
golden, and e9 all pass AND agree; any guest whose golden output diverges from
native is DROPPED (no false parity, hermit issue #152).

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (10)

| guest | option (SOL_SOCKET optname) | stdout |
|-------|-----------------------------|--------|
| getsockopt_bsdcompat | SO_BSDCOMPAT(14) | `sobsdcompat=0` |
| getsockopt_rxq_ovfl | SO_RXQ_OVFL(40) | `sorxqovfl=0` |
| getsockopt_nofcs | SO_NOFCS(43) | `sonofcs=0` |
| getsockopt_lock_filter | SO_LOCK_FILTER(44) | `solockfilter=0` |
| getsockopt_select_err_queue | SO_SELECT_ERR_QUEUE(45) | `soselecterrq=0` |
| getsockopt_timestamping | SO_TIMESTAMPING(37) | `sotimestamping=0` |
| getsockopt_txtime | SO_TXTIME(61) | `sotxtime=0` |
| getsockopt_bindtoifindex | SO_BINDTOIFINDEX(62) | `sobindtoifidx=0` |
| getsockopt_incoming_napi_id | SO_INCOMING_NAPI_ID(56) | `sonapiid=0` |
| getsockopt_wifi_status | SO_WIFI_STATUS(41) | `sowifistatus=0` |

Unlike the round-47 host-sysctl-tuned options, these `SO_*` flags default to a
fixed per-socket constant independent of host sysctls, so native and golden
agree on every probe.

## Dropped (0)

All ten probed candidates matched golden ptrace; none dropped this round.

## Results

- native: 10/10 exit 0 with expected stdout.
- golden ptrace: 10/10 L2, native-matching stdout.
- e9patch: 10/10 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **343/343 PASS_L2** (333 → 343, net +10).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
