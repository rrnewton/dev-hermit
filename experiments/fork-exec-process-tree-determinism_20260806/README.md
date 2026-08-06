# fork/exec + process-tree ordering determinism

**Task:** `fork-exec-process-tree-determinism` (P1) · **Agent:** hermit-det1 · **Date:** 2026-08-06
**Bound to:** hermit `f89c69766371806d3c9b2c3003531df2d59d6118` (clean detached worktree `worktrees/det1/hermit`,
version string `gf89c69766371`, no `-dirty`), reverie cargo pin `9470712afa9b421c72850ab7955fb335692e43a0`.
Instrument sha256 `5f4aa9ab8e6b1cf3bf98f73cca35001211e28c2829215e97d9c2c927e5ae9047`.
**Host:** devbig014 (316 cores, shared). **Local only, no egress.**

---

## 1. Question

Process creation and reaping order is a determinism surface that had not been swept: `fork`/`clone`
ordering, `exec`, `wait`/`waitpid` reap order, and zombie handling across a process tree. Do
multi-process programs produce **identical process-event ordering in the detlog** under
strict + `--verify` + double-run, across backends?

## 2. Method

### 2.1 The metric: an address-normalized process-event projection

Raw `--detlog-heap`/`--detlog-stack` hashes are hashes of memory **content**, and that content holds
absolute pointers, so any relocating backend diverges on every record while behaving identically
(established in `ai_docs/cross-backend-detlog-parity-sweep-20260806.md` §6). Raw hashes are therefore
the right instrument for **self**-determinism and the wrong one for **cross-backend** comparison.

`harness/pevents.py` computes the projection the process-tree question actually needs: the ordered
sequence of

* `clone`/`clone3`/`fork`/`vfork`, `execve`/`execveat`, `wait4`/`waitid`, `exit`/`exit_group`,
  `getpid`/`getppid`/`gettid`/`set_tid_address` — inbound and finished, with pointer operands
  replaced by `PTR`;
* scheduler `COMMIT` turns whose resource scenario is process-structural (`ParentContinue`,
  `ChildStart`, `Exit`);
* `logically_kill` (the scheduler forgetting a dettid), `post_exec`, new-thread go-aheads;
* the run report's `Final thread-tree was: …` and group/thread counts.

Detcore hands out **virtual** pids deterministically, so pid values are retained verbatim —
normalizing them away would delete the signal. Only host-address literals are ordinalized.

### 2.2 Environment pinning

Every cell runs under `env -i` with one fixed variable set, **including `HERMIT_E9TOOL` and
`LD_LIBRARY_PATH` exported even for backends that ignore them**. The kernel writes `envp` into the
guest's initial stack, so an unpinned comparison measures the launcher, not the backend. (Same
discipline, and the same validated positive control, as `ignored/detlog-parity/run-cell.sh`.)

### 2.3 Legs

| leg | what it does |
| --- | --- |
| **sweep** | 14 guests × {ptrace, e9patch}; per cell: 2 independent hermit processes + 1 `hermit --verify` |
| **stress-serial** | 8 sharpest guests × 20 sequential runs; require ONE process-event trace |
| **stress-concurrent** | same 8 guests × 32 runs at 16 in flight — the vfork/ptrace-stop death-race condition |
| **proc-shapes** | a guest emitting `clone3`, `setsid`, `setpgid`, `waitid`+`WNOWAIT` |
| **chaos** | `--chaos --sched-seed S`: same seed twice (must match) **and** across 3 seeds (must differ, or the pass is inert) |
| **sysinfo-bracket** | two-sided attribution of the one red cell |

Guests: `forkwait_ordered` (targeted `waitpid` in fork order), `forkwait_any` at 5 and 12 children
(`wait(-1)`, so **reap order is scheduler-determined** — children do reverse-ordered work so a real
machine reorders them), `zombie_delay` (children exit while the parent burns CPU, then bulk reap),
`orphan_reparent` (double fork; middle exits first, grandchild is reparented), `exec_chain` (depth-3
`fork`+`execve`), `vfork_exec`, `spawn_wait` (`posix_spawn`), `fork_pipe` (pipe rendezvous couples
process order to data order), `sh_pipeline`, `sh_seqtree` (`make -j1`-shaped serial subprocess tree),
`sh_nested` (depth-3 shells), `sh_bgjobs` (`wait` over background jobs), and a real
`/usr/bin/make -s -j1`.

