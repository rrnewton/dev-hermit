# PR #1847 measured: both predictions confirmed exactly — 410/410 and 0/56

**Task:** `build-and-measure-pr1847-liteinst-maps-inode-fix` · **Agent:** hermit-w2 · **2026-08-07**
**PR head built and measured:** `077833ad65955b30309d40ac3105a135779c0dce` (OPEN, draft, not merged,
not an ancestor of hermit `main` at measurement time) · **Reverie pin in build:** `0ae0c01b`
**Baseline comparison build:** `86842f741` (`worktrees/cc/hermit`, read-only)

## Result

The prior task predicted these two numbers from source inspection alone. Both are now measured.

| check | predicted | **actual** | agrees |
|---|---|---|---|
| liteinst stack ordinals matching | 410 / 410 | **410 / 410** | **yes** |
| liteinst maps reproducer differing | 0 / 56 | **0 / 56** | **yes** |

Pre-fix versus post-fix, same guest, same commands, same host:

| | pre-fix `86842f741` | post-fix `077833ad` |
|---|---|---|
| liteinst stack **matching** | 110 / 410 | **410 / 410** |
| liteinst stack first differing ordinal | 110 (contiguous tail) | none |
| liteinst maps lines differing | 14 / 56 | **0 / 56** |
| ptrace stack matching (control) | 44 / 44 | 44 / 44 |
| ptrace maps differing (control) | 0 / 25 | 0 / 25 |

**The denominator did not move: 410 stack records before and 410 after.** That matters — a fix that
reduced the record count could reach "0 differing" by emitting less. It did not; the same 410
records are emitted and now all 410 repeat. The ptrace control is unchanged in both directions, so
the fix did not perturb the reference backend.

## The measurement is falsifiable — proven on the fix binary itself

A prediction that comes true exactly deserves suspicion, so the harness was tested for inertness
**on the same binary that produced the clean result**. `plant.c` reads a host file into a live stack
buffer and then makes syscalls so the stack is hashed while the bytes are resident; the file content
is changed between run 1 and run 2, so the stack hash must move.

```
ptrace    PLANTED: counts 48 vs 48    DIFFERING 11 / 48    -> detected
liteinst  PLANTED: counts 414 vs 414  DIFFERING 11 / 414   -> detected
```

The harness detects planted stack-content nondeterminism on both backends at the fix commit. The
410/410 is therefore a real negative, not an inert one.

Note what this control is and is not: it proves **the measurement can fail**. It is not a control on
the fix.

## The TSC-leak probe, now unblocked — and it is clean

The probe was previously unfalsifiable for LiteInst purely because its baseline was 110/410. With
the baseline at 410/410 it becomes meaningful, so it was run (`tscleak.c`, a real `RDTSC` written
into 64 live stack slots):

```
ptrace     DIFFERING 0 / 44     tsc_captured=1
liteinst   DIFFERING 0 / 410    tsc_captured=1
```

`tsc_captured=1` confirms nonzero timestamps really were captured, so this is not a vacuous pass.
**Both backends virtualise RDTSC**: a raw TSC resident in stack memory does not move the stack hash
across runs. One host, one guest, one run pair — presence, not flake rate.

## Method

```bash
# stack self-determinism, 2 runs per backend
hermit --log=info --log-file=$L run --backend $BE \
  --strict --base-env=minimal --detlog-stack --tmp=/tmp -- notsc
grep -oE '\[stack\]->[0-9a-f]+' $L | sed 's/.*->//' > $BE-$R.h
paste $BE-1.h $BE-2.h | awk '$1!=$2' | wc -l

# maps reproducer, 2 runs per backend
hermit run --backend $BE --strict --base-env=minimal --tmp=/tmp -- /bin/cat /proc/self/maps
```

Guests: `scratch/w27-tsc/notsc.c` (control — constant stamps, `rdtsc()` defined but never called),
`scratch/w27-tsc/tscleak.c` (real RDTSC), and `plant.c` here (planted defect).

Guards applied on every cell: a zero-hash extraction is **refused as a no-result**, not scored 0/0;
a count mismatch between runs is reported as *structure moved* rather than folded into a ratio.

## Build notes for whoever lands this

**`make release-core` is not sufficient to run LiteInst.** It builds `hermit` but leaves only the
hashed dep artifact `target/release/deps/libreverie_liteinst-<hash>.so`. Hermit refuses:

> `libreverie_liteinst.so was not built beside .../target/release/hermit or staged as an installed
> resource; build the locked liteinst-runtime-build manifest and stage its constructor-enabled DSO`

The hashed dep is **not** the right file — copying it into place would be wrong. The correct step is

```
scripts/stage-liteinst-runtime.sh release "$PWD/target/release/libreverie_liteinst.so" "$PWD/target/liteinst-runtime"
```

which builds the constructor-enabled DSO (26.6s on top of a 1m13s `release-core`).

### Slot handling

`worktrees/pr1847` was **not** used: it carries a registered owner (`hermit-pr1847`) whose
coordinator lease PID `1890624` was verified **alive**. `allocate-worktree.rs` refuses a second slot
per agent (`agent 'hermit-w2' already owns slot 'w2'`). The build therefore ran in a **temporary
detached git worktree** created from the shared hermit git dir, removed afterwards. This left
`worktrees/w2` (whose hermit checkout holds hermit-w13's `pin/reverie-038e9939-cargo-only` at
`77951bcd`), `worktrees/pr1847`, and `worktrees/w12chaos` all untouched.

**The stale-build trap still stands:** `worktrees/w12chaos` sits on the fix branch at `de638e587`
but its binary reports `gc67774dd`, which the fix is not an ancestor of. Do not measure there.

## Recommendation

The fix does exactly what it claimed, on the measurement that exposed the problem, with a proven
non-inert harness and an unmoved denominator. The remaining gates for landing are the ordinary ones
— authoritative CI green at the PR head and adversarial review — neither of which this task covers.

## Scope and limits

- **One host, one guest per check, one run pair per backend.** Presence of the defect and its
  removal, not a flake rate. No repetition, so this does not bound intermittency.
- Measured at PR head `077833ad`, which is **not** rebased onto current main; a rebase could change
  the result and would need a re-measure.
- Only the stack dimension was scored. Heap and detlog were not re-measured here.
- **SaBRe untouched** — different cause (121/121 differing from ordinal 0, maps byte-identical,
  needs a Reverie-side change). Do not conflate.
