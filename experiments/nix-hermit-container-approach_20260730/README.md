# Nix under Hermit without host nix-install: rootless-podman container approach

**Date:** 2026-07-30 **Host:** devbig014 (non-root user `newton`) **Task:**
`nix-without-host-install-container-approach` (child of `epic-nix-reprobuild`).
Unblocks `rb-nix-execbuilder-prototype`, whose original host-nix-install
assumption is blocked (chef reverts/destroys the devserver).

## Question

Can a Nix build run under `hermit run --strict` with **no host nix-install** and
**no host-root**, by running Hermit *inside* a `nixos/nix` OCI image in
**rootless podman**? In particular: does Hermit's ptrace backend work inside the
rootless-podman user namespace? (Option 1 of the task.)

## TL;DR

- **YES — ptrace works in the rootless-podman userns.** `hermit run --strict
  --no-namespace` traces a guest (with virtualized PIDs) inside a rootless
  `nixos/nix` container. This is the core question, answered.
- **A full Nix build runs under `hermit run --strict` via the container**, with
  no host nix-install and no host-root: `nixos/nix:2.3.16` +
  `hermit run --strict --no-namespace -- nix-build …` builds a derivation and
  registers its store path (`rc=0`). Reproduce with
  [`recipe/nix-under-hermit.sh`](recipe/nix-under-hermit.sh).
- **Modern Nix (2.35) is blocked by one Hermit gap:** it manages the builder via
  `pidfd_send_signal` (syscall 424), which detcore classifies **Unsupported**.
  Under `--strict` detcore fails closed; the non-strict passthrough also fails
  for a pid-virtualized pidfd. Fix `pidfd_send_signal` in detcore and modern Nix
  works too.

## Recipe (Option 1)

Podman provides the container (rootless userns + filesystem); Hermit provides
deterministic execution *inside* it. Three non-obvious requirements:

1. **Run Hermit through the host loader + hostlibs.** The host-built `hermit`
   binary is dynamically linked with interpreter `/lib64/ld-linux-x86-64.so.2`,
   which does **not** exist in the `nixos/nix` image. Bind-mount a `hostlibs/`
   dir (the `ld-linux` loader plus every `.so` from `ldd hermit`) and invoke:
   `/hostlibs/ld-linux-x86-64.so.2 --library-path /hostlibs /hermit/hermit run …`.
2. **`podman run --security-opt seccomp=unconfined`.** The default podman
   seccomp profile makes `personality(2)` return `ENOSYS`; reverie uses
   `personality(ADDR_NO_RANDOMIZE)` to disable ASLR and otherwise aborts
   (`ERROR: Reverie could not disable address-space randomization`).
3. **`hermit run --no-namespace`.** Full-namespace Hermit tries to create/
   configure UTS+mount namespaces and fails rootless (`EPERM` on Hostname, then
   on Mount even with `--cap-add=SYS_ADMIN`) — rootless podman cannot grant
   those caps. Podman already provides the container isolation, so Hermit only
   needs to determinize, which `--no-namespace` does.

Plus a Nix-version caveat (see root cause): use a **pre-pidfd Nix**
(`nixos/nix:2.3.16`) until detcore implements `pidfd_send_signal`.

Minimal working invocation (what the recipe script runs):

```sh
podman run --rm --security-opt seccomp=unconfined \
  -v <hermit>/target/release:/hermit:ro -v <hostlibs>:/hostlibs:ro \
  docker.io/nixos/nix:2.3.16 \
  /hostlibs/ld-linux-x86-64.so.2 --library-path /hostlibs \
  /hermit/hermit run --strict --no-namespace -- \
  nix-build --no-out-link \
    --option build-users-group '' --option sandbox false --option substituters '' \
    -E 'derivation { name = "hermit-strict-hello"; builder = "/bin/sh";
        args = [ "-c" "echo hi > $out" ]; system = "x86_64-linux"; }'
# -> building '/nix/store/…-hermit-strict-hello.drv'...
# -> /nix/store/…-hermit-strict-hello        (rc=0)
```

`--option build-users-group ''` avoids Nix's per-uid kill-sweep; `sandbox false`
avoids namespace/mount setup Hermit+rootless can't do; `substituters ''` keeps
the build offline/hermetic.

## Test ladder (what was actually run)

