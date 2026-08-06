# `FileContents(N)` embeds a raw host inode: confirmed, root-caused, and scoped

**Task:** `detlog_embeds_raw_host` · hermit-clone (opus-5), 2026-08-06
**Local, no egress.** hermit release binary, primary checkout, **read-only** (no product edit — see
*Landing*). Probe: `scratch/inodedet/fw.c`.

## 1. Premise independently confirmed

`fw.c` creates its own file and `pwrite64`s to it. Two runs, `--backend ptrace --strict`:

| | result |
|---|---|
| stdout | **identical** |
| detlog (`--log-file`) | **differs** |
| the differing token | `FileContents(221742951)` vs `FileContents(221742955)` |

## 2. The inode is the SOLE divergence — not a correlate

The whole-log diff is **exactly 2 lines** (one COMMIT record, each direction):

```
< COMMIT turn 14, dettid 3 using resources {FileContents(221742951): W}, on previously committed 1_767_225_600.008_288_735s
> COMMIT turn 14, dettid 3 using resources {FileContents(221742955): W}, on previously committed 1_767_225_600.008_288_735s
```

Normalising **only** `FileContents\([0-9]+\)` → `FileContents(NORM)` makes the two logs
**byte-identical**. So the raw host inode is the entire cause of detlog non-reproducibility for this
guest — nothing else drifts, including the virtual timestamp.

## 3. Root cause, at file:line

Four sites construct the resource id from a **raw** inode, as a fallback when the deterministic
resource is absent:

```rust
// detcore/src/syscalls/files.rs:969 (and :849, :1226, :1256)
let (resource, raw_ino) = guest.thread_state().with_detfd(call.fd(), |detfd| {
    (detfd.resource(), detfd.stat().map(|stat| stat.inode))   // <- HOST inode
})?;
let resource = resource.or_else(|| raw_ino.map(ResourceID::FileContents));
```

**The determinization mechanism already exists and is already correct** — this path just bypasses it:

- `determinize_inode()` — `detcore/src/tool_global.rs:2402`, a `GlobalRequest::DeterminizeInode` RPC
  returning a `DetInode`.
- The allocator is a monotonic per-run counter: `next_inode: 1` (`tool_global.rs:157`), incremented
  at `:168-169`. That yields a **guest-scoped ordinal stable across runs and across hosts** —
  precisely the identity the task asks for.
- It is already used on the neighbouring path at `files.rs:441`.

**Why the compiler could not catch this:** `detcore-model/src/fd.rs:22` reads

```rust
pub type DetInode = RawInode;
```

a bare **type alias**, so `ResourceID::FileContents(DetInode)` accepts a raw host inode with no
diagnostic. The field is documented as deterministic and typed as anything-but.

## 4. The fix (two parts; the second prevents recurrence)

1. **Determinize at the four construction sites.** Replace the raw fallback with
   `determinize_inode(guest, raw_ino).await.0` before building `ResourceID::FileContents`. These are
   `async` contexts already (`resource_request(guest, …).await` follows immediately), so the RPC is
   available without restructuring. Note the sites are inside a closure that borrows
   `guest.thread_state()` — the determinization must happen **after** that closure returns, on the
   extracted `raw_ino`.
2. **Make `DetInode` a newtype** rather than `type DetInode = RawInode`. This converts the entire bug
   class from "reviewer must notice" into a compile error. It is the reason to expect other
   instances; grep for other `RawInode`-into-`DetInode` flows before assuming these four are all.

**Not the fix:** normalizing `FileContents(N)` in the comparator. That hides an
environment-derived value inside a record that is *specified* to be reproducible, and it is the
#140 shape — making the check pass rather than making the log deterministic.

## 5. Which prior parity "passes" were affected — the answer inverts the premise

The task states "every cross-backend detlog comparison we've run on file-writing programs was
comparing logs containing host-specific data." Checked against the artifact:

**`compat-envelope/scorecard.csv` has no detlog column.** Header:
`run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason`.
`parity` is a stdout SHA (`output_hash`), consistent with the previously recorded finding that the
scorecard's parity column is a stdout hash, not a bitwise or detlog comparison.

So of **514 rows carrying a parity value, zero were computed from a detlog** — meaning:

- **No recorded parity verdict needs retracting on account of this defect.** Nothing was corrupted.
- **The real exposure is the opposite one:** those 514 verdicts never had the power to detect it.
  This defect has been invisible not because it is rare but because the only instrument that would
  see it has never been run in a gate.

**Self-audit of my own detlog work today, since I am the one who ran cross-backend detlog
comparisons:** the e9patch-vs-ptrace comparison (1120 vs 1132 lines, 6 extra loader syscalls) used
`churn3`, which opens `/dev/null` — a device, not a regular file. Its detlog contains **0
`FileContents` records** (measured). That finding is **not** contaminated.

**Who IS exposed going forward:** any detlog/record-replay comparison over a guest that writes a
regular file. Of the six file-IO modes in the originating report, exactly the two emitting a
`FileContents` record (`preadwrite`, `sendfile`) diverged; `bigread` and `readvwritev` emit none and
were byte-identical — a perfect correlation, reproduced here on a third guest.

## 6. Reproduction

```sh
cd scratch/inodedet && gcc -O2 -o fw fw.c
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64
for i in 1 2; do rm -f fw.dat
  hermit --log=info --log-file=$PWD/run$i.log run --backend ptrace --strict -- $PWD/fw \
    > run$i.out 2> run$i.err; done
cmp run1.out run2.out    # identical
cmp run1.log run2.log    # DIFFERS -- one COMMIT record, FileContents(N)
```

Write the log **outside `/tmp`**: hermit replaces guest `/tmp` and the file silently never appears.

## 7. Landing

**No product edit made.** The change is hermit product code and I hold no worktree slot; Hard
Invariant 1 forbids feature development in the primary checkout, and slot provisioning is
coordinator-only. Handoff is this document plus the four `file:line` sites and the newtype
recommendation. The verification for whoever lands it is section 6: after the fix, `cmp run1.log
run2.log` must be silent **without** any comparator-side normalization, and the recorded id should
be a small ordinal (from `next_inode: 1`), not a ~2.2e8 host inode.
