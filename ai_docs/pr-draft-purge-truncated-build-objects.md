[impl agent, opus-5] validate.sh: widen the truncated-artifact purge past 0-byte `*.o`

Stack 4 (ci-tooling) of the Part-B topic plan (`tg coalesce-staged-work-into-topic-prs`, PART B).

## Summary

`validate.sh`'s build-integrity pre-flight (`purge_zero_byte_objects`) caught exactly one shape of
one failure mode: a `*.o` of exactly zero bytes. The failure it exists for is broader than that.

The scenario, already documented in the function's own comment: a compiler or archiver is killed
mid-write — classically the OOM-killer firing on a **neighbour's** step cgroup with
`memory.oom.group=1` — so `make` never runs its `.DELETE_ON_ERROR` cleanup. cmake/make key
incremental freshness on **timestamp, not content**, so they trust the corrupt artifact forever and
link it. The result is an "undefined reference" that reads as a source defect and never self-corrects.

Two gaps let the same corruption through:

**1. File types.** A kill truncates whatever the tool had open. The same steps that emit `*.o` also
emit the archiver's `*.a` and the linker's `*.so` — DynamoRIO (`reverie-dbi`) ships both, and
`hermit-install` stages `.so`. A truncated archive links exactly as badly as a truncated object.
Coverage widens to `*.o`, `*.a`, `*.so`, `*.so.*`.

**2. Header magic.** `-size 0` misses a file killed *after* `open()` but *before* its header finished:
nonzero on disk, still garbage. An artifact that does not begin with its format's magic
(`0x7f 'E' 'L' 'F'`, or `!<arch>\n` for `ar`) cannot be valid whatever its size.

Both checks remain **content facts, not heuristics**, which is precisely what preserves the warm
cache: a healthy artifact always has a well-formed header, so no valid artifact is deleted and
incremental skipping survives. A blanket "clean rebuild after any failure" would not be — cold
rebuilds cost +232s and fail more.

### Honest limit

Stated in the code so this is not read as a completeness guarantee: the magic check only proves the
**first bytes arrived**. A file truncated *after* a valid header still passes. Catching that needs a
full structural parse this pre-flight deliberately does not attempt. **This widens the net; it does
not close it.**

## Determinism

**No guest-visible behaviour changes.** `validate.sh` is the local validation harness — it is not
compiled into `hermit`, never runs inside a guest, and takes no part in scheduling, virtual time, or
any determinization decision. It cannot affect the determinism of any run it validates.

The determinism argument that *does* apply here is about the validation signal itself, and this
change strengthens it in one specific direction: a truncated artifact makes validation outcomes
**depend on host history** — whether some unrelated neighbour got OOM-killed during an earlier build
— rather than on the tree under test. That is nondeterminism in the *harness*, and it presents as a
phantom source defect ("undefined reference") that no amount of re-reading the diff explains.
Removing provably-corrupt artifacts makes the build a function of the tree again.

The purge itself is deterministic: it is a pure function of file content (size, and the leading
bytes), evaluated per file with no ordering dependence, no timestamps, and no host state. The same
tree yields the same set of removals and the same count on every run and every host.

Care was taken not to over-delete, since the failure mode of a too-eager purge is a silent cold-cache
regression rather than a visible error. The positive half of the test suite is the guard for that.

## Linux Semantics

Not applicable — no guest-visible syscall behaviour is touched. The magic constants match the
on-disk formats Linux toolchains produce: `ELFMAG` (`\x7fELF`, `elf(5)`) for relocatables and shared
objects, and `ARMAG` (`!<arch>\n`, `ar(5)`) for static archives.

## Validation

**No validate receipt — disclosed, not omitted.** No validate can be admitted from an agent sandbox:
`ci-hub/validate/preflight_validate.py::resolve_current_base()` runs `with-proxy git fetch` and raises
`AdmissionError` on non-zero exit, with no offline flag; `herdr-run` refuses `python3`
(`Allowed: cargo, gh, git`). **Do not land without a receipt at
`79e935d2e02c916b8ae9805998827a03fe5c3d1d`.**

| check | result |
|---|---|
| `bash -n validate.sh` | parses |
| `bash -n scripts/purge-truncated-objects-test.sh` | parses |
| `shellcheck -S warning` on both | **no findings from this change** (2 pre-existing `SC2034` in `validate.sh` at lines 1172, 4478, untouched) |
| `./scripts/check-script-sigpipe.sh` | OK — SIGPIPE clean; prelude-cache-key: 10 consumers current |
| `./scripts/purge-truncated-objects-test.sh` | **16/16 checks pass** |

**The test brackets both directions**, because a purge that deletes nothing and a purge that deletes
everything both pass a one-sided test:

- **Negative (must be removed):** 0-byte `.o`/`.a`/`.so`, a `.o` killed after 1 byte of ELF magic, a
  `.a` killed mid-`ar` magic, and a nonzero `.so` with wrong magic — 6 fixtures.
- **Positive (must be preserved):** well-formed `.o`/`.a`/`.so`, a versioned `libfoo.so.1` (proving
  the `*.so.*` glob is reached and *not* over-deleted), plus unrelated `notes.txt` and a 0-byte
  `empty.txt` that is not an artifact — 6 fixtures.
- **The returned count is asserted** (`6`), so a purge that silently removes extra files cannot pass
  by also removing the right ones.
- A clean tree must be a no-op, and an absent root must return `0` (which `validate.sh` relies on).

**Non-vacuity, measured:** run against the pre-change function, the same suite **fails 6 checks**
(5 corrupt fixtures survive, and the count is 1 instead of 6). The test detects the absence of the
fix rather than passing regardless.

## Human Review Required

Not applied. None of the four triggers apply: no new syscall support, no Reverie API or
core-abstraction change, no new determinization strategy, no DetCore scheduling change. This is
validation-harness tooling only.

Base `4c70658e785834737cbe1524f77330c781a6f5ea` · head `79e935d2e02c916b8ae9805998827a03fe5c3d1d`
