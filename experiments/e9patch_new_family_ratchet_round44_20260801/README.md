# e9patch corpus ratchet — round 44 (fchownat, socket opts, mmap flags, netlink, O_DIRECT pipe, fcntl/arch_prctl)

## Question

Round 44 of the standing e9patch corpus ratchet. Can nine freestanding
raw-syscall x86-64 guests on previously uncovered inert axes — a path-based
credential no-op (`fchownat`), two more socket options (`SO_ZEROCOPY`,
`SO_MARK`), two mmap flags (`MAP_LOCKED`, `MAP_GROWSDOWN`), a new address family
(`socket(AF_NETLINK)`), a packet-mode pipe (`pipe2(O_DIRECT)`), an I/O-signal
round-trip (`fcntl(F_SETSIG)`/`F_GETSIG`), and the CPUID-fault flag
(`arch_prctl(ARCH_GET_CPUID)`) — reach L2 parity across the golden ptrace backend
and the e9patch-rewritten ptrace path?

**Answer: yes, all nine.** Corpus 282 → 291, 291/291 PASS_L2. Two additional
candidates were probed and dropped/set-aside (golden diverges from native, or
errno-only).

## Method

Each guest is a freestanding, statically linked, raw-syscall C program
(`-nostdlib -static -ffreestanding -O0 -fno-pie -no-pie`) printing only
host-independent values. Candidates were first native-probed AND golden-probed
(`hermit run --strict`) to catch divergence before authoring; each authored
guest was then native-tested, golden-hermit-ptrace L2-tested (`--strict
--verify`), and e9patch L2-tested (candidate_sites>0, mapped==candidate, no SIGILL
fallback `b0==0`, deterministic e9loader `prologue=8`, DETLOG tail-match). A
candidate is KEPT only if native, golden, and e9 all pass AND agree; any guest
whose golden output diverges from native is DROPPED (no false parity, hermit
issue #152).

e9patch is a binary-rewriting AOT preprocessing pass used together with the
ptrace backend; it is not a Detcore backend, so these guests live in the
dedicated `e9patch_corpus` and never in a backend scorecard.

## Kept (9)

| guest | syscall | assertion | stdout |
|-------|---------|-----------|--------|
| fchownat_devnull | fchownat(260) AT_FDCWD owner/group -1 | path-based no-op chown returns 0 | `fchownat=0` |
| getsockopt_zerocopy | getsockopt(55) SO_ZEROCOPY=60 | zero-copy send off by default | `zerocopy=0` |
| getsockopt_mark | getsockopt(55) SO_MARK=36 | socket fwmark 0 by default | `mark=0` |
| mmap_locked | mmap(9) MAP_LOCKED=0x2000 | lock anon page at fault time (boolean) | `maplocked=1` |
| mmap_growsdown | mmap(9) MAP_GROWSDOWN=0x100 | downward-growing mapping (boolean) | `growsdown=1` |
| socket_netlink | socket(41) AF_NETLINK/SOCK_RAW/NETLINK_ROUTE | open route-netlink socket (boolean) | `netlink=1` |
| pipe2_direct | pipe2(293) O_DIRECT=0x4000 | packet-mode 2-byte round-trip | `pd=hi` |
| fcntl_setsig | fcntl(72) F_SETSIG=10/F_GETSIG=11 | set then read I/O-ready signal | `setsig=10` |
| arch_prctl_getcpuid | arch_prctl(158) ARCH_GET_CPUID=0x1011 | CPUID-fault flag disabled by default | `getcpuid=0` |

`fchownat_devnull` is a path-based `*at` credential op (distinct from the fd-based
`fchown_memfd` guest); `mmap_locked`/`mmap_growsdown`/`socket_netlink` print only
a success boolean because the address, fd number, or key index are host-variable;
`arch_prctl_getcpuid` reads the CPUID-fault flag (distinct from the FS/GS-base
arch_prctl guests).

## Dropped / set aside (2)

| candidate | syscall | reason |
|-----------|---------|--------|
| prctl PR_GET_NO_NEW_PRIVS | prctl(157) op=39 | native 0; golden hermit -ENOSYS (-38) → hermit limitation, not parity (#152) |
| getsockopt SO_PASSSEC | getsockopt(55) op=34 | errno-only -EOPNOTSUPP (-95) on AF_INET; set aside to keep the batch value-bearing |

## Results

- native: 9/9 exit 0 with expected stdout.
- golden ptrace: 9/9 L2, native-matching stdout.
- e9patch: 9/9 PASS_L2 (`exit=0 sites c/1 m/1 b0/0 prologue=8 tail_match=yes`).
- full corpus: **291/291 PASS_L2** (282 → 291, net +9, 2 dropped pre-authoring).
- inventory: `./ci/test_harness.sh audit-inventory` EXIT=0.

## Reproduction

```
export HERMIT_E9TOOL=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9tool
export HERMIT_E9PATCH_BACKEND=~/work/dev-hermit/worktrees/e9patch/reverie/third-party/e9patch/e9patch
cd ~/work/dev-hermit/worktrees/e9patch/hermit
python3 tests/backend-parity/e9patch_corpus.py --hermit target/debug/hermit
```

See `metadata.json` for exact SHAs and environment.
