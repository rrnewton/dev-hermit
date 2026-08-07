# nondet-demo.nix
#
# A deliberately non-reproducible derivation whose nondeterminism comes from the
# exact sources a deterministic runtime is supposed to virtualize: wall-clock
# time and the kernel RNG. Native rebuilds produce different output (different
# NAR hash); the same derivation wrapped under Hermit should collapse to one.
#
# This is the controlled positive control for the mechanism. The real targets
# (nftables-1.1.6, bcachefs-tools) are the harder, less-controlled cases.

{ pkgs ? import <nixpkgs> { } }:

pkgs.stdenv.mkDerivation {
  name = "nondet-demo";
  dontUnpack = true;

  # Do not let stdenv's own reproducibility hooks (SOURCE_DATE_EPOCH etc.) hide
  # the nondeterminism we are trying to observe.
  buildPhase = ''
    runHook preBuild
    {
      echo "date=$(date -u +%s.%N)"
      echo "urandom=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')"
      echo "bashrandom=$RANDOM$RANDOM"
      echo "uuid=$( (cat /proc/sys/kernel/random/uuid 2>/dev/null) || echo none)"
    } > result.txt
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp result.txt "$out/result.txt"
    runHook postInstall
  '';
}
