# Pinned, self-verified ptrace golden INFO logs for the prefix-depth ratchet

Date: 2026-08-06 · Agent: hermit-verify · Task: `golden-logs-for-prefix-depth-ratchet`

## Question

The prefix-depth metric measures every backend against a ptrace **golden** INFO
log: how many messages `Y` of the golden's `Z` a backend reproduces before
diverging. That metric is only as trustworthy as its reference. So: can we build
goldens that are **pinned** (SHA + flags + host) and **proven deterministic
against themselves**, and what is `Z` per guest?

A nondeterministic golden is worse than no golden — it makes every backend look
divergent and the resulting `Y/Z` measures our own noise.

## Method

`capture_goldens.py` runs each guest **twice** under the ptrace backend, strips
the one irreproducible datum (the leading wall-clock timestamp), and requires the
two normalized logs, the guest stdout, and the exit status to be identical. A
guest that fails is recorded `NOT-A-GOLDEN` and **no golden file is written**.
`NO-RESULT` (timeout or empty log) is never a pass.

`Z` = count of `COMMIT` messages, which equals the scheduler turn count hermit
reports itself (`the hermit scheduler ran N turns`).

**Everything is stored in this directory, in-repo.** The two prior attempts at
this work left their evidence in `/tmp` (`/tmp/dbi-compat-slot86`,
`/tmp/kvm-compat-expansion-0ad5ad5`) and it is gone.

### The pin

| field | value |
|---|---|
| hermit SHA | `4c70658e785834737cbe1524f77330c781a6f5ea` |
| profile | release |
| backend | ptrace |
| flags | `--log info --log-file <path> run --base-env minimal -e LC_ALL=C -e TZ=UTC --strict` |
| guest env | `minimal` + `LC_ALL=C`, `TZ=UTC` |
| host | Linux 6.18.39, AMD EPYC 9D85, 316 cores |

Binary sha256 and full host facts are in `manifest.json`.

## Results

All 7 rungs produced a self-verified golden.

| guest | argv | Z (commits) | DETLOG msgs | log lines | verdict |
|---|---|---|---|---|---|
| `true` | `/bin/true` | 5 | 68 | 114 | GOLDEN |
| `echo` | `/bin/echo hermit-golden` | 6 | 82 | 131 | GOLDEN |
| `cat-hostname` | `/bin/cat /etc/hostname` | 7 | 98 | 151 | GOLDEN |
| `wc-passwd` | `/usr/bin/wc -l /etc/passwd` | 7 | 92 | 145 | GOLDEN |
| `head-passwd` | `/usr/bin/head -1 /etc/passwd` | 7 | 90 | 143 | GOLDEN |
| `sh-pipeline` | `/bin/sh -c '/bin/echo a \| /usr/bin/wc -c'` | 30 | 493 | 661 | GOLDEN |
| `sh-loop-exec` | `/bin/sh -c 'for i in 1 2 3; do /bin/echo $i; done'` | 45 | 619 | 857 | GOLDEN |

Probed but outside the ladder: `id` (Z=14, 325 lines) and
`getent passwd root` (Z=8, 177 lines), both deterministic here — see below.

## The gate is bracketed, because 7/7 first-try is a suspicious result

A gate that cannot refuse anything yields exactly this output. `bracket_gate.py`
plants each divergence class through the **same** `classify()` the capture uses
(one verifier, both consumers — not a reimplementation that could drift):

```
unmutated (positive control)      -> GOLDEN
digit-flip in a DETLOG line       -> CAUGHT
one line dropped                  -> CAUGHT
one line inserted                 -> CAUGHT
trailing truncation               -> CAUGHT
exit code differs                 -> CAUGHT
guest stdout differs              -> CAUGHT
empty log is NO-RESULT not a pass -> CAUGHT
7/7 caught; positive control held
```

**A live negative control was attempted and failed to be negative.** Prior work
(`impl-dbi-golden-log-comparison`) found ptrace `id` nondeterministic: run 1 did
NSS `socket/connect/sendto/poll`, run 2 went straight to `write/close`, a 14,838
message difference. That did **not** reproduce — `id` here is stably
deterministic with no NSS traffic at all. This is a **narrowing, not a
contradiction**: the prior finding was explicitly a *cold-cache* effect, the
cache is warm now, and `--base-env minimal` removes the environment that steered
the lookup. `/proc/uptime`, `/proc/loadavg`, `/proc/self/stat`,
`/proc/interrupts` and `getent` were also probed and are all virtualized and
deterministic. Since no available live guest is nondeterministic, the mutation
bracket is the only thing that proves the gate works.

## `Z` is a function of the ENVIRONMENT, not just the guest

This is the load-bearing measurement of this experiment. Same guest
(`/bin/true`), same binary, two environments:

| env | log lines | host-path (`lu-parity`) mentions | **Z** |
|---|---|---|---|
| inherited host env | 177 | 45 | **14** |
| `--base-env minimal` | 114 | 0 | **5** |

