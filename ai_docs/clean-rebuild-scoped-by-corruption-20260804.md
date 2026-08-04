# Clean-rebuild-after-failure, scoped by what can actually corrupt

Owner: hermit-dbi (opus-4.8). Date: 2026-08-04. Task:
`clean-rebuild-after-failure-but-scoped-by-what-can-actually-corrupt` (P0, owner-directed).

Binds to: hermit primary HEAD `11108a3e94b353344a1c6d66eb61614eb9849d63`; reverie PR **#371**
head `c7681cee` (branch `fix/reverie-dbi-buildrs-named-cmake-error`, slot `dbibuild`).

---

## TL;DR

The owner asked: *"shouldn't ANY failure be followed by a clean rebuild — wiping all artifacts?"*
Answer, refined by the task and confirmed here: **no — a blanket wipe is both wrong-costed and
over-broad.** The corruption the owner is right about is real but has exactly ONE shape (a
timestamp-trusting build system linking a killed-mid-write object), and the correct fix is a
**cheap content-fact scan for zero-byte objects**, not a wipe.

Status of the two legs:

1. **Cheap guard + the task's VERIFY — ALREADY DELIVERED by reverie PR #371** (`c7681ce`,
   another in-flight PR on slot `dbibuild`). Its `remove_zero_byte_objects()` + the
   `|| purged_objects > 0` force-rebuild wiring, plus two unit tests, implement the task's
   VERIFY 1:1. I did **not** re-implement it (Invariant 2: `dbibuild` owns
   `reverie-dbi/build.rs`).
2. **Residual gap this doc closes:** #371 self-heals only the **DynamoRIO** tree and only when
   cargo re-runs `build.rs`. It misses (a) the **SaBRe/e9patch** trees built by
   `hermit-install/build.rs`, and (b) an object truncated by a **neighbour's** OOM *after* a
   successful build (cargo fingerprint unchanged → `build.rs` never re-runs → the guard never
   fires). Both are closed by a **pre-flight scan at the harness layer** — the owner's literal
   *"scan for zero-byte objects before trusting a tree … catches corruption from a kill we did
   not observe."* Ready-to-apply `validate.sh` patch below; verified on a real 13 MB object.

---

## 1. The corruption vector (established, not re-derived)

`scheduler_impl.cpp.o` was **0 bytes** in the DynamoRIO cmake tree. A truncated object has no
symbols → `ld` reports `undefined reference to scheduler_impl_tmpl_t<…>::set_cur_input`, which
**reads as a source defect**. cmake keys incremental freshness on **timestamp, not content**, so
it trusts the empty object forever; the corruption is permanent and every later attempt on that
slot fails identically. Mechanism (see memory
`dynamorio-zerobyte-object-oomgroup-defeats-delete-on-error`): `memory.oom.group=1` SIGKILLs the
whole step cgroup **including make**, so make never runs its `.DELETE_ON_ERROR` cleanup. An
OOM-killed *neighbour* plants exactly this (hermit-sabre demonstrated it end-to-end).

**Only timestamp-trusting build systems have this bug.** `rustc`/cargo key freshness on content
fingerprints and self-heal (a killed `rustc` leaves a fingerprint mismatch → rebuild). So the
corruption surface is exactly the **cmake/make** trees, not `target/deps`.

## 2. Why "ANY failure → wipe" is wrong (the measured caveat)

- Cold-cache runs cost **732s median vs 500s warm** (n=14 / n=95). A mandatory wipe adds ~232s to
  every retry, and pushes each retry into the **worse population**: cold fails **79% vs 55% warm**
  (memory `cache-state-cold-fail-is-build-surface-not-slot-or-oom`).
- **Most failures cannot corrupt anything:** a clippy denial, a test assertion, a stale lockfile,
  a manifest mismatch. Wiping after those is pure cost, zero risk reduction.

