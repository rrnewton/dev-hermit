# KVM-vs-ptrace tty/winsize determinization: full detlog-depth verification

**Task:** `kvm-stdout-tty-winsize-divergence` (owner hermit-kvm).
**Date:** 2026-08-03. **Directive:** verify the tty/winsize fix to the owner's
full cross-backend standard — `--log INFO` match + `--detlog-stack` match +
`--detlog-heap` match against the ptrace reference, only `--strict` counts,
report stack/heap/INFO **separately** (which one diverges tells us where the
backend differs), and say "not measured" rather than infer.

## Question

Two claims must be checked at execution-trace depth, not just stdout:

1. **ptrace golden is host-width-independent.** The pre-fix ptrace golden leaked
   the real terminal WIDTH into `--strict` output (40-col vs 200-col ⇒ different
   bytes; `--verify` misses it because winsize is stable within one run). Does
   hermit#1445's detcore canonicalization (fixed 80×24 winsize + N_TTY termios,
   applied after the real ioctl) make the **entire detlog** width-independent?

2. **KVM matches the ptrace golden** at INFO / detlog-stack / detlog-heap depth
   under a real pty (isatty(1)==true), not just piped stdout.

## Exact versions measured

| Component | SHA | Role |
|---|---|---|
| hermit | `39e95cf8d48ae10710b57818b7d8a446c5995b8d` (PR #1445 head) | detcore winsize/termios canonicalization (ptrace + KVM) |
| reverie (Cargo git pin) | `d973a85b328610c14c41c39fa57495b9f77c3c90` | build dependency; **includes** #332 merge `c1355d17581216492d3d0101888a59fa2867c9b6` (`forward_terminal_ioctl`, verified present ×4) |

Binary: `worktrees/kvm/hermit/target/release/hermit` (release, built 10:12:51).
The reverie-kvm executor is **not on the ptrace code path**, so the ptrace result
is attributable solely to hermit `39e95cf8`. The pin includes both halves of the
coordinated fix, so the KVM measurement below is blocked only by host load, not by
a missing dependency.

## Method

`ignored/run_parity.py` runs the guest with stdout attached to a real pty
(`pty.openpty()` + `TIOCSWINSZ` to impose a host terminal size), so
`isatty(1)==true` and `TIOCGWINSZ` has a real answer, while the hermit `--log
info` trace goes to a separate `--log-file`. Command per cell:

```
hermit --log info --log-file <log> run --strict --backend <b> \
  --detlog-stack --detlog-heap -- /bin/ls ignored/lsdir
```

`lsdir/` = 30 short fixed filenames (file01..file30), chosen so column layout is
sensitive to terminal width. Comparison via `hermit log-diff` (`--skip-commit`
for DETLOG/data/memory-hash; timestamp-stripped `diff` for byte identity).

## Results (per cell, `--strict`)

### ptrace — MEASURED, PASS

| cell | stdout hash | rc | detlog lines |
|---|---|---|---|
| ptrace @ 40×24 | `a593ad56…71c7d` | 0 | 593 |
| ptrace @ 80×24 | `a593ad56…71c7d` | 0 | 593 |
| ptrace @ 200×50 | `a593ad56…71c7d` | 0 | 593 |

- **stdout-match (width independence):** all three widths byte-identical
  (`sha256 a593ad56…`). Output is multi-column tab-separated ⇒ `isatty(1)==true`
  correctly detected on the pty. Pre-fix these would differ by width.
- **INFO-match:** `hermit log-diff --skip-commit --strip-lines ptrace_c40 vs
  ptrace_c200` ⇒ **"no substantive differences found"** (676|676 messages,
  675|675 INFO, 593|593 DETLOG).
- **detlog-stack-match / detlog-heap-match:** the 593 DETLOG lines (which include
  the stack/heap memory-hash entries emitted under `--detlog-stack
  --detlog-heap`) are **byte-identical** across 40 vs 200 col after timestamp
  stripping. The `ioctl(1, TCGETS)` and `ioctl(1, TIOCGWINSZ)` calls both return
  `Ok(0)` and the canonicalized winsize is written, so no host width reaches the
  trace.

**Conclusion (ptrace):** the fix makes the ptrace golden **fully
host-width-independent at execution-trace depth**, not just at stdout. Bug #2 (the
ptrace golden size-leak) is confirmed fixed and measured at the required depth.

