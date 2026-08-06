# ioctl / tty determinism: the host terminal reaches the guest, and none of hermit's gates can see it

**Task:** `ioctl-tty-determinism` · **Agent:** hermit-det4 (`[impl agent, opus-5]`) ·
**2026-08-06** · local only, no egress · research-only, no product change made.

## Result

Under `hermit run --strict`, **the host terminal's geometry and line discipline pass straight
through to the guest on every backend that presents a terminal at all** (ptrace, dbi, sabre).
Two runs of the same command, with the same flags, on the same host, differ in guest-visible
output when the only thing that changed is the size of the terminal hermit was launched from.

The leak itself is one missing `match` arm. The reason it survived is more interesting: **all
three of hermit's own determinism gates are structurally incapable of detecting it.**

| | finding | evidence |
| --- | --- | --- |
| **L1** | host terminal **geometry** reaches the guest | 12 / 15 (guest, backend) pairs change guest-visible output across pty 24x80 / 40x120 / 50x200 — `geometry-leak.csv` |
| **L2** | host **termios** reaches the guest | 3 / 3 distinct guest-visible termios across 3 host line-discipline settings, on 3 / 3 backends — `termios-leak.csv` |
| **G1** | `--verify` **destroys the terminal it would need to test** | on a real pty, `ioctl(fd, TIOCGWINSZ)` succeeds for fd 0,1,2 under `--strict` and fails for all three under `--strict --verify` |
| **G2** | DETLOG parity is **blind to the leaked bytes** | across two geometries, the guest that reads-and-ignores compares `120 \| 120` DETLOG messages with *no* differences |
| **B1** | **dbi** does not virtualize pgid/sid | 4 / 60 cells fail the double-run gate, all dbi, all on raw host `TIOCGPGRP`/`TIOCGSID` values |
| **B2** | **kvm** presents *no* terminal at all | `isatty` = 0 on fd 0,1,2 on a real pty, where the other three backends report 1 |

Everything else measured is clean: **56 / 60** cells are byte-identical across 3 separate hermit
invocations, and sabre matches the ptrace reference on **20 / 20** cells.

## Root cause, located in source rather than inferred

`detcore/src/syscalls/files.rs:1539`, `handle_ioctl`, matches exactly four requests —
`SIOCETHTOOL` (rejected as `ENODEV`), `FIOCLEX`, `FIONCLEX`, `FIONBIO` — and sends everything
else to `self.record_or_replay(guest, call)`, which in run mode is the host syscall. There is no
arm for `TIOCGWINSZ`, `TCGETS`, `TIOCGPGRP`, or `TIOCGSID`.

The determinization pattern already exists a few hundred lines below, in the same file:
`handle_statfs` runs the real syscall and then calls `canonicalize_statfs_buf` to overwrite the
host-varying fields, with a comment explaining that free-block counts "vary between runs as the
underlying host filesystem fills and drains, which makes a bare passthrough diverge under
`--verify`". Terminal geometry is the same class of host state. `ioctl` simply never got the
treatment.

## The measurement, and why "stable" would not have been enough

Two runs agreeing proves nothing if nothing varied. The independent variable here is the **host
terminal**, held apart from everything else: `TERM` is pinned to `xterm-256color`, `COLUMNS` and
`LINES` are removed from the environment (so `TIOCGWINSZ` is the *only* width channel a guest
has), and the flags, cwd, and guest binaries are identical. Only the pty's `TIOCSWINSZ` differs.

`probe.c` prints one `key=value` line per guest-visible tty fact. Under ptrace, the diff between
the 24x80 run and the 40x120 run is *exactly* four lines and nothing else:

```
< TIOCGWINSZ.stdin=ok rows=24 cols=80    > TIOCGWINSZ.stdin=ok rows=40 cols=120
< TIOCGWINSZ.stdout=ok rows=24 cols=80   > TIOCGWINSZ.stdout=ok rows=40 cols=120
< TIOCGWINSZ.stderr=ok rows=24 cols=80   > TIOCGWINSZ.stderr=ok rows=40 cols=120
< devtty=ok rows=24 cols=80              > devtty=ok rows=40 cols=120
```