## 3. Results

### 3.1 Process-event ordering: clean, 28/28 cells

`results-sweep-raw.csv`. Every cell: run1 ≡ run2 on stdout **and** on the process-event trace; the
`Final thread-tree` shape is identical run-to-run and identical between ptrace and e9patch in all 14
guests. Examples of the trees actually exercised: `[3 [5] [7] [9] [11] [13]]` (flat 5-child),
`[3 [5 [7 [9]]]]` (depth-3 exec chain), `[3 [5 [7]]]` (orphan/reparent),
`[3 [5] … [27]]` (12-child wide), `[3 [5 [7 [9]]] [11]]` (nested shells).

**Reap order under `wait(-1)` is fully determinized.** Native, `forkwait_any 5` reaps in a different
order on nearly every run (measured: `2,0,3,1,4` then `0,4,3,2,1`); under `hermit run --strict` it is
`0,1,2,3,4` on every run.

### 3.2 Repetition, including under concurrency: clean, 416 runs

`results-stress-serial-raw.csv`, `results-stress-conc-raw.csv`. 8 guests × 20 serial and 8 × 32 at
16-wide: **1 distinct process-event trace and 1 distinct stdout per guest in every case, zero
timeouts, zero run failures.**

### 3.3 Chaos mode: the ordering is seed-pinned, and the seed genuinely moves it

`results-chaos-raw.csv`, `results-reap-order.csv`. This is the leg that keeps §3.1 from being a
vacuous pass, because it brackets **both** sides:

* **Same seed, twice — identical.** 7/7 guests: identical process-event trace *and* identical stdout.
* **Across 3 seeds — different.** 7/7 guests produce more than one distinct process-event trace
  (3 distinct for six guests, 2 for `orphan_reparent`). So `--chaos` really is exploring different
  process-tree interleavings on this corpus; the same-seed pass is not inert.

The reap order of `forkwait_any 5` under `wait(-1)` makes it concrete:

| config | reap order (child slots) |
| --- | --- |
| native, run 1 | `2 0 3 1 4` |
| native, run 2 | `0 4 3 2 1` |
| `hermit run --strict` (any run) | `0 1 2 3 4` |
| `--strict --chaos --sched-seed 1` | `0 3 1 2 4` |
| `--strict --chaos --sched-seed 2` | `0 1 3 2 4` |
| `--strict --chaos --sched-seed 3` | `0 1 2 3 4` |

Three different reap orders, each **exactly reproducible** for its seed.

### 3.4 New process-event shapes: clean, and faithful

`results-proc-shapes-raw.csv`. A guest exercising `clone3`, `setsid`, `setpgid(0,0)`, and
`waitid(…, WEXITED|WNOWAIT)` followed by a real reap is clean on both backends (`sd_pev`, `sd_detlog`,
`px_pev` all 1; plain `--verify` matched), and its stdout under hermit is **byte-identical to native**
— including `setsid` self-leadership, and `WNOWAIT` correctly *not* consuming the zombie so the
second `waitid` still returns the status.

### 3.5 The one red cell, and it is not a process-tree defect

`sh_pipeline` (`echo | tr | tr | sort | uniq | wc`) is the only guest where the same-backend
double-run diverges — on **both** ptrace and e9patch:

| | value |
| --- | --- |
| stdout run1 vs run2 | **identical** |
| process-event trace run1 vs run2 | **identical** |
| full DETLOG (`hermit log-diff`) | **differs** |
| `hermit run --strict --verify` (plain, Stripped comparator) | **`verdict: diverged`** |

First divergence: a `[stack]` content hash for dtid 11 **at identical addresses**, immediately after
`finish syscall #148: sysinfo(…) = Ok(0)`. GNU `sort` calls `sysinfo(2)` to size its merge buffer.

**Root cause — `sysinfo(2)` writes uninitialized padding into guest memory.**
`reverie-syscalls/src/args/sysinfo.rs:48-51` at the pinned reverie rev:

```rust
impl From<SysInfo> for libc::sysinfo {
    fn from(sys_info: SysInfo) -> libc::sysinfo { unsafe { std::mem::transmute(sys_info) } }
}
```

