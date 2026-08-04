# LiteInst genuinely-bracketed parity cells — the honest 0.2 coverage list

- **Date:** 2026-08-04 (UTC)
- **Scorecard:** `compat-envelope/fullcorpus-scorecard.csv`, hermit `82a8e853`, liteinst
  backend, `--strict --verify` RECORDED double-run, `parity` field (col 15).
- **Task:** `parity-scorecard-cells-may-pass-on-tests-that-cannot-fail` /
  `parity-metric-has-no-negative-side-by-construction`.
- **Scope:** ENUMERATION ONLY. No fixes. Extends the `## Vacuity audit` section of this
  dir's `README.md`.

## Why a list, not a percentage

Parity = stdout-SHA-256 EQUALITY vs the ptrace golden (`collect-envelope.rs::capture_parity`).
It has **no negative side by construction**: it passes whenever two hashes match, including
when both backends emit the same meaningless/constant string. So `108/200 parity` is NOT a
coverage claim. The honest coverage number is the subset of parity cells that carry a
**negative witness** — a host control that demonstrably FAILS (rc≠0 / empty stdout / host value
≠ canonical), so the cell cannot pass unless the backend actually determinized. Two ways a cell
earns a witness, both enumerated below and each verified by reading the fixture source THIS
session:

- **value-emitting** — a canonicalized nondeterministic value reaches stdout (the hashed
  channel); an inert liteinst leaks the raw host value and diverges from the golden (implicit
  witness via the ptrace differential; a *planted* host control still needs formalizing under
  the fix task).
- **bracketed-via-gate with a DEMONSTRATED non-host witness** — the cell gates on a canonical
  constant that is manifestly not the host's value, so the host control fails outright. The
  `meminfo-*` family is the reference case codex-rev cites and is already the fix's seed set.

**GENUINELY-BRACKETED honest total at `82a8e853` = 17 cells (14 value-emitting + 3 meminfo-gate),
→ 18 when #1397 arch-prctl lands.** This is the range the audit called "~18–22"; the tight,
witness-backed floor is 17.

## TIER 1a — VALUE-EMITTING = 14 cells (implicit witness, witness-ready)

Each emits a determinized nondeterministic quantity to **stdout**; an inert liteinst would leak
the raw host value and diverge from the ptrace golden (de-facto negative side). Source-verified.
These are the primary targets for a *planted* host-control witness under
`parity-metric-has-no-negative-side-by-construction`.

| cell (test_id) | stdout evidence (source) | canonicalized source |
|---|---|---|
| `backend-parity-c/pid-probe` | `printf("pid=%ld", getpid())` | canonical PID |
| `system-utils/record-getpid` | `printf("My pid: %d", pid)` (tests/c/getpid.c) | canonical PID |
| `backend-parity-c/cpuid-probe` | `printf("...vendor=%s signature=%08x")` | canonical CPUID |
| `c-programs/uname` | `printf("...release: %s ... version: %s ... machine: %s")` | canonical kernel id |
| `c-programs/adjtimex-deterministic` | `printf("adjtimex-ok state=%ld status=%d tick=%ld")` | determinized time state |
| `c-programs/clock-adjtime-deterministic` | `printf("clock-adjtime-ok state=%ld status=%d tick=%ld")` | determinized time state |
| `c-programs/syslog-deterministic` | `printf("syslog-ok size=%ld")` | determinized syscall return |
| `c-programs/syscall-quick-wins` | `printf("...uids=%u:%u:%u gids=%u:%u:%u...")` | determinized uids/gids |
| `c-programs/so-incoming-cpu-tcp4` | `printf("tcp4-incoming-cpu=%d", cpu)` | virtual CPU 0 |
| `c-programs/so-incoming-cpu-tcp6` | `printf("tcp6-incoming-cpu=%d", cpu)` | virtual CPU 0 |
| `c-programs/so-incoming-cpu-udp4` | `printf("udp4-incoming-cpu=%d", cpu)` | virtual CPU 0 |
| `c-programs/tcp-info-accept4` | `printf("accept4 state=%u ca=%u options=%u scales=%u")` | canonicalized TCP_INFO |
| `c-programs/tcp-info-accept6` | `printf("accept6 state=%u ca=%u options=%u scales=%u")` | canonicalized TCP_INFO |
| `c-programs/tcp-info-client4` | `printf("client4 state=%u ca=%u options=%u scales=%u")` | canonicalized TCP_INFO |

## TIER 1b — BRACKETED-VIA-GATE with a DEMONSTRATED witness = 3 cells (the fix's seed set)

The `meminfo-*` family gates on `MemTotal != 976562` (canonical ≈ 953 MB). On devbig014 the host
control returns **rc=1, stdout=0 bytes, MemTotal 791458316 KB ≠ 976562** — a witness that already
demonstrably fails, exactly as codex-rev's fix requires. Source-verified this session.

