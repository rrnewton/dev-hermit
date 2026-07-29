# Hermit Development Workspace

This repository is the multi-repository development harness for the maintained
Hermit fork. It pins Hermit and Reverie, stores durable research and
experiments, and provides isolated paired worktrees for concurrent tasks.

Product development happens in:

- <https://github.com/rrnewton/hermit>
- <https://github.com/rrnewton/reverie>

The historical upstream repositories remain useful references, but day-to-day
Hermit changes flow through `rrnewton/hermit:main`.

## Clone the workspace

```bash
git clone --recurse-submodules https://github.com/rrnewton/dev-hermit.git
cd dev-hermit
git submodule update --init --recursive
```

Read `AGENTS.md` and `WORKTREES.md` before creating a feature worktree. Do not
develop in the primary `hermit/` or `reverie/` checkout.

## Workspace dependency profiles

The parent Makefile separates the lightweight ptrace build from optional
full-backend and QEMU demo dependencies. Each installer may invoke `sudo` and
is followed by the corresponding non-mutating doctor:

```bash
make install-deps-core   # compiler, native libraries, Rust, Python/GDB
make install-deps-full   # core + CMake/Perl/Ninja for the default DBI feature
make install-deps-qemu   # core + QEMU, qemu-img, and static BusyBox
```

On CentOS/RHEL, enable EPEL for the static `busybox` package. The distro QEMU
binary may be `/usr/libexec/qemu-kvm` and BusyBox may be
`/usr/sbin/busybox`; in that case export those paths as `QEMU_BIN` and
`BUSYBOX` for `doctor-qemu` and the QEMU demos.

`make install-deps` remains an alias for `install-deps-core`. Rust is managed
by rustup rather than distro packages. If rustup is absent, install it from
<https://rustup.rs>, reload the shell, and rerun the installer; it installs the
channel selected by `hermit/rust-toolchain.toml` with rustfmt and Clippy.

Run `make doctor` for one aggregated, zero-build report covering all profiles,
or select `doctor-core`, `doctor-full`, or `doctor-qemu`. The doctor verifies
the selected Rust nightly and components, compilers/native libraries, CMake and
Perl, Python/GDB, QEMU/static BusyBox, user/PID namespaces, parent-child ptrace,
seccomp, and PMU availability. PMU and Ninja are warnings because compilation
does not require them; missing required tools or runtime capabilities fail the
selected profile.

Build the lightweight ptrace binary or the full default feature set with:

```bash
make                 # release Hermit without optional DBI dependencies
make build-full      # release Hermit with default features, including DBI
```

## Build Hermit directly

For a standalone product checkout:

```bash
git clone https://github.com/rrnewton/hermit.git
cd hermit
```

Hermit requires x86-64 Linux and uses the nightly toolchain selected by
`rust-toolchain.toml`.

Debian/Ubuntu dependencies:

```bash
sudo apt-get update
sudo apt-get install -y build-essential git libunwind-dev liblzma-dev
```

Fedora/CentOS dependencies:

```bash
sudo dnf install -y gcc gcc-c++ git libunwind-devel xz-devel
```

Build and run:

```bash
cargo build --workspace
./target/debug/hermit run -- /bin/echo hello
```

Some integration fixtures require Go. Precise scheduling, CPUID tests, and
namespace-backed integration tests also depend on host hardware and runtime
policy; see [the container deployment guide](ai_docs/container-deployment.md).

## Validate changes

Start with the narrowest relevant test, then use the repository gates that the
host can actually support:

```bash
cargo test -p detcore
cargo test -p hermit
cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
```

`cargo test --workspace` does not represent the complete historical Buck test
matrix. Report PMU, CPUID, namespace, ignored, quarantined, and unlanded cases
separately. `validate.sh` is the broader repository gate, but hardware-specific
steps require a suitable self-hosted environment.

## Contribution flow

Hermit product changes use:

```text
feature branch -> pull request -> rrnewton/hermit:main
```

Use `origin` for the maintained fork and `upstream` for
`facebookexperimental/hermit`. Do not push feature work directly to `main`.
Keep commits scoped, include exact validation evidence, and preserve explicit
human-review holds even when CI is green.

For GitHub CLI access in this environment:

In Meta environments, use appropriate proxies for accessing the web.

```bash
with-proxy gh pr list -R rrnewton/hermit
```

## Documentation map

- [Architecture overview](ai_docs/architecture-overview.md)
- [Container deployment](ai_docs/container-deployment.md)
- [QEMU integration status](ai_docs/qemu-integration-status.md)
- [Schedule search guide](ai_docs/schedule-search-guide.md)
- [PR status snapshot](ai_docs/pr-status.md)
- [Known limitations and future work](ai_docs/known-limitations.md)
- [Hermit v2 roadmap](ai_docs/hermit-v2-roadmap.md)
- [QEMU and virtme-ng host setup](ai_docs/qemu_vng_setup.md)
- [SaBRe assessment](ai_docs/sabre_backend_assessment.md)
- [KVM backend design](ai_docs/kvm_backend_design.md)
