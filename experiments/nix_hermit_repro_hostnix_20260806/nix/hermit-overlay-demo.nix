# hermit-overlay-demo.nix — "how do we enable hermit for ONLY the builds that
# need it?", as a nixpkgs-side mechanism. No nix patch, no nixpkgs fork.
#
# The design splits the decision in two, which is what makes it upstreamable:
#
#   1. DECLARATION (belongs beside the package, ideally in nixpkgs itself):
#        passthru.needsHermit = true;
#      A package maintainer who knows their build is not reproducible marks it.
#      The attribute is inert: it changes no derivation and no output hash.
#
#   2. ENFORCEMENT (belongs to the consumer, one overlay):
#        hermitizeIfNeeded  -- wraps iff the package declared needsHermit
#      A consumer who wants hermit-determinized builds adds ONE overlay. Every
#      package that did not declare the flag is byte-identical to stock nixpkgs,
#      so nothing else in the closure is rebuilt.
#
# `hermitize` (unconditional, one package) is also exported for the case where
# the consumer, not the maintainer, knows a package needs it.
#
# Evaluate with:
#   nix-instantiate ./nix/hermit-overlay-demo.nix -A hermitized.lensfun
#   nix-instantiate ./nix/hermit-overlay-demo.nix -A untouched.hello

{ hermit ? "/home/newton/work/dev-hermit/worktrees/nix-repro176/hermit/target/release/hermit"
, hermitArgs ? [ "run" "--tmp=/tmp" "--no-rcb-time" "--max-timeslice" "disabled" ]
  # Packages the CONSUMER wants considered. An overlay cannot cheaply scan all
  # of nixpkgs for `needsHermit` (that would force evaluation of every
  # derivation), so the consumer names the set it cares about. In an upstream
  # world this list is the consumer's own build set, not a curated list of
  # broken packages.
, considered ? [ "lensfun" "hello" ]
}:

let
  wrapLib = pkgs: import ./hermit-wrap.nix { inherit pkgs hermit hermitArgs; useSetarch = false; };

  # (1) DECLARATION overlay — this is what would live in nixpkgs next to the
  # package. `lensfun` bakes `date +%s` into $out/share/lensfun/version_1/
  # timestamp.txt, so it is exactly a package a maintainer would mark.
  declareNeedsHermit = final: prev: {
    lensfun = prev.lensfun.overrideAttrs (old: {
      passthru = (old.passthru or { }) // { needsHermit = true; };
    });
  };

  # (2) ENFORCEMENT overlay — this is the consumer's single opt-in.
  enforceHermit = final: prev:
    let w = wrapLib prev;
    in builtins.listToAttrs (map (n: {
      name = n;
      value = w.hermitizeIfNeeded prev.${n};
    }) considered);

  pkgsPlain = import <nixpkgs> { };
  pkgsHermit = import <nixpkgs> { overlays = [ declareNeedsHermit enforceHermit ]; };
in
{
  # The package that DECLARED needsHermit: wrapped.
  hermitized = { inherit (pkgsHermit) lensfun; };
  # A package that did NOT declare it: untouched, same derivation as stock.
  untouched = { inherit (pkgsHermit) hello; };
  # Stock references for the equality assertions in harness/ergonomics-check.sh.
  stock = { inherit (pkgsPlain) lensfun hello; };

  # Escape hatch: consumer-side unconditional opt-in for one package.
  forced = (wrapLib pkgsPlain).hermitize pkgsPlain.hello;
}