`/dev/tty` leaks too, so redirecting the standard descriptors does not close the channel.

### It is consequential, not just a probe artifact

`/bin/ls -C /etc` under `hermit run --strict --backend=ptrace`, same command, three host
terminals:

| host terminal | guest stdout | sha256 (first 16) |
| --- | --- | --- |
| pty 24x80 | 4 457 B | `66e54070ffc254ab` |
| pty 40x120 | 5 851 B | `d9dd4f1403b936d8` |
| pty 50x200 | 5 762 B | `395b425bfe489023` |

An internal consistency check falls out of this: the `pipe` configuration also produces
`66e54070ffc254ab`, because GNU `ls -C` assumes 80 columns when stdout is not a terminal. The
80-column terminal and the pipe agree, and the wider terminals do not — which is what a real
width dependence looks like, not noise.

`/bin/stty -a` likewise produces three distinct outputs (619 / 620 / 620 B, three distinct hashes).

### The control that keeps the verdict honest

`winsz.c silent` performs the identical `TIOCGWINSZ` and then returns 0 without printing or
branching. It is the only guest in the matrix that is **stable across all three geometries**, on
all three backends — 3 of the 15 pairs. So the sweep is not simply reporting "everything
differs": it separates *reading* host state from *acting* on it, and only the latter shows up.

## Why hermit never caught this

### G1 — `--verify` has zero tty coverage, by construction

`winsz.c branch <fd>` exits 70 when `ioctl(fd, TIOCGWINSZ)` fails and otherwise exits with the
column count. Launched on a real pty 40x120, ptrace backend:

| fd | `--strict` | `--strict --verify --verify-allow both` |
| --- | --- | --- |
| 0 | 120 | **70** |
| 1 | 120 | **70** |
| 2 | 120 | **70** |

`--verify` has to capture stdout and stderr in order to compare the two runs, and in doing so it
replaces them with non-terminals. Every strict+verify test in the suite therefore runs with
non-tty stdio: `isatty`, `TIOCGWINSZ` and `TCGETS` are never exercised under the determinism
gate at all. This is the reason the gap is invisible to CI, and it is a coverage hole
independent of whether the leak is fixed.

### G2 — DETLOG records the ioctl's return code, not its output buffer

The trace line is `finish syscall: ioctl(1, TIOCGWINSZ, 0x7fffffffb854) = Ok(0)` — identical at
every geometry, because the leaked bytes are in the guest's buffer, not in the return value.
Bracketing the parity gate across host 24x80 vs 40x120, ptrace, address-normalized
`hermit log-diff`:

| guest | same ioctl? | verdict | messages compared |
| --- | --- | --- | --- |
| `winsz silent` (reads, ignores) | yes | **no substantive differences** | 120 \| 120 |
| `winsz branch` (reads, acts) | yes | differences found, first diff `exit_group(80)` vs `exit_group(120)` | 120 \| 120 |

Same syscall, same leak; detectability depends entirely on whether the guest happened to act on
the value within the traced run. Host state can sit in guest memory indefinitely and the
deterministic trace will call the run clean.

### Gate bracketing (both sides, with counts)

`--verify-strict` is **not** used as a gate in this experiment and **no L2 claim is made**: it is
red on this box for `/bin/true` — a guest with no ioctl and no terminal — diverging at log
message 5 on a `DEBUG reverie_ptrace::timer` line that embeds `CpuId { ... initial_local_apic_id:
73 }` vs `247`, i.e. which physical core the tracee landed on. `taskset -c 5` does not fix it
(the mismatch moves to message 10). That is a pre-existing box-level condition, unrelated to this
task.

The substitute gate is `hermit log-diff` (address-normalized COMMIT+DETLOG, which excludes that
DEBUG line), bracketed on both sides:

* **inert-check / positive:** two `/bin/true` runs → no differences, **118** DETLOG messages
  compared. Two `probe` runs at the *same* pty 24x80 → no differences, **2117** compared. The
  gate is comparing real messages, not passing vacuously.
* **planted violation / negative:** `probe` at 24x80 vs 40x120 → mismatches at DETLOG messages
  158, 176, 188, 572, 829, 841, 853, 1237. The gate fires.

