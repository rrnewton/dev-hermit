# readdir/getdents enumeration-ordering determinism

**Date:** 2026-08-06 · **Task:** `readdir-ordering-determinism` · **Agent:** hermit-det2
**Binary under test:** `hermit 0.2.0 (gf89c69766371)`, clean-tree debug build at hermit
`f89c69766371806d3c9b2c3003531df2d59d6118` (= hermit primary `main` tip on this date)
**Backend:** ptrace · **Flags:** `run --strict` (`virtualize_metadata` defaults on) · **Relaxations:** none
**Host:** devbig014, kernel 6.18.39, glibc 2.34, btrfs + tmpfs

## Question

Filesystem enumeration order is a classic nondeterminism source: `getdents64` returns entries
in filesystem-dependent order, so a program that iterates a directory can diverge run-to-run
and host-to-host while every syscall "succeeds". Does Hermit determinize it?

## Headline

**Hermit determinizes directory enumeration order only when the whole directory fits in a single
`getdents64` result buffer. It does not determinize a multi-buffer directory stream.** The
determinizer sorts *each result buffer independently*; which names land in which buffer is decided
by the host filesystem, so for any directory larger than the guest's buffer the global enumeration
order remains host- and filesystem-dependent. Three further guest-visible values inherit the same
leak or are broken outright: `d_off` cookies, `telldir`/`seekdir`, and virtual inode numbers.

This is a *silent* class: the run-to-run double-run gate (`--verify`, and `--verify` with
`--detlog-heap --detlog-stack`) is **green** on exactly the cells that leak.

## Mechanism (source)

`detcore/src/syscalls/files.rs::handle_getdents64` (line 2615; `handle_getdents`, line 2575, is
identical in shape):

```rust
let nb = self.record_or_replay(guest, call).await?;   // one host getdents64
...
let mut dents = unsafe { deserialize_dirents64(&dents_bytes) };
dents.sort();                                          // <-- sorts THIS BUFFER only
for dent in &mut dents { dent.ino = determinize_inode(guest, dent.ino).await.0; }
let _ = unsafe { serialize_dirents64(&dents, &mut dents_bytes) };
```

The comparator is `impl Ord for Dirent64` in `detcore/src/dirents.rs` (`"." < ".." <= rest`,
then bytewise name order). The handler holds no per-fd state: there is no cached, globally
sorted view of the directory stream, so it has no way to sort across buffers. Two consequences
follow directly from the code, both confirmed by measurement:

1. The host decides the **partition** of names into buffers. Sorting inside a host-chosen
   partition does not produce a host-independent global order.
