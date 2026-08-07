# ca-nondet.nix — the same nondeterministic derivation in input-addressed and
# content-addressed form, for the `ca-derivations` assessment.
#
# `__contentAddressed = true` makes nix build into a scratch path, hash the
# result, and move it to a path derived from its CONTENT, recording a
# "realisation" from derivation-output identity to store path.

{ pkgs ? import <nixpkgs> { }
, contentAddressed ? false
}:

pkgs.stdenv.mkDerivation ({
  name = "ca-nondet";
  dontUnpack = true;
  dontFixup = true;
  buildPhase = ''
    runHook preBuild
    echo "date=$(date -u +%s.%N)" > result.txt
    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"; cp result.txt "$out/result.txt"
    runHook postInstall
  '';
} // pkgs.lib.optionalAttrs contentAddressed {
  __contentAddressed = true;
  outputHashMode = "recursive";
  outputHashAlgo = "sha256";
})
