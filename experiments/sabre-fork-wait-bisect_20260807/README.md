# SaBRe fork/wait determinism regression — bisected to a single commit

**Task:** `bisect_sabre_fork_wait` · **2026-08-07** · one clean discriminating cell

## Question

The SaBRe backend lost 17 determinism greens between hermit `82a8e853` and `1fadc037`, 16 of them
genuine divergences clustered on child-process lifecycle and pid/wait semantics. Which commit
introduced it?

## Answer

**First bad commit: `9c233ed0bfd63fbce4a54ac486cece448d4988ab`** — *"Determinize make -jN child-exit
SIGCHLD via timed_waiters at t_exit"* (2026-08-02).

Last good is `c3472722c`, verified to be the **immediate parent** (`git rev-parse 9c233ed0b^ ==
c3472722c`), so the bracket is adjacent rather than a range.

The commit registers the child-exit SIGCHLD as a one-shot timed event on the `timed_waiters`
min-vtime heap at the child's scheduler-ordered `Exit` grant (`t_exit`), dispatched at `t_exit + 1ns`.
It changes **when a child's exit signal reaches the reaping parent**. The SaBRe symptom is the guest's
own assertion `child 0 exit mismatch` — `wait()` returning the wrong exit status for a specific child.
Mechanism and symptom are the same subsystem; this is not a coincidental adjacency.

**Not claimed:** that the commit is wrong. It fixed a real `make -jN` divergence on ptrace. The finding
is that SaBRe does not survive the new `t_exit`-pinned delivery path.

## Method

Cell `determinism-stress-c/fork-tree`. The guest was **compiled once and pinned** (sha256 prefix
`0c880f36bfdd1952014c`), reused at every step, so only hermit varies.

Two confound exclusions, independently:
- `git log 82a8e853..1fadc037 -- tests/e2e/determinism-stress/fork_tree.c` → **0 commits**. The guest
  never changed in the range, so any movement is backend-side.
- The guest was pinned anyway, so not even a build-flag difference can enter.

The guest is built **outside `/tmp`** deliberately: hermit isolates the guest's `/tmp`, and the failure
mode there is `rc=1` with zero output, which misreads as "this binary emits no detlog".

A **ptrace control** at identical flags and guest passes (`5815 | 5815` DETLOG messages compared),
establishing that the divergence is SaBRe-specific rather than a property of the guest or the flags.

### The unbuildable band, and the bisect aid

`git bisect run` halted at *"only skipped commits left"*: **21 candidates, all unbuildable** on a real
compile error, `detcore/src/lib.rs:1484 cannot find value 'config' in this scope` — introduced inside
the band and fixed by `36ee7e70a`.

Resolved with a documented aid rather than a guess: `36ee7e70a` is a **2-line pure compile fix**
(`config` → `guest.config()`, the same value reached through the guest). Applying it to each band
commit makes it testable with no semantic change.

Disposition of all 21: **3 tested directly + 18 excluded by the resulting bracket = 21**, none dropped.
The 8 pre-band bisect verdicts were 3 GOOD / 5 BAD, all `rc=1` (no timeouts needing reclassification).

## Results

See `results.csv`. 8 rows = 6 comparable + 2 NOT-COMPARABLE, and the counts sum.

| commit | role | verdict | trials |
|---|---|---|---|
| `c3472722c` | last good | GOOD, deterministic | 3/3 |
| `9c233ed0b` | **FIRST BAD** | BAD, `child 0 exit mismatch` | 3/3 |
| `82a8e853` / `1fadc037` | range endpoints | GOOD / BAD, same signatures | 1 each |

## Two NOT-COMPARABLE results, each carrying its reason

1. **`1bc63c230` first measured `rc=124` (timeout at 300s).** A timeout is a different *dimension* from
   an `rc=1` divergence and must not collapse into BAD. Retested at a 600s bound: `rc=1`,
   `child 0 exit mismatch`, in **0s wall** — the 124 was a load artifact from a concurrent build, not a
   hang. That reclassification is what **exonerates the SaBRe DETLOG trio** (`86f540972`, `84281c364`,
   `6c9d19ec4`): all three are newer than a commit that was already bad.

2. **Second cell `c-programs/wait-on-child`: NOT-COMPARABLE — side = GOOD-side, dimension = timeout.**
   It times out (`rc=124`, 300s) at `c3472722c`, the commit that is GOOD for fork-tree. A cell that
   fails on the good side cannot discriminate the boundary, so it corroborates nothing and is not
   reported as a second confirmation. `wait_on_child.c` also changed in 0 commits across the range, so
   the timeout is not a guest change.

The task asked for 2–3 cells. **One** cell discriminated cleanly; that is stated rather than presenting
a timeout as agreement.

## Interpretation, and what is deliberately absent

**No parity percentage appears anywhere in this artifact.** Every figure is a per-commit pass/fail on
one named cell with its trial count. The originating sweep's positives are stripped-probe scored, the
stripped comparator misses 3 of 5 planted defects, and strict diverged even on its own `clean_ctrl` —
so a percentage over that corpus would rest on a nondeterministic reference. The 16-cell figure remains
a **lower** bound, as the task states.

## Reproduction

```bash
cd <hermit worktree>
git checkout --detach 9c233ed0b
git show 36ee7e70a -- detcore/src/lib.rs | git apply     # 2-line build aid
cargo build --release --features sabre --bin hermit      # 'sabre' is a pure feature flag, no added deps
gcc -O0 -g -o /tmp-free/path/fork_tree tests/e2e/determinism-stress/fork_tree.c   # NOT under /tmp
./target/release/hermit run --backend sabre --strict --verify --no-virtualize-cpuid \
    --max-timeslice=disabled --base-env minimal -e LC_ALL=C -e TZ=UTC -- <prebuilt fork_tree>
# rc=1 "child 0 exit mismatch".  Repeat at c3472722c for rc=0 deterministic.
```

## Environment notes worth carrying forward

- The shipped primary release binary has **no SaBRe** (`SaBRe support was not included in this build`)
  and self-reports `gf89c69766371-DIRTY`, so nothing measured with it is attributable to a clean commit.
- `tests/backend-parity/run_matrix.py` **cannot produce sabre rows**: its `--backend` choices are
  `{ptrace,dbi,kvm}`. The 16 cells came from a different harness; anyone reproducing via `run_matrix.py`
  will find no sabre backend at all.