The zero-byte scan is the resolution: it removes **only** genuinely-corrupt objects, so it is the
scoped rule's cheap superset — it makes the blanket-wipe question moot while preserving the warm
cache and incremental skipping.

## 3. The scoped rule → enforcement-point map

| Owner rule | Enforcement point | Status |
|---|---|---|
| (1) BUILD/COMPILE/LINK failure → wipe (the corrupt object) | `build.rs` guard re-runs after a failed build script; pre-flight scan (below) before every build | #371 (DynamoRIO) + this patch (all trees) |
| (2) TEST/LINT/MANIFEST failure → do NOT wipe | Structural: these are not corruption vectors and the scan only ever removes 0-byte `*.o`; healthy artifacts (and the whole warm cache) are untouched. `validate.sh`'s environmental-retry path is not even entered for a test-assertion failure. | Satisfied by construction |
| (3) ANY KILL (OOM/timeout/cpu_timeout) → wipe regardless of step | The scan is content-based, so it catches truncation from any kill independent of which step ran, including an **unobserved** neighbour kill | #371 (partial) + this patch (full) |

Key insight: rules (1)/(2)/(3) matter only for a **blanket** wipe. The zero-byte scan sidesteps the
classification entirely — it is a fact, not a heuristic, and never touches anything that a
test/lint/manifest failure could have produced.

## 4. Coverage: what #371 covers vs the residual gap

Timestamp-trusting object trees under `target/*/build/*/out/`:

| Tree | Built by | Guarded by #371? | Closed by pre-flight scan? |
|---|---|---|---|
| DynamoRIO (cmake) | `reverie-dbi/build.rs` | **yes** (`remove_zero_byte_objects` on `build_dir`, forces rebuild) | yes (belt-and-suspenders) |
| SaBRe (cmake) | `hermit-install/build.rs` (`build_sabre`) | **no** | **yes** |
| e9patch (make) | `hermit-install/build.rs` (`build_e9patch`) | **no** | **yes** |
| rust `target/deps` | cargo/rustc | n/a (content fingerprints self-heal) | scan is `*.o`-scoped; harmless |

**The `build.rs`-only gap:** cargo re-runs a build script only when its `rerun-if-*` inputs change
or the prior run failed. A neighbour OOM that truncates an object in an **already-built** tree
leaves my crate's fingerprint unchanged → `build.rs` is skipped → the guard never fires → the next
`cargo build` links the corrupt object. A **pre-flight** scan, run before any build and independent
of cargo's fingerprint decision, is the only thing that closes this. This is exactly the owner's
*"before trusting a tree."*

## 5. Ready-to-apply patch — `hermit/validate.sh`

Placement chosen so the scan runs **before this run builds anything** (same rationale as the
existing `detect_cache_state`), covers every cmake/make tree in one pass, and is `O(files)` fast.

**(a)** Add the function immediately after `detect_cache_state` closes (after line ~610):

```bash
# Artifact-integrity pre-flight. A compiler/archiver killed mid-write leaves a
# TRUNCATED zero-length *.o in a build tree — classically the OOM-killer firing on
# a NEIGHBOUR's step cgroup with memory.oom.group=1, so make never runs its
# .DELETE_ON_ERROR cleanup. cmake/make key incremental freshness on TIMESTAMP not
# CONTENT, so they trust the empty object forever and link it, producing an
# "undefined reference" that reads as a source defect and never self-corrects.
# Scan before we trust the tree and delete any such object so the build rebuilds
# it. This is a CONTENT FACT, not a heuristic: it removes ONLY genuinely-corrupt
# (0-byte) objects, so healthy artifacts — and thus incremental skipping and the
# warm cache — are preserved (a blanket "clean rebuild after any failure" would
# not be: cold rebuilds cost +232s and fail more). Covers DynamoRIO (reverie-dbi),
# SaBRe + e9patch (hermit-install); rustc's target/deps self-heal via fingerprints.
# Catches corruption from a kill we did not observe, which the per-crate build.rs
# guard cannot: cargo re-runs a build script only on input change or prior failure,
# so a neighbour that truncates an already-built object is otherwise linked as-is.
function purge_zero_byte_objects {
    local root=$1 removed=0 f
    [[ -d $root ]] || { printf 0; return 0; }
    while IFS= read -r -d '' f; do
        rm -f -- "$f" && removed=$((removed + 1))
    done < <(find "$root" -type f -name '*.o' -size 0 -print0 2>/dev/null)
    printf '%s' "$removed"
}
```

