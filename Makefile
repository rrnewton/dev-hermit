.DEFAULT_GOAL := build

PKG_CONFIG ?= pkg-config
PKG_CONFIG_MODULES := libunwind-ptrace liblzma

.PHONY: build build-hermit check-deps init-hermit install-deps clean distclean help

build: check-deps build-hermit

# Wipe stale QEMU/Linux demo results (anchors, run history, snapshots) so the
# next demo run starts fresh. distclean also removes the kernel download and
# built initramfs. Both delegate to demos/clean.sh (honors QEMU_ASSETS).
clean:
	@demos/clean.sh

distclean:
	@demos/clean.sh --distclean

init-hermit:
	@set -eu; \
	if [ ! -e hermit/.git ]; then \
		echo "Hermit submodule is not initialized; checking it out..."; \
		git submodule update --init hermit; \
	fi

build-hermit: init-hermit
	@if [ ! -f hermit/Cargo.toml ]; then \
		echo "ERROR: Hermit submodule checkout did not produce hermit/Cargo.toml." >&2; \
		exit 1; \
	fi
	@command -v cargo >/dev/null 2>&1 || { \
		echo "ERROR: cargo is required to build Hermit." >&2; \
		exit 1; \
	}
	cd hermit && cargo build --release -p hermit --bin hermit

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

install-deps:
	@set -eu; \
	echo "WARNING: install-deps installs system packages and may invoke sudo."; \
	if [ ! -r /etc/os-release ]; then \
		echo "ERROR: cannot detect the operating system (/etc/os-release is missing)." >&2; \
		exit 1; \
	fi; \
	. /etc/os-release; \
	if [ "$$(id -u)" -eq 0 ]; then \
		sudo_cmd=""; \
	elif command -v sudo >/dev/null 2>&1; then \
		sudo_cmd="sudo"; \
	else \
		echo "ERROR: sudo is required when make is not running as root." >&2; \
		exit 1; \
	fi; \
	distro="$${ID:-} $${ID_LIKE:-}"; \
	case "$$distro" in \
		*debian*|*ubuntu*) \
			$$sudo_cmd apt install -y \
				libunwind-dev liblzma-dev pkg-config \
			;; \
		*rhel*|*fedora*|*centos*) \
			if ! command -v dnf >/dev/null 2>&1; then \
				echo "ERROR: dnf is required on CentOS/RHEL/Fedora." >&2; \
				exit 1; \
			fi; \
			$$sudo_cmd dnf install -y \
				libunwind-devel xz-devel pkgconf \
			;; \
		*) \
			echo "ERROR: unsupported distribution: $${PRETTY_NAME:-unknown}." >&2; \
			echo "Install pkg-config, libunwind-ptrace.pc, and liblzma.pc, then run make check-deps." >&2; \
			exit 1 \
			;; \
	esac
	@$(MAKE) --no-print-directory check-deps

help:
	@echo "make install-deps  Install native Hermit build dependencies"
	@echo "make check-deps    Verify required pkg-config modules"
	@echo "make / make build  Initialize and build Hermit in release mode"
	@echo "make clean         Wipe stale QEMU/Linux demo results (fresh start)"
	@echo "make distclean     Also remove the demo kernel download + initramfs"
