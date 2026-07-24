# Container Deployment Guide

Status: validated capability audit on 2026-07-21. The rootless runtime
experiments used Podman with Ubuntu 24.04. Docker translations are called out
where they have not been independently verified.

Hermit has two different privilege surfaces:

1. Core tracing uses parent-child ptrace, tracee seccomp, and optional PMU
   counters. It normally needs no Linux capability.
2. The current CLI wrapper creates nested namespaces and mounts for isolation
   and repeatability. Container runtime policies can block that setup even when
   the process gains capabilities inside its own user namespace.

Do not diagnose every `EPERM` as missing host `CAP_SYS_ADMIN`. Seccomp policy,
masked paths, namespace mapping, host perf policy, Yama/LSM policy, and actual
capabilities are separate controls.

## Deployment tiers

| Tier | Intended use | Runtime requirements | Determinism/isolation |
| --- | --- | --- | --- |
| Core-only mode (planned) | Trace and determinize without Hermit's namespace wrapper | Custom/relaxed seccomp for ptrace, personality, seccomp/prctl, and optional perf | Reduced PID, `/proc`, `/tmp`, filesystem, and network isolation |
| Current CLI with `--network=host` | Practical rootless container deployment | Tested with `seccomp=unconfined` plus `unmask=ALL`; no capability add | USER/PID/UTS/MOUNT and isolated `/tmp`; host network |
| Current default local network | Full namespace layout | Privileged was the only tested Podman setting that allowed nested `sysfs` | Adds NET namespace, loopback, and fresh `sysfs` |
| Replay/custom mounts | Recording replay and explicit bind/mount behavior | USER/MOUNT setup, system-path unmasking, validated bind sources; replay also uses chroot | Stronger filesystem reconstruction, still dependent on immutable inputs |

The first tier is a design validated through library paths and focused tests,
but the CLI option is not on `main` at this snapshot. Do not put
`--no-namespace` into production automation until its feature PR lands and its
reduced guarantees are documented by `hermit --help`.

## What core tracing requires

The audit ran Detcore time tests and Reverie ptrace/PMU tests with zero
effective, permitted, or ambient process capabilities. `perf_event_open`
succeeded for the current process and another thread with host
`perf_event_paranoid=1`.

Core requirements are:

- x86-64 Linux;
- same-uid parent tracing a child that uses `PTRACE_TRACEME`;
- `fork`/`clone`/`exec`, `ptrace`, `prctl(PR_SET_NO_NEW_PRIVS)`, tracee
  seccomp-BPF, and `personality(ADDR_NO_RANDOMIZE)` allowed by runtime policy;
- Yama/LSM policy that permits the parent-child relationship;
- for precise preemption, a permitted user-only per-thread
  `perf_event_open` RCB event.

`CAP_SYS_PTRACE` is not normally required. `CAP_PERFMON`, or host
`CAP_SYS_ADMIN` on older kernels, is needed only when host perf policy denies
the counter. A capability gained inside a nested user namespace does not
override perf policy in the initial user namespace.

Without usable PMU counters, Hermit can continue with preemption disabled, but
CPU-bound/spinning threads are no longer preempted at deterministic RCB
boundaries. That is a material reduction in scheduling coverage.

## What the namespace wrapper does

The current default container path requests:

- USER namespace with the invoking euid mapped to inner root;
- PID namespace and a mounted `/proc`;
- UTS namespace with deterministic hostname/domainname;
- MOUNT namespace with an isolated, shared bind-mounted `/tmp`;
- by default, NET namespace, loopback setup, and a fresh `sysfs` mount.

The USER namespace supplies inner `CAP_SYS_ADMIN` for UTS and mount operations
and inner `CAP_NET_ADMIN` for network setup. These are not equivalent to host
capabilities. Reverie performs namespace creation through `clone` flags and
then writes uid/gid maps, mounts filesystems, configures loopback, and installs
tracee seccomp. Errors propagate; the current CLI has no automatic reduced
fallback.

`--namespace-only` still creates namespaces; it means "do not intercept
syscalls," not "run without namespaces."

## Validated rootless Podman configuration

Build Hermit in an image or bind a prebuilt tree, then run the image with the
policy controls below. Replace `hermit-dev:latest` with the image that contains
the repository toolchain and dependencies.

