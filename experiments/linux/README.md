# Deterministic Linux / QEMU under Hermit — durable reference

_Consolidated 2026-07-27. This directory is the single durable landing spot for
the "run a whole Linux VM (QEMU) as a Hermit guest" line of work: the milestone
`goal-qemu-linux-under-hermit`._

Hermit runs an **unmodified Linux kernel + userspace as a QEMU/TCG process under
the Hermit ptrace backend**, imposing deterministic thread scheduling, virtual
time, and I/O on QEMU's own host-level execution. The guest OS is reproducible
*by construction* (Hermit determinizes the emulator), not by replaying a
captured log.

Results here follow the Hermit **Communication Precision** and **Assurance
Level** rules: every claim names the exact backend, determinism level (L0–L4),
kernel, QEMU version, relaxations, and a commit SHA — never a bare "it works".

---

## 1. What has been accomplished (evidence-bound)

The assurance ladder (from `hermit/AGENTS.md`): **L1** = deterministic under
`run --strict`; **L2** = bitwise-identical repeat under `run --strict --verify`.

| Result | Level | Backend / relaxations | Hermit SHA | Kernel | Date | Wall | Primary evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Cold boot → initramfs marker → clean poweroff | **L2** (`--strict --verify`) — **historical, regressed on current main (see §2)** | ptrace; none | `fe97efd1` | Linux 6.13.2 guest | 2026-07-24 | — | roadmap table `experiments/linux-vm-roadmap_20260726/README.md:84`; no co-located per-message capture |
| `scx_rlfifo` sched_ext + 4 CPU workers | **L2** (`--strict --verify`) — **historical, not reproducible as-is** | ptrace; none | `0c419bf` | 6.13.2 guest | ~2026-07-24 | — | roadmap `README.md:87`; 1,340,266 messages/run; harness uncommitted, initramfs machine-local |
| Cold boot → initramfs marker → poweroff | **L1** (`run --strict`, no relaxations) — **reproducible** | ptrace; none; INFO log | `dd60278f` | 6.17.13 guest | 2026-07-23/24 | 166.486 s | [`STRICT_BOOT_20260723.md`](STRICT_BOOT_20260723.md); 167,521 syscalls; 987 turns; 0 clock failures |
| Cold boot + 5 guest programs + poweroff (compat profile) | **not L2** — reproducible virtual-time boot | ptrace; `--no-sequentialize-threads`, `--max-timeslice disabled` | `a85f015` | 6.17.13 guest | 2026-07-27 | ~19 s | [`SAMPLE_OUTPUT.txt`](SAMPLE_OUTPUT.txt) via [`demo.sh`](demo.sh) |
| Cold boot → marker → poweroff (compat, fixed icount) | **not L2** — reproducible virtual-time boot | ptrace; `--no-sequentialize-threads`, preemption disabled | `fb464663` (sha256) | 6.13.2 guest | 2026-07-22 | 13.25 s | [`QEMU_VIRTUAL_TIME_DIAGNOSIS.md`](QEMU_VIRTUAL_TIME_DIAGNOSIS.md); [`results.csv`](results.csv) row `virtual_minimal_fixed_icount` |
| Cold boot `run --verify` (no `--strict`) | **NOT L2** — fail-open diagnostic control | ptrace; unsupported-syscall passthrough | `54ff993` | 6.17.13 guest | 2026-07-26 | — | `linux-vm-roadmap_20260726/results.csv` row `minimal_fail_open_verify`: 1,130,696 messages/run, 852,621 DETLOG+COMMIT/run, "no substantive differences" |

**Reading the table honestly:**

- The **highest genuine determinism level ever reached** is **L2 for the minimal
  cold boot at `fe97efd` (2026-07-24)** and **L2 for `scx_rlfifo` at `0c419bf`**.
  Both are **historical**: the cold-boot L2 has **regressed on current main**
  (§2), and the sched_ext L2 used an uncommitted harness and a machine-local
  initramfs, so it is not reproducible from this tree as-is.
- The **highest reproducible-from-this-tree** result is the **L1 strict boot**
  (166.486 s, `dd60278f`) and the **compat-profile boots** (13–19 s), which are
  explicitly **not** determinism (L2) claims.
