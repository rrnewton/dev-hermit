# CMake trusts mtime, not content: a truncated object is a permanent fake-green

**Task:** `cmake-trusts-mtime-not-content-so-a-truncated-artifact-is-permanent` ·
**Agent:** hermit-audit (`[impl agent, opus-5]`) · **2026-08-06** · local only, no egress.

## Question

Does CMake/make decide "is this object up to date?" by **timestamp** rather than **content**, so a
truncated artifact is trusted permanently — and can content validation fix it **without** giving up
incremental builds?

## Answer

**Yes, and the safeguard that should have caught it is bound to the wrong thing.** Two separate
defects, both demonstrated:

1. **Freshness is mtime-only.** A 0-byte object whose mtime is newer than its source satisfies make's
   rule. `gmake -q` reports **UP TO DATE** on an empty object, make never recompiles, and the link
   fails with `undefined reference` naming a source symbol — indistinguishable from a missing template
   instantiation.
2. **`.DELETE_ON_ERROR` is bound to make's own survival, not to the artifact.** CMake 3.31 *does* emit
   `.DELETE_ON_ERROR:` (line 5 of every generated `build.make`) and it fires correctly on an ordinary
   compile failure **and** when the recipe's child is SIGKILLed while make survives. It cannot fire when
   the **whole process group** is killed — which is exactly what `memory.oom.group=1` does — and it does
   not apply at all when a recipe *succeeds* having written zero bytes.

So the corruption window is not "any OOM"; it is precisely **group-kill** and **silent short write**.
Everything else self-heals.

The fix is cheap and measured: content validation over the real 834-object / 407 MiB DynamoRIO tree
costs **0.25 s** (ELF magic) or **0.51 s** (full sha256) against a 500–800 s validate run — **under
0.1%** — and it triggers **zero** spurious rebuilds.

## Method

The instrument is a 4-TU C++ project whose `build.make` is a faithful replica of the rule shape CMake
3.31 actually generated for DynamoRIO, taken verbatim from the live tree
(`worktrees/226/hermit/target/debug/build/reverie-dbi-89ddd5351296fa25/out/dynamorio-build/clients/drcachesim/CMakeFiles/drmemtrace_launcher.dir/build.make`):

```make
# Delete rule output on recipe failure.
.DELETE_ON_ERROR:
...
clients/.../scheduler_impl.cpp.o: clients/.../flags.make
clients/.../scheduler_impl.cpp.o: /home/newton/.cargo/git/checkouts/.../scheduler_impl.cpp
clients/.../scheduler_impl.cpp.o: clients/.../compiler_depend.ts
	cd ... && /usr/bin/c++ ... -MD -MT <obj> -MF <obj>.d -o <obj> -c <src>
```

Three prerequisites, all compared by **mtime**. There is no content check anywhere in the generated
build system. `CMAKE_GENERATOR: Unix Makefiles`, `CMAKE_MAKE_PROGRAM: /usr/bin/gmake` (confirmed from
`CMakeCache.txt`; the Ninja branch in `reverie-dbi/build.rs:308` is not the path in use). The replica
reproduces the reported symptom exactly, including a template symbol so the link error matches the
class reported in the field.

`cmake` and `ninja` are **not on PATH on this box** (consistent with the known
"dynamorio configure ENOENT = missing cmake" finding), so the experiment uses the generated-Makefile
layer directly — which is where the defect lives anyway.

## Results

Full table in `results.csv`; `bash repro.sh` regenerates every line from scratch in ~40 s.

### The defect

| id | mutation | observation |
| --- | --- | --- |
| E1 | none (control) | 0 of 4 rebuilt → **SKIPPED 4 of 4**; `gmake -q` rc=0 on all four |
| **E2** | `truncate -s 0 scheduler_impl.cpp.o` (obj mtime 20:21:35 > src 20:21:07) | **`gmake -q` rc=0 — make says UP TO DATE.** No recompile. Link: `undefined reference to sched_t<int>::set_cur_input(int, int, bool)` |
| E3 | `touch` the source (control) | exactly 1 of 4 rebuilt, object restored, app runs — the source dependency is intact; only *content* is unchecked |

