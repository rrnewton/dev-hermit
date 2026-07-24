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

```bash
export HTTPS_PROXY=http://fwdproxy:8080
gh pr list -R rrnewton/hermit
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
