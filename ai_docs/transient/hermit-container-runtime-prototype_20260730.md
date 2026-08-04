# Hermit-as-container-runtime prototype: OCI image rootfs under `hermit run --image`

- **Task:** `hermit-container-runtime-prototype` (Option 2 from the nix-container research).
- **Status:** IMPLEMENTED (prototype), on branch, not landed.
- **PR:** https://github.com/rrnewton/hermit/pull/1179 (draft)
- **Hermit branch:** `codex/hermit-container-runtime-prototype`
- **Hermit commit:** `c23152ffc8b9d7f9b88a7c8b49cf07559126bb57`
- **Base:** `origin/main` `45205d40a1e222a7b2729355d0ffb798f765e05f`
- **Reverie:** unchanged (pin `9216e22f`); this is a Hermit-only change.
- **Author:** impl agent, claude-opus-4-8.

## Question

Can hermit itself act as (part of) a container runtime — running an arbitrary
command against the **deterministic file inputs of a pinned OCI image** — while
keeping hermit's deterministic execution guarantees? The nix-container research
proposed Option 2: reuse *part* of the podman stack as a library
(`containers/image` to pull an image by digest + `containers/storage` to unpack
it to an overlay rootfs), then have hermit add the piece podman-as-a-whole would
otherwise own — the mount namespace + `pivot_root`/`chroot` — so a hermit
invocation executes the guest against that pinned rootfs.

Net goal: `hermit run --image <oci-digest> -- <cmd>` runs `<cmd>` with file
inputs coming deterministically from the image digest, under hermit's
deterministic execution, with no full podman and no host root (rootless/userns).

## Result (short)

Works end-to-end as a prototype.

- **busybox (trivial image):** `hermit run --strict --verify --image busybox@sha256:… -- /bin/busybox uname -a` → **L2** (`:: Success: deterministic. Determinism verified.`). `--strict` runs of `echo`, `ls /bin | md5sum` are **byte-identical across 3 runs (L1)**.
- **nixos/nix (real image):** `hermit run --strict --image nixos/nix -- /bin/sh -c 'nix --version; id -u; echo $PATH'` runs `nix (Nix) 2.35.1`, resolves the image's own coreutils, reports `USER=root`, applies the image `PATH` (`/root/.nix-profile/bin:…`), and is **byte-identical across 2 runs (L1)**.
- **Determinism-of-inputs proof:** the image's `/bin/busybox` is dynamically
  linked and needs `GLIBC_2.38`; the host has glibc `2.34`. Run **directly on
  the host** it fails (`version 'GLIBC_2.38' not found`); run **under
  `--image`** it succeeds because the chroot makes the *image's own*
  `/lib64/ld-linux-x86-64.so.2` + libc the ones that resolve. The file inputs
  come from the digest, not the host.

All backend claims here are the **ptrace** backend (the default and only backend
exercised for this prototype). See the backend-agnostic section for how the
filesystem layer is expected to compose with DBI/KVM.

## Seam design

The design cleaves the container runtime into two halves and only adds the one
hermit was missing:

```
                 ┌──────────────────────── hermit run --image ─────────────────────────┐
 OCI reference ─▶│  materialize_rootfs()  ──▶  plain user-owned rootfs dir (by digest)  │
   (by digest)   │        (image.rs)                       │                            │
                 │                                          ▼                            │
                 │  image_container()  ──▶  unshare(PID) + map_root + mount /proc into   │
                 │     (container.rs)         <rootfs>/proc  + chroot(rootfs)            │
                 │                                          │                            │
                 │  guest_command()  ──▶  cwd=image WorkingDir; env = image Config.Env   │
                 │     (run.rs)             over empty base; program resolved in rootfs  │
                 └──────────────────────────────────────────┬──────────────────────────┘
                                                             ▼
                        Detcore / Reverie backend attaches to the guest as usual
                        (ptrace today; DBI/KVM unchanged by this layer — see below)
```

### The filesystem half hermit added

`hermit-cli/src/bin/hermit/image.rs` (new module):

- **`materialize_rootfs(image_ref) -> rootfs dir`.** The task frames Option 2 as
  "use PART of podman as a library." `containers/image` and `containers/storage`
  are Go libraries; wiring them into this Rust binary directly is out of scope
  for a prototype, so the module shells out to **rootless `buildah`**, which
  *embeds exactly those two libraries*. `buildah from` pulls the reference
  through `containers/image` into `containers/storage`; `buildah mount` (inside a
  `buildah unshare` user namespace) exposes the unpacked overlay rootfs; we
  `cp -a` the merged tree out to a plain, user-owned, **digest-keyed cache dir**
  so the result is independent of the transient overlay mount and reused on the
  next run. The seam it exposes — **`image_ref -> plain rootfs directory`** — is
  unchanged if a production version later links the Go libraries or a Rust OCI
  unpacker instead of forking `buildah`.