- The frequently-cited "**~1.13M messages compared, no differences**" figure is
  the **fail-open `run --verify` control** — run **without** `--strict`. It is a
  determinism-verify diagnostic, **not** a strict L2 result; `results.csv` labels
  it verbatim `"diagnostic only; not L2 because explicit --strict was absent"`.
- A "**337 s**" strict-`--verify` wall time appears in some dispatch notes but is
  **not present in any evidence file** in this workspace. It is consistent with a
  *projection* of ~2×166.486 s (run1 + run2) and should be treated as an estimate
  until a real strict `--verify` capture on a fixed main produces it.

## 2. Current-main regression (the honesty caveat)

On **current main** (`fb5f2014`, 2026-07-26 and later), literal `run --strict`
QEMU boot **fails before L1**. QEMU issues a
`seccomp(SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC, NULL)` capability
probe during startup; after **PR #644** made `--strict` *fail-closed* on
unsupported syscalls, Hermit rejects that probe as `Unsupported` and aborts.
See `linux-vm-roadmap_20260726/results.csv` row `minimal_strict_gate`
(`fail_before_L1`) and `native_seccomp_probe` (the native `EFAULT` shape of the
same probe).

Consequence: the **07-24 `fe97efd` cold-boot L2 cannot currently be reproduced**
on main. Restoring it requires teaching `--strict` to determinize (rather than
fail-close on) QEMU's `seccomp` probe. The compat-profile boots in this
directory are unaffected because they run without `--strict`.

Companion analysis: [`RELATED_WORK.md`](RELATED_WORK.md) (owned by task
`research-related-work-linux-determ`) surveys the prior art and situates these
results; the roadmap and open follow-ups live in
`../linux-vm-roadmap_20260726/`.

## 3. Exact provenance

**Guest / host software (from `SAMPLE_OUTPUT.txt`, `metadata.json`,
`STRICT_BOOT_20260723.md`):**

- **QEMU:** `10.1.0 (qemu-kvm-10.1.0-21.el9)`, `-accel tcg,thread=single -smp 1`,
  `-icount shift=0,sleep=off`.
- **Guest kernels tested:** Linux **6.13.2** (`-0_fbk15_hardened_0_g33ebba20e5e4`)
  for the 07-22/07-24 runs; Linux **6.17.13**
  (`-0_fbk0_crackerjackhost_0_g2b4321c50d79`) for the 07-23 L1 and 07-27 compat
  runs. The `6.17.13` string is **both** a guest bzImage and the host devserver
  kernel — do not conflate them.
- **Backend:** ptrace (the default, best-tested Hermit backend). A separate
  `impl-kvm-linux-boot` effort (slot `worktrees/linux/`) targets the KVM backend
  and is **out of scope** for this directory.

**Hermit revisions referenced above:** `fe97efd1` (07-24 L2, since regressed),
`0c419bf` (sched_ext L2), `dd60278f` (07-23 L1, 166.486 s), `a85f015` (07-27
compat demo capture), `fb464663…` (07-22 compat, sha256), `54ff993`/`fb5f2014`
(07-26 fail-open control + current-main regression).

**Binary/asset checksums** are embedded in `STRICT_BOOT_20260723.md` (Hermit
sha256 `1f49c621…`, kernel sha256 `e4b1c024…`, initramfs sha256 `f88ddaba…`) and
`metadata.json` inside the migration source (`fb464663…`, kernel `ce6aae16…`,
initramfs `10fc5872…`).

**Parent-workspace pins at consolidation time (2026-07-27):**

