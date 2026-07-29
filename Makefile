.DEFAULT_GOAL := build

PKG_CONFIG ?= pkg-config
PKG_CONFIG_MODULES := libunwind-ptrace liblzma
SUBMODULE_PROXY ?= $(shell command -v with-proxy 2>/dev/null)
SUBMODULE_GIT = $(SUBMODULE_PROXY) git

.PHONY: build build-full build-hermit check-deps check-portability clean \
	checkout-all checkout-e9patch checkout-optional-submodules checkout-sabre \
	demo1 demo2 demo3 demo4 demo5 demo6 demo7 demos distclean doctor \
	doctor-core doctor-full doctor-qemu help init-hermit install-deps \
	install-deps-core install-deps-full install-deps-qemu

build: init-hermit
	@$(MAKE) --no-print-directory doctor-core
	@$(MAKE) --no-print-directory build-hermit

# Build the default Hermit feature set, including the DBI backend. This is
# intentionally separate from the lightweight ptrace/default demo build.
build-full: init-hermit
	@$(MAKE) --no-print-directory doctor-full
	cd hermit && cargo build --release -p hermit --bin hermit

# Wipe stale QEMU/Linux demo results (anchors, run history, snapshots) so the
# next demo run starts fresh. distclean also removes the kernel download and
# built initramfs. Both delegate to demos/clean.sh (honors QEMU_ASSETS).
clean:
	@demos/clean.sh

distclean:
	@demos/clean.sh --distclean

# Keep the repository-root entry points thin: demos/Makefile owns the artifact
# graph, including the phase-5 snapshot consumed by demos 6 and 7.
demo1 demo2 demo3 demo4 demo5 demo6 demo7:
	@$(MAKE) -C demos --no-print-directory $@

demos:
	@$(MAKE) -C demos --no-print-directory all

init-hermit:
	@set -eu; \
	if [ ! -e hermit/.git ]; then \
		echo "Hermit submodule is not initialized; checking it out..."; \
		git submodule update --init hermit; \
	fi

checkout-e9patch:
	@scripts/checkout-optional-submodules.rs e9patch

checkout-sabre:
	@scripts/checkout-optional-submodules.rs sabre

checkout-optional-submodules:
	@scripts/checkout-optional-submodules.rs all

checkout-all:
	@$(SUBMODULE_GIT) submodule update --init --recursive
	@$(MAKE) -C hermit --no-print-directory checkout-all
	@$(MAKE) -C reverie --no-print-directory checkout-all

build-hermit: init-hermit
	@if [ ! -f hermit/Cargo.toml ]; then \
		echo "ERROR: Hermit submodule checkout did not produce hermit/Cargo.toml." >&2; \
		exit 1; \
	fi
	@command -v cargo >/dev/null 2>&1 || { \
		echo "ERROR: cargo is required to build Hermit." >&2; \
		exit 1; \
	}
	cd hermit && cargo build --release -p hermit --bin hermit --no-default-features

check-deps:
	@set -eu; \
	pkg_config="$(PKG_CONFIG)"; \
	if ! command -v "$$pkg_config" >/dev/null 2>&1 \
		&& [ "$$pkg_config" = "pkg-config" ] \
		&& command -v pkgconf >/dev/null 2>&1; then \
		pkg_config="pkgconf"; \
	fi; \
	if ! command -v "$$pkg_config" >/dev/null 2>&1; then \
		echo "WARNING: pkg-config (pkgconf on CentOS/RHEL) is required." >&2; \
		echo "Run: make install-deps" >&2; \
		exit 1; \
	fi; \
	missing=""; \
	for module in $(PKG_CONFIG_MODULES); do \
		if ! "$$pkg_config" --exists "$$module"; then \
			missing="$$missing $$module"; \
		fi; \
	done; \
	if [ -n "$$missing" ]; then \
		echo "WARNING: missing required build dependencies:$$missing" >&2; \
		echo "libunwind-ptrace is provided by libunwind-dev (Debian/Ubuntu)" >&2; \
		echo "or libunwind-devel (CentOS/RHEL/Fedora)." >&2; \
		echo "liblzma is provided by liblzma-dev (Debian/Ubuntu)" >&2; \
		echo "or xz-devel (CentOS/RHEL/Fedora)." >&2; \
		echo "Run: make install-deps" >&2; \
		exit 1; \
	fi; \
	echo "Dependency check passed: $(PKG_CONFIG_MODULES)"

check-portability:
	@scripts/check-portable-paths.sh

doctor:
	@scripts/doctor.sh all

doctor-core:
	@scripts/doctor.sh core

doctor-full:
	@scripts/doctor.sh full

doctor-qemu:
	@scripts/doctor.sh qemu

# Backwards-compatible alias: the historical install-deps target is the
# lightweight core ptrace profile.
install-deps: install-deps-core

install-deps-core:
	@scripts/install-deps.sh core
	@$(MAKE) --no-print-directory doctor-core

install-deps-full:
	@scripts/install-deps.sh full
	@$(MAKE) --no-print-directory doctor-full

install-deps-qemu:
	@scripts/install-deps.sh qemu
	@$(MAKE) --no-print-directory doctor-qemu

help:
	@echo "make install-deps[-core]  Install the lightweight ptrace profile"
	@echo "make install-deps-full    Install core + DBI/CMake dependencies"
	@echo "make install-deps-qemu    Install core + QEMU demo dependencies"
	@echo "make doctor               Check every profile without modifying the host"
	@echo "make doctor-{core,full,qemu}  Check one dependency profile"
	@echo "make check-deps           Verify required native pkg-config modules"
	@echo "make check-portability  Reject owner-specific paths in build/run files"
	@echo "make checkout-all       Check out every standard and optional submodule"
	@echo "make checkout-e9patch   Check out the optional pinned e9patch source"
	@echo "make checkout-sabre     Check out the optional pinned SaBRe source"
	@echo "make checkout-optional-submodules  Check out both optional sources"
	@echo "make / make build         Build lightweight ptrace Hermit"
	@echo "make build-full           Build default features including DBI"
	@echo "make demo1 .. demo7       Run one dependency-aware demo"
	@echo "make demos                Run every checked-in demo in order"
	@echo "make clean         Wipe stale QEMU/Linux demo results (fresh start)"
	@echo "make distclean     Also remove the demo kernel download + initramfs"