## Backends

Self-determinism, 3 separate hermit invocations per cell, guest stdout **and** exit status
compared (`results.csv`, 60 cells):

| backend | cells identical across 3 invocations | writes `--log-file`? | DETLOG messages per cell |
| --- | --- | --- | --- |
| ptrace | 20 / 20 | yes | 120 – 2117 |
| sabre | 20 / 20 | yes, **but also dumps 38 KB to stderr** | 4 – 393 |
| dbi | **16 / 20** | **no — writes nothing** | n/a |

**Read the sabre column with care.** "Parity holds on sabre" is close to vacuous where the
comparison covers 4 messages against ptrace's 120 for the same guest. Sabre's DETLOG volume is
roughly 1–20 % of ptrace's, so a sabre parity pass is reported here with its count and should
not be treated as equivalent evidence.

**B1 — dbi does not virtualize process-group or session IDs.** All 4 double-run failures are the
dbi × `probe` cells, and the diff is *only* the pgrp/sid lines (`pgrp=945505` vs `945721` — raw
host IDs, new on every invocation). `pgrp.c` makes it sharper:

| | `getpid()` | `getpgrp()` | `TIOCGPGRP` | self-consistent? |
| --- | --- | --- | --- | --- |
| native | 788860 | 788860 | 788860 | yes |
| ptrace | 3 | 0 | 0 | yes |
| sabre | 3 | 0 | 0 | yes |
| **dbi** | 3 | **789232** | **789229** | **no** |

dbi virtualizes the pid (3) but not the pgid or sid, and `getpgrp()` and `TIOCGPGRP` disagree
with each other within a single run. Note this is not purely an ioctl issue — `getpgrp(2)` and
`getsid(2)` leak the same host state.

Guest-output parity against the ptrace reference (`backend-parity.csv`, 40 rows): sabre **20 / 20**
identical; dbi **16 / 20**, the 4 misses being exactly the pgrp/sid cells above.

**B2 — kvm presents no terminal at all.** On the secondary anchor (the primary hangs on kvm), on
a real pty 24x80 where ptrace/dbi/sabre all report `isatty.stdout=1` and `rows=24 cols=80`, kvm
reports `isatty.stdout=0 errno=25`, `TIOCGWINSZ.stdout=err errno=25`, and `/dev/tty` opens but
its `TIOCGWINSZ` fails. kvm is therefore immune to this leak, but by a route that is itself a
hard semantic divergence: a guest that formats to the terminal takes a different code path under
kvm than under every other backend.

## Two things that are deterministic but not faithful

Flagged, not claimed as bugs — both are the shape issue #140 warns about (a value made
deterministic by freezing it to something that cannot occur):

1. **`TIOCGPGRP` / `TIOCGSID` return 0** under ptrace and sabre. 0 is not a valid Linux process
   group. The guest at least sees a self-consistent story (`getpgrp()` also returns 0), so this
   is not a determinism defect, but a job-control-aware guest is being told something impossible.
2. **`ttyname(3)` is build-dependent**, and the older behaviour was a leak. On a single shared
   terminal where the host reports `/dev/pts/50`: hermit `g464cbd9f` (2026-08-01) returns
   `/dev/pts/50` — the host pts number, which depends on how many terminals the box has open —
   while `g52d56e5c` (2026-08-04) returns `ENODEV` on all three backends. `464cbd9f` is an
   ancestor of `52d56e5c`, so the leak was closed somewhere between them, but no commit in
   `detcore/src` touches `ttyname` or `TIOCGWINSZ` (`git log -S`), so **the mechanism is not
   localized** and the fix may be incidental and therefore fragile. Both builds show the same
   virtualized guest `/dev/pts` (entries `0`, `1` only), so that is not the difference.

## What a fix would have to do (not implemented here)

`handle_ioctl` should determinize the terminal-query requests the way `handle_statfs`
determinizes the volatile `statfs` fields: run or synthesize the call, then write a fixed virtual
terminal into the guest buffer — a constant geometry (80x24 is the conventional choice) and a
canonical termios — rather than the host's.

