# DBT ratchet round 2 — non-gated STDOUT_DIVERGE triage (fd-hygiene lane)

Date: 2026-07-31
Task: `dbt-ratchet-round2-nongated`
Slot: `worktrees/dbt-compat/hermit`
Provenance: hermit @ origin/main `0ca0dec2` + round-1 fd fix `26eb2372`
(PR #1218), reverie pinned `adc147342f`. DBI = DynamoRIO client, in-guest
Detcore tool. Golden = ptrace backend, `hermit run --strict`.

## Question

Round 1 (PR #1218) fixed the report-fd fd-shift bug and took DBI-vs-ptrace
`--strict` guest-observable parity from 17/36 → 19/36 (B3). Round 2 was
dispatched to fix the "next non-time STDOUT_DIVERGE fd-hygiene batch",
strictly avoiding owner-gated families (guest-clock/vtime, core scheduling,
SIGCHLD, exit_group teardown).

## Method

Diffed ptrace vs DBI stdout for every STDOUT_DIVERGE guest from the round-1
sweep (`ignored/dbt-compat-parity-fixed.tsv`), read each guest source, traced
the divergence to a root cause, and confirmed each is **L2-self-consistent
under DBI** (`--strict --verify` rc=0 AND two plain DBI runs byte-identical) —
i.e. every divergence is a *cross-backend parity gap*, not a DBI determinism
hole.

## Result: the non-gated fd-hygiene lane is EXHAUSTED

All 10 STDOUT_DIVERGE guests are deterministic under DBI and diverge from the
golden ptrace reference for reasons that are either **intrinsic to DynamoRIO**
or **owner-gated**. None is a bounded, non-gated, hermit-only fd-hygiene fix.

| Guest | Root cause | Class |
|---|---|---|
| rustbin_clock_gettime | guest clock domain vs vtime | GATED — clock/vtime |
| rustbin_print_clock_nanosleep_monotonic_abs_race | clock/nanosleep timing | GATED — clock/vtime |
| rustbin_print_clock_nanosleep_monotonic_race | clock/nanosleep timing | GATED — clock/vtime |
| rustbin_print_clock_nanosleep_realtime_abs_race | clock/nanosleep timing | GATED — clock/vtime |
| rustbin_print_nanosleep_race | nanosleep timing | GATED — clock/vtime |
| rustbin_rdtsc | tsc/time | GATED — time family |
| rustbin_thread_random | thread scheduling order → getrandom stream assignment | GATED — core scheduling |
| rustbin_interrogate_tty | container/namespace stdio + uid virtualization | GATED — uid/pid-virt + backend container |
| rustbin_heap_ptrs | DynamoRIO shares the guest address space → glibc malloc arena relocated | INTRINSIC — DR memory layout |
| rustbin_stack_ptr | DynamoRIO stack setup → different %rsp | INTRINSIC — DR memory layout |

### Evidence details for the non-time candidates

**rustbin_heap_ptrs / rustbin_stack_ptr — intrinsic DR address-space layout.**
- ptrace heap base `0x00005555555acd60`; DBI `0x00007ffff3c00d60`.
- ptrace `%rsp 0x7fffffffb2ec`; DBI `0x7fffffffaa5c`.
- DynamoRIO loads its client + code cache into the low address region, so
  glibc's mmap arena and the guest stack land at different addresses than the
  ptrace backend (which does not share the guest address space). Both are L2
  self-consistent under DBI. Matching ptrace would require controlling DR's
  memory map — infeasible and out of scope. Not fd-hygiene.

**rustbin_interrogate_tty — container/namespace + uid virtualization (gated).**
- The guest `fstat`s fd 0/1/2. Under ptrace (with `</dev/null`): fd0 is the
  container's `/dev/null` — `st_mode 0o20666` (char dev), `st_rdev 259`
  (makedev(1,3)), `st_uid 65534` (nobody). Under DBI: fd0 is a raw host pipe —
  `st_mode 0o10600` (FIFO), `st_rdev 0`, `st_uid 212630` (real host uid).
- `detcore::determinize_stat` (files.rs:1321) only normalizes inode, dev, and
  timestamps — it does **not** touch uid/gid/mode/rdev (see the standing FIXME
  at `detcore/src/lib.rs:740`, "fstat structure isn't fully deterministic
  yet"). The canonical ptrace values come from the **mount+user namespace
  container** ptrace sets up, which provides a canonical `/dev/null` and uid
  mapping. DBI does not containerize stdio the same way, and the `st_uid` leak
  is the known pid/uid-virtualization gap.
- Fixing this means either (a) giving DBI the same namespace container
  (backend I/O plumbing, reverie-dbi — pinned by git rev, needs a coordinated
  Reverie change), or (b) extending `determinize_stat` to canonicalize
  uid/gid/mode — which would change the **golden ptrace output** (currently
  already deterministic at 65534) and has file-ownership semantics
  implications, i.e. a determinization-strategy change warranting review.
  Both are owner-gated. Not a bounded non-gated hermit-only fix.

**rustbin_thread_random — thread scheduling order (gated).**
- Uses `libc::getrandom()` from 10 threads. L2-self-consistent under DBI
  (two DBI runs identical, `--verify` rc=0), but the per-thread RNG streams
  differ from ptrace because thread interleaving / stream assignment depends
  on scheduler decisions (the guest's own doc comment notes the thread-local
  RNG feeds scheduling). Cross-backend scheduling-order parity is core
  scheduling — owner-gated.

## Conclusion / recommendation

No parity fix shippable in the non-gated fd-hygiene lane this round. Corpus
stays **19/36 PARITY_CLEAN (52.8%, B3)** with **no regression**. The round-1
report-fd fix was the sole clean non-gated fd-hygiene win; the residual
divergences split into intrinsic DR memory layout (2 guests, arguably not a
"bug" — raw pointer values are backend-specific) and owner-gated families
(8 guests: clock/vtime ×6, scheduling ×1, uid/namespace ×1).

Next honest lanes, all owner-gated (do not freelance):
1. uid/pid-virtualization for non-ptrace backends (would clear interrogate_tty
   and the `newton/<hostpid>` residual seen in the round-1 fd probe).
2. guest-clock/vtime parity for DBI (6 guests) — the demo5 clock-domain family.
3. exit_group teardown contract for the 6 `HANG(pt=0,db=124)` guests.

For a bounded non-gated *hermit-only* win, extending `determinize_stat` to
canonicalize uid/gid/mode under `virtualize_metadata` is a candidate but it
alters the golden ptrace reference and carries file-ownership semantics — it
needs owner sign-off (determinization-strategy trigger), so it is explicitly
NOT taken here.
