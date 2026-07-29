# Validation adversarial review, round 2

[adversarial-reviewer agent, gpt-5.6-sol]

Reviewed published implementation:
`f8e44647f2f0cdd7412a125c02be3c80f4616c53`.

## Verdict

**REJECT.** The real bounded matrix passes, but hostile shell/tool state can
still forge success and several evidence declarations are not enforced.

## Major findings

1. Inherited Bash state can force status zero before or during either runner.
   A `BASH_ENV` file containing `exit 0` bypassed both scripts before their
   bodies ran. An exported `exit` function converted a genuine 16/17
   adversarial failure into status zero. Unsetting `BASH_ENV` inside the script
   is necessarily too late; the interpreter startup must ignore startup files
   and imported functions.

2. Tool discovery treats hostile `PATH` as a trust root. A fake Cargo emitted
   matching human-readable test logs without compiling anything; `quick`
   reported 17/17 passes in zero seconds and merely attested the fake binary's
   hash. The rustup proxy hash also does not identify the stable/nightly Cargo
   and rustc binaries it dispatches. Discovery needs a fixed or explicitly
   trusted rustup/toolchain root and must bind the actual dispatched binaries.

3. Attestation and manifest enforcement remain incomplete:

   - Persistent Cargo-symlink or manifest mutation made `self-test` print a
     revalidation failure, then print both success lines and exit zero.
   - Transient symlink/manifest swaps were invisible.
   - Command and fuzz evidence strings can be changed without affecting a
     passing run.
   - Missing or duplicate connector markers fail, but an unexpected extra
     marker is accepted.

## Minor finding

The support document says the adversarial runner prints source revision,
kernel/architecture, and tool versions. It currently prints tool paths and
hashes but none of those metadata values. The release runner prints the
metadata but not tool hashes.

## Passing evidence

- Genuine one-second-fuzz `quick`: 17/17 in 78 seconds.
- Genuine one-second-fuzz `full`: 18/18 in 37 seconds, including all five real
  libFuzzer `DONE` markers.
- Dedicated strict-provenance Miri target: 4/4.
- Migration `Initializing` and both newly covered C-SNZI close cuts stopped and
  were killed at their production hooks.
- Zero-test output failed; deadline returned 124; status 137 was classified as
  ambiguous and returned 1; unavailable nightly returned 2 with all 17 gates
  counted.
- `RELEASE_CHECK_SKIP_PROCESS=1` returned 2 with `INCOMPLETE` and no final
  `PASS`.
- Rust 1.85 feature builds, file modes, shell syntax/lint, formatting, and diff
  checks passed.

The reviewer used an isolated archive and sparse clone and modified no
repository files.
