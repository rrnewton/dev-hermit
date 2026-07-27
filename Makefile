.DEFAULT_GOAL := build

PKG_CONFIG ?= pkg-config
PKG_CONFIG_MODULES := libunwind-ptrace liblzma

.PHONY: build check-deps install-deps help

build: check-deps
	@if [ ! -f hermit/Cargo.toml ]; then \
		echo "ERROR: hermit submodule is not populated." >&2; \
		echo "Run: git submodule update --init hermit" >&2; \
		exit 1; \
	fi
	@command -v cargo >/dev/null 2>&1 || { \
		echo "ERROR: cargo is required to build Hermit." >&2; \
		exit 1; \
	}
	cd hermit && cargo build --release

check-deps:
	@set -eu; \
	if ! command -v "$(PKG_CONFIG)" >/dev/null 2>&1; then \
		echo "ERROR: pkg-config (pkgconf on CentOS/RHEL) is required." >&2; \
		echo "Run: make install-deps" >&2; \
		exit 1; \
	fi; \
	missing=""; \
	for module in $(PKG_CONFIG_MODULES); do \
		if ! "$(PKG_CONFIG)" --exists "$$module"; then \
			missing="$$missing $$module"; \
		fi; \
	done; \
	if [ -n "$$missing" ]; then \
		echo "ERROR: missing required pkg-config modules:$$missing" >&2; \
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
			$$sudo_cmd apt-get update; \
			$$sudo_cmd apt-get install -y \
				build-essential libunwind-dev liblzma-dev pkg-config \
			;; \
		*rhel*|*fedora*|*centos*) \
			package_manager="$$(command -v dnf || command -v yum || true)"; \
			if [ -z "$$package_manager" ]; then \
				echo "ERROR: neither dnf nor yum is available." >&2; \
				exit 1; \
			fi; \
			$$sudo_cmd "$$package_manager" install -y \
				gcc gcc-c++ libunwind-devel xz-devel pkgconf-pkg-config \
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
	@echo "make build         Build hermit in release mode"
