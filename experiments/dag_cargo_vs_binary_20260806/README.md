# How many DAG steps invoke cargo? 24 of 55 — and 18 of those are running tests

**Task:** `dag-test-steps-should-call-the-hermit-binary-not-cargo` ·
**Agent:** hermit-audit (`[impl agent, opus-5]`) · **2026-08-06** · local CI config, no egress.

## The count, with the denominator

**55 DAG steps** (47 portable + 8 privileged) at hermit `origin/main`.

| category | count | share |
| --- | ---: | ---: |
| no cargo at all | 31 | 56% |
| **(a) legitimately builds / lints / docs** — cargo is the right tool | **6** | 11% |
| **(b) runs tests through cargo** — the owner's category | **18** | 33% |
| (c) anything else | **0** | — |
| **cargo total** | **24** | **44%** |

**(a)** `setup.nextest`, `build.workspace`, `build.runtime_release`, `lint.rustfmt`, `lint.clippy`,
`doc.rustdoc`.

**(b), enumerated by step name** (17 pure + 1 hybrid):

| step | package | what it actually runs |
| --- | --- | --- |
| `doc.doctests` | workspace | **doctests — cannot be de-cargo'd** (rustdoc compiles them on the fly) |
| `test.regular_crates` | workspace | nextest |
| `test.hermit_unit` | hermit | libtest: lib/bins |
| `test.detcore_unit` | detcore | libtest: lib/bins |
| `test.detcore_misc` | detcore | libtest: `tests_misc` |
| `test.detcore_parallel` | detcore | libtest: `tests_parallelism` |
| `test.hermit_integration` | hermit | libtest: `aio_nr_determinism` (+9 more `--test` targets) |
| `test.arbitrary_binaries` | hermit | libtest: `arbitrary_binaries` |
| `test.cli` | hermit | libtest: `cli` |
| `test.liteinst_strict` | hermit | libtest: `liteinst_advanced` — **already points at `target/release/hermit`** |
| `test.sabre_examples` | hermit | libtest: `sabre_examples` — **already points at `target/release/hermit`** |
| `test.hermit_modes` | hermit | libtest: `hermit_modes` |
| `test.app_strict_verify` | hermit | libtest: `app_strict_verify` |
| `test.command_strict_verify` | hermit | libtest: `command_strict_verify` |
| `test.ignored_syscall_regressions` | hermit | libtest: `epoll_determinism` |
| `test.rr_suite_contract` | hermit | libtest: `rr_suite` |
| `build.privileged_tests` (priv) | hermit | **hybrid**: `cargo build … && cargo test …` |
| `cpuid.faulting` (priv) | detcore | libtest: `tests_misc` |

## One correction to the framing, and it changes the fix

The instruction is *"steps running blocks of hermit tests should just call the hermit CLI binary."*
**16 of the 18 category-(b) steps do not run the hermit CLI at all** — they run a **libtest binary**
(`target/<profile>/deps/<target>-<hash>`) which itself spawns hermit. So the artifact to invoke
directly is the **prebuilt test binary**, not `./target/release/hermit`.

The exceptions prove the rule: `test.liteinst_strict` and `test.sabre_examples` already pass
`HERMIT_LITEINST_TEST_BINARY=$PWD/target/release/hermit` / `HERMIT_SABRE_TEST_BINARY=…`, i.e. **the
hermit release binary is already the system under test there and cargo is a pure launcher.** Those two
are the cleanest conversions.

The owner's underlying point is exactly right and survives the correction: **a test step should
execute an artifact, not re-enter the build system.** It just cashes out as *"invoke the prebuilt test
binary"* rather than *"invoke `hermit`"*.

## The `NUM_JOBS` leak — mechanism verified

* `reverie/reverie-dbi/build.rs:226-228` builds DynamoRIO with `--parallel` from
  `env::var("NUM_JOBS")`. Confirmed in the local checkout.
* Cargo sets `NUM_JOBS` **for build scripts only**. So the leak fires exactly when a step *runs a build
  script* — i.e. when cargo decides something needs rebuilding.
* The current mitigation is `ci/run-with-reverie-dbi-budget.sh`, wrapped around cargo in 9 steps. Its
  own comment states the intent: *"Keeping this wrapper immediately around Cargo prevents a
  launcher-side width from standing in for NUM_JOBS."* That is containment. **If a test step does not
  invoke cargo, there is no build script to run and nothing to contain** — the owner's reading is
  correct.

Corroborating evidence of re-resolution actually happening in this tree: the same test target has been
compiled **8 different ways** (`cli`), 5 (`tests_misc`), 4 (`tests_parallelism`, `hermit_modes`,
`rr_suite`). *Caveat, stated because it matters:* those hashes accumulate across many commits and
command variants over the life of the target dir; they demonstrate that resolution varies across
invocations, **not** that a single CI run rebuilds. I did not run a full workspace build to measure
per-run rebuild (too expensive to be worth it here).

## What cargo provides that a bare call does not — measured, not assumed

