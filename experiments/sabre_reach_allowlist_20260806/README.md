# SaBRe reach: two distinct root causes, and a correction to my own earlier finding

**Date:** 2026-08-06 · **Task:** `sabre-expand-reach-beyond-main-elf` · **Local only, no egress**
**Hermit:** debug `g0f891e432a75-dirty` · **Status:** committed to the parent, **not pushed** (egress 403)

## Correcting the premise this task was filed on

The filing evidence (my own artifact, `5220310b`) says SaBRe "sees only the four syscalls issued
directly by `main`" and concludes it "rewrites the main ELF and nothing else". **That conclusion
is wrong**, and the task title inherits it.

The main ELF of the fixture contains **zero** syscall instructions — `objdump` finds none; libc
has 525. Its `open`/`read`/`close`/`write` go through `@plt` into **libc.so**. So the four
syscalls SaBRe caught came *from a shared object*, which means SaBRe rewrites shared objects
just fine. "Main-ELF-only" cannot be the mechanism.

The observation was right; the mechanism I inferred from it was not.

## Root cause 1 — a hardcoded library **name allowlist**

`reverie/third-party/sabre/loader/ld_sc_handler.c:40`:

```c
// Libraries known to have syscalls
// ld is not here because it is processed elsewhere (loader.c)
const char *known_syscall_libs[] __hidden = {"libc", "librt", "libpthread",
                                             "libresolv", NULL};
```

Applied at `:123` — as ld opens each library, `which_lib_name_interesting(known_syscall_libs,
pathname)` decides whether to rewrite it. **Four names. Everything else is never rewritten.**

This is not ld-specific and not main-ELF-specific: it is allow-by-name, so any object whose
soname is not one of those four is invisible to Detcore.

**Demonstrated, quantitatively.** A fixture issuing 50 syscalls from a purpose-built
non-allowlisted `libmylib.so` (raw `syscall` instruction in its own text) and 50 identical
`getppid`s through libc:

| backend | intercepted |
|---|---|
| ptrace | **100 / 100** |
| sabre | **exactly 50 / 100** |

Same process, same syscall number, same run. The only difference between the caught 50 and the
missed 50 is **the name of the object containing the instruction**. That is the allowlist,
isolated.

Consequences that follow directly and are worth stating plainly:
- Any application shared library issuing raw syscalls is undeterminized.
- A **Go** binary — which issues raw syscalls from its own text and never links libc — would be
  almost entirely undeterminized under SaBRe. For `goal-hermit-v2` ("arbitrary real-world
  binaries") that is a first-order limitation, not an edge case.

## Root cause 2 — ld's syscalls are intercepted, then **routed away from the plugin**

The loader is a *different* mechanism, and my "never instrumented" reading was also too
coarse. `loader.c:406-420`, dynamic-client path:

```c
const char *libs[] = {"ld", NULL};
memorymaps_rewrite_all(libs, client_path, true);
```

**ld IS rewritten.** Its syscall sites are patched. But they dispatch into
`ld_sc_handler` (`ld_sc_handler.c:247`), which handles them itself:

```c
// long fd = plugin_sc_handler(sc_no, arg1, arg2, arg3, arg4, arg5, arg6, wrapper_sp);
long fd = real_syscall(sc_no, arg1, arg2, arg3, arg4, arg5, arg6);
```

The call to `plugin_sc_handler` **is commented out**, and the syscall goes straight to the
kernel via `real_syscall`. So loader syscalls are captured by SaBRe and then deliberately
withheld from the plugin — they are consumed by SaBRe's own library-tracking bookkeeping
(`interesting_fd` / `interesting_lib`, used to drive root cause 1's rewriting).

This is a **routing** gap, not a reachability gap, and the commented-out line is direct evidence
that plugin routing was implemented and then disabled.

**Confirmed behaviourally, post-startup.** A timing explanation ("rewriting happens after the
loader has run") would predict that loader syscalls issued *later* are caught. They are not. A
fixture calling `dlopen("libm.so.6")` from `main`:

| backend | syscalls observed during the dlopen |
|---|---|
| ptrace | `openat`×14, `mmap`×13, `newfstatat`×9, `pread64`×4, `mprotect`×4, `close`×4, `munmap`×3 |
| sabre | **none of them** (only libc's `write`, `brk`, `getrandom`) |

ld.so's syscalls are missed even when issued long after startup from a live `dlopen`. It is
scope and routing, not timing.

## Why this matters for the fix

The two causes need different work, and neither is "extend the rewriter":

1. **Allowlist → mapping scan.** Replace name matching with a scan of executable mappings,
   rewriting all of them and *excluding* SaBRe's own segments. The exclusion machinery already
   exists: `hide_sbr_maps` (`ld_sc_handler.c:46`) already tracks `start_sbr[]`/`end_sbr[]`, so
   the hard part — knowing which text is SaBRe's own — is solved. This converts a deny-by-
   omission design into allow-with-explicit-exclusions.
2. **Route ld's syscalls to the plugin.** Uncomment/restore `plugin_sc_handler` in
   `ld_sc_handler`, keeping the internal bookkeeping. The re-entrancy guard this needs is
   *already present* at the top of the function:
   ```c
   if (calling_from_plugin != NULL) { return runtime_syscall_router(...); }
   ```
   so a plugin-issued syscall during loader handling already has a defined path. That guard
   existing is the strongest hint the routing was disabled for a specific bug rather than
   because it is unsafe in principle.

**Static clients are already treated correctly**, which is a useful contrast:
`loader.c:431-435` uses `{"ld","libc","librt","libpthread","libresolv"}` *and* passes the client
binary with the comment "The binary itself probably has syscalls too, re-write it". The dynamic
path is the one that narrows to `{"ld"}` plus the runtime allowlist.

## Counting contract for parity cells (task requirement)

The task asks that mapped/intercepted counts ride with every cell. The measurement above gives
the shape: report **intercepted / expected** per cell, where expected comes from the ptrace
reference on the same fixture. `50/100` is informative; "sabre passed" is not — and note the
`--verify` blindness measured separately: a SaBRe run intercepting half the syscalls still
reports "Determinism verified", because verify compares stdout.

## Reproduction

```sh
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64
H=hermit/target/debug/hermit
# allowlist isolation: 50 syscalls from a non-allowlisted .so + 50 via libc
$H --log=info --log-file=$PWD/scratch/reach/m.$be.log run --backend $be -- $PWD/scratch/reach/usemylib
#   ptrace -> 100/100 ; sabre -> 50/100
# loader routing: dlopen post-startup
$H --log=info --log-file=$PWD/scratch/reach/$be.log run --backend $be -- $PWD/scratch/reach/dl
#   ptrace -> openat/mmap/mprotect ; sabre -> none
```

## No fix attempted

Both fixes are edits to **vendored third-party GPL-3.0 SaBRe C source**
(`reverie/third-party/sabre/loader/`) with a REVISION marker that `build.rs` asserts against.
Changing vendored source under a pinned revision marker, in a language and component I cannot
validate here (no egress, cannot run the reverie test suite), is not a change to make
unilaterally — and root cause 2 in particular was *deliberately* disabled by someone, so
re-enabling it without knowing why is how a fixed symptom becomes a new bug.

Filed with both mechanisms, their exact locations, the demonstrations, and the fact that the
required safety machinery (`hide_sbr_maps`, `calling_from_plugin`) already exists.
