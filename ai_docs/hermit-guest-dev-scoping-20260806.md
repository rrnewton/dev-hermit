# Hermit guest `/dev` scoping — host passthrough closed

**Task:** `hermit-default-run-passes-entire-host-dev-through`
**Date:** 2026-08-06
**Agent:** `hermit-devscope` (opus-5)
**Slot:** `worktrees/devscope/hermit`
**Branch:** `feat/scope-guest-dev-minimal-set`
**Commit:** `9570ef9e585e683627bca368b350bbef7b396c63` (base `b64d893ae9ea6404472eae9cb86102d91ec642ef`)
**Publication:** NOT published. GitHub egress was 403 on CONNECT for the whole
session (`with-proxy curl https://github.com/rrnewton/hermit` → `curl: (56)
Received HTTP code 403 from proxy after CONNECT`, http_code `000`;
`git ls-remote` → `CONNECT tunnel failed, response 403`). The branch is
committed locally only; there is no PR.

---

## 1. The defect, confirmed

`hermit run` never mounted anything at `/dev`. `container::default_container()`
mounted only `Mount::proc()`; `RunOpts::mounts()` added a frozen `/etc/group`, an
empty nscd directory, the user's `--mount`/`--bind` entries, and a tmpfs bind over
`/tmp`. Nothing covered `/dev`, so the guest's mount namespace — created by
`CLONE_NEWNS` from the host — inherited **the host `/dev` verbatim**.

Measured on this host (devbig, 316 cores), by reverting the fix and listing the
guest `/dev`: **303 entries**, including

```
kvm  mem  kmsg  fuse  console  vfio  vhost-net  vhost-vsock  vsock  udmabuf
userfaultfd  mapper  md0  loop-control  loop0 … loop127  nvme0n1 nvme0n1p1
nvme1n1p1 … nvme2n1p1  ng0n1 ng1n1 ng2n1  ublk-control  tpm0 tpmrm0  ipmi0
i2c-0 i2c-1 i2c-2  hpet  hsmp  rtc0  ptp0  pps0  tty0 … tty63  ttyS0 … ttyS15
vcs* vcsa* vcsu*  hwrng  mcelog  port  block  char  cpu  disk  net  input
```

This is both an **isolation hole** (raw block devices, physical memory, the KVM
control node, the IPMI/TPM/I2C nodes, and the host's world-writable `/dev/shm`
were all reachable by an untrusted guest running under a tool whose entire
purpose is containment) and a **nondeterminism source** (the set of entries and
the contents of `/dev/shm` differ between hosts and between runs on one host).

## 2. What the guest gets now

A fresh `/dev` with exactly 13 entries:

| Entry | Kind | Backing |
| --- | --- | --- |
| `null` `zero` `full` `random` `urandom` `tty` | char device | bind mount of the host node |
| `shm` | directory | **fresh empty** `tmpfs`, `mode=1777` |
| `pts` | directory | **private** `devpts` instance, `ptmxmode=0666` |
| `fd` `stdin` `stdout` `stderr` | symlink | `/proc/self/fd[/N]` — resolves through the guest's own deterministic `/proc` |
| `ptmx` | symlink | `pts/ptmx` |

`--backend=kvm` additionally gets `/dev/kvm`. That is the one genuine backend
requirement: `reverie-kvm` opens `/dev/kvm` from *inside* the container to create
the VM, so scoping it away would make the KVM backend fail to start. No other
backend reaches for a `/dev` node — ptrace and e9patch use ptrace/seccomp, DBI
rewrites the code stream, and PMU preemption goes through `perf_event_open`.

`--host-dev` restores the old behavior for anyone who needs a device Hermit does
not provide. It is documented as compromising isolation and reproducibility.

## 3. Why the tree is staged, not built in place

This is the non-obvious part of the implementation and the reason a naive
"mount a tmpfs on `/dev`, then bind the allowed nodes into it" does **not** work.

Reverie applies `Container` mounts **in order, inside the forked child**, before
`execve` (`reverie-process/src/container.rs::setup`). Bind-mount *sources* are
resolved at that moment, in the child's namespace. So:

* Mount a tmpfs on `/dev` first → `/dev/null` now names an entry in the *new,
  empty* tmpfs, and binding it fails with `ENOENT`.
* Bind host `/dev` to an alias path first, then tmpfs over `/dev`, then bind from
  the alias → works, but leaves the entire host `/dev` reachable at the alias.
  That is the hole, relocated.