### KVM — NOT MEASURED (blocked by host load)

KVM wedges at guest-startup rendezvous under high host load (repeatedly: KVM
`/bin/true` → exit 124 with ~470 concurrent `hermit` processes and load ~110–276;
ptrace `/bin/true` → exit 0 under the same load). Both pty `ls` cells (80, 200
col) hit the 90 s cap without producing output. This is the known load wedge
([[kvm-python-examples-busywait-gate-timeout]] finding #2), **not** a product
divergence and **not** a missing dependency (the pin includes #332).

Therefore KVM stack-match / heap-match / INFO-match vs the ptrace golden are
**not measured**. They must be run on a quiet host or the privileged `/dev/kvm`
CI lane against this exact binary.

### Addendum 2026-08-03 (hermit-kvm, follow-up quiet-window check)

A follow-up session re-checked the wedge directly with a paired load canary
(primary `hermit` binary `6f0c26de`, main; a pure host-load probe, not the pinned
measurement binary). **The wedge reproduced twice, non-transiently:**

| trial | load1 | `hermit`-bin procs | `kvm /bin/true` | `ptrace /bin/true` |
|---|---|---|---|---|
| 1 | 115.7 | 94 | **exit 124** (timeout 60s) | exit 0 |
| 2 | 107.0 | 56 | **exit 124** (timeout 45s) | exit 0 |
| 3 | 73.9 | 56 | **exit 124** (timeout 45s) | exit 0 |

Trial 3 (2026-08-03 11:50) wedges at **load 74** — meaningfully *lower* than
trials 1–2 (~107–115), same 56 procs — so load-average headroom is NOT a reliable
quiescence signal. Gate strictly on the canary (`kvm /bin/true` == 0); do not
infer readiness from a "lower" load number.

**Refined threshold — materially changes retry guidance:** the wedge is *not*
gated at the ~470-process level recorded above. It reproduces at **56–94**
concurrent `hermit` processes / load ~107–115 (≈36 %/core on a 316-core host,
635 GB free). `/dev/kvm` is world-accessible (`crw-rw-rw-`), so this is pure
scheduler contention at the KVM guest-startup rendezvous, not a permission or
memory gate. Consequence: "several agents idle" is **not** a sufficient quiet
signal — a future run must confirm quiescence with the canary itself
(`kvm /bin/true` must return 0) before spending a rebuild, rather than inferring
it from agent-idle counts or aggregate load headroom.

The pinned measurement binary (`worktrees/kvm/hermit/target/release/hermit`) was
reclaimed with slot `kvm`; the rerun below therefore requires a rebuild at
hermit `39e95cf8` / reverie pin `d973a85b` first. The harness itself
(`ignored/run_parity.py`, `ignored/lsdir/`, ptrace `results/`) is intact and
turnkey.

## Reproduction (KVM side, when load clears)

```
H=worktrees/kvm/hermit/target/release/hermit   # hermit 39e95cf8 / reverie d973a85b
cd experiments/kvm-tty-detlog-parity_20260803/ignored
python3 run_parity.py $H kvm 80 24 results kvm_c80 -- /bin/ls lsdir
python3 run_parity.py $H kvm 200 50 results kvm_c200 -- /bin/ls lsdir
# then, separately:
$H log-diff --skip-commit --strip-lines results/ptrace_c80.log results/kvm_c80.log
$H log-diff --skip-detlog             results/ptrace_c80.log results/kvm_c80.log   # schedule/COMMIT
diff <(sed -E 's/^[0-9T:.Z-]+ +//' results/ptrace_c80.log | grep DETLOG) \
     <(sed -E 's/^[0-9T:.Z-]+ +//' results/kvm_c80.log    | grep DETLOG)
```

Report stack-hash / heap-hash / INFO / COMMIT lines separately; guest stdout is
in `results/<tag>.out`, exit code in `<tag>.rc`.

## Scorecard-depth gap (relevant secondary finding)

The compat-envelope scorecard's `parity` column = guest **stdout hash** match
only, and its cells run **piped** (isatty=false on both backends). It therefore
structurally cannot see (a) this tty divergence, or (b) any execution-trace
divergence that still yields matching stdout. This generalizes the DBI
misattribution (run_dbi never received `--verify-allow`): a green `parity` cell is
NOT evidence of INFO/detlog-stack/detlog-heap parity. See
[[compat-envelope-scorecard-system]].
