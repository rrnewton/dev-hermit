.DEFAULT_GOAL := build

PKG_CONFIG ?= pkg-config
PKG_CONFIG_MODULES := libunwind-ptrace liblzma
SUBMODULE_PROXY ?= $(shell command -v with-proxy 2>/dev/null)
SUBMODULE_GIT = $(SUBMODULE_PROXY) git

.PHONY: build build-full build-hermit check-agent-utils-pin check-claude-md-size check-codex-setup check-compat-envelope-tests check-deps check-harness-help check-portability check-primary-freshness check-rust-error-string-proxies clean \
	restore-primary-freshness \
	check-submodules checkout-all checkout-e9patch checkout-fresh checkout-optional-submodules checkout-sabre submodules \
	compat-envelope compat-envelope-full compat-envelope-fullcorpus \
	demo1 demo2 demo3 demo4 demo5 demo6 demo7 demos distclean doctor \
	doctor-core doctor-full doctor-qemu help init-hermit install-deps install-hooks \
	install-deps-core install-deps-full install-deps-qemu lint list-rust-scripts validate

.PHONY: single-submodule-bump

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

init-hermit: check-submodules

checkout-e9patch:
	@scripts/checkout-optional-submodules.rs e9patch

checkout-sabre:
	@scripts/checkout-optional-submodules.rs sabre

checkout-optional-submodules:
	@scripts/checkout-optional-submodules.rs all

# submodules: safe init/update that keeps every primary ATTACHED. Prefer this
# over `checkout-all` / `init-hermit`: the raw `git submodule update --init
# --recursive` below checks each `update = checkout` product out at its pinned
# gitlink in DETACHED HEAD, silently detaching a primary that was on main (or,
# for liteinst2, its feature branch). scripts/submodules.sh reattaches instead
# and never resets a dirty or divergent checkout. Pass ARGS=... to forward flags
# (e.g. `make submodules ARGS=--no-pull`).
submodules:
	@scripts/submodules.sh $(ARGS)

# checkout-all: raw recursive init. WARNING: this DETACHES attached primaries
# (see `make submodules` for the attach-preserving equivalent). Kept for the
# nested product checkout-all recursion below and legacy call sites.
checkout-all:
	@$(SUBMODULE_GIT) submodule update --init --recursive
	@$(MAKE) -C hermit --no-print-directory checkout-all
	@$(MAKE) -C reverie --no-print-directory checkout-all

checkout-fresh: ## Refresh clean primaries and publish one coherent parent snapshot
	@scripts/primary_checkout.py fresh --publish-parent --strict

check-submodules: checkout-all
	@status="$$($(SUBMODULE_GIT) submodule status --recursive)"; \
		printf '%s\n' "$$status"; \
		if printf '%s\n' "$$status" | awk '$$2 != "agent-utils"' | grep -Eq '^[-+U]'; then \
			echo 'ERROR: a required submodule is missing or not at its pinned revision.' >&2; \
			exit 1; \
		fi
	@test -f hermit/Cargo.toml || { echo 'ERROR: Hermit submodule is missing.' >&2; exit 1; }
	@test -f reverie/Cargo.toml || { echo 'ERROR: Reverie submodule is missing.' >&2; exit 1; }
	@test -f liteinst2/Cargo.toml || { echo 'ERROR: LiteInst2 submodule is missing.' >&2; exit 1; }

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

check-harness-help: ## Assert every harness entrypoint's -h/--help/--version is a pure safe probe
	@scripts/check-harness-help.py

check-primary-freshness: ## One invariant over every primary (parent included): not bare, on main, not detached, equal to origin, clean. Detect+report only; never resets or fast-forwards.
	@scripts/primary_checkout.py freshness

restore-primary-freshness: ## Repair only the unambiguous drift (an accidental core.bare flip); everything else is reported with the exact command for a human.
	@scripts/primary_checkout.py freshness --restore-safe

check-rust-error-string-proxies: ## Reject Rust control flow that classifies typed errors by display strings
	@scripts/lint-rust-error-string-proxies.py . hermit reverie liteinst2

check-agent-utils-pin: ## Fetch and reject stale/diverged agent-utils state, plus STRANDED local commits (in-flight work on a checked-out branch is reported, not failed)
	@scripts/check-agent-utils-pin.rs

