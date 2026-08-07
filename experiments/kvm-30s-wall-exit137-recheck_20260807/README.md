# KVM 30s wall + exit 137: it is a LIVELOCK, and the Reverie pin advance removes it

**Task:** `kvm-pipeline-trials-hit-30s-wall-exit-137` · **Agent:** hermit-w2 · **2026-08-07**
**Host:** devbig014 (316 cores) · **Backends:** kvm, ptrace

## Headline

Three claims in the task were tested. Two are refuted as stated, one is confirmed with its
cause inverted, and the decisive variable turns out to be the **Reverie pin**, not the guest.

| # | claim as filed | verdict |
|---|---|---|
| 1 | pipeline trials hit the 30s wall / exit 137 | **CONFIRMED** on 5 builds, **absent** on the 6th |
| 2 | CPU 4.3s vs 30s wall ⇒ **BLOCKED WAIT**, not a livelock | **REFUTED** — cgroup CPU is **99% of wall** ⇒ **LIVELOCK** |
| 3 | `kvm --strict -- /bin/true` also hangs (killed at 120s) | **CONFIRMED**, and it is the **same** failure, not a second one |

**The 4.3s CPU figure is an instrument artefact.** Hermit runs the guest in a namespace and the
guest is not a waited-for child, so `/usr/bin/time` on the wrapper under-attributes descendant
CPU. Measured at the **cgroup** (`CPUUsageNSec` on a transient `systemd-run --user` unit),
the same hang reports **29.76s CPU against 30.12s wall = 99%**. The task's own discriminator
("a livelock shows wall==CPU at the budget") therefore selects **livelock** once the CPU is
measured where it actually accrues. This matches the known KVM startup epoll busy-spin livelock.

## The variable that separates hang from no-hang is the Reverie pin

Six independently-built hermit binaries, same host, same guest, same command:

| build | hermit | reverie pin | `kvm --strict -- /bin/true` |
|---|---|---|---|
| `worktrees/cc` | `9c233ed0bfd6` | `d973a85b` | **exit 137 @ 30.01s** |
| `worktrees/certify` | `16cbdbb11925` | `d973a85b` | **exit 137 @ 30.01s** |
| `worktrees/clone` | `9cf96a4d9c56` | `d973a85b` | **exit 137 @ 30.01s** |
| `worktrees/det1` | `f93109f2e1e9` | `d973a85b` | **exit 137 @ 30.01s** |
| primary `hermit/` | `f89c69766-dirty` | `d973a85b` | **exit 137 @ 120.01s** |
| `worktrees/w2` | `77951bcd` (w13's pin branch) | **`038e9939`** | **rc=0 @ 0.49s** |

**5 of 5 builds carrying reverie `d973a85b` hang. The one build carrying `038e9939` does not** —
and it also completes without `--strict` (0.46s). The exit-137/30s signature in the task report is
reproduced exactly, on the pinned-`d973a85b` builds.

### Attribution strength — strong correlation, NOT isolated

The pin is the only *declared* change on w13's branch, but the hermit commits also differ:
`590fcc9e` (w2's base) is **not** an ancestor of any of the four hanging heads — the branches are
divergent, not older/newer. So a hermit-side change cannot be formally excluded.
**The clean A/B is hermit `590fcc9e` built against `d973a85b` vs against `038e9939`** — one build
apart. It was not run here because that requires checking out in the slot holding
`pin/reverie-038e9939-cargo-only`, which is owned by **hermit-w13** and must not be touched.

## What KVM does to the pipeline guest once the hang is gone

On the non-hanging build the pipeline guest does **not** need a larger bound and does **not**
complete. It fails deterministically in ~3s. **14/14 KVM trials, zero timeouts, zero survivors:**

* 6 sequential, 30s bound: rc=2, wall 2.82–3.55s, cgroup CPU 2.52–2.92s (79–90% of wall)
* 6 concurrent, 30s bound: rc=2, wall 6.76–7.27s, cgroup CPU 5.78–6.03s (80–87%)
* stdout→pipe instead of file: rc=0 wrapper, same error, 2.48s
* `--verify`: aborts at Run1

Every one prints the reported error:

```
diff: -uniq: error reading '-'
: Resource temporarily unavailable
```

ptrace passes the same guest every time (`uniq-ok`; `--verify` compares 4141|4141 messages).

### Root cause: EAGAIN returned on a *blocking* pipe read

Bisected to a minimal guest — process substitution is **not** involved:

| guest | ptrace | kvm |
|---|---|---|
| `printf "a\nb\n" \| cat` | ok | **`cat: -: Resource temporarily unavailable`** |
| `printf "a\na\nb\n" \| uniq -d` | ok | **`uniq: error reading '-'`** |
| `diff -u <(printf "a\n") <(printf "a\n")` | ok | ok |
| `printf "a\n" \| diff -u <(printf "a\n") -` | ok | **panic (below)** |

A static C probe (`probe.c`) reads stdin and reports `fcntl(F_GETFL)` alongside the errno:

```
ptrace  PROBE fl=0x0 O_NONBLOCK=0 bytes=4 eagain=0 err=none
kvm     PROBE fl=0x0 O_NONBLOCK=0 bytes=0 eagain=4 err=none
```

**The fd is blocking by its own flags — `O_NONBLOCK` is clear on both backends — yet under KVM
`read()` returns `EAGAIN`.** This is not a leaked `O_NONBLOCK`; it is the KVM read path declining
to block on a pipe that has no data yet. With stdin redirected from `/dev/null` instead of a pipe,
KVM is correct (`fl=0x8000`, 0 bytes, 0 EAGAIN, clean EOF), so the defect is **pipe-specific**.

### Second, distinct defect found while bisecting

`printf "a\n" | diff -u <(printf "a\n") -` panics KVM:

```
thread 'main' (1) panicked at detcore/src/scheduler.rs:2174:35:
signal::kill to go through: ESRCH
```

`detcore/src/scheduler.rs:2173-2174`:

```rust
let pid = Pid::from_raw(dettid.as_raw()); // TODO(T78538674): virtualize pid/tid:
signal::kill(pid, signal).expect("signal::kill to go through");
```

An **unvirtualized dettid is used as a host pid**; when it names no live host process the `expect`
aborts the run. Same unvirtualized-tid family as the known DBI `dtid` gap.

## Consequence for the self-determinism corpus

The KVM pipeline cell is **ZERO QUALIFYING TRIALS, not a determinism result** — and not a timeout
either. Under `--verify`, KVM emits:

```
:: Run1...
First run errored during --verify, not continuing to a second.
```

There is no second run, so nothing was compared. Recording this cell as a pass **or** a fail would
be a fabrication in either direction; it is a **refusal**. Denominator: of 1 KVM pipeline cell in
scope, **0 measured**.

## Answer to the task's verify condition

> *"Either the pipeline completes under a justified bound and produces a real self-determinism
> result, or the blocking cause is named."*

**No bound completes it, and the cause is named.** A larger bound is pointless: on the pinned-
`038e9939` build the guest fails in ~3s regardless of bound, and on the pinned-`d973a85b` builds the
livelock burns 99% CPU indefinitely (it is not waiting for anything, so it will not finish either).

## Scope and limits

* **One host** (devbig014), **one guest family** (bash pipelines), **two backends**.
* `--verify` measured once per backend, not repeated — speaks to the abort path, not to flake rate.
* The reverie-pin attribution is **correlational over 6 builds**, not an isolated A/B (above).
* The primary's binary is `-dirty` (6 modified files in `hermit/`, not mine); it is reported as
  corroborating, not as a clean data point. The four slot builds are clean checkouts.
* No repository was mutated. w13's branch was read (`rev-parse`, `diff`) and never checked out,
  reset, or written.

## Reproduction

```bash
cd experiments/kvm-30s-wall-exit137-recheck_20260807
gcc -O0 -static -o /tmp/w2nb probe.c
./run.sh <path-to-hermit-binary>          # prints the table rows for one build
```