### Where `.DELETE_ON_ERROR` holds and where it breaks

| id | failure shape | make sees | artifact |
| --- | --- | --- | --- |
| E4a | recipe writes 0 B then exits 1 (ordinary compile error) | rc=2, prints `Deleting file` | **DELETED** ✅ |
| E4b | recipe SIGKILLs itself, make survives (single-process OOM) | rc=2, prints `Deleting file` | **DELETED** ✅ |
| **E4c / F3-naive** | **whole process group SIGKILLed** (`memory.oom.group=1`) | make exits 137, no cleanup runs | **SURVIVES 0 B**, and `gmake -q` rc=0 forever ❌ |
| E4d | recipe *succeeds* having written 0 B (silent short write) | rc=0 | **SURVIVES 0 B** ❌ |

This corrects the loose framing that "OOM leaves a poisoned object": an OOM that kills only `cc1plus`
is harmless. It is the **group** kill that converts a transient OOM into permanent build poisoning.

### The two fixes, both bracketed

| id | fix | result |
| --- | --- | --- |
| **F1** | `verify-objects` (empty / not-ELF / sha256-sidecar mismatch → delete) run over a tree with one truncated object | `checked=5 corrupt=1`, rc=1, object deleted → next build rebuilt **exactly 1 of 4** → app works again |
| **F2** | same validator on an unmodified tree | `checked=5 corrupt=0`, rc=0 → **0 of 4 rebuilt, SKIPPED 4 of 4**, `make: 'app' is up to date` — **the fix is not bought by disabling incremental builds** |
| **F3-naive** | plain recipe, group-killed mid-compile | target **SURVIVES 0 B**; `gmake -q` rc=0 → poisoned |
| **F3-atomic** | same kill through `cc-atomic` (compile to `$out.tmp.$$`, `mv` only on rc=0 **and** non-empty) | target **ABSENT**; `gmake -q` rc=1 → **make rebuilds it**. Prevented. |

### Cost (measured on the live tree, 834 objects / 407.4 MiB)

| check | wall | cpu |
| --- | ---: | ---: |
| ELF magic (4 bytes/object) | 0.25 s | 0.09 s |
| full sha256 | 0.51 s | 0.44 s |

Against a 500–800 s full validate, **< 0.1%**. Cost is not a reason to skip this.

## Live sweep (2026-08-06T03:16Z)

101 zero-byte artifacts across the whole parent workspace, classified:

| class | count | corrupt? |
| --- | ---: | --- |
| vendored `vcpkg-0.2.15/test-data/**.a` fixtures (mtime 1969-12-31) | 50 | **no** — intentional empty test data |
| rust `*.rcgu.o` (cargo codegen units) | 47 | excluded per the prior scope correction; a 0-byte CGU object is plausible-normal and was **not** verified as corrupt |
| hand-built `scratch/ptrace-stress-20260727/sig_{a,b,c}.o` (2026-07-27) | 3 | probable, but a one-off scratch build, not a CI tree |
| **CMake-produced `.../dynamorio-build/clients/lib64/release/libdrpoints.so`** (2026-08-04 11:44:50) | **1** | **yes** |

**The 2026-08-04 cluster of 29 (11 in real slots) has decayed to 1**, consistent with *one kill event
followed by rebuilds* rather than a recurring condition — which is the discriminator the task asked for.

