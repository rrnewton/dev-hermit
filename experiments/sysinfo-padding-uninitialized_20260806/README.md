# sysinfo(2) writes uninitialized detcore padding into guest memory

**Date:** 2026-08-06 · **Task:** `sysinfo_2_writes_uninitialized` · **Agent:** hermit-det2
**Backend:** ptrace · **Flags:** `run --strict` · **Relaxations:** none · **Host:** devbig014, x86_64, glibc 2.34

## Verdict

**Confirmed, root-caused, fixed, and verified end-to-end.** Ten of the 112 bytes
`sysinfo(2)` returns were uninitialized supervisor-stack residue. **One correction to the
reported symptom:** the stale bytes did *not* vary run to run on this host — they varied
**call to call inside a single run**, which is worse in one respect (a guest sees the
divergence without needing a second run) and is why the double-run gate never caught it.

**Sibling enumeration (#213): sysinfo is the only instance.** Two independent methods agree —
a black-box audit of 23 struct-writing syscalls and a source-level classification of all 70
`write_value`/`write_values` sites in hermit. The correct pattern is already used almost
everywhere; sysinfo is the one place it is bypassed, and it is bypassed in *Reverie*, not
detcore.

## Mechanism

Three pieces compose into the bug:

1. `detcore/src/syscalls/sysinfo.rs:287` `collect_sysinfo` builds a `reverie_syscalls::SysInfo`
   **struct literal** on the supervisor's stack. Rust leaves a `#[repr(C)]` literal's implicit
   padding indeterminate.
2. `reverie-syscalls/src/args/sysinfo.rs:50` converted it with a bare `std::mem::transmute`.
   The two types are the same size but have **different padding**: `SysInfo` has a six-byte
   implicit hole after `procs` and a four-byte trailing hole; `libc::sysinfo` spends the first
   two of those six on its named `pad` field. The transmute copies the indeterminate bytes.
3. `reverie-memory/src/lib.rs:139` `write_value` copies `size_of::<T>()` **raw bytes** into
   guest memory, padding included.

x86_64 `struct sysinfo` is 112 bytes; the affected windows are **82..88** (the ABI `pad` field
plus four bytes of alignment hole) and **108..112** (trailing hole).

Linux does not behave this way: `do_sysinfo()` builds the struct from a zeroed local, so a guest
is entitled to read zeros in every padding byte and to get the same image from two calls with
the same inputs.

## Measurement — before

Guest poisons a 112-byte buffer with `0xAA`, calls `sysinfo(2)`, dumps the whole image.
Binary: `hermit 0.2.0 (gf89c69766371)` = hermit `main` tip.

| | call 0 | call 1 | call 2 |
| --- | --- | --- | --- |
| `struct_hash` (all 112 bytes) | `847db452…` | `b1a84db1…` | `100f7600…` |
| bytes 82..88 | `00 00 00 00 00 00` | `00 00 00 00 00 00` | `df f7 ff 7f 00 00` |
| bytes 108..112 | `00 00 00 00` | `ff 7f 00 00` | `00 00 00 00` |

Three calls, three different images, **differing only in the padding**. All 112 bytes are
written each time — no poison survives — so this is written garbage, not an unwritten hole.

Native Linux, same probe: padding is zero on every call and the full image is byte-identical
across calls.

**What leaked.** The word at offset 80 read `0x00007ffff7df0001`: the low two bytes are
`procs = 1`, the upper six are the padding. `0x00007ffff7df…` falls inside the *guest's own*
`libc.so.6` mapping (`7ffff7d9e000-7ffff7dfb000`, read from the guest's `/proc/self/maps`
under hermit). So the channel is supervisor-stack residue and what it carried here was a
guest-derived address. **This artifact does not claim host-secret disclosure** — it claims an
unbounded uninitialized-memory channel whose contents are incidental to detcore's code path.

**Not run-to-run.** 10/10 identical runs; identical across two hermit builds (`f89c6976`,
`0f891e43`), with and without `--strict`, and under `setarch -R`. The variation axis is
call-to-call within one run: intervening guest syscalls change the supervisor's stack history.

**Fail-quiet confirmed.** `hermit run --strict --verify` on the three-call probe exits 0,
`Success: deterministic. Determinism verified.` (330|330 DETLOG messages compared). The
double-run gate compares two runs and the per-call padding sequence is the same in both.

## Sibling enumeration (#213)

### Method 1 — black-box audit (`fixtures/pad_audit.c`)

For each syscall: poison the output buffer `0xAA`, invoke, **perturb** with `k` unrelated
syscalls, poison `0x55`, invoke again, diff. A byte that differs was not written
deterministically. Run natively and under hermit; the discriminator is *native writes it
deterministically and hermit does not*, which filters out genuinely time-varying fields
without a hand-maintained allowlist.

The perturbation is load-bearing. Without it the two invocations run back-to-back through an
identical supervisor code path, the residue is byte-identical, and the probe reports a **false
clean** — the first version of this audit did exactly that and reported sysinfo as clean.

23 probes, 12 trials. Full table in `results.csv`. Result: **sysinfo is the only probe the
discriminator flags.** Four probes (`getrusage_self`, `clock_gettime`, `gettimeofday`,
`adjtimex`) are indeterminate natively too and are not discriminable; `waitid` likewise —
the raw syscall leaves most of `siginfo_t` untouched on native Linux (105 unstable bytes),
while hermit zero-fills it (a fidelity divergence in the safe direction, worth its own note).

### Method 2 — source classification of all 70 write sites

Every `write_value`/`write_values` site in `detcore/`, `hermit-cli/`, `detcore-dbi/`,
`detcore-sabre/` falls into one of three classes. Only one class can leak.

| class | pattern | padding | sites |
| --- | --- | --- | --- |
| A | `unsafe { std::mem::zeroed() }` then assign fields | defined (zero) | `rusage` ×4, `timex`, `siginfo_t` ×2, `sched_attr`, `sockaddr_un` |
| B | struct literal built in detcore | indeterminate **iff the type has padding** | `rlimit`, `rlimit64`, `tms`, `timespec`, `timeval`, `itimerspec`, scalars — **all padding-free, so safe by type** — and **`SysInfo` → `libc::sysinfo`, which is not** |
| C | read the kernel's/guest's value, patch fields, write back | inherited, already defined | `stat`, `statx`, `statfs`, `utsname`, `sockaddr_in`/`in6`/`nl` |

A handler can only invent padding if it **constructs** the struct. Handlers that patch a value
the kernel already wrote inherit the kernel's zeros. That is why the black-box audit found
exactly one leak, and it is why "audit every handler that writes a struct" over-scopes the
problem: the risky set is (constructed in detcore) ∧ (type has padding).

### Why sysinfo and nothing else

`reverie-syscalls::args` already has the right convention. `StatBuf` and `StatxBuf` name
**every** padding byte as an explicit field — `__pad0`, `__unused: [i64; 3]`,
`__statx_pad1/2/3` — marked `#[serde(getter = "unused")]` / `#[serde(skip)]`, precisely so
their transmutes are total and a struct literal cannot leave a byte undefined. `SysInfo` was
the one type in the module that omitted its padding fields while still being transmuted.

## Fix

`rrnewton/reverie` branch `fix/sysinfo-zero-padding-on-conversion`, commit
**`23970b972871447059873ff0cc86800f76e5e571`**, base `origin/main` `025d3780`, slot
`worktrees/det2/reverie`. One file: `reverie-syscalls/src/args/sysinfo.rs`.

Both `From` impls become field-wise. The forward direction starts from
`unsafe { std::mem::zeroed() }` and assigns only the ABI fields, which defines every byte
including `pad` and both holes. The reverse direction is converted too: a transmute there
lands `libc::sysinfo::pad` in `SysInfo`'s *unnameable* padding, from which the forward
conversion would silently re-emit it.

This fixes the class at the boundary rather than at one call site: every consumer of the
conversion is covered, and the unsound transmute is gone rather than papered over.

### Regression tests, both bracketed against the pre-fix code

| test | on the fix | on the transmute |
| --- | --- | --- |
| `conversion_defines_every_byte_including_padding` — two conversions over stacks dirtied with different patterns must yield identical images | pass | **FAIL**, differing at 82..88 with `1, 0, 95, 33, 72, 127, 0, 0` — a stack pointer |
| `padding_windows_are_zero` — asserts the Linux contract at 82..88 and 108..112 | pass | **FAIL** |
| `round_trip_preserves_abi_fields` | pass | pass (covers a different property) |

`padding_windows_are_zero` checks **four** stack fills. With a single fill it passed even on the
broken code — one pattern can leave a window zero by luck. That inert first version is recorded
here because it is the same failure mode as the un-perturbed audit above: a test that cannot
fail is not evidence.

## Verification — after

`worktrees/det2/hermit` built at hermit `f89c6976` with a **local-only** `[patch]` redirecting
the pinned Reverie git source at the slot's reverie worktree. The patch and the resulting
`Cargo.lock` change were reverted after the build; no hermit file was committed.

| | before | after |
| --- | --- | --- |
| call 0 / 1 / 2 `struct_hash` | `847db452` / `b1a84db1` / `100f7600` — three distinct | `847db452` / `5b2e0447` / `5b2e0447` |
| bytes 82..88, all calls | garbage on call 2 | `00 00 00 00 00 00` |
| bytes 108..112, all calls | garbage on call 1 | `00 00 00 00` |

Calls 1 and 2 now hash identically, matching their identical `field_hash`; call 0 still differs
because its `free_ram` legitimately differs. **`struct_hash` is now a pure function of the ABI
fields, as on native Linux.** Both of the task's VERIFY criteria are met: identical hash for
identical fields, and padding zero rather than arbitrary.

Padding windows now byte-match native Linux exactly. Re-running the 23-probe audit under the
fixed build: sysinfo drops `unstable=2 → 0`, **every other probe unchanged** (no regression).
`--strict --verify` still exits 0.

### Validation commands

```
cargo test -p reverie-syscalls --all-features                    => 23 passed, 0 failed
cargo test --workspace --all-features -- --test-threads=1 \
    --skip container::tests::pin_affinity_to_all_cores \
    --skip tests::seccomp_notify                                 => 0 failed across all bins
cargo clippy -p reverie-syscalls --all-targets --all-features    => 0 warnings
cargo fmt --all -- --check                                       => clean
```

**Assurance: L0.** A Reverie-only change cannot establish L1 or higher. The before/after guest
measurements are single-run observations under `--strict`, not an L2 claim.

## Not pushed

Egress to github.com returns proxy **403** and the task was scoped "local, no egress", so there
is no PR and no target-main ancestry. The commit exists only on the local branch in
`worktrees/det2/reverie`. A coordinator must push and open the PR before this can be closed.

## Reproduction

```bash
cd experiments/sysinfo-padding-uninitialized_20260806
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
gcc -O1 -Wall -Wextra -o fixtures/sysinfo_padding fixtures/sysinfo_padding.c
gcc -O1 -Wall -Wextra -o fixtures/pad_audit      fixtures/pad_audit.c -lrt

./fixtures/sysinfo_padding --hex --repeat 3            # native: padding always zero
<hermit> run --strict -- ./fixtures/sysinfo_padding --hex --repeat 3

./fixtures/pad_audit --trials 12                       # native baseline
<hermit> run --strict -- ./fixtures/pad_audit --trials 12
```

The probe binaries and per-run outputs are gitignored; the fixtures, `results.csv`, and
`metadata.json` are the durable record.

## Follow-ups this justifies

1. **Push the Reverie fix and open the PR**, then bump the hermit pin. Blocked only on egress.
2. **`waitid` writes more of `siginfo_t` than Linux does** — hermit zero-fills 105 bytes the raw
   syscall leaves untouched. Safe (zeros, deterministic) but a fidelity divergence; worth a
   separate task rather than a silent difference.
3. **`shared_ram` and `buffer_ram` both report 0x1000 = 4096** while the source names the
   constant `MB` (`detcore/src/syscalls/sysinfo.rs`). Either the constant or the name is wrong.
   Noticed while reading the byte image; unrelated to padding.
4. **Keep the audit as a fixture.** `pad_audit.c` is a general uninitialized-copyout detector for
   the whole syscall surface. Adding a probe is three lines, and the perturbation lesson —
   back-to-back calls hide the bug — should travel with it.
