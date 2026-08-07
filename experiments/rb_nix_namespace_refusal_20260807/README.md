# Why a nix *build* cannot run under Hermit: Detcore refuses `unshare`

**Date:** 2026-08-07 **Host:** devbig176 **Agent:** claude-coord-176

## Headline

The chroot-store `chown` blocker is **fixed by an existing nix option**
(`--option build-users-group ""`), and `nix-instantiate` then succeeds under
`hermit run --image`. But a nix **build** still cannot run, and the reason is not
a container-construction detail: **Detcore deterministically refuses `unshare`**,
and nix 2.3 unshares a mount namespace before every builder on Linux.

This is a **deliberate determinism policy**, not a bug, so it is written up rather
than fixed. It blocks nix builds under **every** Hermit mode, not just `--image`.

## What was being chased

`experiments/rb_no_namespace_random_leaks_20260806` moved the Nix seam onto
full-namespace Hermit. PR
[#1843](https://github.com/rrnewton/hermit/pull/1843) then gave `run --image` a
minimal `/dev`, after which nix 2.3.16 runs inside the image. The next error was:

```
nix-instantiate --store 'local?root=/tmp/ns' -E 'derivation {...}'
  error: changing ownership of path '/tmp/ns/nix/store': Invalid argument
```

## Findings

### 1. The `chown` is a uid-map miss, and nix already has the switch

Inside image mode the guest is `uid=0(root) gid=0(root)` with every supplementary
group collapsed to `65534(nobody)` — Hermit's `map_root()` maps exactly one id.
The `nixos/nix` image ships `nixbld` at **gid 30000**, and nix (believing it is a
multi-user install because it is root) tries
`chown(realStoreDir, 0, 30000)`. gid 30000 is not in the container's gid map, so
the kernel returns **EINVAL**.

`--option build-users-group ""` is nix's documented single-user switch and skips
the chown entirely:

```
nix-instantiate --option build-users-group "" --store 'local?root=/tmp/ns' -E '…'
  /nix/store/hb09lmdvs22v5ianga0vgpi9k446q3kw-t.drv     <- succeeds
```

**No Hermit change needed for this half.** Extending the gid map instead would
require `/etc/subgid` delegation and a setuid `newgidmap`, which is not available
to a rootless in-process container.

### 2. The real blocker: Detcore refuses `unshare`, by design

With the chown out of the way, `nix-build` fails:

| store | error |
| --- | --- |
| chroot store (`local?root=…`) | `error: writing to file: Operation not permitted` |
| default store (`/nix/store`) | `error: setting up a private mount namespace: Operation not permitted` |

Direct probe — `unshare` inside Hermit, both modes, versus native:

| probe | native | `hermit run` | `hermit run --image` |
| --- | --- | --- | --- |
| `unshare --user` | OK | **EPERM** | **EPERM** |
| `unshare --mount` | OK | **EPERM** | **EPERM** |
| `unshare --pid` | OK | — | **EPERM** |

The host kernel permits user namespaces (`/proc/sys/user/max_user_namespaces` is
non-zero and native `unshare --user` succeeds), so this is Hermit, not host
policy. `detcore/src/syscall_classification.rs` refuses the whole family —
`mount`, `umount2`, `mount_setattr`, `move_mount`, `open_tree`, `fsopen`,
`fsmount`, `fsconfig`, `fspick`, `unshare`, `setns` — with a fixed `-EPERM`, and
says why in-source:

> …reconfigure mount and other namespaces … global kernel state a deterministic
> container pins for the whole run. … a deliberate deterministic refusal for the
> few unprivileged sub-modes (**user-namespace unshare**, non-clone `open_tree`)
> that would otherwise perturb the pinned container. Refusing in Detcore …
> removes a host dependency and a global-state isolation hole, and is
> bitwise-identical across `--verify` and record/replay.

nix needs exactly what that policy exists to forbid. This is a **collision between
two correct designs**, not a defect on either side.

### 3. It is not the `chroot`, and not specific to image mode

`create_user_ns()` returns EPERM for a chrooted process, so image mode's
`chroot(2)` was the obvious suspect. It is refuted: Hermit's **default** mode does
no chroot and `unshare --user` still returns EPERM there. Both modes fail for the
same Detcore reason.

### 4. A chroot store is not the trigger either

nix 2.3 forces a chroot build for a *diverted* store (`storeDir != realStoreDir`)
regardless of `sandbox = false`, so the chroot store looked like the thing to
avoid. Measured: the **default** store fails too, one step earlier, at
`unshare(CLONE_NEWNS)`. Avoiding store diversion does not avoid the namespace
requirement.

## What would have to change

Not a Hermit container-construction change, and not a `--image` change — the two
places where the last three blockers were fixed. The options are:

1. **Relax the Detcore refusal** so a guest may create namespaces it fully owns.
   This is a determinism-policy change to a documented fail-closed rule, with a
   real argument against it (the refusal exists to keep the pinned container
   bitwise-identical under `--verify` and record/replay). It would need the
   owner's judgement, not an agent's.
2. **Virtualize the namespace syscalls** rather than refuse them — let the guest
   believe it unshared while Detcore keeps one pinned container. That is a new
   determinization strategy for a whole syscall family.
3. **Patch nix** to skip its per-build mount namespace when already inside a
   determinized container. A nix patch, out of Hermit's tree.

Option 1 is the smallest and option 2 the most faithful; both are core-abstraction
decisions. Nothing here is an image-mode bug.

## Consequence for the reproducible-builds track

`hermit run --tmp=/tmp` remains the working seam (see
`rb_no_namespace_random_leaks_20260806`) — it wraps the **builder process** that
Nix `execve`s, so Nix itself does the namespace work outside Hermit and only the
build's own execution is determinized. What does not work is running **nix
itself** under Hermit and asking it to perform a build, which is what image mode
would need. The `--image` story is therefore *deterministic file inputs for a
guest*, not *a nix daemon under Hermit*, until the policy question above is
settled.

## Reproduce

```sh
cd experiments/rb_nix_namespace_refusal_20260807/harness
HERMIT_BIN=/path/to/hermit ./run-matrix.sh        # writes ../results.csv
```

Needs a Hermit built from PR #1843 or later (earlier builds fail sooner, on the
empty `/dev`), rootless `buildah`, and network for the image pull. `native` must
report `OK` on all three probes; if it does not, the host forbids user namespaces
and the Hermit rows mean nothing.

## Files

- `harness/probe-namespace-syscalls.sh` — the three `unshare` probes.
- `harness/run-matrix.sh` — runs them native / default / image; writes `results.csv`.
- `results.csv` — probe results plus the four nix outcomes.
- `metadata.json` — SHAs, host, toolchain, exact commands.