2. Each `Dirent64` carries its original `off` (the kernel's resume cookie) through the sort, so
   after reordering the emitted `d_off` sequence is a permutation of the host's — no longer
   monotonically increasing, and no longer meaningful as a `seekdir` target for the entry it
   now follows.

`determinize_inode` → `tool_global.rs::recv_determinize_inode` → `inodes.add_inode` assigns a
virtual inode on **first sight**, so the virtual-inode assignment order is the guest-visible
emission order — which is why the inode leak is downstream of the ordering leak.

## Method

Two guest programs plus one off-the-shelf binary enumerate fixture directories:

| guest | path exercised |
| --- | --- |
| `fixtures/dirls_raw.c` | raw `getdents64(2)` at a **caller-chosen** buffer size (makes batch boundaries and `d_off`/`d_ino` observable) |
| `fixtures/dirls_libc.c` | ordinary `opendir`/`readdir` (glibc picks the buffer: 32 KiB) |
| `/usr/bin/ls -f -a` | a real program, readdir order, unsorted |
| `fixtures/seekdir_rt.c` | POSIX `telldir`/`seekdir` round-trip contract |

Fixtures (`make_fixtures.sh`): directories `asc{10,200,2000}` and `desc{10,200,2000}` hold the
**identical name set** (`n0000…`), created in ascending vs descending order. On btrfs and tmpfs
the directory index tracks creation order, so `ascN` and `descN` are the same content in
**opposite host order**. That pair is the local proxy for "same program, same content, different
host / different filesystem". A second, independent proxy is running the *same* fixture on two
filesystems (btrfs vs tmpfs).

Full results: `results.csv` (also `results/btrfs/results.csv`); curated excerpts in `EVIDENCE.md`;
per-cell stdout under `results/btrfs/raw/` (gitignored — regenerate with `run_sweep.sh`).

## Results

### 1. Ordering — single-batch is clean, multi-batch leaks

`asc_eq_desc` = do the two directories with the identical name set produce the identical
guest-visible order? `run1_eq_run2` = are two hermit runs on a fixed directory identical?

| cell | buffer | batches | mode | globally sorted (desc dir) | asc == desc | run1 == run2 |
| --- | --- | --- | --- | --- | --- | --- |
| `dirls_raw` n=10 | 4096 | 1 | native | no | **no** | – |
| `dirls_raw` n=10 | 4096 | 1 | hermit | yes | **yes** | yes |
| `dirls_raw` n=200 | 65536 | 1 | hermit | yes | **yes** | yes |
| `dirls_raw` n=2000 | 65536 | 1 | hermit | yes | **yes** | yes |
| `dirls_raw` n=200 | 1024 | 7 | hermit | **no** | **no** | yes |
| `dirls_raw` n=200 | 512 | 13 | hermit | **no** | **no** | yes |
| `dirls_raw` n=2000 | 4096 | 16 | hermit | **no** | **no** | yes |
| `dirls_libc` n=200 | glibc 32 KiB | 1 | hermit | yes | **yes** | yes |
| `dirls_libc` n=2000 | glibc 32 KiB | 2 | hermit | **no** | **no** | yes |
| `ls -f -a` n=200 | glibc 32 KiB | 1 | hermit | yes | **yes** | yes |
| `ls -f -a` n=2000 | glibc 32 KiB | 2 | hermit | **no** | **no** | yes |

Batch counts for the glibc rows are measured, not inferred: `hermit --log info run --strict --
dirls_libc <dir>` shows **2** `getdents64` calls for n=200 (one 6448-byte result plus the
zero-length EOF call — i.e. one data batch) and **3** for n=2000 (two data batches plus EOF).

Magnitude on the leaking cells: **200 of 202** positions differ (n=200, buffer 1024) and
**2000 of 2002** positions differ (n=2000, glibc `readdir`). Only `.` and `..` stay put.

The `ls -f` and `dirls_libc` rows matter most: no unusual buffer size is required. glibc's
32 KiB `readdir` buffer holds ≈1000 short-named entries, so **any ordinary program enumerating a
directory of roughly a thousand or more entries is affected on the default path.**

### 2. Ordering — cross-filesystem, identical content and creation order

| | first entries after `.` `..` |
| --- | --- |
| hermit, btrfs `desc200`, buffer 1024 | `n0170 n0171 n0172 n0173` |
| hermit, tmpfs `desc200`, buffer 1024 | `n0000 n0001 n0002 n0003` |

**200 of 202** positions differ. Same hermit binary, same flags, same guest, same directory
content and creation order — only the filesystem differs.

### 3. `d_off` cookie monotonicity — violated whenever host order ≠ sorted order

Linux guarantees `d_off` increases monotonically across a directory stream.

| directory | buffer | native inversions | hermit inversions |
| --- | --- | --- | --- |
| `asc200` | 65536 / 1024 | 0 / 0 | 0 / 0 |
| `desc200` | 65536 / 1024 | 0 / 0 | **199** / **193** (of 202) |
| `desc2000` | 65536 / 1024 | 0 / 0 | **1999** / **1937** (of 2002) |

Note this fires at buffer 65536 too — i.e. **even on the single-batch cells where the name
ordering is correctly determinized.** This defect is broader than the ordering defect.

### 4. `telldir`/`seekdir` round-trip — broken by the permuted cookies

`seekdir(telldir_before(E))` must resume at `E`. First 64 entries:

| directory | native | hermit |
| --- | --- | --- |
| `asc200` (host order already sorted) | 0/64 mismatches, PASS | 0/64 mismatches, PASS |
| `desc200` | 0/64, PASS | **61/64 mismatches, FAIL** |
| `desc2000` | 0/64, PASS | **62/64 mismatches, FAIL** |

This is a Linux-semantics violation, not merely a determinism gap: it is wrong on a single host.

### 5. Virtual inode numbers inherit the leak

Virtual `d_ino` for the same name, `asc2000` vs `desc2000`:

| buffer | batches | names given a different virtual inode |
| --- | --- | --- |
| 65536 | 1 | 0 of 2002 |
| 4096 | 16 | **2000 of 2002** |

### 6. The existing gates do not catch any of this

| gate | multi-batch leaking cell (`asc2000`, buffer 4096) | control `/bin/true` |
| --- | --- | --- |
| `--strict --verify` (Stripped comparator) | **rc=0, "Success: deterministic"** (426 DETLOG messages compared) | rc=0 |
| `--strict --verify --detlog-heap --detlog-stack` (L3) | **rc=0, "Success: deterministic"** (531 DETLOG messages compared) | – |
| `--strict --verify --verify-strict` (L2 canonical) | rc=1, `bitwise_parity: false` | **rc=1, `bitwise_parity: false`** |

`--verify` and L3 pass because they compare two runs *on the same host against the same directory*,
where the host order is identical by construction. **The double-run parity design cannot see a
host-order leak; it needs a host-order-perturbing oracle** — which is exactly what the
`ascN`/`descN` fixture pair provides.

The `--verify-strict` red is **not attributable to readdir**: the `/bin/true` control reds the same
way, diverging at log messages 5–6 on the DEBUG-level `reverie_ptrace::vdso: N patched __vdso_*`
line ordering and on `detcore::tool_global: Nondeterministic realtime elapsed`. L2 could not be
established for any cell on this host, including the control. (Consistent with the standing
"L2 unattainable on this box" observation; it is a separate defect, not part of this finding.)

### 7. The existing regression coverage cannot detect the ordering gap

`hermit/tests/backend-parity/fixtures/readdir_entries.c` is the only readdir/getdents fixture in
the repo. It (a) uses **three** entries — always a single buffer, the case that already works —
and (b) **`qsort`s the names in the guest** before asserting, explicitly "so the result is
independent of on-disk directory order" (its own comment, line 5). It is therefore structurally
incapable of observing enumeration-order nondeterminism. It is also `ci=false` in
`tests/e2e/manifests/backend-parity-c.toml`, so it does not run in blocking CI.

## Interpretation

Hermit's directory-enumeration determinization is a **per-syscall** transform applied to a
**per-stream** problem. Sorting a host-chosen subset is not a determinization: it hides the leak
on small directories (which is why this has gone unnoticed) and preserves it on large ones.

The root-cause fix is to make the *stream*, not the *buffer*, the unit of determinization:
on the first `getdents64` against a directory fd, drain the host directory completely, sort once,
cache the sorted vector in per-fd state, and serve every subsequent `getdents64` from that cache,
**synthesizing monotonically increasing `d_off` cookies** (e.g. the 1-based index in the sorted
vector) so that the cookie is a valid `seekdir` target into the determinized stream and
`lseek`/`seekdir` resumes from the cached vector. That also repairs findings 3, 4, and 5 in one
move, since virtual inodes would then be assigned in a host-independent order.

Costs and open design questions a fix must answer, all real:

- **Unbounded buffering.** Draining a directory with millions of entries into detcore memory is a
  new resource cost and a new failure mode. A cap needs a defined behavior — and per the standing
  rule, "coarsen/freeze the value" is not an acceptable escape (#140); a documented fail-closed
  UNSUPPORTED is preferable to a silently host-dependent result.
- **Concurrent mutation.** Linux's readdir semantics for entries created/removed mid-stream are
  already loose; a snapshot changes them. Draining at first `getdents64` is arguably *more*
  deterministic, but it is a Linux-semantics change and needs to be stated.
- **Per-fd state and `dup`/`fork` sharing.** The cache must key on the open file description, not
  the fd number, and must survive `dup2`/`fork`.
- **Which layer.** The transform lives in shared Detcore, so a fix reaches all backends at once
  (ptrace/DBI/KVM/SaBRe/LiteInst all load the same tool); it does not need per-backend work.

Two of the five findings — `d_off` non-monotonicity (3) and the broken `telldir`/`seekdir`
round-trip (4) — are **plain correctness bugs on a single host**, independent of the determinism
argument, and are cheap to fix even without the streaming rework (reissue synthetic monotonic
cookies within the emitted buffer, plus per-fd resume state).

## Reproduction

```bash
cd experiments/readdir-ordering-determinism_20260806

# libunwind is not installed system-wide on devbig014:
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib

for f in dirls_raw dirls_libc seekdir_rt; do gcc -O1 -Wall -Wextra -o fixtures/$f fixtures/$f.c; done

./make_fixtures.sh /home/newton/scratch-readdir-det/btrfs   # any non-/tmp path: hermit's
./make_fixtures.sh /dev/shm/rdd                             # container has a private /tmp

HERMIT=<path-to-hermit> ALT_ROOT=/dev/shm/rdd \
  ./run_sweep.sh /home/newton/scratch-readdir-det/btrfs results/btrfs

column -s, -t results/btrfs/results.csv
```

Smallest single reproducer of the headline finding:

```bash
# same 200 names, opposite host order, 1 KiB getdents64 buffer
hermit run --strict -- ./fixtures/dirls_raw <root>/asc200  1024 | head -4
hermit run --strict -- ./fixtures/dirls_raw <root>/desc200 1024 | head -4
# => ". .. n0000 n0001"   vs   ". .. n0170 n0171"
```

## Follow-up work this justifies

1. **Product fix (hermit):** stream-level dirent determinization with a per-open-file-description
   sorted cache and synthesized monotonic `d_off`. Touches `detcore/src/syscalls/files.rs` and
   `detcore/src/dirents.rs`; shared across all backends. Likely triggers `post-facto-human-review`
   under "a new determinization strategy".
2. **Cheap standalone fix (hermit):** synthesize monotonic `d_off` in the emitted buffer and keep
   per-fd resume state, repairing findings 3 and 4 without the full rework.
3. **Regression coverage (hermit):** a host-order-perturbing fixture — same name set, two creation
   orders, a directory larger than glibc's 32 KiB buffer — asserting byte-identical enumeration
   between the two. Note the existing `readdir_entries.c` cannot serve: it sorts in-guest.
4. **Gate gap (dev-hermit):** the double-run `--verify` design is structurally blind to
   host-order leaks. Any future "compat sweep" that relies on double-run parity alone should be
   understood to be blind to this whole class.
5. **Unmeasured, expected worse:** on ext4 with `dir_index`, `d_off` is derived from a
   per-filesystem hash seed, so the raw cookies Hermit passes through are host-specific values,
   and the enumeration order is a name-hash order rather than a creation order. Neither was
   measured here (no ext4 mount available without root).