- **0555 image-root problem.** Image roots are frequently mode `0555` and can
  contain entries owned by unmapped sub-UIDs, so hermit (a plain user, outside
  any userns after materialization) cannot reliably write into the rootfs. All
  rootfs mutation — creating the pseudo-dirs `/proc`, `/sys`, `/dev`, `/tmp`,
  `chmod`, and writing the in-root config copy — is therefore done **inside
  `buildah unshare`** where we act as ns-root with `CAP_DAC_OVERRIDE`. The
  readiness marker is written **beside** the rootfs in the hermit-owned cache
  dir (always writable), and written **last**, so an interrupted materialization
  is never mistaken for a complete one.
- **`resolve_in_rootfs(rootfs, guest_abs)`** — a chroot-aware symlink walk.
  Images (nixos/nix is the extreme case) populate `/bin/sh`, `/usr/bin/env`,
  etc. as symlinks whose targets are **absolute guest paths**
  (`/nix/store/…-bash/bin/bash`). Those resolve only relative to the image root;
  a naive host `stat` follows them against the host `/` and fails. The resolver
  walks component-by-component and, on each symlink, re-roots an absolute target
  back onto `rootfs` (relative targets stay relative to the link's dir), exactly
  as a chrooted kernel would, with a 40-hop loop budget and `..` bounded so it
  cannot escape the rootfs.
- **`read_image_config` / `ImageConfig`** — captures the image's OCI
  `Config.Env` and `Config.WorkingDir` (pinned by the digest) and reads them
  from **two locations**: the host cache dir (valid pre-chroot, e.g. program
  validation) and the `/`-relative in-root copy (valid post-chroot). The
  two-location read is what makes the digest-pinned environment reach the guest,
  because the environment that actually runs the program is built in the forked,
  **chrooted** child, where absolute host cache paths no longer resolve.

### The namespace half hermit already owned

`hermit-cli/src/bin/hermit/container.rs`:

- **`image_container(rootfs, pin_threads)`** builds a reverie-process
  `Container` that `unshare(PID)` + `map_root()`, mounts the deterministic
  `/proc` into `<rootfs>/proc` (pre-created by the materializer), then
  `chroot(rootfs)`. Mounts are applied at their literal pre-chroot target paths
  and the chroot makes them visible under the new root — the same ordering the
  replay chroot path (`replay.rs`) uses.
- The image already carries its own `/etc/group`, loader, and libc (all pinned
  by the digest), so the frozen-identity hardening mounts that `run` normally
  adds are unnecessary; `IdentityGuard::empty()` is returned. `IdentityGuard`
  was refactored to hold `Option` backing temp files so the empty case is
  representable.

`hermit-cli/src/bin/hermit/run.rs`:

- `--image <OCI-REFERENCE>` clap flag; conflicts with `--no-namespace` (the
  chroot requires the namespace machinery).
- `container()` returns `image_container(&rootfs, …)` when `--image` is set.
- `validate_program()` resolves the program **inside the rootfs** (via
  `resolve_in_rootfs`) and validates it there, so image-only binaries (e.g.
  busybox `/bin/busybox`, absent on the host) are accepted and host look-alikes
  are never silently used. The program path must be **absolute** — PATH search
  inside the image is out of scope for the prototype.
- `guest_command()` defaults the guest cwd to the image `WorkingDir` (else `/`,
  so `getcwd` does not fail on the now-unreachable host cwd), and applies the
  image's own `Env` over an `env_clear()` base (with hermetic `PATH`/`HOME`
  fallbacks when the image declares none), then merges user `--env` on top.

## How it composes with ptrace determinism

The prototype makes the **file inputs** deterministic and leaves the **execution
determinism** exactly as Detcore already provides it — the two are orthogonal,
which is the whole point.

- **Inputs pinned by digest.** Loader, libc, every tool, and the declared
  `Env`/`WorkingDir` come from the image digest, materialized into a
  content-stable, digest-keyed cache dir and reused byte-for-byte across runs.
  The GLIBC-`2.38`-vs-host-`2.34` proof shows the guest genuinely executes the
  image's files, not host look-alikes: without the chroot the same binary cannot
  even load on this host.
