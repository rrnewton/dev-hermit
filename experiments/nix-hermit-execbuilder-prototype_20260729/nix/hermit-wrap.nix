# hermit-wrap.nix
#
# Prototype of "wrap the Nix builder under `hermit run`" at the
# execBuilder seam (see ai_docs/nix-reprobuild-ca-store-research_20260729.md).
#
# nixpkgs `stdenv.mkDerivation` runs the build by exec'ing
#     realBuilder = stdenv.shell           (defaults to bash)
#     args        = ["-e" source-stdenv.sh default-builder.sh]
# i.e. execBuilder does `execve(bash, ["bash","-e", ...phase scripts...])`.
#
# We keep the derivation byte-identical EXCEPT we replace `realBuilder` with a
# tiny store-resident wrapper whose job is exactly the doc's recipe:
#     exec hermit run <mode> -- <the-real-bash> "$@"
# so the WHOLE builder process tree (unpack/patch/configure/build/install/fixup)
# runs under Hermit's deterministic runtime, while Nix keeps evaluation,
# dependency ordering, output registration, and --check comparison.
#
# Because `realBuilder` is part of the input-addressed derivation, wrapping
# changes the derivation identity and hence the output store path. That is
# expected: compare wrapped-vs-wrapped for reproducibility, and separately diff
# a wrapped output against the native output for semantic parity.

{ pkgs ? import <nixpkgs> { }
, # Absolute path to the hermit binary on the host filesystem. Not a store
  # path: with `--no-namespace` Hermit shares the host FS so the guest build can
  # both see it and write the real $out store path.
  hermit ? "/home/newton/work/dev-hermit/hermit/target/release/hermit"
, # Hermit invocation mode. `--no-namespace` is required so the builder's writes
  # to the output store path persist (Hermit's default private mount namespace
  # discards writes to host paths). `setarch -R` pins ASLR at the host level,
  # which Hermit cannot control while sharing the host namespace.
  hermitArgs ? [ "run" "--no-namespace" ]
, setarch ? "/usr/bin/setarch"
, useSetarch ? true
}:

let
  inherit (pkgs) lib stdenv;

  # Bake the arch at eval time: `uname` is not on the builder's PATH, and the
  # personality (ADDR_NO_RANDOMIZE) is what pins ASLR for the whole build tree.
  arch = stdenv.hostPlatform.uname.processor; # e.g. "x86_64"
  hermitCmd = lib.escapeShellArgs ([ hermit ] ++ hermitArgs);
  prefix = lib.optionalString useSetarch "${setarch} ${arch} -R ";

  # The wrapper is a normal store-resident shell script. Its own shebang is
  # bash; when Nix exec's it as the builder it re-launches the real stdenv shell
  # under Hermit, forwarding the original phase-script args unchanged.
  hermitWrap = pkgs.writeShellScript "hermit-exec-builder" ''
    # Faithful execBuilder wrap: run the original builder + args under Hermit.
    exec ${prefix}${hermitCmd} -- ${stdenv.shell} "$@"
  '';

  # Wrap a single derivation produced by stdenv.mkDerivation.
  wrap = drv: drv.overrideAttrs (_old: {
    realBuilder = hermitWrap;
    # Mark so the wrapped store path/name is distinguishable in logs.
    passthru = (_old.passthru or { }) // { hermitWrapped = true; };
  });
in
{
  inherit hermitWrap wrap;

  # Convenience: wrapped variants of the two research targets.
  nftables = wrap pkgs.nftables;
  bcachefs-tools = wrap pkgs.bcachefs-tools;
}
