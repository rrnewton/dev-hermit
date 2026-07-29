# Validation adversarial review, round 1

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed implementation: `cf7c44e` (published validation chain ending in the
bounded adversarial runner).

## Verdict

**REJECT.** The production-linked tests and dynamic-analysis gates exercise
substantial real behavior, but four release-blocking validation holes remain.

## Major findings

1. The selected Miri gate always reaches the `bootstrap_connector` fork test.
   Miri does not support `fork`, so the documented supported host cannot pass
   this gate. Select only parser/offset tests that are pure under Miri, or add a
   dedicated Miri-safe test target.

2. `RELEASE_CHECK_SKIP_PROCESS=1 ./scripts/release-check.sh quick` says the run
   is not release-green, then unconditionally prints `PASS` and exits zero.
   Skipped required process evidence must produce an explicit incomplete
   result and nonzero status, and must never print `PASS`.

3. The adversarial runner trusts command status without proving that its
   expected evidence ran. Cargo filters that match zero tests return success;
   exported shell functions replacing `cargo` and `timeout` made all 17 gates
   pass in zero seconds. Resolve and hash absolute tool binaries, invoke those
   paths, and attest the expected test names and counts so missing or zero-test
   evidence fails closed.

4. The documented deterministic crash matrix is incomplete. Add a migration
   owner-death test in `Initializing`, and arm/test both
   `CsnziCloseMarkedNonempty` and `CsnziCloseConvertedTail` fault points.

## Minor findings

- Unavailable and unsupported selections do not increment `gate_number`, so a
  17-gate run can report only 12 gates.
- Exit 137 is always labeled a timeout even when caused by OOM or an external
  `SIGKILL`.
- `CloseableDrainScanned` follows a scan of loads, not an RMW as the support
  documentation currently claims.

## Passing evidence

- Production-linked Loom models: 6/6.
- Actual-RMW crash tests: 13/13.
- Mapping, allocator, and existing migration cuts: 5/5.
- AddressSanitizer and ThreadSanitizer subsets: 4/4 each.
- Rust 1.85 no-default and isolated-feature builds passed.
- The fuzz digest harness, corpus generation, package exclusions, and
  unavailable-tool exit classification passed.

No files were modified by the reviewer.