- **Execution determinized by Detcore as usual.** Once `image_container`
  chroots and the guest execs, the ptrace backend attaches and Detcore
  virtualizes time, randomness, scheduling, and syscalls exactly as it does for
  a host-filesystem run. `--strict` gave byte-identical repeats (L1) for busybox
  and nixos/nix; `--strict --verify` confirmed L2 for busybox `uname -a`.
- **Environment determinism.** Leaking the host environment into an image with a
  different filesystem layout is both a usability bug (host `PATH` entries are
  absent from the image) and a determinism leak. Applying the image's *own*
  declared `Env` over an empty base makes the environment a function of the
  digest. For nixos/nix this is what lets `nix` find its store paths, certs, and
  channels (`PATH`, `SSL_CERT_FILE`, `NIX_PATH`, …).
- **Residual host coupling (honest limitations below).** The prototype does not
  yet make the *image contents themselves* immutable at runtime (the rootfs is a
  plain writable dir), nor does it virtualize `/proc`/`/sys` contents beyond
  Detcore's normal handling. Determinism of a run that *writes* into the rootfs
  is only as good as starting from the same materialized bytes.

## What works / what doesn't

### Works

- Rootless pull-by-digest → unpack → chroot → run, with no host root.
- Trivial image (busybox): L2 verified; L1 byte-identical x3.
- Real image (nixos/nix): `nix` runs, coreutils resolve, image `Env` applied,
  L1 byte-identical x2.
- Image-only binaries and absolute-symlink `/bin/sh` (nix) resolve correctly.
- Digest-keyed caching: second run reuses the materialized rootfs (no re-pull).
- Negative paths: `--image` + `--no-namespace` is a clap conflict; a
  non-absolute program path is a clear error; `--image` documented in `--help`.

### Doesn't / out of scope for the prototype

- **PATH search inside the image** is unsupported; the program path must be
  absolute (`/bin/sh`, not `sh`).
- **No Go-library linking.** Materialization forks `buildah` rather than linking
  `containers/image`/`containers/storage`; the seam is designed so this can be
  swapped without touching callers, but the prototype hard-depends on a rootless
  `buildah` on `PATH`.
- **Writable rootfs / no overlay-at-run.** The chroot uses the materialized dir
  directly; there is no per-run copy-on-write upper layer, so a guest that
  writes to the rootfs mutates the cache. A production version wants an overlay
  (or `pivot_root` into a fresh upper) per run.
- **`--verify` teardown is slow on this host** (DETLOG logging); a 2-minute cap
  is too short, a 300s run succeeds. This is host I/O, not a hang.
- **No image signature/policy verification** beyond what `buildah` does by
  default; pin by digest for reproducibility.
- **`/dev` is a bare empty dir**, not a populated/deterministic device tree.

## Backend-agnostic composition (KVM / DBI) — owner footnote