**(b)** Call it right after the `Build cache:` line prints (after line ~727):

```bash
VALIDATION_ZERO_BYTE_PURGED=$(purge_zero_byte_objects "$ROOT_DIR/target")
readonly VALIDATION_ZERO_BYTE_PURGED
if ((VALIDATION_ZERO_BYTE_PURGED > 0)); then
    printf "🧹 Artifact-integrity: purged %s zero-byte object(s) from target/ before build (killed/OOM-truncated; would otherwise link as 'undefined reference'). Rebuild will regenerate them.\n" \
        "$VALIDATION_ZERO_BYTE_PURGED"
    printf "validate.sh: purged %s zero-byte object(s) from target/ pre-build\n" \
        "$VALIDATION_ZERO_BYTE_PURGED" >>"$LOG_FILE"
fi
```

Optional (recommended): add `,"zero_byte_purged":$VALIDATION_ZERO_BYTE_PURGED` to the ledger
record beside `cache_state` (line ~1177) so the frequency of real corruption becomes measurable.

Landing: needs a hermit worktree slot. `validate.sh` is currently exercised by several live
`lander-validate-*` units — apply on a feature branch/slot and land when the landing wave settles
(the change only adds a fast pre-build scan on a path that already runs before builds; no behavior
change when zero corruption is present, so it is safe to stack).

## 6. Verification (real 13 MB object, not just the unit fixture)

Fixture from a warm `land-1604` tree; scan is the exact `validate.sh` function above.

```
BEFORE: truncated.cpp.o=0B  scheduler_impl.cpp.o=13232192B  options.cpp.o=13232192B  progress.marks=0B
RUN:    removed_count=1
AFTER:  scheduler_impl.cpp.o=13232192B  options.cpp.o=13232192B  progress.marks=0B  (truncated.cpp.o gone)

PASS truncated .o REMOVED (next build rebuilds it, not link-against)      [VERIFY step 1]
PASS healthy .o KEPT + byte-identical (incremental SKIPS it)             [VERIFY step 2]
PASS zero-byte NON-object (progress.marks) KEPT (scan is .o-scoped)
PASS exactly 1 removed
PASS clean incremental tree untouched: removed=0, 2/2 objects skipped     [VERIFY step 2]
```

Both halves of the task's VERIFY hold: a truncated object is rebuilt on the next attempt, and a
normal incremental tree still skips every unchanged object (this is NOT bought by disabling
incremental builds).

At the crate level the same behavior is proven by #371's unit tests
(`zero_byte_objects_are_removed_and_healthy_ones_kept`,
`incremental_tree_with_no_truncation_is_untouched`) and the earlier manual end-to-end relink at
fixtures `fedc81ed`/`310a3689` (`make drmemtrace_launcher -j2` rc=0, `scheduler_impl.cpp.o`
rebuilt to 13,232,192 B, `set_cur_input` DEFINED).

## 7. Routing

- **Guard leg** = reverie PR **#371** (`c7681ce`), owned by `hermit-dbibuild`. Lands independently;
  satisfies the task's cheap-guard + VERIFY. Do not duplicate.
- **Pre-flight scan leg** = the `validate.sh` patch in §5, verified in §6. Hand to whoever holds a
  hermit slot after the current landing wave; land as its own small PR (`[impl agent, opus-4.8]`).
- Do **not** add a blanket `cargo clean`/wipe-on-any-failure — §2/§3 explain why it is a net
  regression.
