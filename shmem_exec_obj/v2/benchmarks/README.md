# Benchmark Harness

`harness.rs` is the implementation behind `../scripts/run-benchmarks.sh`. The
script compiles it as a temporary locked Cargo application so benchmark-only
code and private executable-image crates do not enter the published
`shmem-pod` package.

Run the script rather than compiling this file directly. See
[`../docs/benchmarks.md`](../docs/benchmarks.md) for methodology, operation
definitions, output schemas, and interpretation limits.