```bash
podman run --rm -it \
  --security-opt seccomp=unconfined \
  --security-opt unmask=ALL \
  --network=host \
  -v "$PWD:/work:Z" \
  -w /work \
  hermit-dev:latest \
  ./target/release/hermit run --network=host -- /bin/true
```

The validated rootless matrix was:

- default policy: namespace clone/mapping worked, but hostname was denied;
- `seccomp=unconfined` only: hostname/personality worked, but the masked
  `/proc` policy blocked the nested proc mount;
- `unmask=ALL` only: proc mount worked, but seccomp still blocked hostname;
- both options: full host-network Hermit run passed with PMU preemption and no
  added capability;
- `--cap-add SYS_ADMIN` alone: insufficient, and the OCI profile returned
  `ENOSYS` for `perf_event_open`;
- default local-network mode: nested `sysfs` mount still failed until the
  container was privileged.

For Docker, the expected analogue is `--security-opt seccomp=unconfined` plus
an equivalent system-path unmask such as `systempaths=unconfined`. This exact
combination was not validated in the audit; test it before treating it as a
supported recipe.

## Host preflight

Record these values with every deployment or failure report:

```bash
uname -a
id
grep '^Cap\(Eff\|Prm\|Amb\):' /proc/self/status
cat /proc/sys/kernel/perf_event_paranoid
cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || true
test -r /proc/sys/kernel/unprivileged_userns_clone && \
  cat /proc/sys/kernel/unprivileged_userns_clone || true
```

Then distinguish namespace/mount support from PMU support:

```bash
unshare --user --map-root-user --pid --fork --uts --mount \
  sh -c 'mount -t proc proc /proc && mount --bind /tmp /tmp && umount /tmp'

perf stat -e branches:u -- /bin/true
```

The first probe does not cover Hermit's default network namespace and nested
`sysfs` mount. A passing user/mount probe can therefore coexist with Hermit
mode tests failing `EPERM` at `Mount`.

## Dependencies

Hermit follows the checked-in nightly Rust toolchain. A development image
needs Git, a C toolchain, Cargo/Rustup, libunwind headers, and LZMA headers.

Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y build-essential git libunwind-dev liblzma-dev
```

Fedora/CentOS:

```bash
sudo dnf install -y gcc gcc-c++ git libunwind-devel xz-devel
```

Some lit fixtures also require Go. Keep image versions pinned when the output
is used as deterministic or replay evidence.

## CI and security

Self-hosted runner jobs can execute repository code with the runner's host and
container privileges. The Hermit and Reverie workflows on `main` restrict
self-hosted pull-request jobs to PRs authored by `rrnewton`; GitHub-hosted jobs
remain the lane for other PRs. Reverie additionally retains the
`REVERIE_SELF_HOSTED` variable gate and restricts manual dispatch to
`rrnewton`.

Never attach a rootful or privileged runner to an unreviewed public fork PR.
Use separate runner registrations, state volumes, work directories, and
container names for Hermit and Reverie. Sharing hardware does not justify
sharing `_work` or runner state.

At this snapshot both registered runners were offline. Earlier live runs
proved that a limited namespace probe could pass while all six
`hermit_modes` tests failed on a nested mount with `EPERM`. Report the full
command and passing/failing test counts; do not call the unit-test fallback a
full Hermit integration run.

## Troubleshooting

| Symptom | Likely boundary | Next check |
| --- | --- | --- |
| `EPERM` setting hostname | OCI seccomp | Retry in a controlled environment with the required clone/hostname/personality rules |
| `EPERM` mounting proc | Masked system path or mount policy | Check runtime unmask policy separately from capabilities |
| `EPERM` mounting sysfs | Nested network/sysfs mount policy | Use `--network=host` or a dedicated privileged runner |
| PMU probe returns `EPERM`/`EACCES` | Host perf policy | Check `perf_event_paranoid`, PMU availability, and `CAP_PERFMON` policy |
| PMU probe returns `ENOSYS` | OCI seccomp filter | Relax/customize seccomp; a fix to treat this as unsupported is tracked separately |
| ptrace denied | Yama, LSM, or OCI policy | Confirm same-uid parent/child tracing and `ptrace_scope` |
| Guest sees unstable PIDs or host `/proc` | Reduced/no namespace execution | Use the namespace wrapper or add PID/proc virtualization before claiming parity |
