# nondet-time.nix
#
# Non-reproducible only through the two sources Hermit's runtime actually
# virtualizes under `--no-namespace`: wall-clock time and /dev/urandom. Unlike
# nondet-demo.nix this deliberately omits AT_RANDOM-seeded userspace PRNGs
# (bash $RANDOM) and /proc/sys/kernel/random/uuid, which Hermit does NOT
# virtualize while sharing the host namespace.
#
# Expectation: NONDETERMINISTIC native, fully reproducible under the Hermit wrap.

{ pkgs ? import <nixpkgs> { } }:

pkgs.stdenv.mkDerivation {
  name = "nondet-time";
  dontUnpack = true;
  dontFixup = true; # no ELF; skip slow patchelf so the demo is quick under Hermit
  buildPhase = ''
    runHook preBuild
    {
      echo "date=$(date -u +%s.%N)"
      echo "urandom=$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
    } > result.txt
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"; cp result.txt "$out/result.txt"
    runHook postInstall
  '';
}