# Keep the root policy compact even though `.codex/config.toml` raises Codex's
# instruction-chain limit. Stock Codex defaults to 32768 bytes and would truncate
# this file; `check-codex-setup` verifies the explicit project override. Policy is
# now SPLIT: executable predicates
# live in AGENTS.md, rationale/examples/glossary in ai_docs/agents-md-policy-rationale.md
# (read on demand). This gate is a REGRESSION guard set just above the trimmed size to
# catch bloat (e.g. re-inlining rationale that belongs in the companion doc). It also
# requires the load-verification TAIL CANARY, so a truncated tail fails loudly. If
# genuinely-required new policy pushes past LIMIT, move background to the companion doc
# first; raise LIMIT deliberately here only for irreducible new predicates, never by
# dropping a rule to fit a number.
check-claude-md-size: ## Guard AGENTS.md against size regression + require the tail canary
	@limit=42000; f=AGENTS.md; \
	test -f $$f || { echo "ERROR: $$f missing" >&2; exit 1; }; \
	size=$$(wc -c < $$f); \
	if [ $$size -gt $$limit ]; then \
	  echo "ERROR: $$f is $$size bytes, over the $$limit-byte regression guard (harness warns at 40000 chars)." >&2; \
	  echo "  Move background to ai_docs/agents-md-policy-rationale.md; raise LIMIT only for irreducible predicates, never drop a rule to fit." >&2; \
	  exit 1; \
	fi; \
	grep -q 'TAIL-CANARY-KESTREL-7731' $$f || { echo "ERROR: $$f missing the load-verification TAIL CANARY (tail may be truncated)." >&2; exit 1; }; \
	echo "AGENTS.md size OK ($$size <= $$limit chars) and tail canary present."

check-codex-setup: ## Verify stock-Codex instruction and skill discovery
	@python3 scripts/check-codex-setup.py

list-rust-scripts: ## Inventory executable Rust and rust-script source files
	@scripts/list-rust-scripts.rs

single-submodule-bump: ## Plan/run one isolated gitlink A→B verification (ARGS='...')
	@scripts/single-submodule-bump.rs $(ARGS)

