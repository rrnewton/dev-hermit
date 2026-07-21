# Hermit self-hosted GitHub Actions runner

A single self-hosted GitHub Actions runner for Hermit, packaged as a container
image plus lifecycle scripts. It runs on this host to expose x86 CPUID, ptrace,
and Performance Monitoring Unit (PMU) behavior unavailable on GitHub-hosted
runners.

**Before you start:** read "hermit needs relaxed container isolation" near
the bottom of this file. It is not optional for running hermit's own test
suite, and it is the one part of this setup you cannot skip.

## Why run a self-hosted runner at all

GitHub-hosted runners are free (with usage limits) and require no setup, but
they are also generic cloud VMs: no local caches, capped CPU/RAM, and a
per-minute budget that can run out. A self-hosted runner is just your own
machine (or VM) running the same `actions/runner` agent GitHub uses, except
it can be as big, as cached, and as long-lived as you want, and it costs
nothing beyond the electricity/hosting you already pay for. The tradeoff is
that you now own its security and its uptime — see "Two things to actually
customize" below for what that means concretely for hermit.

## How the pieces fit together

```
 Containerfile  ──build──>  runner image  ──run──>  container (the runner)
                                                        │
                                                        ├─ registers with GitHub using a
                                                        │  short-lived token (init-runner.sh)
                                                        ├─ long-polls GitHub for a matching
                                                        │  queued job, runs it, reports back
                                                        └─ two independent "keep it alive"
                                                           mechanisms (see below)
```

1. **Image.** `Containerfile` builds an Ubuntu 24.04 image containing the
   official GitHub Actions runner binary and a Rust toolchain. `make build`
   (or `make start`, which calls it automatically) builds it whenever the
   `Containerfile` has changed since the last build.

2. **Registration with a short-lived token.** GitHub does not hand out
   long-lived runner credentials directly. Instead you mint a **registration
   token** that expires in about an hour, and the runner's `config.sh`
   exchanges that token for a durable local credential file
   (`state/.runner`, `state/.credentials*`) the *first* time it configures.
   `init-runner.sh` does this minting + configuring step. See "Minting a
   registration token" below for the exact command and what permissions it
   needs.

3. **Running as a container.** `start-runner.sh` runs the image as a
   container with the freshly-configured `state/` directory bind-mounted in,
   and with explicit CPU/RAM limits enforced at the cgroup level (see
   "Resource limits" below — this matters more than it sounds like it should,
   because a runaway CI job on your own machine can otherwise take the whole
   machine down). The runner then sits in its normal long-poll loop, waiting
   for GitHub to assign it a job whose `runs-on:` labels match.

4. **Two independent "keep it alive" mechanisms**, because a self-hosted
   runner needs to survive both process crashes and machine reboots:
   - **Container restart policy `--restart=always`** — the container engine
     restarts the runner process if it exits unexpectedly (crash, OOM-kill,
     etc). This is the process-death safety net.
   - **A per-container `systemd --user` unit** (Podman only —
     `podman generate systemd`) — enabled so the *container itself* comes
     back up after a full machine reboot, even before any interactive login
     (`loginctl enable-linger` makes that work without a login session).
     This is the separate reboot safety net; `--restart=always` alone does
     nothing if the container engine's own daemon/service was not itself
     restarted after boot.

   `ensure-runner-autostart.sh` sets up and verifies both; `start-runner.sh`
   calls it after every successful start. Docker has no equivalent to
   `podman generate systemd`, so on Docker only the restart policy applies —
   you'd need your own reboot hook (e.g. a `crontab -e` `@reboot` line) for
   full reboot persistence there.

5. **Drain vs. stop.** These are deliberately different operations:
   - `make drain` sends the runner's listener process `SIGINT` (the same
     signal Ctrl-C sends a foreground run). The official runner treats this
     as "finish the current job if there is one, then leave the polling loop
     and exit" — graceful, but it leaves the GitHub-side registration and the
     `state/` directory alone, so `make start` brings the *same* runner back.
   - `make stop` does a harder shutdown: stops the container, then mints a
     **removal token** and runs `config.sh remove` to actually deregister the
     runner from GitHub. Use this when you are done with the runner, want to
     free up its name for reuse, or need a clean re-registration.

## Step by step: stand up one runner for `rrnewton/hermit`

```sh
cd ci-runner/
cp .env.example .env
```

Edit `.env`:

```
OWNER=rrnewton
REPO_NAME=hermit
```

(leave the rest at their defaults for a first run — see `.env.example` for
what each setting does).

Make sure you have a `gh` CLI session logged in with **admin rights** on
`rrnewton/hermit` (your own fork, so this is just your normal login):

```sh
gh auth status
```

Then:

```sh
make build     # builds the image (first time only, or after Containerfile changes)
make init      # mints a registration token, registers the runner with GitHub
make start     # runs the container, follows its logs (Ctrl-C stops the log tail only)
```

`make start` calls `make init` and `make build` automatically if needed, so
day-to-day you can usually just run `make start`. Verify it registered:

```sh
gh api repos/rrnewton/hermit/actions/runners
```

You should see one runner listed, `status: online`, with the labels you set
in `.env` (default: `self-hosted,linux,x64,hermit`). Point a workflow at it
with:

```yaml
jobs:
  test:
    runs-on: [self-hosted, linux, x64, hermit]
```

When you're done for now:

```sh
make drain     # graceful: finishes any active job, keeps the GitHub registration
# or
make stop      # harder: stops the container AND deregisters it from GitHub
```

## Pointing this at `facebookexperimental/hermit` instead of your fork

Set `OWNER=facebookexperimental` in `.env` and re-run `make init`. The
catch: **minting a registration token requires admin rights on that exact
repo** (see below), so this only works if you (or whoever holds the `gh`
login you're using) has been granted admin/maintainer access to
`facebookexperimental/hermit` — an outside contributor normally does not have
that, even with a merged PR history. For upstream, most contributors will
instead run this setup against their own fork (as above) and only push
runner-bound workflow changes upstream once they've been reviewed and the
upstream maintainers set up their own runner (or grant you the access to
register one directly on their repo).

## Minting a registration token

The registration-token step is exactly:

```sh
gh api -X POST repos/<OWNER>/<REPO>/actions/runners/registration-token
```

This returns JSON with a `token` (feed it to `config.sh --token`) and an
`expires_at` about one hour out. `init-runner.sh` runs this exact call, reads
`.token` out of the JSON with `jq`, and fails loudly if it comes back empty —
usually because the logged-in `gh` account lacks admin on that repo (repo
**write** access is not enough; the runners API needs **admin**). If you run
multiple GitHub accounts on one machine and need to pin which one this uses,
that is the one place in this setup to substitute your own wrapper around
`gh` — everything else here calls `gh` directly.

The removal-token call `stop-runner.sh` uses is the same shape, against
`.../actions/runners/remove-token`.

## Resource limits (the cgroup-limit concept)

By default the container gets 4 CPUs / 16 GB RAM — the same shape as a
standard GitHub-hosted Linux runner — enforced as real cgroup-v2 hard caps,
not just advisory Docker/Podman flags:

- `cpu.max` is pinned to exactly the requested CPU count.
- `memory.max` is the RAM hard cap.
- `memory.swap.max=0` — a runaway process is OOM-killed at the RAM cap
  instead of being pushed into host swap (which would just make the whole
  machine crawl instead of failing the job cleanly).
- `memory.high=max` — there is no *additional* soft-cap throttle hiding below
  the advertised hard cap.

`verify_runner_limits.py` audits all four properties from *inside* the
container, both before the runner process starts and again whenever you run
`make verify`. If the audit fails, the launcher refuses to let the container
start polling GitHub for jobs — a container that never got real limits is
worse than no container, because it can silently degrade or crash the host
under load instead of failing one job. Override the defaults with:

```sh
make start RUNNER_CPUS=8 RUNNER_MEMORY=32g
```

## hermit needs relaxed container isolation

This is the one hermit-specific thing that is not optional. hermit works by
intercepting the guest program's syscalls via **ptrace**, and then installing
its **own seccomp-bpf filter** on the traced process (see hermit's README,
"How it works" — it's built on Meta's [Reverie](https://github.com/facebookexperimental/reverie)
ptrace library). A container engine's *default* seccomp profile blocks the
`ptrace` syscall outright, and the default capability set does not include
`CAP_SYS_PTRACE` — so hermit's own test suite (and anything that runs `hermit
run <prog>`) will fail inside this runner container unless you relax that.

Set the engine-specific value in `.env` as `CONTAINER_EXTRA_ARGS` (passed
straight through to `podman run`/`docker run`):

```sh
# Podman: allow ptrace/seccomp plus the nested mount namespaces used by tests.
CONTAINER_EXTRA_ARGS="--cap-add=SYS_PTRACE --security-opt seccomp=unconfined --security-opt unmask=ALL"

# Docker equivalent:
CONTAINER_EXTRA_ARGS="--cap-add=SYS_PTRACE --security-opt seccomp=unconfined --security-opt systempaths=unconfined"
```

One further wrinkle: hermit's deterministic thread scheduler uses the CPU's
Performance Monitoring Unit (`perf_event_open`, counting retired conditional
branches) to bound how long a thread runs before a scheduled context switch.
`perf_event_open` needs either `CAP_SYS_ADMIN` (add
`--cap-add=SYS_ADMIN` to `CONTAINER_EXTRA_ARGS`) or a relaxed
`/proc/sys/kernel/perf_event_paranoid` **on the host** — that sysctl cannot
be set from inside a container, so if jobs still fail with a permission error
from `perf_event_open` after adding the capability flags above, check that
setting on the bare host, not in the container.

**Kernel/architecture constraints:** hermit is x86_64-focused — its own
README states aarch64 support is a work in progress as of this writing. Do
not expect this to work on an ARM host or runner. It also assumes a
reasonably modern Linux kernel (perf-counter and ptrace behavior it depends
on has moved around across kernel versions historically); if in doubt, build
the runner host on the same kernel family CI is meant to represent.

## Build dependencies

hermit's own `.github/workflows/ci.yml` (checked as of 2026-07-21) does
exactly two things before `cargo build`: `sudo apt-get install -y
libunwind-dev`, then `cargo build`. It pins a **nightly** Rust toolchain via
its checked-in `rust-toolchain.toml`, so `rustup` fetches and uses nightly
automatically the first time you build inside a hermit checkout — the
`Containerfile`'s hermit-specific section preinstalls nightly anyway, purely
so that first build doesn't pay a download. If hermit's test suite (as
opposed to just `cargo build`) turns out to need more system libraries,
check `CONTRIBUTING.md` and every workflow file in `.github/workflows/` at
the exact commit you're building against, and add packages to that same
`apt-get install` list in the `Containerfile` rather than adding a second
`RUN` layer.

## Two things to actually customize

1. **The ptrace/seccomp relaxation above.** Get this wrong and every hermit
   job will fail at the exact same syscall-interception step; get it right
   once in `.env` and it's done.
2. **`.env`'s `OWNER`/`REPO_NAME`/`RUNNER_LABELS`.** These decide which repo
   this runner registers against and which workflows can target it. If you
   fork `hermit` under a different GitHub username, or rename the runner's
   labels to match your own workflow files, this is the one file to edit —
   nothing else in the setup needs to change.

## What's deliberately NOT in this setup

This setup is intentionally the smallest useful slice. Left out on
purpose, for you to add if/when you actually need it:

- **A GitHub-account wrapper.** The original this was distilled from routed
  every `gh` call through a project-specific account-safe wrapper (because
  that project runs bot + human GitHub accounts side by side on one
  machine). This setup calls `gh` directly everywhere; if you ever need
  to pin a specific account, that's a one-line substitution at each `gh`
  call site, not a structural change.
- **Multi-runner "fleet" orchestration** (running N runners on one host,
  reconciling them against a desired count, tmux/terminal-multiplexer views
  per runner, an automatic health-check-and-rotate supervisor). All of that
  is a straightforward generalization of this setup — parameterize the
  container/state-dir names by a numeric suffix, and the same `init` /
  `start` / `drain` / `stop` scripts extend cleanly to slot N. Reach for it
  only once one runner is a real bottleneck; it's meaningfully more moving
  parts (registration-state auditing, view reconciliation, a judged
  health-check loop) than most single-repo setups need.
- **A cloud burst pool / autoscaling.** Likewise a real extension (spin up
  ephemeral cloud runners when the local queue backs up) but out of scope
  for "one runner on one machine."

## What runs as root

The runner process inside the container runs as root
(`RUNNER_ALLOW_RUNASROOT=1`). This keeps the bind-mounted `state/` directory
simple across Podman/Docker on a single workstation. The container is only
started on demand, stopped by `make stop`, and (per the caveat above) may
also be running with relaxed seccomp/capabilities for hermit's sake — treat
this runner host as at least as trusted as your own dev machine, not as a
locked-down shared CI fleet.

## Files in this setup

| File | Purpose |
| --- | --- |
| `Containerfile` | Ubuntu 24.04 + GitHub Actions runner + Rust stable/nightly + hermit's one extra apt package |
| `Makefile` | `build` / `init` / `start` / `verify` / `drain` / `stop` / `status` targets, single-runner |
| `.env.example` | Copy to `.env`; all per-repo/per-host configuration lives here |
| `init-runner.sh` | Mints a registration token, runs `config.sh` inside a throwaway container |
| `start-runner.sh` | Starts the persistent runner container, audits its cgroup limits, sets up the two persistence mechanisms |
| `drain-runner.sh` | Graceful SIGINT shutdown; keeps the GitHub registration |
| `stop-runner.sh` | Stops the container and deregisters it from GitHub |
| `ensure-runner-autostart.sh` | Verifies/applies the restart policy and the Podman systemd user unit |
| `verify_runner_limits.py` | The cgroup-v2 audit `start-runner.sh`/`make verify` run inside the container |
