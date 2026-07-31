# Wheezy reconstruction pilots: `hello` and `hostname`

Run date: 2026-07-29. This is a reconstruction at Debian Snapshot
`20190301T000000Z`, not an exact replay of the paper's unavailable mirror.

## 2026-07-30 continuation: blocker fixed, pilot reproducible

The legacy-vsyscall blocker below is fixed by Reverie
[PR #287](https://github.com/rrnewton/reverie/pull/287) at commit
`8d3a041001fa929ba9e2a898781c3b2d31dab09a`. At a legacy-vsyscall seccomp
stop, Reverie now installs `orig_rax = -1` and the Tool-provided result before
resuming. Linux can then perform its own synthetic `ret` without Reverie
restoring the fixed-page RIP or single-stepping the caller's first instruction.

The minimized fixed-address `time()` regression timed out before the fix after
120 seconds and 432,359 repeated interceptions. With the fix it returns the
Tool-provided value and passes in 0.01 seconds. The package-scoped Reverie gate
passes 112 unit tests, the new integration test, and one doctest; Clippy with
warnings denied and rustfmt also pass. The full workspace gate is blocked by
the slot's unrelated, uninitialized `third-party/dynamorio` submodule.

Hermit PR #1160 plus exact Reverie PR #287 was rebuilt as release binary
SHA-256 `2f13daf57ac5186fffadefe956729f363f98c8fdbd381976b1a4ad447b47b948`.
That binary starts Wheezy Bash at L2 (ptrace backend, `--log=info`, no
determinism relaxations): both runs contain 1,285 messages and the verifier
reports no substantive differences.

Two independent fresh-root package builds then ran with the ptrace backend,
`--strict`, `--log=warn`, and no determinism relaxations. They completed in
276.920 and 274.738 seconds. Both produced a 68,896-byte
`hello_2.8-2_amd64.deb` with SHA-256
`55306cc92707186360357cc92652217f5650c4a22f2105328a6713410a7ef05f`;
`cmp` returned 0. This establishes the reconstructed pilot at L1 plus
bitwise-identical Debian-package output across independent roots.

An earlier in-process `--verify` attempt was intentionally bounded at 1,200
seconds. Run 1 completed the package after 152,421 scheduler turns; Run 2 was
still making normal build progress at turn 135,131 when the outer deadline
expired. The nested detached runner had created a separate session, so the
outer timeout did not reap it; the exact task-owned process group was stopped
and no processes remained. This is a harness timeout/reaping defect, not a
Hermit divergence. The independent-root result above is the package-level
reproducibility evidence; an L2 full-build trace still needs a larger
single-owner bound or the calibrated experiment runner.

### Second target: `hostname 3.11`

The next bounded target was `hostname`, selected directly from the 8,688-name
manifest. Its Wheezy source tuple is `hostname 3.11`, and its only declared
build dependency is `debhelper (>= 5)`. Preparation initially exposed a host
compatibility issue: ancient Wheezy Apt could open `fwdproxy:8080`, but its
HTTP connection was reset when made directly from the rootless container.
An exact-PGID, temporary IPv6-loopback `ncat` bridge kept the external proxy
connection in the host process; Apt then fetched the pinned indices and
prepared the package successfully. The bridge was stopped immediately after
preparation.

Two independent fresh-root builds ran with the same PR #1160 plus Reverie
PR #287 release binary, ptrace backend, `--strict`, `--log=info`, and no
determinism relaxations. They completed in 67.269 and 66.858 seconds. Each
trace has 227,797 lines, 37,031,749 bytes, and a final scheduler commit at
turn 11,537. After removing only the host wall-clock RFC 3339 prefix emitted
by the logger, both complete traces are byte-identical with SHA-256
`463bdc1bdc9c310a5f9ee48aff607aee4c666743f78bbb6f2a9d42fb5eb9df4c`.

Both runs produced a 15,048-byte `hostname_3.11_amd64.deb` with SHA-256
`303aa2e61061d8a4f981c92a2d8073d952e53d345f0927e20fc463827de1d978`;
`cmp` returned 0. This establishes L2-equivalent execution-log determinism
across fresh roots as well as bitwise-identical package output for the second
target.

These results cover two packages, not the complete 8,688-name
Dettrace-reproduced target. Scaling remains gated on the calibrated parallel
experiment runner and on landing the #1160/#287 dependency chain.

## 2026-07-30 batch: sixteen additional target packages reproduce

Continued on the same fixed dependency chain, rebuilt as a fresh release binary
from the stacked branch tip `f3b29a1f` (Hermit #1168, which git-pins Reverie
PR #287 `8d3a041` via `Cargo.lock`). Binary SHA-256
`f170c29d3c317e9b3d711c7c9da48f5bfaa8dbc41f207e690ab9909160573972`,
`hermit 0.1`; `hermit run --strict --verify -- /bin/echo` reports "Determinism
verified" (L2 smoke). `hostname 3.11` was reconfirmed on this fresh binary and
reproduced the pilot's exact `.deb` (`303aa2e6…`), i.e. three independent builds
(the pilot's two roots plus this binary) now agree on that artifact.

Sixteen new target packages were built this run; all sixteen reproduce
byte-identically across two independent roots (16/16, no divergences).

Method: each package is prepared once from snapshot `20190301T000000Z`, then
built in **two independent fresh roots** with `hermit run --strict` (ptrace
backend, `--base-env=minimal --network=local`, no `--verify`, no determinism
relaxations); the produced `.deb` SHA-256 is compared across the two roots.
Byte-identical `.deb` across independent roots is L1 plus reproducible package
output. All nine new packages are drawn directly from
`asplos20_dettrace_reproduced_target.txt`.

| Source package | Version | `.deb` bytes | `.deb` SHA-256 | Result |
| --- | --- | --- | --- | --- |
| figlet | 2.2.5-2 | 167998 | `045c3d8c11f084b2354f55279df35397093ed1076fb8ad14c470430859618f7b` | REPRODUCIBLE |
| tree | 1.6.0-1 | 43540 | `34b810e77d6411475a459034525741a870e1aaa2c9c972092b36b6384ddd6f7a` | REPRODUCIBLE |
| pv | 1.2.0-1 | 33700 | `e145b58d9f9e8f1a43bf58312f2483c9d8e3f85b075ea984c31c87fdff6888ae` | REPRODUCIBLE |
| sensible-utils | 0.0.7 | 8844 | `1476bad5c5addccf572428003a7b861f011d522c7f0dbfed54e816f9f62a0c02` | REPRODUCIBLE |
| acpidump | 20100513-3.1 | 20334 | `31d433d1baddb152ad451a96372d283fb220f39df02c94dfe67ab65a36edeb9b` | REPRODUCIBLE |
| admesh | 0.95-12 | 33646 | `364eca889d16c44bdb7e8b0fe10003593d09605618a5d35bf4e679b641c7684d` | REPRODUCIBLE |
| ascii2binary | 2.14-1 | 20796 | `9a02e0333d2aad7530f0db7b567960ef0f269ab8b19d549f926ef3030a99590c` | REPRODUCIBLE |
| whois | 5.1.1~deb7u1 | 61146 | `bfa66b142e3631a18e9d4763790532eb9065f7e0101818adcc6ea03f3dac556d` | REPRODUCIBLE |
| units | 1.88-1 | 153034 | `b708a4c0d5c8f3f5c148ef14427baa445a129d5882d8cd1467abafa48e78517e` | REPRODUCIBLE |
| moreutils | 0.47 | 61884 | `7c5b91045a50e81d4dec485cfdf904551f353c6c4ba58d7cc08b529e730c5e5d` | REPRODUCIBLE |
| ahcpd | 0.53-1 | 31656 | `b0341a533dcbf2e6835fdcbf3ff5ca1720389ca7566df2ba3fe7644d7c3a1519` | REPRODUCIBLE |
| and | 1.2.2-4.1 | 29736 | `6439baa040ac0e37d8ad65e431bc9ecd4acc3ac2aa6ca77591d2f641bf7b5679` | REPRODUCIBLE |
| as31 | 2.3.1-6 | 27508 | `0240ea9915b0c3f6e2de53e03201597f50407072a1d225655363844ba533c092` | REPRODUCIBLE |
| colortail | 0.3.3-1 | 25434 | `1929ffdb3a6966a4828464f4274ec5f96f53035b059259c21ae4ae9a31908d66` | REPRODUCIBLE |
| dtach | 0.8-2.1 | 15156 | `111a85bd0f3c7d1d0e236aaf5f9e0bce4d9fadcbe98b410e01cc22da4dc6ee91` | REPRODUCIBLE |
| fdupes | 1.50-PR2-4 | 20652 | `5c6f54fdac1e09b0ac9b0740961dd4e808a16d0fdcec741795b7ea96e2a90d0a` | REPRODUCIBLE |

The sixteen span several ASPLOS'20 categories: system/text utilities (hostname,
tree, pv, sensible-utils, moreutils, colortail, fdupes, ascii2binary), C
network/daemon tools (ahcpd, and, dtach), assembler/toolchain (as31),
scientific/CAD (units, admesh), hardware/ACPI (acpidump), and a display banner
generator (figlet). Cumulative reproduced target subset: **hello, hostname, and
the sixteen above = 18 of 8,688** (all builds attempted this run reproduced;
0 divergent). The remaining sweep is still gated on the calibrated parallel
experiment runner and on landing the #1160/#287/#1168 dependency chain.

## 2026-07-31 batch: seven more reproduce; first shallow (mtime-only) divergence

Same fixed binary (`f170c29d…0573972`, Hermit #1168 tip `f3b29a1f` git-pinning
Reverie #287 `8d3a041`), same two-independent-root method (`hermit run --strict`,
ptrace, `--base-env=minimal --network=local`, no `--verify`, no relaxations),
compared by `.deb` SHA-256 across roots. All eight packages are drawn directly
from `asplos20_dettrace_reproduced_target.txt` and were selected as low-build-dep
compiled (arch != `all`) targets mined from the pinned `Sources.gz`.

Seven reproduce byte-identically across two independent roots (each verified
per-`.deb`, including all sub-packages):

| Source package | Version | `.deb`(s) | Result |
| --- | --- | --- | --- |
| aa3d | 1.0-8 | `48fb1988…` | REPRODUCIBLE |
| acpi | 1.6-1 | `6dc92d9b…` | REPRODUCIBLE |
| acpitool | 0.5.1-3 | `368c0dbc…` (+ `-dbg` `88c118a1…`) | REPRODUCIBLE |
| anacron | 2.3-23 | `c0b889c4…` | REPRODUCIBLE |
| aoetools | 32-2 | `2cbbd68c…` | REPRODUCIBLE |
| apparix | 07-2 | `712e8e93…` | REPRODUCIBLE |
| argtable2 | 12-1 | `dfe65302…` (+ `-dev` `ff9c96be…`, `-docs` `445db680…`) | REPRODUCIBLE |

### First divergence: `a52dec 0.7.4-16` — shallow, timestamp-only

`a52dec` is the first DRB-target that does **not** fully reproduce under Hermit.
It is a **shallow, metadata-only** miss, **not** a compiled-output bug. Both of
its binary packages differ across the two roots:

```text
liba52-0.7.4       A=5a4a213e…  B=757ae651…
liba52-0.7.4-dev   A=8d7bb67a…  B=b530c5b0…
```

Full byte-level analysis:

- `data.tar` **content is byte-identical** across roots — all 15 entries,
  including the compiled `usr/lib/liba52-0.7.4.so`, have identical SHA-256.
- gzip headers are identical (MTIME=0; dpkg's reproducible gzip).
- The **only** divergence is **two tar-header MTIME fields**, each off by
  exactly **+1 second**:
  - `data.tar` `./usr/share/doc/liba52-0.7.4/HISTORY`: `1767229783` vs `1767229782`
  - `control.tar` `./postrm` (maintainer script): `1767229917` vs `1767229916`

Those mtimes equal Hermit's `--strict` **virtual wall-clock** (the ~2026-01-01
deterministic epoch) at file-write time. So the payload is fully deterministic;
the `.deb` differs only because a virtual-clock reading captured into two file
mtimes drifted by ~1 s between two otherwise-identical runs.

**Root class.** `a52dec`'s build runs a full autoconf `./configure` — a
fork/wait-heavy, multi-process phase. Async SIGCHLD / IO-completion ordering
shifts *committed* virtual time (the same class root-caused in
`fix-parallel-make-determinism` — SIGCHLD/vtime-commit — and
`scheduler-vtime-jump-past-unproductive-pollers`), so late build steps
(`dh_installdocs HISTORY`, `dh_installdeb` of `postrm`) capture a virtual-clock
mtime that drifts ~1 s across runs. dettrace reproduces `a52dec` because its
virtual time is a **pure function of the deterministic schedule** — no
host-timing leak into committed time. This is a genuine Hermit-vs-dettrace gap
but narrow: the fix is to eliminate the vtime-commit host-timing channel
(existing open scheduler work), or to clamp build file mtimes. It is not a new
P0 content divergence.

### Cumulative

Fully byte-identical reproduced target subset: hello, hostname, the 2026-07-30
sixteen, and the seven above = **25 of 8,688**. Plus `a52dec`, the 26th package
built and the first shallow (mtime-only) divergence. The full sweep remains
gated on the calibrated parallel-experiment runner and on landing the
#1160/#287/#1168 dependency chain.

## 2026-07-31 batch 2: twelve more built; shallow ±1 s mtime class recurs

Same fixed binary (`f170c29d…0573972`, Hermit #1168 tip `f3b29a1f`), same
two-independent-root method (`hermit run --strict`, ptrace,
`--base-env=minimal --network=local`, no `--verify`, no relaxations), `.deb`
SHA-256 compared across roots. This batch was selected mechanically from the
pinned `Sources.gz`: DRB targets with `Architecture` exactly `any` and a single
`Build-Depends` (typically just `debhelper`) — the simplest compiled utilities —
skipping the 26 already built. Twelve prepared and built cleanly.

**Eight reproduce byte-identically across two independent roots:**

| Source package | Version | `.deb` | Result |
| --- | --- | --- | --- |
| athena-jot | 9.0-5 | `6e91b79b…` | REPRODUCIBLE |
| atsar | 1.7-1 | `687aee80…` | REPRODUCIBLE |
| autoclass | 3.3.4-2 | `77eac335…` | REPRODUCIBLE |
| autolog | 0.41-2 | `95df7fbf…` | REPRODUCIBLE |
| avce00 | 2.0.0-2 | `16f3c21a…` | REPRODUCIBLE |
| babeld | 1.3.1-1 | `ee09b1ef…` | REPRODUCIBLE |
| beav | 1:1.40-18 | (byte-identical) | REPRODUCIBLE |
| bing | (portable) | `3340802c…` | REPRODUCIBLE |

**Four are shallow-divergent — the identical single-file ±1 s tar-header mtime
class as `a52dec`, not a content bug.** For every one, the *decompressed*
`data.tar`/`control.tar` payload is byte-identical across roots (same extracted
content and same tar metadata for every entry except one), and exactly **one**
file's tar-header `mtime` differs by ±1 second:

| Source package | Version | Differing entry | mtime delta (B−A) |
| --- | --- | --- | --- |
| bdfresize | 1.5-6 | `data.tar ./usr/bin/bdfresize` | +1 s |
| bible-kjv | 4.26 | `data.tar ./usr/bin/bible` | +1 s |
| binfmtc | 0.17-1 | `control.tar ./postinst` | −1 s |
| bison++ | 1.21.11-3 | `data.tar ./usr/share/bison++/bison.h` | +1 s |

These deltas were invisible to `tar -tvf` (minute-granularity display) and only
surfaced by comparing the raw tar byte streams: the first differing byte falls
inside the 12-byte octal `mtime` field of one header (offset 136–147).
Confirmation for each package: identical content-payload SHA-256 across roots
with a single mtime-only header delta.

The mtimes equal Hermit's `--strict` virtual wall-clock at file-write time, so
this is the same root class documented for `a52dec`: residual virtual-time
nondeterminism in fork/wait-heavy build/install steps (SIGCHLD/vtime-commit +
`scheduler-vtime-jump-past-unproductive-pollers`) lets one file land on second
`N` in one root and `N±1` in the other. Note this batch tilted toward the
divergence (4/12) versus batch 1 (1/8): `debhelper`-stamped `install`/`dh_*`
steps that set a file's mtime to "now" are exactly what this channel perturbs,
and these minimal single-build-dep packages are dominated by that step.
dettrace reproduces all four because its virtual time is a pure function of the
deterministic schedule. The fix is the existing open scheduler work
(eliminate the vtime-commit host-timing channel) or clamping build file mtimes;
it is **not** a new P0 content divergence.

### Cumulative (through 2026-07-31 batch 2)

- **Fully byte-identical reproduced:** 25 (through batch 1) + 8 here =
  **33 of 8,688**.
- **Shallow ±1 s tar-mtime divergences (metadata only, payload identical):**
  `a52dec` + `bdfresize`, `bible-kjv`, `binfmtc`, `bison++` = **5 total**, every
  one the same single-file virtual-wall-clock class.
- **38 of 8,688 target packages built end-to-end** under the fixed binary; every
  divergence observed so far is the shallow mtime class, none a compiled-content
  bug.

Evidence roots: `experiments/…/ignored/tworoot/{athena-jot,atsar,autoclass,`
`autolog,avce00,babeld,beav,bing,bdfresize,bible-kjv,binfmtc,bison++}/{A,B}`;
batch result log `ignored/batch-results-1785506513.txt`. The full sweep remains
gated on the calibrated parallel-experiment runner and the #1160/#287/#1168
dependency chain.

### Methodology note: in-process `--verify` is unusable for full package builds

`rebuild.sh hermit <pkg>` uses one shared root and runs `dpkg-buildpackage`
twice in-process via `--strict --verify`. This **structurally** reports
`:: Failure: nondeterministic` on any non-trivial package, and it is not a guest
determinism failure. Reconfirming `hostname` this way produced the correct
`.deb` (`303aa2e6…`) yet the verifier flagged a mismatch whose **first**
divergence is `newfstatat("build-stamp")` = `Err(ENOENT)` in run 1 versus
`Ok(S_IFREG)` in run 2, followed by `hostname.o` and the `hostname` binary. The
cause is that run 1 leaves its build products in the shared root, so run 2 sees
them already present — `dpkg-buildpackage` is not idempotent on a shared root.
The robust reproducibility signal is therefore two **independent** fresh roots
compared by `.deb` hash (and, as in the hostname pilot, timestamp-stripped
DETLOG), which is the method used for the table above. A local, gitignored
helper (`ignored/two_root_hermit.sh`) implements it; no product or `rebuild.sh`
change was made.

## Inputs

- Source tuple: `hello 2.8-2` (`pool/main/h/hello`).
- The `.dsc`, upstream tarball, and Debian tarball match all three SHA-256
  values in `reconstruction.json`.
- Hermit: ptrace backend at PR #1160 commit
  `91bf22088c5665ffe632768136ea8f16cbcab90b`; release-binary SHA-256
  `1699e7b9a08804f3f99df4a7d0494d0c911d0dfdec4002a1e2d5ceb76efe060e`.
- Dettrace source: `3c27bce648fecc1495721d30bd35030df6743e3d`, the
  ASPLOS'20 artifact gitlink.

## Control

The network-disabled Podman control completed `dpkg-buildpackage -uc -us -b`.
It produced `hello_2.8-2_amd64.deb` with SHA-256
`b8268de958d45105960d216d931ebad0d578f8c47d1bbab739885ffceca6fa10`.

## Hermit result: P0 legacy-vsyscall return divergence

The rootfs/chroot plumbing first passed a small `echo` command at L2 with
`hermit run --strict --verify`. The package run used the same traced chroot,
default logging, local-only networking, and no determinism relaxations.

The package run did not reach compilation. After the Wheezy Bash exec, it
entered a high-rate sequence of `time(NULL)` events. In the first diagnostic:

- completed time syscall number: `4,144,696`;
- DetCore thread: `dtid 3`;
- RCB counter: `57,054` across the sampled loop;
- verify-log size: `4,243,267,435` bytes after about 3 minutes 15 seconds;
- output rate: approximately 20 MB/s;
- build outputs: no object or Debian package was produced.

Post-stop sampling showed virtual time advancing exactly five seconds per
500,000 calls, from `1,767,225,600` at syscall 94 through `1,767,225,645` at
syscall 4,500,093. Initially that looked like application polling, so the P0
classification was retracted and a fresh run was started.

The rerun proved that interpretation wrong. Reading the already-traced
process's stack showed the vsyscall return address in glibc at
`0x7ffff709efed` and its caller's return address in Bash at `0x420324`.
Disassembly identifies:

```text
0x42031f  call time@plt
0x420324  mov 0x28(%rsp),%edx
```

This is Bash startup's single call from `main`, not a polling loop. Across more
than 6.5 million events, every post-syscall register record still had RIP at
the legacy vsyscall entry `0xffffffffff600400` and RCB count `57,054`.
DetCore changes the emulated return value but never returns control to
`0x420324`. The fresh rerun reproduced the same zero-control-flow-progress
failure and was terminated before another unbounded multi-gigabyte log formed.

The pinned Dettrace oracle completes this exact source tuple twice, while
Hermit cannot leave Bash startup. This is therefore a confirmed P0
Dettrace-success/Hermit-fail result in legacy-vsyscall `time()` return handling.

## Dettrace source oracle

The exact pinned source builds unmodified in the paper's Ubuntu 18.04 outer
environment; its source-built static binary has SHA-256
`f3a06944cbc0d09f591cda8c0794bb684932e92478218c59e96f084ab8861c48`.
That unmodified binary cannot launch on this newer host kernel:
the old startup check requires exactly four global vDSO function symbols, while
the host exports seven. The oracle continuation uses a recorded host adapter
that filters the discovered vDSO map to the same four functions Dettrace has
always replaced; it does not change their replacement bytecode or scheduling.

The adapted static binary has SHA-256
`8f603f539dd6b8174fb3d7be31a39a4f25795a41c225558a244e5ac9892a9681`.
Two fresh-root runs completed in 17.174 and 17.875 seconds. Their complete logs
are byte-identical (SHA-256
`2bf6188febf95bc7566dca9ecfba739bd94ddc101f70d6d9e3d3716649602759`),
including all 16 printed Dettrace counters. Their Debian packages are also
byte-identical, with SHA-256
`cc8f8fb8017093b17e7a70cfcfa4463879dbb631d6d3c9000d5617b921d46caf`.