- Parent `hermit` gitlink: recorded `a23a235b` (#883, "Determinize
  /proc/interrupts/softirqs/modules"); primary checkout was momentarily ahead at
  `ee7964ea` from concurrent integration.
- Parent `reverie` pin: **`cad5d56e`**.
- ⚠️ Provenance correction: the dispatch brief cited "reverie `f1ed0280`", but
  `f1ed0280` is a **liteinst2 pin-bump** commit
  (`[coordinator] Bump liteinst2 dependency pin`), **not** a Reverie change.
  The correct current Reverie pin is `cad5d56e`.

## 4. Assets (large binaries — git-ignored, not committed)

Boot inputs live under `../../ignored/qemu-linux/` (git-ignored per the
binary/large-file policy — kernels and initramfs images are never committed):

- `bzImage` (~12.7 MB) — the guest kernel.
- `initramfs.cpio.gz`, `initramfs-autotest.cpio.gz`,
  `initramfs-interactive.cpio.gz` — busybox roots.
- `initramfs-scx.cpio.gz` (~11 MB) + `scx/`, `scx-logs/` — sched_ext demo assets.
- `boot.sh`, `boot-scx.sh`, `build-initramfs.sh`, `test-scx-all.sh` — asset
  builders (also mirrored in the source dirs below).

`demo.sh` is **self-contained** and needs none of these pre-staged: it builds a
minimal auto-poweroff initramfs from the host `busybox` and boots the host's
`/boot/vmlinuz` on the fly.

## 5. How to reproduce

**(a) Fast compat-profile boot demo (recommended first run, ~15–25 s, no assets):**

```bash
./experiments/linux/demo.sh          # captured run: SAMPLE_OUTPUT.txt
```

Expected: full kernel boot log, five guest programs, clean power-off,
`RESULT: PASS`, exit 0. This is a **virtual-time compatibility boot**, not an L2
claim.

**(b) Strict L1 boot (reproducible, ~166 s at `dd60278f`; current main regresses
— see §2):** exact command and inputs in
[`STRICT_BOOT_20260723.md`](STRICT_BOOT_20260723.md).

**(c) Strict L2 driver (boot oracle → `--strict --verify` determinism gate):**

```bash
QEMU_L2_PHASE_TIMEOUT_SECONDS=420 ./experiments/linux/strict_l2_test.sh
```

This is the authoritative L2 driver (from `hermit/tests/qemu-boot/`). On current
main it will surface the §2 seccomp regression; on a fixed tree it asserts
`":: Success: deterministic. Determinism verified."`.

**(d) Compat smoke test (marker + clock-failure checks):**

```bash
./experiments/linux/smoke_test.sh
```

## 6. Directory contents

| File | Role | Source |
| --- | --- | --- |
| `README.md` | This durable index | — |
| `demo.sh` | Self-contained compat boot demo | `hermit-experiments-migration_20260727/qemu-linux/demo.sh` |
| `SAMPLE_OUTPUT.txt` | Captured 07-27 compat boot (19 s) | same |
| `strict_l2_test.sh` | Authoritative `--strict --verify` L2 driver | `hermit/tests/qemu-boot/strict_l2_test.sh` |
| `smoke_test.sh` | Compat boot smoke test | `hermit/tests/qemu-boot/smoke_test.sh` |
| `qemu_init.c` | Minimal freestanding `/init` (marker + poweroff) | `…/shared-futex-verify_20260722/qemu_init.c` |
| `STRICT_BOOT_20260723.md` | L1 strict-boot report (166.486 s) + syscall diagnosis | `…/qemu-boot-debug/STRICT_BOOT_20260723.md` |
| `QEMU_VIRTUAL_TIME_DIAGNOSIS.md` | Why virtual-time boot needs the relaxed profile + fixed icount | `…/qemu-boot-debug/README.md` |
| `QEMU_BOOT.md` | Maintained user-facing QEMU boot guide | `hermit/docs/QEMU_BOOT.md` |
| `results.csv` | Mode matrix incl. `strict_current_main_ppoll` (166.486 s) | `…/qemu-boot-debug/results.csv` |
| `archived/qemu-linux-compat-harness_20260723.md` | Superseded 07-23 compat harness README (historical) | `experiments/qemu-linux/README.md` |
| `RELATED_WORK.md` | Prior-art survey (owned by `research-related-work-linux-determ`) | concurrent task |

**Companion / superseding locations** (not copied here, kept authoritative in
place):

- `../linux-vm-roadmap_20260726/` — current roadmap, open follow-ups, and the
  current-main strict regression evidence (`results.csv`).
- `experiments/qemu-linux/` — the original 07-23 self-contained harness (its
  README is archived here as historical).
- `hermit/docs/QEMU_BOOT.md`, `hermit/tests/qemu-boot/` — the maintained
  in-repo product docs and tests (source of truth for the drivers copied here).

## 7. Next milestone

Restore current-main strict L2 by determinizing QEMU's `seccomp` capability
probe under `--strict` (undoing the PR #644 fail-close for this specific probe),
then re-run `strict_l2_test.sh` to reconfirm the `fe97efd`-class bitwise repeat
and land a reproducible strict-L2 sched_ext fixture. Do not close
`goal-qemu-linux-under-hermit` without the required human verification.