`#[repr(C)] SysInfo` is `u64 × 10`, `u16 procs`, `u64 total_high`, `u64 free_high`, `u32 mem_unit`, so
bytes **82..88** (the alignment hole after `procs`) and **108..112** (tail padding) are never written
by `collect_sysinfo`'s struct literal in `detcore/src/syscalls/sysinfo.rs`. `transmute` copies all 112
bytes, so `handle_sysinfo`'s `write_value` copies **detcore's uninitialized stack padding** into the
guest.

**Two-sided bracket** (`results-sysinfo-bracket-raw.csv`), same binary, same guest, one variant apart:

| variant | detlog self-determinism | `--verify` |
| --- | --- | --- |
| `sysinfo(NULL)` → EFAULT, nothing written | **matched** | matched |
| `uname(2)` into a stack buffer (control) | **matched** | matched |
| `sysinfo` → 256 B **stack** buffer | **diverged**, offsets exactly {82,83,84,108} | diverged |
| `sysinfo` → 256 B **heap** buffer | **diverged**, offsets exactly {83,84,108} | diverged |
| single-process guest, only `sysinfo` | **diverged** | diverged |
| same guest, call skipped | **matched** | matched |

Bytes 112..256 stay `0xAA` in both buffer variants, so there is no over-write past `sizeof`.
Because a **single-process** guest with no `fork`, `exec` or `wait` reproduces it, the divergence is
**not attributable to fork/exec/process trees**.

**Quantified** (`results-sysinfo-padding-10samples.txt`): 10 identical runs under the L3 config gave
**10/10 distinct padding signatures**, e.g. `82..88 = 3e15567f0000 / 7ec27d7f0000 / 9e6daa7f0000 …`
and `108..112 = 567f0000 / 7d7f0000 / aa7f0000 …`. The trailing `7f0000` is bytes 4..7 of a host
userspace pointer `0x00007fXXXXXXXXXX` — **host ASLR address bytes crossing into the guest.**

**Fidelity, not only determinism.** Native (no hermit) padding is all zeros, 3/3 — Linux
`do_sysinfo()` memsets the whole struct. Under plain `hermit run --strict` (no detlog flags) it is
`ffffffffffff`, 5/5 — also never the zeros Linux guarantees.