| test | image | hermit args | podman security | result |
|---|---|---|---|---|
| A | latest | `--strict` | default | FAIL — `EPERM Hostname` (can't set hostname in userns) |
| B | latest | `--strict --no-namespace` | default | FAIL — `personality(2) ENOSYS` (seccomp blocks it) |
| C | latest | `--strict` | `+SYS_ADMIN +SYS_PTRACE seccomp=unconfined` | FAIL — `EPERM Mount` (rootless can't mount) |
| D | latest | `--strict --no-namespace` | `seccomp=unconfined` | **PASS** — guest traced, virtualized `pid=7`; **ptrace works** |
| E/F/G | latest | `--strict --no-namespace` | `seccomp=unconfined` | FAIL — `killing process N: Function not implemented`, hangs (modern-nix pidfd) |
| probe | latest | `--strict --no-namespace` | `seccomp=unconfined` | `pidfd_open`=fd3 OK; `pidfd_send_signal` → detcore abort |
| I | latest | `--no-namespace` (no `--strict`) | `seccomp=unconfined` | FAIL — same pidfd failure even via passthrough |
| **H / recipe** | **2.3.16** | **`--strict --no-namespace`** | **`seccomp=unconfined`** | **PASS — nix build completes, store path registered, `rc=0`** |

Logs for each are under [`logs/`](logs/). No host nix-install and no host-root
were used; the winning build's store path is **not** on the host `/nix` (the
host `/nix` is an unrelated leftover from the prior rb-nix task and was never
bind-mounted).

## Root cause for modern Nix (2.35): `pidfd_send_signal`

Modern Nix creates the builder, then signals/kills it via `pidfd_send_signal`
(nr 424). detcore's classifier (source, `hermit` @ `9c964fce`):

- `detcore/src/syscall_classification.rs:551` — `pidfd_open` → **Determinized**
  (implemented: `lib.rs:1959` → `syscalls/files.rs:2357`, registers
  `FdType::Pidfd`). Matches the probe: `pidfd_open` returned fd 3.
- `detcore/src/syscall_classification.rs:784-785` — `pidfd_getfd |
  pidfd_send_signal` → **Unsupported**.
- `detcore/src/lib.rs:284-309` — `handle_unsupported_syscall`: under `--strict`
  (`panic_on_unsupported_syscalls`) it **fails closed** (error/panic → sandbox
  abort); otherwise it **passes through to the host** (never `ENOSYS`).

Minimal confirmation ([`recipe/pidfd_probe.c`](recipe/pidfd_probe.c)): natively
in-container `pidfd_open`/`pidfd_send_signal`/`kill` all succeed; **under Hermit**
`pidfd_open` succeeds but `pidfd_send_signal` triggers
`ERROR detcore: inbound syscall: pidfd_send_signal(...) = ?` and aborts. Even in
non-strict mode (test I) the host passthrough does not work for Nix — the pidfd
refers to a Hermit-virtualized pid, so the passed-through signal fails and Nix
reports `Function not implemented` and hangs.

## Recommendations

1. **Implement `pidfd_send_signal` (and `pidfd_getfd`) in detcore** — translate
   the `FdType::Pidfd` target to the virtual pid and deliver the signal through
   detcore's existing signal path, deterministically. This unblocks modern Nix
   (2.35) under `--strict` and is the single highest-value fix for the nix RB
   track. (New determinization of a process-signalling syscall → likely
   `post-facto-human-review` trigger.)
2. **Until then**, the container recipe works today with pre-pidfd Nix
   (`2.3.16`) under `--strict`.
3. **Option 2** (Hermit as a lightweight container runtime via
   `containers/storage` to materialize a rootfs + Hermit's namespace half)
   remains the cleaner long-term seam; Option 1 here is the validated immediate
   unblock and needs no host changes.

## Reproduce

```sh
cd experiments/nix-hermit-container-approach_20260730
bash recipe/nix-under-hermit.sh            # old nix (2.3.16), --strict: PASS
IMAGE=docker.io/nixos/nix:latest bash recipe/nix-under-hermit.sh   # modern nix: reproduces the pidfd block
```

## Files

- `recipe/nix-under-hermit.sh` — the working recipe (self-populates hostlibs).
- `recipe/pidfd_probe.c` — minimal syscall probe isolating the root cause.
- `recipe/run-build.sh`, `recipe/trace-build.sh` — investigation drivers.
- `hostlibs/` — host loader + libs bind-mounted so hermit runs in the image.
- `logs/` — per-test output. `metadata.json` — SHAs, digests, host facts.
