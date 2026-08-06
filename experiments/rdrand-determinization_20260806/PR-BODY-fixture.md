[impl agent, claude-opus-5]

**Stacked on #1671** (`determinize-rdrand-rdseed` @ `e01ccfdda`). The RDRAND
assertion cannot pass without that fix — demonstrated below — so the fixture and
the fix land together.

## Summary

A sweep's finding decays the moment someone changes the code; a fixture **fails
the build**. This pins the contract for every randomness source Hermit claims to
determinize — `getrandom(2)`, `/dev/urandom`, `/dev/random`, `AT_RANDOM`,
`getentropy`, `arc4random`, and the **`RDRAND` and `RDSEED` instructions** — in
one guest, in one process, so no source can be silently skipped.

The probe issues `RDRAND`/`RDSEED` **without consulting CPUID**. That is the
specific hole this locks down: Detcore reports those feature bits as absent, so a
CPUID-checking probe takes a determinized fallback and the fixture would pass
while the instruction remained a live entropy source.

## Design decisions that are load-bearing

**A separate binary, not an in-process closure.** Detcore's RDRAND rewriting is
armed at `execve` and at executable `mmap`. `det_test_fn` forks the test process
without exec'ing, so the guest would never be rewritten and the fixture would
report a hole that is really an artifact of the launch path. It uses
`det_test_cmd` against a real `[[bin]]`.

**Every source read at the lowest available layer.** A libc wrapper can quietly
substitute one mechanism for another (glibc's `getrandom` falls back to
`/dev/urandom`; `arc4random` may be built on `getentropy`). Reading through the
wrapper would let a determinized source stand in for an undeterminized one. So
`getrandom` is a raw syscall, the character devices are opened directly,
`AT_RANDOM` comes from the auxiliary vector, and `RDRAND`/`RDSEED` are emitted as
instructions.

**Three asserted properties, each covering a different way this could pass
falsely:**

1. **Coverage** — exactly the expected source set appears, and every source
   present on the platform yields real bytes. Without this, a source that stopped
   reporting (an errno, a short read, `CF=0`, or a line quietly deleted) would
   leave a *shorter but still self-consistent* stream and the identity check
   would pass while coverage silently shrank.
2. **Anti-vacuity** — the same probe run natively must produce *different* bytes
   across 3 runs. A probe whose sources were naturally constant would satisfy
   identity trivially.
3. **Identity** — the whole output stream is byte-identical across repeated
   Detcore runs.

A source missing from the platform (`arc4random` on glibc < 2.36, as here) is
reported as `ABSENT` and excluded from the variation check, but is **still
required to appear as a line** — so "not available here" can never be read as
"determinized here".

## Determinism

The fixture asserts determinism rather than implementing it, so the argument is
about why a pass means what it claims.

- The identity assertion is over the guest's **entire stdout**, not a per-source
  spot check, so a divergence anywhere in the stream fails the test and the
  failure output names the diverging lines.
- Determinism is asserted **only against a native control that varies**. Property
  2 makes the test self-invalidating: if the host ever stopped producing entropy
  for a source, the fixture fails loudly instead of quietly becoming a tautology.
- Nothing in the fixture depends on a *particular* value — no golden bytes are
  hardcoded — so it stays valid across seeds, hosts, and Hermit builds. It
  constrains the *relation* (all runs equal, natives differ), which is the
  property the product actually promises.
- The probe's own output order is fixed, so the comparison is over a stable
  stream rather than an incidentally-ordered set.

## The negative bracket — proof the fixture is load-bearing

Cherry-picked onto bare `main` (`4c70658e7`, i.e. **without** #1671) the fixture
**FAILS**, and names exactly the two sources at fault:

```
test randomness_sources_are_determinized ... FAILED
  Consecutive runs of test had different stdout
   getrandom 12ab376880e4f387b99fd21da39c4e9f      <- matches
   urandom   2972bb044d96df2871ba034c95de2770      <- matches
   random    2972bb044d96df2871ba034c95de2770      <- matches
   at_random a2cd18d300537a5cb083dc48dbfa0ef2      <- matches
   getentropy 7c2e57e8739e284b622a35093a91226c     <- matches
   arc4random ABSENT
  <rdrand 0dde0268b77f75b1429ad0cce26641b1         <- DIVERGES
  <rdseed 154843fa15500fcd509b7d9224756756         <- DIVERGES
```

Six sources match; `rdrand` and `rdseed` are named as the divergence. That is the
whole point: the fixture would have caught the hole, and it will catch a
regression.

## Validation

**Head:** `60e86f2d26a653d7ba2faa99af732f829647754c`
**Base:** #1671 `e01ccfdda`, itself on `origin/main` `4c70658e785834737cbe1524f77330c781a6f5ea`
**Backend:** ptrace · **Relaxations:** none

| Check | Result |
| --- | --- |
| Fixture passes with the fix | `randomness_sources_are_determinized ... ok` |
| **Fixture fails without the fix** | FAILED on bare `4c70658e7`, naming `rdrand`+`rdseed` |
| Native control | 7 available sources all vary across 3 runs |
| Coverage | 8 expected lines; `arc4random ABSENT` on this glibc, reported not skipped |
| `cargo test -p hermit-detcore --test tests_misc` | **29 passed, 0 failed** |
| `cargo test -p hermit-detcore --lib` | **396 passed, 0 failed** |
| `cargo fmt --all -- --check`, clippy | clean |

Manual cross-backend evidence (not asserted by the fixture — see below): under
ptrace, two runs of the probe are byte-identical across all 7 available sources.

## Scope — stated, not implied

**The fixture is ptrace-scoped.** `det_test_cmd` drives the ptrace backend only,
so "across backends" is *not* asserted here. That is deliberate and not merely a
missing feature: **RDRAND identity does not hold on DBI.** #1671's companion
commit `e01ccfdda` fences determinization off there because the in-place text
rewriting crashes DynamoRIO's code cache, so on DBI the instruction is back to
masked-but-live and a cross-backend identity assertion would be asserting
something false. The DBI gap is recorded and printed at runtime rather than
asserted away. Extending the fixture across backends should follow DR-native
instrumentation of the instruction, not precede it.

`arc4random` is `ABSENT` on this host's glibc, so its determinism is **untested
here** — the fixture will begin covering it automatically on a glibc 2.36+ host
because the source list is asserted, not the availability.

## Blocker

**No validate receipt.** `ci-hub validate-run` refuses at admission
(`preflight_validate.py` shells out to `with-proxy git fetch`, 403 from an agent
shell; the only working egress, `herdr-run`, refuses `ci-hub`). Admission
predicate computed locally: moving-base PASS, fixed-floor PASS.