hermit needs `LD_LIBRARY_PATH` for its **own** runtime on this host (its release
binary reports `libunwind-x86_64.so.8 => not found` without it), but the guest
inherits that variable, its loader probes those directories, and every probe
lands in the DETLOG as an absolute host path. `--base-env minimal` keeps it out
of the guest.

**Consequence: a `Y/Z` ratio quoted without its env pin is meaningless** — the
denominator nearly triples on the same guest. Any ratchet consuming these
goldens must pin the env identically for the backend arm.

## The two inherited findings

**1. The presentation difference — EXCLUDED deliberately, and the premise is
partly refuted.**

The handed premise was: *`backend_banner` is `Some` only for KVM/LiteInst and
hermit-cli then re-emits `out1.stdout`/`out1.stderr` after verify; ptrace has no
banner and does not re-emit.*

Verified at `hermit-cli/src/bin/hermit/run.rs:2809-2820`:

- **Confirmed:** the banner is `Some` only for `Backend::Kvm` and
  `Backend::Liteinst`, printed to **stderr** as `:: Backend: ...`.
- **Refuted:** "ptrace ... does not re-emit". The
  `stdout().write_all(&out1.stdout)` / `stderr().write_all(&out1.stderr)` calls
  are **outside** the `if let Some(backend_banner)` block, so the re-emit is
  **unconditional for every backend**. Measured under ptrace `--verify`: the
  guest marker appears exactly **once** on stdout and there are **zero**
  `:: Backend:` lines.

So the real presentation delta is one **stderr-only** line on KVM/LiteInst — not
a stdout re-emit asymmetry.

**Decision: exclude, by construction, for two independent reasons.** The golden
is the INFO log captured via `--log-file` (a separate stream from stderr) plus
guest stdout and exit status; **stderr is deliberately not part of the golden**,
precisely because it carries the backend-specific banner. And capture uses plain
`run` without `--verify`, while the banner site is only on the verify path, so no
banner is emitted during capture at all. No product change was made — this is an
exclusion, recorded here so nobody later "fixes" a divergence that our own output
path invented.

**2. KVM's comparison is weaker — carry this label wherever KVM numbers appear.**

KVM reports its internal trace order as nondeterministic and compares
**output + exit only**. A KVM "pass" therefore does **not** mean what a ptrace
pass means: it is not evidence of INFO-log prefix agreement. Any table mixing
them must say so per row rather than in a footnote. KVM also could not be
measured on this host at all (startup livelock).

## Coordination with the preload-vs-ptrace env-equalisation P0

These goldens adopt that work's convention verbatim
(`--base-env minimal -e LC_ALL=C -e TZ=UTC`), so they stay comparable after it
lands. Two facts from it that bound what this ratchet can claim:

- Equalising env blocks does **not** converge ptrace-vs-DBI stack hashes
  (0/36 shared before and after) — env is not the cause of cross-backend stack
  divergence.
- DBI adds 4 env vars / +2112 bytes, one of which embeds the guest path, so the
  DBI env delta **varies per cell**. A ratchet comparing DBI against these
  goldens must expect env-derived DETLOG differences that are not backend bugs.

## Not done, and why

- **demo05 (QEMU Linux boot) — unreachable here**, two independent blockers:
  `qemu-system-x86_64` is not installed, and `hermit/demos/05-qemu-busybox.sh`
  downloads a kernel image, which the per-destination egress allowlist blocks.
  The ladder therefore tops out at `sh-loop-exec` (Z=45). Reaching demo05 needs
  a host with qemu and a locally provided `KERNEL_IMAGE=`.
- **No backend arm was run.** This task produces the *reference* only; `Y` is not
  measured here. The goldens plus `manifest.json` are what a comparator consumes.
- **KVM golden**: impossible on this host (startup livelock).

## Reproduction

```bash
cd experiments/golden-logs-prefix-depth_20260806
python3 capture_goldens.py \
  --binary <checkout>/target/release/hermit \
  --hermit-sha 4c70658e785834737cbe1524f77330c781a6f5ea \
  --libdir <dir containing libunwind>
python3 bracket_gate.py     # negative control; must report 7/7 caught
```

The binary must be built from the pinned SHA with
`PKG_CONFIG_PATH`, `LIBRARY_PATH` and `LD_LIBRARY_PATH` all set to the libunwind
directory (`LIBRARY_PATH` is required for the **link** step; omitting it fails
with `rust-lld: unable to find library`, which reads as a code error and is not
one).

## Files

| file | contents |
|---|---|
| `capture_goldens.py` | the harness; owns the single `classify()` decision |
| `bracket_gate.py` | negative control; mutation-brackets `classify()` |
| `manifest.json` | full pin: SHA, binary sha256, flags, env, host, per-guest Z |
| `results.csv` | one row per guest |
| `goldens/*.log` | the 7 self-verified goldens (260 KB total, largest 99 KB) |