Filed as task `sysinfo_2_writes_uninitialized`. **Do not "fix" it by excluding `sysinfo` from the
detlog or coarsening the memory hash (#140): the guest-visible bytes really do differ.**

### 3.6 Side-finding: `--verify-strict` is inert on this host

`--verify-strict` returns `bitwise_parity: false` for `/bin/true` and `/bin/echo` as well as for every
fork guest, so it cannot discriminate anything here. This **refines** the standing note
(`l2-unattainable-and-kvm-strict-hangs-on-this-box`), which says the divergent lines "render
identically on both sides ⇒ comparator gap". At `f89c69766` they are **genuinely different host
state**, two leaks inside the compared envelope:

1. `DEBUG tracee.attach{pid=3}: reverie_ptrace::timer: … CpuId { … initial_local_apic_id: 227 vs 239 … }`
   — the host core the tracee attached on;
2. `DEBUG detcore::tool_global: Nondeterministic realtime elapsed: 38.904157ms vs 37.06689ms`
   (`detcore/src/tool_global.rs:541`) — a host wall clock.

Both are **DEBUG** lines, although `AGENTS.md` defines `--verify-strict`/BitwiseInfoV1 over **INFO**
events; `hermit-cli/src/bin/hermit/verify.rs:557` sets `global.log = Some(LevelFilter::DEBUG)` for
verification paths, and passing an explicit `--log info` does **not** remove them. Bracketed on
`/bin/true`, so this is an instrument defect, not a fork/exec result. Plain `--verify` (Stripped)
works and is what the sweep used.

## 4. Interpretation

1. **No fork/exec/process-tree ordering defect was found** at this depth: 28/28 sweep cells and 416
   repeated runs agree on process-event order, including `wait(-1)` reap order, zombie bulk reap,
   double-fork reparenting, `posix_spawn`, depth-3 exec chains, and real `make -j1`.
2. **Chaos-mode bracketing is what makes claim 1 non-vacuous** (§3.3): the same corpus yields three
   *different* reap orders under three seeds, so the instrument demonstrably can express a different
   ordering — hermit pins each one to its seed rather than the corpus being insensitive.
3. **The sweep still paid for itself**, because the guest it chose surfaced a real, root-caused
   determinism *and* Linux-fidelity *and* container-isolation defect in `sysinfo(2)` that the shipped
   stdout-only compat scorecard cannot see: `sh_pipeline`'s stdout is byte-identical, so
   `collect-envelope.rs` would score it `parity=1`. That is a **worked counterexample** to the shipped
   metric, not an argument that one exists.
4. **The strongest fork/exec claim supported is L3-on-ptrace + a weak e9patch leg**, not
   cross-backend parity. See §5.

## 5. Not established

* **Only ptrace was genuinely exercised.** e9patch reported `candidate_sites=0` on every guest, so
  the AOT pass was a no-op and the unmodified binary ran under the **ptrace** runtime. Its 14/14
  agreement is therefore near-tautological, *not* independent cross-backend evidence. dbi and sabre
  are not in this build (cargo feature; DynamoRIO needs cmake, absent); liteinst has no
  `libreverie_liteinst.so`; kvm livelocks. **The task's "across backends" leg is substantially unmet
  and is a host/build-provisioning limitation, not a result.**
* **The vfork hazard was not really probed.** The documented `detcore_misc`
  `vfork_parent_resumes_after_child_exec` hang is a load-dependent reverie ESRCH death race that
  reproduces 16-wide at host load **150–763** and usually passes standalone. My concurrent leg ran
  16-wide at measured load **55–56** on a shared box. A clean result at that load is **not** evidence
  about that race; use `experiments/multisect_detcore_misc_20260803/matched.sh` for it. I did not
  generate synthetic load, to avoid disrupting the ~18 other agents on this box.
* **The chaos leg is ptrace-only and 3 seeds deep.** §3.3 shows the seed moves the ordering and
  pins it, but 3 seeds is a spot check, not a search; `--chaos-target-races` and
  `--chaos-per-thread-slowdown` were not exercised at all.
* **`--verify-strict`/L2 was not established for any cell** — it is inert here (§3.4). Every cell's
  verify leg is plain `--verify` (Stripped comparator), which is weaker.
* **`sysinfo` padding was not fixed** and no reverie branch or PR exists. §3.3 is a diagnosis plus a
  proposed one-line determinization, not code.
* **The corpus is 14 hand-picked guests**, chosen to hit named process-tree shapes. It does not
  quantify what fraction of the 200-cell compat corpus is affected by anything here.
* **Still unexercised:** `CLONE_NEWPID` and other namespace creation, `SIGCHLD` *delivery* order and
  handlers (all guests reap synchronously; none installs a `SIGCHLD` handler), `wait4` with `WUNTRACED`
  /`WCONTINUED` and job-control stop/continue, `pidfd_open`/`pidfd_send_signal`, `execveat`, and
  process trees that outlive the root. `clone3`, `setsid`, `setpgid`, and `waitid`+`WNOWAIT` ARE now
  covered (§3.4).

## 6. Reproduction

```bash
cd ~/work/dev-hermit
# 1. Build the instrument (see metadata.json for the four pkg-config vars libunwind needs)
export LU=$PWD/ignored/lu-parity/usr
export PKG_CONFIG_PATH=$LU/lib64/pkgconfig LIBRARY_PATH=$LU/lib64 \
       C_INCLUDE_PATH=$LU/include LD_LIBRARY_PATH=$LU/lib64
(cd worktrees/det1/hermit && cargo build --release -p hermit --bin hermit --features hermit/e9patch)

# 2. Build the guests
cd experiments/fork-exec-process-tree-determinism_20260806/guests
for f in *.c; do gcc -O1 -o "${f%.c}" "$f"; done      # dynamic: host has no static libc

# 3. Run the legs (edit the paths at the top of the run-*.sh wrappers)
../harness/run-sweep.sh      # 14 guests x 2 backends -> results-sweep-raw.csv
../harness/run-stress.sh     # 20 serial + 32 @16-wide -> results-stress-*-raw.csv

# 4. Inspect one divergence by hand
hermit log-diff --no-color --limit 3 --syscall-history 4 <dir>/ptrace.r1.log <dir>/ptrace.r2.log
python3 ../harness/pevents.py <dir>/ptrace.r1.log <dir>/ptrace.r2.log   # exit 0 == equal
```