The #140 caution applies but does not block this: freezing is the wrong move when a value must
*evolve* (a clock, a counter). Terminal geometry has no such evolution semantics — but it is
still not a constant, because a guest may call `TIOCSWINSZ` and must read back what it set. The
correct shape is per-container virtual terminal *state*, initialized to a fixed value and
mutated only by the guest, not a hardcoded return. The pgid/sid values should be translated
through the same pid virtualization the rest of detcore already uses, rather than frozen at 0.

## RIGOR — what was and was not established

**Swept is not covered.** This is a research sweep on one host; no product change was made and
no PR is proposed.

* **Repetitions:** n = 3 separate hermit invocations per cell for the 60-cell main matrix (180
  invocations), n = 1 per cell for the 12-cell termios axis. This is a double-run depth, **not**
  L4 stress (which would be ~20x).
* **Hosts: one.** `devbig014`, 316 cores, AMD, kernel 6.18.39. Cross-host variation was *not*
  measured — it did not need to be, because varying the terminal on one host is sufficient to
  demonstrate the leak, but a genuine cross-host figure is absent.
* **Assurance level: L1 only.** No L2 claim anywhere: `--verify-strict` is red on this box for
  `/bin/true` for reasons unrelated to this task, and `--verify` cannot see a terminal at all.
  Relaxations: none beyond `--strict` (no `--no-sequentialize-threads`, no chaos mode).
* **Backends: 3 of 4 in the matrix.** kvm is a single hand-run cell on a secondary, older binary,
  because it does not complete this workload on the primary anchor. liteinst and e9patch were
  not exercised at all.
* **Binary provenance:** the primary binary's sha256 was recorded before and after the sweep and
  is unchanged (`binary-sha256.before.txt` / `.after.txt`). Note the binary's own commit
  (`g52d56e5ceb38`) is the anchor, **not** the worktree HEAD it sits in — the secondary anchor is
  a live example of a binary that is stale relative to its checkout.
* **Sub-cases NOT exercised:** `TIOCSWINSZ` (write path — whether a guest can set and read back
  its own geometry, which is the crux of any fix); `SIGWINCH` delivery on resize; `TCSETS`
  round-trip; `TIOCSTI`; pty *slave-side* vs master-side asymmetry; ncurses/terminfo-driven
  programs (only `ls`/`stty` were used); multi-threaded guests; guests that resize mid-run;
  record/replay mode (`hermit record`), which may behave differently since `record_or_replay`
  is the code path in question.
* **One harness caveat:** in pty mode the guest's fd 2 is a *second* pty, so that its stdout can
  be captured without hermit's own stderr mixed in (only ptrace honours `--log-file`; sabre
  duplicates 38 KB to stderr and dbi ignores it entirely, writing 68 KB there). Both are real
  terminals of the same size, so every ioctl behaves identically; the visible consequence is that
  `ttyname(2)` legitimately differs from `ttyname(0)`/`ttyname(1)` in `out/`. The single-terminal
  configuration was measured separately for `ttyname` (`termios-sweep.out`, section A). No
  post-hoc text filtering is applied to any measurement — the separation is at the descriptor
  level.

## Reproduction

```bash
cd experiments/ioctl-tty-determinism_20260806
gcc -O0 -o probe probe.c && gcc -O0 -o winsz winsz.c && gcc -O0 -o pgrp pgrp.c
export LD_LIBRARY_PATH=$HOME/.local/hermit-deps/lu/usr/lib64
export HERMIT_BIN=/home/newton/work/dev-hermit/worktrees/dbi/hermit/target/release/hermit

python3 run-sweep.py          # -> results.csv, geometry-leak.csv, backend-parity.csv
python3 termios-sweep.py      # -> termios-sweep.out (then termios-leak.csv)

# the headline, on its own:
python3 ptyrun.py 24 80  -- $HERMIT_BIN run --strict -- ./probe | grep TIOCGWINSZ
python3 ptyrun.py 40 120 -- $HERMIT_BIN run --strict -- ./probe | grep TIOCGWINSZ
```

Guest binaries and per-run outputs are gitignored; `run-sweep.py` regenerates them.
Exact SHAs, host, flags, and gate limitations: `metadata.json`.