| cell (test_id) | gate (source) | demonstrated host witness |
|---|---|---|
| `c-programs/meminfo-free-deterministic` | `if (total != 976562 \|\| free_kb != total) return 1;` then `puts("MemFree is deterministic")` | host rc=1, stdout empty, 791458316 vs 976562 |
| `c-programs/meminfo-available-deterministic` | `if (total != 976562 \|\| available != total) return 1;` | host rc=1, stdout empty |
| `c-programs/meminfo-cached-deterministic` | `if (total != 976562 \|\| cached != 0) return 1;` | host rc=1, stdout empty |

**TIER 1a + 1b = 17 genuinely-bracketed cells = the honest 0.2 liteinst parity claim.** #1397
arch-prctl (bracketed-via-gate, det+parity flip 0→1 causally) is the 18th when it lands.

## TIER 2 — WEAK brackets = 6 cells (parity=1 but NOT honest coverage)

Would pass a partially-inert backend; do NOT count toward the 0.2 claim.

| cell | why weak (source-verified this session) |
|---|---|
| `c-programs/getcpu` | **REFUTES README:** stdout is `atomic_puts("EXIT-SUCCESS")` (const); the CPU id is `test_assert`ed in-program, and the assert `*cpu <= 0xffffff` is near-tautological (true for the raw host CPU too). Value never reaches stdout. Near-vacuous gate. |
| `c-programs/setitimer-determinism` | stdout emits only a boolean armed-flag `%d` (1/0), not the determinized timer value. Gate-like. |
| `c-programs/timer-create-determinism` | stdout emits only `remaining>0: %d` boolean, not the value. Gate-like. |
| `c-programs/io-uring-ring-determinism` | emits a data checksum, not a canonicalized nondeterministic source — checksum matches even if determinization is inert (README-flagged). |
| `c-programs/mmap-stress-determinism` | data checksum, same weakness. |
| `c-programs/rcx-canonicalization` | mostly literal `=1` + 0/1 bits; checksum would match inert. |

**Ceiling if weak brackets are included = 23** (17 + 6). The prior `README.md` count (22
value-emit, floor ~18) was looser on classification: it credited `getcpu` and the two boolean
timer cells as firm value-emitters (this session's source read downgrades them to weak) but did
not separately promote the `meminfo` gate cells; the witness-backed genuinely-bracketed total is
**17**.

## The remaining 85 of 108 — NOT honest coverage

- **43 error-canonicalization cells** (`*-enosys`/`*-eopnotsupp`/`*-eperm`/`*-refusal`/dbi-error):
  **BOX-BLOCKED — NOT ESTIMATED.** Whether each is vacuous (host natively returns the same errno
  ⇒ inert liteinst passes) or bracketed-via-gate (host implements it, Hermit refuses by policy)
  requires per-syscall native host errno, which the agent sandbox cannot obtain. Do not claim or
  estimate these for 0.2. (The negative-witness / native-control column on
  `parity-metric-has-no-negative-side-by-construction` is exactly what would resolve them — it is
  the generalization of the `meminfo` gate to every cell.)
- **4 firmly-vacuous const-string signal cells:** `hello-alarm`, `hello-signals`,
  `sigpipe-siginfo`, `dbi-self-sigqueue` — fixed banner/signum/si_code, no nondeterministic
  source in stdout. Not coverage.
- **~38 remaining self-check "ok" gates** (autobind/netns-cookie/proc/timer/socket/etc.):
  bracketing depends on host≠canonical = BOX-BLOCKED. Not estimated. (These become citable one at
  a time as each earns a demonstrated host-control witness, the same way `meminfo` already has.)

## Bottom line

**Honest liteinst parity coverage for 0.2 = the 17 genuinely-bracketed cells above (14
value-emitting + 3 meminfo-gate), → 18 with #1397.** NOT 108. Of the 17, the **3 meminfo cells
already carry a demonstrated negative witness** (host rc=1, empty stdout) and are the fix's seed
set; the 14 value-emitters have an implicit ptrace-differential witness and are the first to get
a planted host control. Everything outside these 17 is weak, box-blocked, or vacuous —
unqualified until the parity metric carries a negative witness per cell.

## Reproduction

```
cd compat-envelope
awk -F, 'NR>1&&$11=="liteinst"&&$15=="1"{print $9}' fullcorpus-scorecard.csv | sort   # 108 parity cells
# per-cell classification: read each fixture's NON-stderr printf/puts for a %-formatted
# value fed by a syscall/proc source (=firm) vs a fixed string / boolean flag / checksum (=weak/gate)
# sources: hermit/tests/c/<name>.c, hermit/tests/backend-parity/*/<name>.c
```
