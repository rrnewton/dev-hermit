# nondet-seconds.nix — a faithful SURROGATE for the lensfun class.
#
# lensfun's prePatch does `date +%s > data/db/timestamp.txt`: WHOLE SECONDS of
# wall clock, baked into $out. This probe does exactly that and nothing else.
#
# Why it matters: hermit's virtual clock under `--no-rcb-time` is stable to a
# discrete check-in quantum, measured here at ~250 ms (see README R1). A build
# that bakes NANOSECONDS (nondet-time.nix) can therefore still differ; a build
# that bakes SECONDS cannot. This probe isolates that distinction, and it runs
# in ~1 s, unlike the real lensfun build (>25 min under the wrap).

{ pkgs ? import <nixpkgs> { } }:

pkgs.stdenv.mkDerivation {
  name = "nondet-seconds";
  dontUnpack = true;
  dontFixup = true;
  buildPhase = ''
    runHook preBuild
    date +%s > timestamp.txt
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"; cp timestamp.txt "$out/timestamp.txt"
    runHook postInstall
  '';
}