**And the one survivor is a `.so`, not a `.o`.** That matters: the two mitigations currently in flight
both key on `*.o` and would miss it —
`validate.sh::purge_zero_byte_objects` (PR #1616) uses `find "$root" -type f -name '*.o' -size 0`, and
the proposed `reverie-dbi/build.rs` pre-scan is described the same way. A truncated `.so`/`.a`/`.rlib`
is exactly as linkable-and-wrong as a truncated `.o`.

## Proposal

Ordered by leverage. (1) and (2) are small and independent; (3) is the durable one.

1. **Widen the existing purge to every emitted binary artifact, not just `*.o`.** One-line change to
   PR #1616's `find`: `-name '*.o' -o -name '*.a' -o -name '*.so' -o -name '*.rlib' -o -name '*.lo'`.
   Today's only live corrupt artifact is invisible to the current form. *Positive control to ship with
   it:* plant a 0-byte `.so`, confirm the purge removes it and reports a nonzero count; plant a healthy
   one, confirm it survives (I ran exactly this shape for `.o` during the PR #1616 audit — 2 purged,
   healthy `.o` and `.a` untouched, missing root → 0).

2. **Upgrade emptiness to a content predicate.** `size == 0` is a proxy for "corrupt"; the observable
   fact is "this file is not a valid object". Check the 4-byte ELF magic — it costs 0.25 s for the whole
   DynamoRIO tree and additionally catches the truncated-to-N-bytes case that a size floor cannot. This
   is the same Proxy-Binding move as everywhere else: **bind the check to the artifact's identity, not
   to a correlate of it.**

3. **Make object emission atomic via `CMAKE_<LANG>_COMPILER_LAUNCHER`.** This is prevention, it is a
   supported CMake hook, and it needs **no patch to DynamoRIO**: set
   `CMAKE_C_COMPILER_LAUNCHER`/`CMAKE_CXX_COMPILER_LAUNCHER` to a wrapper that compiles to
   `$out.tmp.$$` and renames only on `rc == 0 && -s $tmp`. A killed compiler then leaves **no** `$out`,
   so make rebuilds on the next run by its ordinary rule (F3-atomic, proven). `reverie-dbi/build.rs`
   already threads `CMAKE_GENERATOR` through, so adding two more cache entries is the same shape of
   change.
   *Residual, disclosed:* each kill leaves one `$out.tmp.$$` behind (F3-atomic left 1). Sweep
   `*.tmp.*` at configure time or age them out; they are inert (make never links a file it has no rule
   for) but they consume disk.

4. **Only if 1–3 prove insufficient: sha256 sidecars.** Write `$obj.sha256` beside each object on
   successful compile and verify before link. Full-tree cost 0.51 s. This is the only variant that
   catches a corruption which preserves a valid ELF header, but no such instance has been observed —
   so ship it behind the evidence, not ahead of it.

**Do not "clean the build dir after any failure."** That is the tempting blanket fix and it is the
expensive wrong one: a cold DynamoRIO rebuild costs ~232 s and fails more often, and F2 shows the
targeted validator preserves the warm cache completely (4 of 4 skipped).

### What to require of any patch here

The mutation bar this experiment sets, and which every proposed fix should have to clear:

* **Negative:** plant a truncated artifact of the relevant kind → the build must rebuild it or fail
  closed. State the count found.
* **Positive:** run the identical check on an unmodified warm tree → **state N skipped** and confirm the
  check fires nothing. A fix that heals corruption by defeating incrementality is not a fix.

## Reproduction

```bash
bash experiments/cmake_mtime_vs_content_20260806/repro.sh   # ~40 s, needs only GNU make + c++
```

Prints one line per row of `results.csv`. Verified end-to-end from a clean workspace on 2026-08-06;
every row reproduced.

## Files

| file | what |
| --- | --- |
| `repro.sh` | builds the replica project and runs the whole mutation matrix |
| `verify-objects` | prototype content validator (empty / not-ELF / sha256-sidecar mismatch → delete) |
| `cc-atomic` | prototype `CMAKE_<LANG>_COMPILER_LAUNCHER`: compile to temp, rename on success |
| `slowcc` | stand-in for a heavy `cc1plus` — creates its `-o` target then takes 30 s, to widen the kill window |
| `results.csv` | one row per experiment, with the observation and verdict |
| `metadata.json` | host, toolchain, real-tree parameters, repo SHAs |
