# e9patch corpus ratchet — round 43 (utimensat, OFD-setlk, socket opts, mmap/mprotect, pkey)

## Question

Round 43 of the standing e9patch corpus ratchet. Can seven freestanding
raw-syscall x86-64 guests on previously uncovered inert axes — a file-time set
(`utimensat`), the OFD-lock SET path (`fcntl(F_OFD_SETLK)`), two more socket
options (`SO_TIMESTAMPNS`, `SO_BUSY_POLL`), an mmap flag (`MAP_32BIT`), a
no-access protection transition (`mprotect(PROT_NONE)`), and a
memory-protection-key pair (`pkey_alloc`/`pkey_free`) — reach L2 parity across
the golden ptrace backend and the e9patch-rewritten ptrace path?

**Answer: yes, all seven.** Corpus 275 → 282, 282/282 PASS_L2. Two additional
candidates were probed and dropped (golden diverges from native).

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Candidates were first native-probed AND golden-probed
(`hermit run --strict`) to catch divergence before authoring; each authored
guest was then native-tested, golden-hermit-ptrace L2-tested (`--strict
--verify`), and e9patch L2-tested (`--backend e9patch`: candidate_sites>0,
mapped==candidate, no SIGILL fallback `b0==0`, deterministic e9loader
`prologue=8`, DETLOG tail-match). A candidate is KEPT only if native, golden, and
e9 all pass AND agree; any guest whose golden output diverges from native is
DROPPED (no false parity, hermit issue #152).

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (7)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| utimensat_memfd | utimensat(280) times=NULL | set memfd times to now, return only | `utimens=0` |
| fcntl_ofd_setlk_memfd | fcntl(72) F_OFD_SETLK=37 | clear whole-file OFD lock (F_UNLCK) | `ofdsetlk=0` |
| getsockopt_timestampns | getsockopt(55) SO_TIMESTAMPNS=35 | ns receive-timestamping off by default | `timestampns=0` |
| getsockopt_busy_poll | getsockopt(55) SO_BUSY_POLL=46 | busy-poll budget 0 by default | `busypoll=0` |
| mmap_32bit | mmap(9) MAP_32BIT=0x40 | anon page placed in low 4 GiB (boolean) | `map32=1` |
| mprotect_none | mprotect(10) PROT_NONE→PROT_READ | revoke then restore access | `protnone=0` |
| pkey_alloc_free | pkey_alloc(330)+pkey_free(331) | allocate/release a protection key (boolean) | `pkeyalloc=1` |

`fcntl_ofd_setlk_memfd` exercises the OFD-lock SET path (distinct from the
`F_OFD_GETLK` query and the process-associated `F_SETLK` guests);
`mprotect_none` is a no-access transition (distinct from the RW→RO→RW
`mprotect_roundtrip` guest); `mmap_32bit` and `pkey_alloc_free` print only a
success boolean because the returned address and key index are host-variable.

## Dropped (2)

| candidate | syscall | reason |
|-----------|---------|--------|
| getsockopt SO_INCOMING_CPU | getsockopt(55) op=49 | native -1; golden hermit 0 → hermit limitation, not parity (#152) |
| prctl PR_GET_TID_ADDRESS | prctl(157) op=40 | native -EINVAL (-22); golden hermit -ENOSYS (-38) → hermit limitation, not parity (#152) |

## Results

- native: 7/7 exit 0 with expected stdout.
- golden ptrace: 7/7 L2, native-matching stdout.
- e9patch: 7/7 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **282/282 PASS_L2** (275 → 282, net +7, 2 dropped pre-authoring).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
