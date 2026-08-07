# real-candidates.nix — the REAL nixpkgs packages under test, plus the minimal
# documented tweaks needed to make a package runnable under the seam at all.
#
# Every tweak here is applied to BOTH arms (native and hermit-wrapped) so the
# only independent variable stays `realBuilder`.

{ pkgs ? import <nixpkgs> { } }:

rec {
  # --- the tar/uid workaround -------------------------------------------
  # Detcore answers getuid/geteuid/getgid/getegid with a constant 0 in BOTH
  # namespace modes, so GNU tar believes it is root and tries to restore each
  # archive member's recorded ownership. What happens next depends on the mode:
  #   --no-namespace : no user namespace at all -> chown fails EPERM
  #                    ("Operation not permitted"), even to 0:0.
  #   --tmp=/tmp     : real user namespace, but uid_map is `0 <caller-uid> 1`,
  #                    i.e. exactly ONE mapped uid. chown 0:0 SUCCEEDS; chown to
  #                    any other uid fails EINVAL ("Invalid argument").
  # nixpkgs unpacks upstream tarballs that record foreign uids (1000, 500, ...),
  # so `unpackPhase` fails for essentially every tarball-sourced package.
  #
  # TAR_OPTIONS makes tar skip ownership/permission restoration. It is a NO-OP
  # for the native arm (tar only restores ownership when euid==0), so applying
  # it to BOTH arms keeps `realBuilder` the only independent variable.
  noSameOwner = drv: drv.overrideAttrs (_: {
    TAR_OPTIONS = "--no-same-owner --no-same-permissions";
  });

  hello-tarfix = noSameOwner pkgs.hello;
  which-tarfix = noSameOwner pkgs.which;
  bc-tarfix = noSameOwner pkgs.bc;
  figlet-tarfix = noSameOwner pkgs.figlet;

  # --- lensfun -----------------------------------------------------------
  # `pkgs/by-name/le/lensfun/package.nix` prePatch ends with
  #     date +%s > data/db/timestamp.txt
  # and cmake installs data/db into $out/share/lensfun/version_1/. That is the
  # real wall-clock second of the build, baked into the output: genuinely
  # on-machine nondeterministic, and exactly the class hermit virtualizes.
  lensfun = pkgs.lensfun;

  # WORKAROUND for the uid-virtualization blocker (README "Seam blockers"):
  # detcore answers getuid/geteuid/getgid/getegid with a constant 0, but under
  # `--no-namespace` the process holds no real privileges, so GNU tar believes
  # it is root, attempts to restore ownership, and dies with EPERM. Setting
  # TAR_OPTIONS is a NO-OP for the native arm (tar only restores ownership when
  # euid==0), so both arms remain the same derivation modulo `realBuilder`.
  lensfun-tarfix = pkgs.lensfun.overrideAttrs (_: {
    TAR_OPTIONS = "--no-same-owner --no-same-permissions";
  });

  # --- controlled urandom probe shaped like a real package ---------------
  # Kept next to the real ones so the urandom class has a witness even when a
  # real urandom-seeded package is not reachable offline.
  urandom-temp-names = pkgs.stdenv.mkDerivation {
    name = "urandom-temp-names";
    dontUnpack = true;
    dontFixup = true;
    buildPhase = ''
      runHook preBuild
      mkdir -p build
      # The classic "seed a scratch name from the kernel RNG" build pattern.
      for i in 1 2 3; do
        n=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
        echo "artifact-$i" > "build/tmp-$n.part"
      done
      ( cd build && ls -1 > ../manifest.txt )
      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall
      mkdir -p "$out"; cp manifest.txt "$out/"; cp -r build "$out/build"
      runHook postInstall
    '';
  };
}