The OCI-rootfs + mount-namespace/chroot filesystem layer is **orthogonal to the
execution backend**. It runs entirely in `image_container()` *before* any
Detcore/Reverie backend attaches: it configures the mount namespace and the root
(and the guest's cwd/env) and then the normal backend attach happens against a
guest that merely has a different root filesystem. Nothing in `image.rs` or
`image_container` references ptrace, DBI, or KVM. This matches the repo's
backend definition — a backend is the `Detcore<XxxGuest>` execution path; the
rootfs is an input to whichever path runs. ptrace is the compatibility baseline;
the performance win is graduating this same filesystem layer to DBI/KVM.

**ptrace (baseline, exercised here).** The tracer and guest share the host
kernel; `chroot` + the private mount namespace put the guest's file inputs in
the image while syscalls are still trapped and determinized by Detcore. This is
what the validation below covers. No backend-specific concern.

**DBI (in-process instrumentation).** The guest and the instrumentation run in
the **same process/address space** on the host kernel, so it observes the same
mount namespace + chroot the container setup established: the filesystem layer
composes unchanged, because "which files the guest sees" is a host-kernel
namespace property, independent of *how* instructions are intercepted.
Backend-specific concerns are narrow and about *ordering/self-view*, not the
rootfs: (a) the DBI engine's own loader/agent must be resolvable at attach time
— either mapped before the chroot or present inside the rootfs — since after
chroot the host paths it might otherwise `dlopen`/read are gone; (b) any
code-cache or agent temp files must live on a path that still exists post-chroot
(e.g. `/tmp`, which the materializer creates). Neither changes the seam; both
are "make the agent's own inputs available inside the new root," the same
discipline the guest program already follows.

**KVM (guest executes in a VM; gVisor-style).** Here the model inverts: the
rootfs is no longer consumed via the host kernel's VFS through a chroot, but must
be presented **to the guest kernel/sandbox as its filesystem**. The materialized
rootfs directory is exactly the right artifact for that — it is the same thing a
gVisor `--rootfs`/OCI bundle or a virtio-fs/9p share would export into the VM.
The seam (`image_ref -> plain rootfs directory`) is preserved; what changes is
the *presentation mechanism*: instead of `chroot(rootfs)` on the host, a KVM
backend would (i) export `rootfs` to the guest (virtio-fs / 9p / an initramfs or
block image built from it), and (ii) set the guest's root to it. `image.rs` is
untouched; only a KVM-specific analogue of `image_container` (a "present this
rootfs to the VM" step) is needed. Relationship to gVisor: gVisor already
consumes an unpacked OCI bundle as a directory and applies its own sandbox
filesystem gofer; our `materialize_rootfs` output is directly analogous, so a
KVM/gVisor-style backend can reuse the materializer verbatim and supply its own
presentation layer. The determinism argument is *stronger* under KVM: the guest
kernel's own VFS sees only the exported image, so host filesystem coupling is
eliminated rather than merely chrooted away.

**Summary:** ptrace and DBI consume the rootfs through the **host kernel** via
the private mount namespace + chroot (same code path, `image_container`); KVM
consumes it by **exporting the directory into the VM** (a new
presentation step, same materialized artifact). In all three the pull/unpack/
config-capture (`image.rs`) is identical and backend-neutral.

## Validation

```
Hermit SHA:  c23152ffc8b9d7f9b88a7c8b49cf07559126bb57
Base:        origin/main 45205d40a1e222a7b2729355d0ffb798f765e05f
Reverie:     unchanged (9216e22f)
Backend:     ptrace (default; only backend exercised)
Host:        x86_64 Linux; host glibc 2.34; buildah 1.43.2
```

| Check | Command | Result |
| --- | --- | --- |
| Build | `cargo build -p hermit --bin hermit` | pass |
| Format | `cargo fmt --all -- --check` | clean |
| Clippy | `cargo clippy -p hermit --bin hermit` | clean (no warnings) |
| Unit/bin tests | `cargo test -p hermit --bin hermit` | 86 passed, 0 failed (incl. 7 new `image` tests) |
| busybox L2 | `hermit run --strict --verify --image busybox@sha256:fd8d9aa6…9715d -- /bin/busybox uname -a` | `:: Success: deterministic. Determinism verified.` |
| busybox L1 x3 | `hermit run --strict --image busybox@… -- /bin/echo …` and `… -- /bin/busybox sh -c 'ls /bin | md5sum'` | byte-identical across 3 runs |
| nix runs | `hermit run --strict --image nixos/nix -- /bin/sh -c 'nix --version; id -u; echo $PATH'` | `nix (Nix) 2.35.1`, `id -u`=0, image PATH applied |
| nix L1 x2 | same command, two runs, `diff` | byte-identical (`6ff4d49c…`) |
| Inputs-from-digest proof | `<rootfs>/bin/busybox` run directly on host | `GLIBC_2.38 not found` (host 2.34); succeeds only under `--image` chroot |
| Negative: ns conflict | `hermit run --image X --no-namespace …` | clap error (mutually exclusive) |
| Negative: rel program | `hermit run --image X -- echo …` | clear "path must be absolute inside the image" error |

Determinism levels reached: **L2** (busybox `uname -a`, ptrace), **L1**
(busybox + nixos/nix multi-command), ptrace backend, default log level, no
relaxations.

## Reproduction

```bash
# In a slot's hermit worktree, on the feature branch:
cargo build -p hermit --bin hermit
BB="docker.io/library/busybox@sha256:fd8d9aa63ba2f0982b5304e1ee8d3b90a210bc1ffb5314d980eb6962f1a9715d"
./target/debug/hermit run --strict --verify --image "$BB" -- /bin/busybox uname -a
./target/debug/hermit run --strict --image docker.io/nixos/nix:latest -- \
  /bin/sh -c 'nix --version; id -u; echo $PATH'
```

Requires a rootless `buildah` on `PATH` and network/proxy access to the
registry for the first (uncached) materialization.

## Follow-ups (if graduated past prototype)

1. Per-run overlay/`pivot_root` upper layer so guest writes do not mutate the
   shared cache.
2. Link `containers/image`/`containers/storage` (or a Rust OCI unpacker) to drop
   the `buildah` fork; the `image_ref -> rootfs dir` seam is already isolated.
3. Optional PATH search inside the image.
4. KVM presentation layer (virtio-fs/9p/block image from the same materialized
   dir) to realize the backend perf win with stronger host isolation.
5. Digest/signature policy enforcement.
