SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

# Single-runner happy path for a self-hosted GitHub Actions runner. Reads its
# configuration from .env (copy .env.example to .env first — see README.md).
# This intentionally does NOT implement the multi-runner "fleet" tooling of
# the original multi-runner setup this setup was distilled from; see README.md
# "Scaling beyond one runner" if you outgrow one runner later.

CONFIG_DIR ?= .
CONFIG_ENV := $(CONFIG_DIR)/.env
ifeq (,$(wildcard $(CONFIG_ENV)))
$(error $(CONFIG_ENV) is missing — copy .env.example there and fill in OWNER/REPO_NAME first)
endif
include $(CONFIG_ENV)
export

CONTAINER_ENGINE ?= $(shell command -v podman >/dev/null 2>&1 && echo podman || echo docker)
RUNNER_CPUS ?= 4
RUNNER_MEMORY ?= 16g
IMAGE_TAG ?= hermit-ci-runner:$(RUNNER_VERSION)
BUILD_EXTRA_ARGS ?= --dns=1.1.1.1
CONTAINER_NAME ?= hermit-ci-runner
ENV_FILE := $(CONFIG_DIR)/.runner-registration.env
STATE_DIR := $(CONFIG_DIR)/state
IMAGE_STAMP := .runner-image-$(RUNNER_VERSION).stamp
# Never inherit a stray shell START_ARGS; one-job mode is opt-in per-invocation.
START_ARGS :=

export CONTAINER_ENGINE RUNNER_CPUS RUNNER_MEMORY IMAGE_TAG CONTAINER_NAME ENV_FILE STATE_DIR
export RUNNER_CONFIG_DIR := $(abspath $(CONFIG_DIR))

.PHONY: help build init start verify stop drain status

help:
	@printf '%s\n' \
	  'Targets (single-runner happy path):' \
	  '  make build     Build the runner image when Containerfile changed' \
	  '  make init      Mint a registration token and configure the runner' \
	  '  make start     Build/init as needed, then run and attach to logs' \
	  '  make verify    Audit a running container'"'"'s cgroup limits without changing it' \
	  '  make drain     Ask a running container to finish its current job and exit' \
	  '  make stop      Stop and deregister this runner' \
	  '  make status    Show whether the container is running' \
	  '' \
	  'Useful overrides:' \
	  '  make start RUNNER_CPUS=8 RUNNER_MEMORY=32g' \
	  '  make start START_ARGS=--detach' \
	  '  make start START_ARGS=--once   # exit after one job, no restart policy' \
	  '' \
	  'Config lives in CONFIG_DIR/.env — see .env.example and README.md.'

build: $(IMAGE_STAMP)

$(IMAGE_STAMP): Containerfile
	"$(CONTAINER_ENGINE)" build \
	  $(BUILD_EXTRA_ARGS) \
	  --build-arg RUNNER_VERSION="$(RUNNER_VERSION)" \
	  --build-arg RUNNER_ARCH="$(RUNNER_ARCH)" \
	  -t "$(IMAGE_TAG)" -f Containerfile .
	@touch "$@"

init: build
	@if [ -f "$(ENV_FILE)" ] && [ -f "$(STATE_DIR)/.runner" ]; then \
	  echo "Runner is already configured ($(ENV_FILE), $(STATE_DIR)/.runner exist)."; \
	  echo "Run 'make stop' first if you need to re-register."; \
	else \
	  ./init-runner.sh; \
	fi

start: init
	@./start-runner.sh $(START_ARGS)

verify:
	@./start-runner.sh --verify-only

drain:
	@./drain-runner.sh

stop:
	@./stop-runner.sh

status:
	@if [ -f "$(ENV_FILE)" ]; then \
	  "$(CONTAINER_ENGINE)" ps --filter "name=$(CONTAINER_NAME)" --format 'table {{.Names}}\t{{.Status}}'; \
	else \
	  echo "Not initialized — run 'make init' first."; \
	fi
