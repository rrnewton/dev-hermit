# hermit-wrap.nix — the "execBuilder seam": run a Nix derivation's whole builder
# process tree under `hermit run`, with no patch to nix and no patch to nixpkgs.
#
# Background: nixpkgs `stdenv.mkDerivation` builds by exec'ing
#     realBuilder = stdenv.shell            (the binary that is exec'd)
#     args        = ["-e" source-stdenv.sh default-builder.sh]
# The user-facing `builder` *attribute* is the phase script, NOT the exec'd
# binary; `realBuilder` is the binary. Overriding `realBuilder` therefore puts
# hermit around unpack -> patch -> configure -> build -> install -> fixup while
# nix keeps evaluation, dependency ordering, output registration and comparison.
#
# Differences from experiments/nix-hermit-execbuilder-prototype_20260729:
#  * the original builder is read off the ALREADY-EVALUATED derivation
#    (`drv.drvAttrs.builder`) instead of being hard-coded to `stdenv.shell`, so
#    packages with a non-default builder (or a different stdenv) wrap correctly;
#  * `hermitize` / `hermitizeIfNeeded` / `overlay` are exported so a nixpkgs
#    consumer can opt ONE package in (see README "Ergonomic integration").
#
# Because `realBuilder` is part of the input-addressed derivation, wrapping
# changes the derivation identity and hence the output path. Compare
# wrapped-vs-wrapped for reproducibility; compare wrapped-vs-native separately
# for semantic parity.

{ pkgs ? import <nixpkgs> { }
, # Absolute HOST path to the hermit binary. Not a store path: `--no-namespace`
  # shares the host filesystem, so the guest both sees it and can write the real
  # /nix/store output path.
  hermit ? "/home/newton/work/dev-hermit/worktrees/nix-repro176/hermit/target/release/hermit"
, # `--no-namespace` is REQUIRED: hermit's default private mount namespace
  # discards the builder's writes to the output store path.
  hermitArgs ? [ "run" "--no-namespace" ]
, # `setarch -R` (ADDR_NO_RANDOMIZE) pins ASLR at the host level, which hermit
  # cannot do while sharing the host namespace.
  setarch ? "/usr/bin/setarch"
, useSetarch ? true
}:

let
  inherit (pkgs) lib stdenv;

  # Bake the arch at eval time: `uname` is not on the builder's PATH.
  arch = stdenv.hostPlatform.uname.processor; # e.g. "x86_64"
  hermitCmd = lib.escapeShellArgs ([ hermit ] ++ hermitArgs);
  prefix = lib.optionalString useSetarch "${lib.escapeShellArg setarch} ${arch} -R ";

  # A store-resident shell script that re-execs the derivation's ORIGINAL
  # builder under hermit, forwarding the original argv unchanged.
  mkWrapper = origBuilder:
    pkgs.writeShellScript "hermit-exec-builder" ''
      exec ${prefix}${hermitCmd} -- ${lib.escapeShellArg origBuilder} "$@"
    '';

  # ---- the public API -------------------------------------------------------

  # hermitize : derivation -> derivation
  # Run this one derivation's builder under hermit. Idempotent.
  hermitize = drv:
    if (drv.passthru or { }) ? hermitWrapped then drv
    else drv.overrideAttrs (old: {
      realBuilder = mkWrapper drv.drvAttrs.builder;
      passthru = (old.passthru or { }) // { hermitWrapped = true; };
    });

  # hermitizeIfNeeded : derivation -> derivation
  # Opt-in by CONVENTION: a package declares `passthru.needsHermit = true;`
  # (in nixpkgs, or in a small overlay next to it) and this helper is a no-op
  # for every other package. That is the "enable hermit for ONLY the builds
  # that need it" knob: no nix patch, no nixpkgs fork.
  hermitizeIfNeeded = drv:
    if (drv.passthru or { }).needsHermit or false then hermitize drv else drv;

  # overlay : the same thing as a nixpkgs overlay, applied to a NAMED set of
  # packages. Usage:
  #   import <nixpkgs> { overlays = [ ((import ./hermit-wrap.nix {}).overlayFor [ "unrar" "zsh" ]) ]; }
  overlayFor = names: final: prev:
    lib.genAttrs names (n: hermitize prev.${n});

  # overlayNeedsHermit : honours `passthru.needsHermit` across an explicit list.
  overlayNeedsHermit = names: final: prev:
    lib.genAttrs names (n: hermitizeIfNeeded prev.${n});
in
{
  inherit hermitize hermitizeIfNeeded overlayFor overlayNeedsHermit mkWrapper;
  # Back-compat alias with the 20260729 prototype.
  wrap = hermitize;
}