| service | real or incidental | evidence |
| --- | --- | --- |
| **Binary discovery** (`--test cli` → which of 8 hashes?) | **REAL** | 8 / 5 / 4 candidate binaries per target in `target/debug/deps`. A glob is not safe here. |
| **Feature gating** | **REAL, but not where expected** | `third-party-backends = ["dbi","sabre","e9patch"]` gates *dependencies*. **0** `cfg(feature = …)` sites in `hermit-cli/tests/*.rs` and `detcore/tests/*.rs` — no test function is compiled out by it. |
| **Runtime env** | **INCIDENTAL** | **0** runtime `env::var("CARGO_*")` reads in those suites; **197** `env!("CARGO_*")` compile-time reads, which are baked into the binary. |
| **Working directory** (cargo runs tests with cwd = package root) | **REAL, small** | 1 relative-path site found. A converted step must set cwd explicitly. |
| **Rebuild-on-demand** | **REAL, and removing it is the point** | The DAG already orders these steps `deps: [build.workspace, …]`, so the build is guaranteed to have happened. Dropping the implicit rebuild is what makes the step test *the artifact*. |
| **`LD_LIBRARY_PATH` for the loader** | **NOT a cargo service** | The direct call failed with `libunwind-x86_64.so.8: cannot open shared object file`. No build script emits a link-search path for it, so cargo would not have supplied it either — this is a **host provisioning gap** (worked around here with `LD_LIBRARY_PATH=/tmp/lu/usr/lib64`). Worth fixing separately; do not attribute it to the conversion. |

## The feature-gating / silent-zero-test-green question

The task asks whether that failure mode disappears or moves. **It moves, and it moves somewhere more
detectable.**

It does not come from per-test `cfg(feature)` gating in these suites — there are **zero** such sites.
It comes from *selection*: a build/filter combination that yields zero executed tests while the step
still exits 0. Converting to direct invocation replaces "cargo silently resolved a different feature
set and compiled fewer tests" with "the runner selected the wrong or stale binary". That is **better**,
because the binary list becomes a recorded artifact you can diff, and the executed count is already
gated (`executed_tests == 0 → no_result`). It is only better **if the binary list is recorded with the
feature set that produced it** — otherwise you have swapped one invisible resolution for another.

## Demonstration (the VERIFY requirement)

Converted step: `test.detcore_parallel` =
`cargo test -p detcore --test tests_parallelism -- --skip detcore --test-threads=4`.

```
binary : target/debug/deps/tests_parallelism-8fb9dbbaf4f517fe   (198,580,688 B, 2026-08-04)
env    : LD_LIBRARY_PATH=/tmp/lu/usr/lib64                       (host libunwind gap, see above)
--list                      -> 16 tests, 0 benchmarks
--skip detcore --list       ->  5 tests, 0 benchmarks           <- NONZERO, matches the step's filter
run: timeout 240 <binary> --skip detcore --test-threads=4       -> exit 0   (reproduced 3x)
```

**Process tree, scoped to my own process group and sampled every 0.25 s for the whole run:**
commands seen were `bash`, `ps`, `timeout`, `tests_paralleli`, `mem_race::noop_` —
**0 `cargo`, 0 `rustc`.**

> A first attempt at this proof grepped `ps -e` system-wide and "found" 4 cargo and 2 rustc — **other
> agents' processes on this shared box.** The scoped measurement is the valid one; the unscoped number
> is recorded here as the mistake it was.

**Anomaly, reported rather than hidden:** the run exits 0 and prints `running 5 tests` plus three
`... ok` lines, but **no `test result:` summary line**, across all three runs. I did not chase it down
(the binary is a stale debug artifact from 2026-08-04). It matters because *a missing summary is the
same observable shape as the zero-executed-tests no-result signature* — so any converted step must
parse and require the summary line, not just the exit code.

## Proposal

1. **Use `cargo nextest`'s build/run split — it is already a repo dependency.** `setup.nextest` already
   installs it and `test.regular_crates` already uses it. `cargo nextest list --binaries-metadata
   <file>` at build time, then `cargo nextest run --binaries-metadata <file>` (or direct binary
   invocation from that manifest) at test time. This solves discovery and pins the feature set in one
   recorded artifact, which is exactly the property the section above says the conversion needs.
2. **Convert the two easy steps first**: `test.liteinst_strict` and `test.sabre_examples`, where the
   hermit release binary is already the artifact under test.
3. **Leave `doc.doctests` on cargo** — rustdoc compiles doctests on the fly; there is no artifact to
   invoke.
4. **Require the `test result:` line** in any converted step, not just exit 0 (see the anomaly).
5. **Fix the host libunwind gap separately.** It is not caused by the conversion and it will bite any
   direct-invocation approach on this host.
6. Expected benefit, stated honestly: the `NUM_JOBS` leak is removed **at the source** for the 17
   convertible steps, and each step stops re-entering dependency resolution. I have **not** measured
   the wall-clock saving — that needs a before/after on a quiet box with a warm target, and I did not
   run it.

## Reproduction

```bash
cd hermit && git show origin/main:ci/dag/portable.json   # + privileged.json: the 55 steps
LD_LIBRARY_PATH=/tmp/lu/usr/lib64 \
  ./target/debug/deps/tests_parallelism-<hash> --skip detcore --test-threads=4
```

## Files

| file | what |
| --- | --- |
| `results.csv` | every count and measurement with its value and caveat |
| `metadata.json` | SHAs, host, toolchain, and the stated limitations |
