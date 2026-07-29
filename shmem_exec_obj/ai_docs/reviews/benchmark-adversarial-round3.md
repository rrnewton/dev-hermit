# Benchmark adversarial review, round 3

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed benchmark implementation/evidence: `bb6ee4d2ec46f49507e8d9829fa2ad72539f152d`.

## Verdict

**REJECT.** One major build-provenance gap and one minor result-schema gap
remain.

## Major finding: inherited Cargo controls are unbound

The runner records a selected set of build variables, but Cargo also consumes
target-specific environment controls. An adversarial run set:

```sh
RUSTC_WRAPPER=/usr/bin/env \
CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS=-Cdebuginfo=1 \
CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=/usr/bin/gcc \
./scripts/run-benchmarks.sh --smoke --warmup 0 --iterations 1 --workers 1 \
  --output /tmp/shmem-bench-r3-ambient.JFwcPu
```

The run exited zero, published `complete=true`, and produced 22 verified rows.
Its canonical environment recorded neither target-specific variable and
reported ordinary `rustflags` as unset. It named `/usr/bin/env` as a wrapper
but did not retain or hash that executable.

The source manifest remained byte-identical to the clean run. A mutable or
change-then-restore wrapper can therefore alter compilation without appearing
in the claimed source-to-binary provenance. Output hashes authenticate the
resulting bytes, but not the inputs that produced them.

The runner must either reject every build-affecting ambient Cargo/rustc
variable outside a narrow explicit policy or capture, normalize, retain, hash,
and revalidate all such values and executable paths. Wrapper chains must be
closed over their actual binaries and configuration.

## Minor finding: result JSON schema is not exact

Matrix and CSV validation project only known fields before comparison. Extra
members therefore disappear, and duplicate JSON object keys are not rejected,
despite documentation claiming full field equality. Validate the exact key set
and reject duplicate keys before projecting values.

## Passing evidence

- Current and Rust 1.85 clean-source smoke runs each produced 22 rows and a
  valid 149-entry inventory.
- Forged legacy `SHMEM_POD_BENCH_*` variables did not reach canonical output.
- Compiler evidence bound object, ELF, image, rlib, dep-info and probe files,
  rustc launcher/binary, `rust-lld`, linker script, and dependency closures.
- A standalone retained harness could not publish `environment.json`.
- Completed-output reuse failed without changing its environment digest.
- The 126-path source closure included all observed `include_str!` inputs and
  rejected symlinks.

No files were modified by the reviewer.