So `minimal_dev_mounts()` stages the finished tree in a private host temp
directory: it binds each allow-listed host node onto a pre-created placeholder
file *inside the staging directory* (host `/dev` is still the one in view at that
point), mounts the fresh `tmpfs`/`devpts` on pre-created staging subdirectories,
creates the symlinks with ordinary `symlink(2)` from the parent, and only then
recursive-binds the whole staging directory over `/dev` as the **last** mount.
Host `/dev` is never exposed under a second path.

`MS_REC` on the final bind is load-bearing: without it the staged `tmpfs`/`devpts`
submounts would not come along and the guest would see empty `shm`/`pts`
directories.

**Propagation safety.** These mounts cannot leak back to the host. `copy_mnt_ns()`
applies `CL_SHARED_TO_SLAVE` when the new mount namespace is owned by a different
user namespace, and Hermit's container unshares `CLONE_NEWNS` together with
`CLONE_NEWUSER` (`map_root()`). Propagation is therefore one-way, host → guest.

**Why bind mounts and not `mknod`.** A user namespace does not grant the ability
to create device nodes, so the standard rootless-container technique — bind the
host's nodes — is the only option. Verified in-guest: the nodes are real
character devices with the correct major/minor, not the empty placeholder files
they were staged from.

## 4. Code

All in `hermit-cli/src/bin/hermit/`:

* `container.rs`
  * `DEFAULT_GUEST_DEVICES`, `GUEST_DEV_SYMLINKS`, `DEV_DIR` constants.
  * `minimal_dev_mounts(extra_devices) -> (Vec<Mount>, TempDir)` — builds the
    staged tree described above.
  * `guest_device_names(extra_devices)` — the device name list, for tests.
  * `identity_mounts()` — the previous frozen-`/etc/group` + nscd logic, split out.
  * `hardening_mounts(extra_devices)` — identity + scoped `/dev`. Replaces the old
    `identity_hardening_mounts()`.
  * `host_dev_hardening_mounts()` — identity only; the `--host-dev` path.
  * `IdentityGuard` → **`MountGuard`**, because it now also owns the `/dev`
    staging directory, which must outlive `Container::run`.
  * `deterministic_container()` (record/replay) now uses `hardening_mounts(&[])`,
    so record and replay get the same scoped `/dev` as `run`.
* `run.rs`
  * `--host-dev` flag; conflicts with `--no-namespace`; rejected with an
    explanatory message when combined with `--image`.
  * `RunOpts::extra_guest_devices()` → `["kvm"]` for the KVM backend, else empty.
  * `RunOpts::mounts()` selects scoped vs. host `/dev`.
* `record_start.rs`, `replay.rs` — `MountGuard` rename only.
* `hermit-cli/tests/hermit_modes.rs` — the two integration tests below.

**Deliberately out of scope.** `--image` (`image_container`) is untouched. That
path gives the guest whatever `/dev` the OCI image carries — usually empty, with
no `/dev/null`. That is a *separate* known `--image` defect recorded in the
`research-oci-integration-next-phases` notes; it is a missing-`/dev` bug, not the
host-passthrough hole this task names, and `--image` could not be exercised here
(no egress to pull an image). The `image_container` doc comment now says so
explicitly so the omission is not mistaken for coverage.

## 5. Validation

Host: devbig, 316 cores, heavily contended (56 worktrees, concurrent validate
runs). Backend `ptrace` unless stated. Log level: default. Relaxations: the
listing runs use `--no-sequentialize-threads --no-deterministic-io` (the repo's
`default_hermit_command`); the L1 run uses none.

Build workaround: `libunwind` is not installed on this box (fleet-wide), so all
builds ran with `PKG_CONFIG_PATH=/tmp/lu/usr/lib64/pkgconfig
LIBRARY_PATH=/tmp/lu/usr/lib64 C_INCLUDE_PATH=/tmp/lu/usr/include
LD_LIBRARY_PATH=/tmp/lu/usr/lib64` against an unpacked `libunwind-devel` RPM.

### 5.1 Direct observation