lint: ## Lint parent-repository scripts, tests, paths, and submodule policy
	@command -v rustfmt >/dev/null 2>&1 || { echo 'ERROR: rustfmt is required.' >&2; exit 1; }
	@command -v shellcheck >/dev/null 2>&1 || { echo 'ERROR: shellcheck is required.' >&2; exit 1; }
	@command -v python3 >/dev/null 2>&1 || { echo 'ERROR: python3 is required.' >&2; exit 1; }
	rustfmt --edition 2021 --check scripts/*.rs
	shellcheck --severity=warning scripts/*.sh .githooks/pre-commit .githooks/pre-push \
	    .orc/plugins/hermit-dev/gh-issue-create \
	    .orc/plugins/hermit-dev/gh-coord-comment \
	    .orc/plugins/hermit-dev/gh-coord-pr-create
	python3 -m py_compile scripts/*.py
	python3 -m unittest discover -s scripts -p 'test_*.py'
	@$(MAKE) --no-print-directory check-rust-error-string-proxies
	@scripts/check-parent-gitmodules.sh
	@scripts/check-agent-utils-pin.rs
	@scripts/primary_checkout.py check
	@$(MAKE) --no-print-directory check-codex-setup
	@$(MAKE) --no-print-directory check-claude-md-size
	@$(MAKE) --no-print-directory check-portability
	@$(MAKE) --no-print-directory check-harness-help
	@$(MAKE) --no-print-directory check-compat-envelope-tests

check-compat-envelope-tests: ## Run the compat-envelope renderer unit tests (fixture-only; no hermit build)
	@compat-envelope/tests/run-all.sh

# compat-envelope: the cross-backend compatibility REGRESSION gate. Builds the
# RELEASE hermit binary with the in-process DBI backend and asserts every
# known-green compat cell stayed green (green-stays-green), refreshing the
# scorecard CSVs as a side effect. This is the portable, always-on lane:
# ptrace (golden denominator) + DBI. SaBRe (needs `make checkout-sabre` + build)
# and KVM/reverie (need a /dev/kvm runner) are honestly recorded as n/a /
# not-runnable when absent — never a false red — and are exercised by
# compat-envelope-full.
#
# The gate runs against the RELEASE binary via HERMIT_BIN: a debug build is
# ~5-10x slower and blows the harness per-test timeout on subprocess-heavy cells
# (e.g. python-io-subprocess-time verify: 90s timeout under debug, passes under
# release), which is also the binary hermit's own CI validates against.
compat-envelope: init-hermit
	cd hermit && cargo build --release -p hermit --bin hermit --features dbi
	HERMIT_BIN="$(CURDIR)/hermit/target/release/hermit" \
		compat-envelope/validate-envelope.sh --lane portable \
		--backends ptrace,dbi --no-reverie

# compat-envelope-full: the privileged superset lane. Adds the SaBRe backend and
# the reverie B1.5 ptrace-vs-KVM boundary (needs /dev/kvm + the counter
# launchers). Intended for the privileged self-hosted CI runner, not portable CI.
compat-envelope-full: init-hermit
	cd hermit && cargo build --release -p hermit --bin hermit --features third-party-backends
	cd reverie && cargo build --release -p reverie-examples \
		--bin counter1 --bin counter2 \
		--bin reverie-kvm-counter1 --bin reverie-kvm-counter2
	HERMIT_BIN="$(CURDIR)/hermit/target/release/hermit" \
		compat-envelope/validate-envelope.sh --lane portable --backends ptrace,dbi,sabre

# compat-envelope-fullcorpus: the LOCAL definition-of-done gate. The
# portable/privileged split (compat-envelope[-full]) exists for GitHub CI, where
# a runner may lack /dev/kvm or the third-party-backend feature build. On a
# fully-provisioned local box (this machine has /dev/kvm) the gate should instead
# measure the UNION — the FULL 235-cell verify corpus (214 compiled C + 21
# shell/interpreter cells) across EVERY backend the local binary can run — not
# the ~28-cell ci=true portable subset. Backends are auto-detected; a missing one
# is recorded n/a, never a false red. Ratchet-asserted (green-stays-green) against
# per-backend det floors measured at 82a8e853.
compat-envelope-fullcorpus: init-hermit
	cd hermit && cargo build --release -p hermit --bin hermit --features third-party-backends
	HERMIT_BIN="$(CURDIR)/hermit/target/release/hermit" \
		compat-envelope/collect-fullcorpus.sh

# validate: the outer-repo definition-of-done gate. Locally this is the FULL
# 235-cell cross-backend envelope (both lanes' union); the portable/privileged
# split is CI-only. Extend as other workspace-level checks are added.
validate: compat-envelope-fullcorpus

doctor:
	@scripts/doctor.sh all

doctor-core:
	@scripts/doctor.sh core

doctor-full:
	@scripts/doctor.sh full

doctor-qemu:
	@scripts/doctor.sh qemu

# Install this clone's git pre-commit hooks (core.hooksPath -> .githooks). Wired
# as a prerequisite of every install-deps profile so a fresh clone/worktree gets
# the blocking pin-drift + hygiene pre-commit hook WITHOUT a separate manual step.
# core.hooksPath is per-repo local config (not tracked), so it must be set once
# per checkout; this is that step.
install-hooks:
	@scripts/setup-hooks.sh

# Backwards-compatible alias: the historical install-deps target is the
# lightweight core ptrace profile.
install-deps: install-deps-core

install-deps-core: install-hooks
	@scripts/install-deps.sh core
	@$(MAKE) --no-print-directory doctor-core

install-deps-full: install-hooks
	@scripts/install-deps.sh full
	@$(MAKE) --no-print-directory doctor-full

install-deps-qemu: install-hooks
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
	@echo "make check-agent-utils-pin  Require parent pin and checkout at agent-utils origin/main"
	@echo "make list-rust-scripts Inventory executable Rust and rust-script source files"
	@echo "make single-submodule-bump  Plan/run one isolated gitlink A→B verification"
	@echo "make lint               Lint parent scripts, tests, paths, and submodule policy"
	@echo "make compat-envelope    Cross-backend compat regression gate (ptrace+DBI, portable CI lane)"
	@echo "make compat-envelope-full  Privileged superset (adds SaBRe + KVM/reverie, privileged CI lane)"
	@echo "make compat-envelope-fullcorpus  LOCAL full 235-cell union across all runnable backends"
	@echo "make validate           Outer-repo definition-of-done gate (local = full-corpus envelope)"
	@echo "make submodules         Safe init/update; keeps primaries ATTACHED (no detach)"
	@echo "make checkout-all       Recursive submodule init (WARNING: detaches primaries)"
	@echo "make checkout-fresh     Refresh clean primaries and publish parent gitlinks"
	@echo "make checkout-e9patch   Check out the optional pinned e9patch source"
	@echo "make checkout-sabre     Check out the optional pinned SaBRe source"
	@echo "make checkout-optional-submodules  Check out both optional sources"
	@echo "make / make build         Build lightweight ptrace Hermit"
	@echo "make build-full           Build default features including DBI"
	@echo "make demo1 .. demo7       Run one dependency-aware demo"
	@echo "make demos                Run every checked-in demo in order"
	@echo "make clean         Wipe stale QEMU/Linux demo results (fresh start)"
	@echo "make distclean     Also remove the demo kernel download + initramfs"
