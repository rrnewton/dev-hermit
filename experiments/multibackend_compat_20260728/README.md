# Multi-backend compat push: harder programs under `--strict --verify`

**Date:** 2026-07-28 · **Task:** compat push (multi-backend)
**Mode:** `hermit run --backend <be> --strict --verify` (L2).
**Backends:** ptrace, sabre, dbi, liteinst, kvm.
**Hermit:** `worktrees/275/hermit`, branch `codex/compat-push-multibackend`,
base `origin/main` `9b4f6896`. **reverie pin:** `9233c0d0`.
**Host:** devbig030, kernel 6.17.13.

## Question

Building on the prior ptrace-only survey (`compat_push_survey_20260728`, which
found the ptrace L2 frontier mature and landed draft PR #1041), do *other*
backends fail harder programs at L2, and is any failure a fresh, safe, landable
**in-tree** (hermit repo) fix?

## Headline

**The ptrace L2 frontier is mature, and every multi-backend compat gap found
routes to the pinned `reverie` dependency (approval-gated + pin bump). No
in-tree hermit code fix was landable this round.** The genuinely new finding is
a **liteinst syscall-interception incompleteness** bug (nondeterministic
`--verify`, no fork/clone) — filed as **hermit #1047**.

## Results (`results.csv`)

| program | category | ptrace | sabre | dbi | liteinst |
|---|---|---|---|---|---|
| awk-compute | compute-locale | L2 | L2 | L2 | **NONDET ~3/30** |
| awk-tolower-utf8 | compute-locale | L2 | L2 | L2 | flaky (locale) |
| python-threads4 | threads | L2 | L2 | L2 | **NOFORK** (can't start thread) |
| sha256sum | crypto | L2 | L2 | L2 | L2 |
| openssl-sha256 | crypto | L2 | L2 | L2 | L2 |
| perl-hash | interp | L2 | L2 | L2 | L2 |
| base32-single | text | L2 | L2 | L2 | L2 |
| `echo\|base32\|base32 -d` | pipeline | L2 | EAGAIN(#1035) | **COPIEDCHILD ioctl** | — |
| date (fork wrapper) | fork | L2 | **RPCPANIC** | L2 | **NOFORK** |

## Per-backend gaps (all in pinned reverie)

### liteinst — interception incomplete → nondeterministic (NEW)

`detcore-liteinst/src/lib.rs` is a 34-line `.init_array` constructor that only
calls `reverie_liteinst::install_tool` (pinned rev `9233c0d0`); all interception
lives in `reverie-liteinst`. Consequences:

- **Incomplete interception.** A single-process `awk 'BEGIN{...}'` commits only
  **5** scheduler turns under liteinst vs **27** under ptrace; ptrace logs 88
  individual locale-file reads + 8 archive refs every run, liteinst logs **0**.
  glibc's internal syscalls execute natively/untraced.
- **Nondeterministic `--verify`.** Because the traced subset varies run-to-run,
  `awk-compute` flakes **3/30 (~10%)** with `Mismatch between run 1 and run 2
  outputs` even though stdout ("4999950000") is stable. The diverging COMMIT
  turns are exactly the glibc locale-file opens (`/usr/lib/locale/...`,
  `locale-archive`) — present in one run, absent in the other.
- **No fork/clone.** `reverie-liteinst: clone/fork injection is unsupported` →
  the `date` fork wrapper fails and `python3` threading raises `can't start new
  thread`.
- **No PMU timer.** Warns `--backend=liteinst does not implement PMU/RCB timer
  delivery; continuing with --max-timeslice=disabled`.

Note LANG is visible and `python locale.setlocale(LC_ALL,"")` returns
`en_US.UTF-8` under liteinst — liteinst *can* load locale, just not
consistently traced. **Fix is in pinned `reverie-liteinst`.**

### sabre

- **RDTSCP** not intercepted → raw host TSC leak (companion experiment
  `sabre_rdtscp_gap_20260728`).
- **Pipe EAGAIN** in execve'd children — **#1035**.
- **RPC panic on fork wrapper.** `date` aborts with a panic at
  `reverie-rpc-transport/src/blocking_client.rs:97`: `blocking RPC to
  coordinator failed: Io(... code: 88 ... "Socket operation on non-socket")` →
  core dump. A forked child inherits an fd the blocking RPC client treats as the
  coordinator socket but which is not a socket; the `.unwrap()`/`expect` there
  aborts instead of erroring cleanly. **Pinned `reverie-rpc-transport`.**

### dbi

- **Copied-child ioctl fail-closed.** `detcore-dbi`
  `reverie_dbi_runtime_copied_syscall` (in-tree) returns 1 (abort) for
  `ioctl|recvmsg|recvmmsg|readlink|readlinkat` in strict copied pre-exec
  children (PR-981, deliberate — the ABI passes no syscall args, so it cannot
  distinguish socket-timestamp ioctls). This breaks bash pipelines
  (`echo|base32|base32 -d` → `unsupported syscall 16 in copied child`). A proper
  fix needs the **pinned reverie-dbi ABI** to pass syscall args to this callback.
- **Clock (#705) effectively FIXED:** `date -u +%Y` → 2026 under dbi.

### kvm

Uses a distinct verify (guest output + exit-status compare). `echo` L2.
Remaining un-enumerated-syscall gaps live in `reverie-kvm` executor (pinned).

## Disposition

No in-tree hermit PR is landable this round: the shared ptrace/detcore engine is
mature, and every backend gap is an interception-semantics change to a core
Reverie contract (`reverie-sabre`/`third-party/sabre`, `reverie-dbi` ABI,
`reverie-liteinst`, `reverie-kvm`, `reverie-rpc-transport`). Per the parent
**Reverie API Policy** those require user approval + a reverie feature branch +
a parent pin bump before implementation. Contribution this round: the
multi-backend matrix above and a new hermit issue for the liteinst
interception-incompleteness bug. The prior round already landed the one safe
in-tree fix (draft PR #1041).

## Reproduction

```bash
cd ~/work/dev-hermit/worktrees/275/hermit && cargo build --release
H=./target/release/hermit
# liteinst nondeterminism (flakes ~10%):
for i in $(seq 30); do $H run --backend liteinst --strict --verify -- \
  awk 'BEGIN{s=0;for(i=0;i<100000;i++)s+=i;print s}'; done
# liteinst interception depth vs ptrace (5 vs 27 commits):
$H --log=trace run --backend liteinst --strict -- awk 'BEGIN{print 1}' 2>&1 | grep -c 'COMMIT turn'
$H --log=trace run --backend ptrace   --strict -- awk 'BEGIN{print 1}' 2>&1 | grep -c 'COMMIT turn'
# sabre RPC panic on fork wrapper:
$H run --backend sabre --strict -- date -u +%Y
# dbi copied-child ioctl:
$H run --backend dbi --strict -- bash -c 'echo hello | base32 | base32 -d'
```