| Check | Command (abbrev.) | Result |
| --- | --- | --- |
| Scoped listing | `hermit run -- sh -c 'ls -1 /dev'` | exactly `fd full null ptmx pts random shm stderr stdin stdout tty urandom zero` (13) |
| Host listing | same, `--host-dev` | 303 entries |
| Node identity | `ls -lL /dev/{null,zero,full,random,urandom,tty}` in-guest | `crw-rw-rw-` with `1,3` `1,5` `1,7` `1,8` `1,9` `5,0` |
| Devices usable | `echo > /dev/null`, `head -c 8 /dev/{zero,urandom,random}`, `echo ok > /dev/shm/scratch && cat`, `test -d /dev/pts`, `test -e /dev/fd/1` | `ALL-OK` |
| `/dev/full` semantics | `echo x > /dev/full` | `ENOSPC` (`full-enospc-ok`) |
| Private devpts | `python3 -c 'import pty,os; m,s=pty.openpty(); print(os.ttyname(s))'` | `/dev/pts/0` — a private instance, not host numbering |
| KVM extra device | `hermit run --backend=kvm -- sh -c 'ls -1 /dev'` | 14 entries, includes `kvm` |
| KVM absent by default | `test -e /dev/kvm` in-guest | `kvm-scoped` |

### 5.2 Planted-artifact bracket

A file `/dev/shm/hermit-dev-leak-probe-<pid>-<nanos>` is created on the host while
the test runs, so its absence cannot be explained by a stale image:

* default → `SCOPED` (probe not visible in-guest)
* `--host-dev` → `LEAKED` (probe visible in-guest)

Both halves fire, so the negative result is a real refusal and not a broken probe.

### 5.3 Mutation bracket

Reverting `RunOpts::mounts()` to the host-`/dev` path (keeping everything else)
and rerunning:

```
test default_guest_dev_is_scoped_to_the_minimal_set ... FAILED
  left:  [303 host entries: autofs block … kvm … loop0 … nvme2n1p1 … vhost-vsock zero]
  right: [fd full null ptmx pts random shm stderr stdin stdout tty urandom zero]
test host_dev_flag_restores_the_host_device_tree ... ok   (correct: tests the unchanged path)
```

The test is not inert.

### 5.4 Suites

| Gate | Result |
| --- | --- |
| `cargo fmt --all -- --check` | clean |
| `cargo clippy -p hermit --all-targets -- -D warnings` | clean |
| `cargo test -p hermit --bin hermit` | **110 passed, 0 failed** (includes 4 new/updated `container::tests`) |
| `cargo test -p hermit --test hermit_modes -- guest_dev host_dev` | 2 passed |
| `mount_introspection`, `pty_nr_determinism` | 1 passed each — the two targets most directly exposed to the new mounts |
| `random_uuid_determinism`, `host_kernel_probes`, `privileged_observation`, `host_security_identity`, `process_isolation_refusals`, `syscall_file_metadata`, `syscall_file_io`, `procfs_determinism` | all pass (25 tests in `procfs_determinism`) |
| `cargo test -p hermit --test hermit_modes` (whole target) | **73 passed, 0 failed, 10 ignored** |
| 35-target integration sweep | **33 pass, 2 fail** — both environmental, §5.5 |
| `cargo test -p hermit --test cli -- --skip run_kvm --skip dbi_ --skip sabre_ --skip liteinst` | 17 passed, 1 failed (DBI feature gate, §5.5) |

Targets covered by the sweep, all passing: `record_replay` (42 tests),
`procfs_determinism` (25), `signal_determinism` (12), `epoll_determinism` (7),
`mmap_determinism` (5), `arbitrary_binaries` (4), `integration_matrix`,
`mount_introspection`, `pty_nr_determinism`, `host_kernel_probes`,
`privileged_observation`, `host_security_identity`,
`process_isolation_refusals`, `syscall_file_metadata`, `syscall_file_io`,
`ipc_determinism`, `prodcons_determinism`, `thread_sync_determinism`,
`writev_determinism`, `syscall_quick_wins`, `hashseed_determinism`,
`clock_determinism`, `relaxed_flag_matrix`, `compression`, `analyze`,
`kernel_keyring`, `pidfd_creation`, `ptrace_refusal`, `perf_event_refusal`,
`robust_list_queries`, `scheduler_policy_queries`, `sysv_legacy_fallbacks`,
`socket_cookie_determinism`, `unix_socket_table_determinism`,
`zero_copy_pipe_fallback`, `sqlite_veryquick`, `leveldb`, `python_stdlib`,
`random_uuid_determinism`, `app_strict_verify`, `command_strict_verify`,
`language_runtime_determinism`.

### 5.5 Every failure is environmental, none is this change

