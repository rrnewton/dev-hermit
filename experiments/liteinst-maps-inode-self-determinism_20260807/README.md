# LiteInst's baseline stack nondeterminism: it was never virtual time, and it is already fixed

**Task:** `liteinst-virtual-time-nondeterminism-in-proc-self-maps-self-scan` · hermit-w7
(`[impl agent, opus-5]`) · **2026-08-07** · local, no egress beyond `git fetch`.
**Arms:** hermit `0041130ccb0d` (merge-base, pre-fix) vs `077833ad6595`
(head of open PR [#1847](https://github.com/rrnewton/hermit/pull/1847)), both release, both
built from the same worktree, both with the identical Reverie pin `0ae0c01b`.

## 0. Question

Does PR #1847 take LiteInst's baseline stack self-determinism from 302/412 differing to
0/412, and — once that baseline is clean — is there a TSC leak into LiteInst stack memory?

## 1. The task title names the wrong mechanism

The task is filed as *virtual-time* nondeterminism in a *self-scan*. Neither word survives
contact with the evidence, and the correction matters because it changes which code is at
fault.

- **Not virtual time.** The upstream measurement (hermit-w27) recorded LiteInst DETLOG at
  **0 / 1245 differing** while stack was 303/413. The event stream, the schedule and every
  virtual-time field repeat exactly. Nothing about the clock is implicated.
- **Not a self-scan.** LiteInst *does* scan `/proc/self/maps` in its own runtime
  (`reverie/reverie-liteinst/src/runtime.rs:595,682`), but that scan is not what leaks. The
  leak is in the **guest's** read of its own `/proc/self/maps`: LiteInst maps its trampoline
  arena from a `memfd`, the kernel assigns that memfd a **host-global inode number**, and that
  number is printed in the guest-visible maps text.

The real mechanism, reproduced directly rather than inferred:

```
$ hermit --backend=liteinst run --strict --base-env=minimal -- /bin/cat /proc/self/maps   # ×2
run1: 70f80000-71000000 r-xs 00000000 00:01 102777   /memfd:liteinst2-trampoline (deleted)
run2: 70f80000-71000000 r-xs 00000000 00:01 209345   /memfd:liteinst2-trampoline (deleted)
```

56 lines, **14 differing**, and the differing column is only ever the inode. The address range
is stable. The guest reads that text into a stack buffer, the buffer is hashed, and the value
persists in dead stack — which is why the divergence is a *contiguous tail* from the first hash
after `openat("/proc/self/maps")` rather than diffuse drift.

## 2. The fix already exists, and it works

PR #1847 (`fix/liteinst-maps-inode-nondeterminism-w23`, opened 03:32Z 2026-08-07, **still
draft**) routes every nonzero maps inode through Hermit's existing virtual inode namespace,
fail-closed to `EIO`. This experiment is an independent A/B of that PR, not a re-derivation of
it. Both arms are release builds from the *same* worktree with the *same* Reverie pin, so the
only variable is the 7-file diff.

| arm | binary | maps lines differing | `notsc` stack | `tscleak` stack |
| --- | --- | --- | --- | --- |
| **base** `0041130ccb0d` | clean | **14 / 56** | **302 / 412** (3/3 pairs) | **302 / 412** (3/3 pairs) |
| **fix** `077833ad6595` | clean | **0 / 56** (5 runs) | **0 / 412** (0/66 pairs) | **0 / 412** (0/10 pairs) |
| ptrace reference @fix | clean | — | 0 / 44 | 0 / 44 |

**The Verify clause is satisfied: baseline stack self-determinism is n/n — 412 of 412 hashes
identical across 12 runs, all 66 pairs.**

### The harness is not inert

A determinism harness that only ever prints 0 is worthless. Two brackets:

- **Negative control.** Same binary, same guest, `--no-virtualize-time
  --no-virtualize-metadata`: `notsc` **362 / 412** differing, `tscleak` **369 / 412**. The
  harness fires.
- **Positive control.** The base arm above *is* the positive control, at 302/412, from a
  single-variable diff in the same tree.

## 3. TSC-leak probe: LiteInst stack is CLEAN, and here is why that is now sayable

`tscleak.c` writes 64 raw `rdtsc()` values into live stack memory and then makes syscalls so
the stack is hashed while they are resident; `notsc.c` is byte-identical except that it writes
fixed constants. Both print only a boolean so stdout cannot itself be the signal.

| arm | `notsc` | `tscleak` | discriminating? |
| --- | --- | --- | --- |
| base | 302 / 412 | 302 / 412 | **no** — the inode noise swamps the question |
| fix | 0 / 412 | 0 / 412 | **yes** |

At the base commit the two guests are indistinguishable: the inode leak alone moves 302 hashes,
so a TSC leak of any size would have been invisible underneath it. That is exactly the
"unfalsifiable" state the task describes. At the fix, `notsc` is 0/412 — so the floor is clean
— and `tscleak` is *also* 0/412 with `tsc_captured=1` confirming the 64 values really were
nonzero and resident. **LiteInst virtualizes RDTSC into stack memory deterministically; there
is no TSC leak in the stack dimension.**

Scope limit, stated rather than papered over: this is the **stack** dimension only. Hermit
exposes no flag that disables RDTSC virtualization while leaving time virtualization on, so
there is no TSC-*specific* negative control — the negative control above disables time
wholesale and moves `notsc` too. The claim rests on the differential (`tscleak` vs `notsc`
under identical strict configuration) plus a clean floor, not on a TSC-only knob. The DETLOG
dimension is a different emission path and is owned by
`detlog-tsc-leak-unmeasured-across-all-five-backends` (hermit-w22); do not infer it from this.

## 4. Parity ratchet: unchanged, and it has no LiteInst column

Re-run of the prefix-parity depth method (Y = length of the identical leading run of
`COMMIT turn` records against the ptrace golden; Z = golden's record count), using
`ci-hub/parity/prefix_depth.sh`'s own `commits()`/`depth()` logic verbatim over its own four
pinned reference guests:

| guest | Z | LiteInst Y @base | LiteInst Y @fix | EMIT |
| --- | --- | --- | --- | --- |
| `detlog_syscalls` | 15 | 2 | 2 | 42 |
| `heap_fragment_reuse` | 15 | 2 | 2 | 42 |
| `stack_deep_recursion` | 15 | 2 | 2 | 42 |
| `stdout_bytes` | 16 | 2 | 2 | 43 |

**The fix moves cross-backend parity by zero.** That is the correct result, not a
disappointment: the ratchet compares scheduler `COMMIT` records, and LiteInst emits 42 where
ptrace emits 15 because the preload runtime contributes its own turns. Divergence at record 2
is that structural difference, and no inode fix can touch it.

Two things worth recording. First, `ci-hub/parity/prefix_depth.sh` iterates
`for be in dbi sabre e9patch` — **there is no `liteinst` row in any ratchet script under
`ci-hub/parity/`.** The numbers above come from a standalone replica
(`harness/liteinst_depth.sh`) written precisely so the shared ratchet, owned by another task,
was not mutated. Adding a `liteinst` row there is a one-word change someone should make.
Second, the task's Verify clause treats "self-determinism reaches n/n" and "the parity ratchet
moves" as one consequence. They are independent dimensions and only the first one moved.

## 5. New finding: LiteInst stack hashes are install-path dependent

Not asked for, and it will bite the next person who compares LiteInst memory hashes across
machines or checkouts.

Running the *same* fix binary from three different filesystem paths gives three different —
but each internally perfectly stable — hash classes:

| install path | length | runs | md5 of the 412-hash list | pairs differing |
| --- | --- | --- | --- | --- |
| `worktrees/w7/hermit/target/release/hermit` | 70 | 3 | `391cb999…` | 0 / 3 |
| `ignored/w7-1847/fix/hermit` | 55 | 12 | `370e0e57…` | 0 / 66 |
| `ignored/w7-1847/pl-aaaaaaaaaaaaaaa/hermit` | 70 | 3 | `2f504201…` | 0 / 3 |

Note the first and third have the *same* length and still differ, so this is path **content**,
not padding. The mechanism is observable, not inferred:

```
$ hermit --backend=liteinst run --strict --base-env=minimal -- /usr/bin/env
LD_PRELOAD=/home/newton/work/dev-hermit/ignored/w7-1847/fix/libreverie_liteinst.so
REVERIE_LITEINST_HOST_RUNTIME=1
$ hermit run --strict --base-env=minimal -- /usr/bin/env          # ptrace: neither variable
```

The absolute path of the LiteInst runtime is in the guest environment block, the environment
block is on the initial stack, and so every stack hash is a function of where the runtime is
installed. This is **not** nondeterminism — each invocation is exactly reproducible — but any
LiteInst memory-hash comparison across two checkouts, two machines, or a moved artifact must
hold the runtime path fixed or it will read a path difference as a determinism failure. I lost
about ten minutes to exactly this before spotting the time-ordered split in my own data.

## 6. What is actually left

The engineering is done and is not mine. What remains is landing:

- **#1847 is still a DRAFT** and `mergeStateStatus=BLOCKED`, on
  `reverie-pin-is-latest-main FAILURE` → `merge-gate-v4 FAILURE`. That is the known fleet-wide
  stale-pin collateral, not a defect in this change; every real test job on the PR is green
  (`P0 demo gate (demos 1-8)` SUCCESS, `core-review-protocol` SUCCESS).
- It carries `adversarial-review-claude1` and `mechanism:deterministic-inode-identity`.
- The branch head `077833ad` is one commit behind `origin/main` `75506005`, with no overlapping
  source change.

## 7. Reproduction

```bash
cd worktrees/<slot>/hermit
git checkout 077833ad65955b30309d40ac3105a135779c0dce      # or 0041130c for the base arm
cargo build --release -p hermit --features third-party-backends
./scripts/stage-liteinst-runtime.sh release \
    "$PWD/target/release/libreverie_liteinst.so" "$PWD/target/liteinst-runtime-build"

gcc -O0 -o notsc   harness/notsc.c
gcc -O0 -o tscleak harness/tscleak.c

# stack ratchet, N runs, keep the binary path FIXED across runs (see §5)
for i in $(seq 1 12); do
  ./target/release/hermit --log=info --backend=liteinst run \
      --strict --base-env=minimal --detlog-stack -- ./notsc 2> r$i.err
  grep -o '\[stack\]->[0-9a-f]*' r$i.err > r$i.h
done
# every r*.h must be identical, and `wc -l` must be 412 (nonzero denominator)

# one-command mechanism check
./target/release/hermit --backend=liteinst run --strict --base-env=minimal \
    -- /bin/cat /proc/self/maps
```

`harness/maps-{pre,fix}-{1,2}.txt` are the captured maps outputs for both arms.
`results.csv` and `prefix-parity-depth.csv` carry every number above with its denominator.
