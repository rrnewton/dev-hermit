# Artifact integrity: structural validation, not size, not magic

**Task:** `cmake-content-hash-elf-magic-not-size` · **Date:** 2026-08-06
**Mode:** local build + bracket. No egress, no validate, hermit primary unmodified.

## What main does today

`hermit/validate.sh:872-878` `purge_zero_byte_objects`:
`find "$root" -type f -name '*.o' -size 0`. Two proxies: **`*.o`** (misses `.a`/`.so`/`.rlib`/`.lo`)
and **`-size 0`** (misses every partial write).

## Two findings the bracket produced — the second corrected my own first attempt

**1. A naive "validate ELF magic" would delete every valid archive.** Measured on real artifacts:

```
.a     !<arch>\n      .rlib  !<arch>\n      .so   \x7fELF      .o   \x7fELF
```

`.a` and `.rlib` are **ar archives, not ELF**. The task's phrasing ("validate ELF magic bytes")
applied across the widened extension set would flag and `rm` every static archive and rlib. Magic
must be **per-format**.

**2. Magic alone still misses the exact case this task is about.** My first predicate (magic
per-format) was bracketed and **failed**: a 64-byte head of a real `.o` retains valid `\x7fELF` and
was scored `ok (kept)`. A partial write that survives the first 4 bytes defeats a magic check
entirely.

**So neither size nor magic is sufficient. ELF is self-describing** — the header declares where the
section table ends, so truncation is detectable from the file's own metadata, with no sidecar hash
and no rebuild:

```
e_shoff + e_shentsize * e_shnum  >  filesize   =>  truncated
```

## The bracket, both sides

`validate_artifact.py`:

| fixture | verdict |
|---|---|
| `valid.o` | **OK** |
| `valid.a` | **OK** ← the regression a naive ELF check would cause |
| `valid.so` | **OK** |
| `truncated.o` (64 B, valid magic) | **CORRUPT:truncated (needs 79056, has 64)** ← the task's case |
| `nearly_whole.o` (**100 bytes short**) | **CORRUPT:truncated (needs 79056, has 78956)** |
| `tiny.o` (3 B) | CORRUPT:bad-elf-magic |
| `truncated.a` (64 B) | CORRUPT:archive-truncated |
| `empty.o` (0 B) | CORRUPT:empty ← old behaviour preserved |

`checked=8 corrupt=5`, rc=1. **5/5 plants caught, 3/3 valid kept.**

`nearly_whole.o` is the strongest row: a file **100 bytes** from complete is detected. Size and magic
both miss it completely, and it is precisely what a mid-write SIGKILL leaves behind.

## Why this closes the defect class

`memory.oom.group=1` kills the build tool with its child, so `.DELETE_ON_ERROR` never runs; cmake
then trusts the fresh mtime and never recompiles. The poisoned artifact is *whatever the compiler
had flushed* — usually a valid header plus a partial body. Detecting that requires reading the
artifact's declared structure, which is what this does. Cost is 64 bytes read per file (the earlier
run measured full sha256 over 834 objects / 407 MiB at 0.51 s; this is strictly cheaper).

## Recommended change to `validate.sh:872-878`

Replace the `find` predicate with the widened extension set, and gate deletion on
`validate_artifact.py` rather than `-size 0`. Keep the existing purge-count banner. Zero-byte files
still purge, so no behaviour is lost — the change is strictly additive in detection.

**Not applied:** `validate.sh` is in the hermit primary (Hard Invariant 1, no slot assigned), and
egress is down so nothing could be pushed or CI-verified.

## Reproduce

```bash
cd experiments/cmake_mtime_vs_content_20260806/elf-magic
python3 validate_artifact.py <paths...>     # rc=1 if any corrupt
```