| Failure | Cause | Why not this change |
| --- | --- | --- |
| `random_determinism::dbi_random_sources_…`, `cli::backend_accepted_in_global_position` | `Error: backend 'dbi' is unavailable: DBI support was not included in this build` | Third-party backends are behind a cargo feature that is off in a plain `cargo test`. Independent of `/dev`. |
| `redis_strict` (3 tests) | `redis-server is required; the portable CI job installs it` | Missing host binary. |
| `version_provenance::cargo_rebuilds_provenance_after_an_unstaged_tracked_edit` | `error: let chains are only allowed in Rust 2024 or later --> build_support.rs:60:8` | `hermit-cli/build_support.rs` is not in this diff (`git diff HEAD~1 HEAD -- hermit-cli/build_support.rs` is empty). The let chain came from `63a542af5` "CI: collapse nested if-let in build_support to satisfy clippy". **Pre-existing bug on main.** |

### 5.6 KVM is not covered, for a pre-existing reason

Every `hermit run --backend kvm --strict` hangs on this box. Bracketed against the
unmodified path:

```
timeout 120 hermit run --backend kvm --strict            -- /bin/echo hello   →  rc=124
timeout 120 hermit run --backend kvm --strict --host-dev -- /bin/echo hello   →  rc=124
```

`--host-dev` routes through the unmodified mount logic, so the hang is not the
`/dev` scoping. Concurrently, an unrelated agent's
`hermit/target/release/hermit run --backend=kvm /bin/true` (a different worktree)
had been running for 1h13m. What *is* verified for KVM: `--backend=kvm -- sh -c
'ls -1 /dev'` completes and lists 14 entries including `kvm`.

Cleanup note: 40 hung KVM processes from the aborted full-suite run were
terminated by **exact PID**, each confirmed mine by matching
`/proc/<pid>/cmdline` against the `worktrees/devscope/hermit/target/debug` path.
No pattern, name, `-f`, or user-wide kill was used (Hard Invariant 15).

### 5.7 Assurance level reached: **L1**, not L2

`hermit run --strict` (relaxations: none) over a `/dev`-touching guest exits 0 and
prints the expected scoped listing → **L1, ptrace backend**.

**L2 could not be established on this host, for a reason unrelated to this
change.** `--verify-strict` diverges for *every* guest tried, including
`/usr/bin/true`, and — critically — it diverges **identically with and without
the change**:

| Guest | `--verify-strict` scoped | `--verify-strict --host-dev` (= pre-change `/dev`) |
| --- | --- | --- |
| `/usr/bin/true` | `diverged`, `bitwise_parity: false`, 393/393 compared | `diverged`, `bitwise_parity: false`, 393/393 compared |
| `sh -c 'ls -1 /dev; head -c 16 /dev/urandom \| od; echo x > /dev/null'` | `diverged`, 3466/3466 | `diverged`, 3476/3476 |

The reported divergent lines render **identically** on both sides (e.g. run 1 and
run 2 both show `finish syscall #2: brk(NULL) = Ok(93824992260096)`), which is the
signature of the known FullTrace/address-ordinalization comparator gap, not guest
nondeterminism. Because `--host-dev` routes through the *unmodified* mount logic,
this is a clean control: the divergence is pre-existing and independent of `/dev`
scoping.

Default `--verify` (the lossy `Stripped` comparator) passes in **both** modes.

## 6. Residue / follow-ups

1. **No PR.** Egress blocked all session. Branch `feat/scope-guest-dev-minimal-set`
   in `worktrees/devscope/hermit` needs pushing and a PR against
   `rrnewton/hermit:main` once the proxy recovers, then an exact-head validate
   receipt.
2. **`--image` empty `/dev`** — the sibling defect (a) from the OCI audit — is
   untouched and still open. `minimal_dev_mounts()` is now the obvious mechanism
   to fix it (mount the staged tree at `<rootfs>/dev` with `touch_target()`), but
   it needs an image to test against, i.e. egress or a local podman store.
3. **Not exercised here:** the DBI and SaBRe backends (feature-gated off in this
   build) and the `--image` path. The KVM backend was exercised only to the extent
   of confirming `/dev/kvm` appears in the guest listing; a full KVM determinism
   run was not attempted.
4. **`/dev/console` deliberately omitted** from the default set. It is a host
   resource; the OCI default-device list includes it, Hermit's does not. If a
   guest turns out to need it, add it to `DEFAULT_GUEST_DEVICES` rather than
   reaching for `--host-dev`.
