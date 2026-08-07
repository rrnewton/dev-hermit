# nondet-demo-fast.nix — `nondet-demo` with `dontFixup = true`.
#
# nondet-demo exercises all FOUR nondeterminism sources (wall clock,
# /dev/urandom, AT_RANDOM-seeded bash $RANDOM, /proc/sys/kernel/random/uuid),
# which is what makes it the interesting probe for the `--tmp=/tmp` correction:
# the last two were the documented leaks of the superseded `--no-namespace`
# mode.
#
# But it produces no ELF, so stdenv's fixupPhase (patchelf RPATH shrinking,
# strip, ...) contributes nothing to the measurement — while costing minutes
# under hermit, because fixup is dozens of short-lived processes and hermit
# sequentializes. Dropping it changes no observed nondeterminism source and
# makes N=20 affordable. The slow variant is retained as nondet-demo.nix.

{ pkgs ? import <nixpkgs> { } }:

((import ./nondet-demo.nix) { inherit pkgs; }).overrideAttrs (_: {
  dontFixup = true;
})
