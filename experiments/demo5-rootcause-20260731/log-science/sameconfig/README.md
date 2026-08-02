# demo5 log-science: SAME-CONFIG wedge-vs-green INFO diff (skid point)

**Task:** `demo5-log-science-diff` (hermit-237). **Feeds:** `demo5-rigorous-rootcause`
(lead hermit-226; ledger H1/H6/H7). **Date:** 2026-07-31.

This is the **same-config cross-run axis** the lead asked for (vs the earlier
cross-SHA step-back axis). Both runs are the **same hermit binary + same flags**;
the **only variable is 2 injected host listening sockets**. So any divergence is
the socket effect, not a code delta or host load.

## The A/B pair (reused fleet captures under `scratch/demo5-icount-sleep/out/`)

| run | console | listening sockets | outcome | INFO log |
|---|---|---|---|---|
| GREEN | `-serial file:` | none | boots → `PASS`, ts 1.903 | `wedge-filecon-run1/hermit-info.log` (235 MB / 2.45 M lines) |
| WEDGE | `-serial file:` | **+2** (serial unix + QMP, `server,nowait`) | frozen at `hpet0`, ts 0.7158 | `wedge-sock-run1/hermit-info.log` (357 MB / 3.61 M lines) |

Config (both): bare busybox, `hermit run --strict --no-rcb-time --max-timeslice
disabled --target-timeslice 100000 -- qemu … -icount shift=0,sleep=off`. Binary
`hermit/target/release/hermit` @`1ece0654` (**post-#1190**). Host devbig014.

## Findings (see `signatures.txt`, `dtid_activity_*.txt`, `logdiff_*`)

### The H1 scheduler signatures do NOT discriminate wedge from green
Measured identically in **both** runs → they are the *background substrate*, not
the trigger (confirms the ledger's H1 SCOPE correction, now at INFO-log level with
a controlled same-config A/B):

| signature | WEDGE | GREEN |
|---|---|---|
| `Skipping global time ahead` (step2d jump) | **0** | **0** |
| `SleepUntil(LogicalTime(0))` COMMITs | 558,354 | 372,124 |
| future `SleepUntil(LogicalTime(>0))` | 104 | 127 |
| future `timed_waiter` registered | **0** | **0** |
| committed clock races ahead | +2432 s | +1378 s |
| `dtid_activity` STARVED-TAIL fires | yes | yes |

### The discriminator = an extra socket-poller thread
By per-dtid **role fingerprint** (top syscalls — dtid numbers are not stable
across runs; match by fingerprint):

- **socket-poller** (`clock_gettime×166156 poll×13846 read×13846`): **WEDGE only**
  (208,674 turns = 32.3%). GREEN has no thread with this profile.
- **vCPU / serial-writer** (`write futev gettimeofday writev`): GREEN 292,381
  turns; WEDGE 289,810 turns — **~equal ABSOLUTE turns in both.**
- qemu main (`futex …`): both ~146–151 k turns.

**⇒ The guest is NOT aggregate turn-starved** (the vCPU worker gets ~the same turn
count in both). The sockets inject a whole extra poller thread that raises total
turns +45 % (646 k vs 446 k) and perturbs the interleaving so the guest never
completes HPET calibration (frozen at ts 0.7158), whereas GREEN crosses it to PASS
(ts 1.903). The precise per-turn "why can't the vCPU cross HPET" is the open
turn-order question (210). This *refines* the "vCPU starved of re-selection"
framing: in the parent-controller case QEMU got 0 turns, but in this bare-socket
case the vCPU gets ~equal turns yet still can't progress.

### First functional divergence (the skid onset)
`hermit log-diff --strip-lines` reports its first divergence at msg 1492 = the
**boot-script path** in an `openat` (`boot_qemu_filecon` vs `boot_qemu_sock`) —
run-path noise (tooling gap #1, again). The real functional divergence is the
QEMU **`socket(1,524289,0)` / `bind(9)` / `listen(9)` + `bind(14)` / `listen(14)`**
(AF_UNIX|NONBLOCK|CLOEXEC) by qemu main — **0 such calls in GREEN**. The extra
poller thread first appears shortly after (its `poll`/`read` loop on those fds).

### H7 — #1095 guest-vs-committed clock lag post-#1190: **KILLED**
Guest `CLOCK_MONOTONIC` late reads = `1767228032.4845 … .5525 s` vs final
committed `1767228032.553 s` → tracks to **~ms**, not the pre-#1190 **~8.53 s**
lag seen in aa5258b. #1190's process-tree clock unification holds; the wedge is
**not** a clock-lag problem.

## Tooling extended this run
`../dtid_activity.rs` gained a **per-dtid role fingerprint** (top-4 syscalls) so
threads can be matched across runs where dtid numbering shifts — the query this
diff needed (it had to be hand-derived by grep before). Validated on the 357 MB
wedge + 235 MB green logs. Reconfirmed gap #1 (log-diff doesn't normalize per-run
paths). Not committed/PR'd per this task's protocol; patch-ready in the artifact.

## Reproduction
```
DA=experiments/demo5-rootcause-20260731/log-science/dtid_activity.rs
W=scratch/demo5-icount-sleep/out/wedge-sock-run1/hermit-info.log
G=scratch/demo5-icount-sleep/out/wedge-filecon-run1/hermit-info.log
$DA < $W ; $DA < $G                         # per-dtid table + fingerprints
grep -c 'Skipping global time ahead' $W $G  # H1a
grep -c 'SleepUntil(LogicalTime(0))'  $W $G # H1b
hermit log-diff --strip-lines --limit 5 <(head -70000 $G) <(head -70000 $W)
```
