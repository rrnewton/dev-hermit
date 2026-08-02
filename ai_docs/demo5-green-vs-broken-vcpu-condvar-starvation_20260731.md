# demo5 green-vs-broken: trace-grounded characterization (post-#1190)

Task: `verify-demo5-green-reliability-post1190` (hermit-220). Owner live-debug
question. All evidence below is verbatim `--log`/DETLOG, no reconstruction.

Traces:
- **BROKEN (current main, ae2565be, post-#1190):**
  `ignored/h/a/r3/.work/boot-o68sg66f/hermit-info.log` (5,695,286 lines,
  1,032,362 turns, wedged → 600s wall timeout).
- **GOOD (2a7ca98, #1077, boots to exit):**
  `ignored/logs/demo5-good-boot-2a7ca98-rerun-1785460783-197-1542507.log`
  (1,373,029 turns, clean `Exit{group:true}`).

Role map (dtid numbering differs between runs; mapped by syscall fingerprint):
QEMU process = detpid 5's thread group; controller (python) = detpid 3.
- BROKEN: dtid 3 = QEMU main (file/disk I/O), dtid 5 = BQL waiter, dtid 7 =
  vCPU/TCG worker, dtid 9 = short-lived setup (exits turn 4500), dtid 11 = aux
  (poll/clock), dtid 13 = iothread.
- GOOD: dtid 7 = dominant vCPU, dtid 9 = BQL waiter, dtid 15 = main-loop poll,
  dtid 17 = iothread (+ threads up to dtid 59 = userspace booted).

---

## (1) CURRENT broken run: exactly which dtids are runnable in the livelock

At the wedge, the run queue cycles **exactly four** dtids: **3, 5, 11, 13**.
dtid 7 is BLOCKED (see below); dtid 9 EXITED at turn 4500.

Whole-run COMMIT distribution (`grep 'COMMIT turn' | dettid`):

```
342869 dettid 13     5037 dettid 7
258850 dettid 3        39 dettid 9
243095 dettid 11
182473 dettid 5
```

Last-200k-lines inbound distribution = only {13, 3, 5, 11}. Verbatim terminal
spin (tail of BROKEN trace), showing what each does:

```
COMMIT turn 1032355, dettid 5 using resources {FutexWait: R}, ...465.300_175_000s
  [dtid 5] finish syscall #92190: futex(0x5555570c8ec0, 128, 2, NULL, NULL, 0) = Ok(0)
COMMIT turn 1032354, dettid 13 using resources {SleepUntil(LogicalTime(0)): W}, ...465.299_550_000s
  [dtid 13] finish syscall #342750: writev(14, 0x7ffff4d5e4b0, 1) = Ok(1)
COMMIT turn 1032352, dettid 13 ...
  [dtid 13] finish syscall #342749: futex(0x5555570c8ec0, 129, 1, NULL, NULL, 0) = Ok(1)
COMMIT turn 1032356, dettid 3 using resources {SleepUntil(LogicalTime(0)): W}, ...465.300_675_000s
  [dtid 3] finish syscall #202890: read(3, 0x7fffea28cd40, 3) = Ok(3)
  [dtid 3] finish syscall #202891: read(3, 0xf1ba33, 8192) = Ok(1)
  [dtid 3] finish syscall #202892: read(3, 0xf1ba34, 8191) = Ok(0)   # EOF, then lseek + re-read
```

Per-dtid syscall profile (last 40k lines):
- **dtid 5** = QEMU **BQL waiter**: `futex(0x5555570c8ec0, 128/FUTEX_WAIT_PRIVATE,
  2)=Ok(0)` (808×). `val=2` = glibc mutex "locked-with-waiters" (the Big QEMU
  Lock). Returns `Ok(0)` = genuinely **woken**, not timed out.
- **dtid 13** = QEMU **iothread**: `writev(14,…)=Ok(1)` (1866×) +
  `futex(0x5555570c8ec0, 129/FUTEX_WAKE_PRIVATE, 1)=Ok(1)` (810×) — kicks the
  vCPU eventfd (fd 14) and wakes the BQL.
- **dtid 3** = QEMU **main**: `read`(1378×)/`lseek`(401)/`newfstatat`(401) on a
  23 KB regular file (fd 3) in a re-read loop + `clock_gettime`(408) + a few
  `clock_nanosleep`(7).
- **dtid 11** = aux thread: `clock_gettime`(60)/`poll`(5)/`read`(5).

Every runnable turn is immediately grantable (`FutexWait` woken, or
`SleepUntil(LogicalTime(0))` = yield in the past), so the run queue never
empties. Confirmed on this exact trace: **`Skipping global time ahead` = 0**,
**`registering waiter at future` = 0**, committed vtime raced **epoch →
+3865.324s** over 1.03M turns. This is deadline-less.

## The actual wedge: the vCPU is DEADLOCK-blocked, not merely "outvoted"

dtid 7 (the vCPU/TCG worker that runs guest instructions) is **not** in the
spin — it is BLOCKED on a **different** futex word, a QEMU condition variable:

```
[dtid 7] inbound syscall: futex(0x5555570f6708, 0, -1, NULL, NULL, 0) = ?   # FUTEX_WAIT, no timeout
```

History of that word (whole run):
- dtid 7 waited 7×, **completed 6**; woken by `futex(0x5555570f6708,
  1/FUTEX_WAKE, INT_MAX)=Ok(1)` issued by **dtid 13 (×5) and dtid 5 (×1)**.
- The **last wake was turn 152,490.** dtid 7 then issued its **7th
  FUTEX_WAIT at turn 171,416** (committed +522.652s) — **never woken again.**

After turn 171,416, dtid 5/13 spin **only** the BQL word `0x5555570c8ec0` and
never re-signal the cond-var `0x5555570f6708`. So the vCPU is parked for the
**final 860,946 turns** (171,416 → 1,032,362), during which committed vtime
advanced **+3342.7s** (from +522.65s to +3865.32s) with **zero guest-instruction
progress**. Max dtid ever created = **13** — the guest never spawned a single
userspace thread.

Circular dependency: the vCPU waits on a cond-var that only *guest forward
progress* would re-signal; guest progress needs the vCPU. Under the serialized
deterministic schedule the interleaving parks the vCPU permanently; the
remaining BQL/iothread/main threads livelock on their own handshake + disk
re-read and keep committing turns.

---

## (2) HISTORY: is this the same as the old dtid-3 spin? — NO, DIFFERENT pathology

**Old wedge (pre-#1190, aa5258b):** the *controller* (python, detpid 3) spun
forever busy-polling `qmp.sock`. Root cause = **clock-domain skew (#1095)**:
guest `CLOCK_MONOTONIC` lagged committed vtime ~8.53 s, so the controller's
`clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, now+~100ms)` deadline was
already **in the past** vs committed → the scheduler ran it immediately (COMMIT,
not SKIP) → run queue never emptied → QEMU starved. (Verified then: target
1767225630.500 vs committed 1767225639.034.)

**The fix = #1190:** unify the process-tree guest clock to committed logical
time (`observe(raw) = now.max(raw)`, `raw` = committed). Now abs deadlines land
in the **future** → the controller **BLOCKS** → QEMU is scheduled. Confirmed
working on the current trace: the controller's monotonic tracks committed to
~33 ms (not 8.53 s), and there is **zero `qmp.sock` polling in the terminal
region** — the old spin is gone.

**Why the fix doesn't cover 5/11/13:** it addressed a *clock/deadline* skew in a
*cross-process controller poll*. The current 5/11/13 spin has **no deadline and
no clock** — it is a QEMU-**internal** producer/consumer futex handshake
(`FUTEX_WAIT val=2` ↔ `FUTEX_WAKE`) that returns `Ok(0)`/`Ok(1)` because it is
**genuinely woken by the peer**, not because a deadline expired. Nothing about
guest-clock unification touches a deadline-less intra-process futex handshake.
Same *family* as the old bug (immediately-grantable turns keep the run queue
non-empty so committed races ahead while the guest starves —
[[scheduler-vtime-jump-unproductive-pollers]]) but a **different trigger**:
expired-abs-deadline controller poll (fixed) vs. deadline-less QEMU-internal
cond-var starvation of the vCPU (open).

---

## (3) GOOD run: what the same roles do that lets them progress

**The BQL handshake is NORMAL — it is not the bug.** The identical word
`0x5555570c8ec0` handshakes **~185k times** in the GOOD run
(`FUTEX_WAKE`=185,928 / `FUTEX_WAIT`=184,512), the GOOD run **also** has
`Skipping global time ahead`=0 and `registering waiter at future`=0, and it
**also** races committed vtime ahead (+5792.076 s). Committed-racing-ahead is
therefore **not** the discriminator — GOOD does it and still boots.

Verbatim GOOD mid-run window (turn ~661,900, same BQL word):

```
COMMIT turn 661900, dettid 9 using resources {FutexWait: R}, ...965.675_375_000s
  [dtid 9]  finish #46930: futex(0x5555570c8ec0, 128, 2, NULL, NULL, 0) = Ok(0)   # BQL waiter (=broken dtid 5)
COMMIT turn 661901, dettid 15 using resources {InternalIOPolling: W}, ...965.675_875_000s
  [dtid 15] finish #104734: poll(0x7fffe8003150, 3, -1) = Ok(1)                    # main-loop poll, fd READY
COMMIT turn 661902, dettid 17 using resources {SleepUntil(LogicalTime(0)): W}, ...
  [dtid 17] finish #204193: write(7, 0x555556096d88, 8) = Ok(8)                    # iothread kick (=broken dtid 13)
```

In that 4k-line window the **vCPU dtid 7 is the most active** (217 inbound vs
9/15/17), i.e. it is executing guest code between handshakes. End-of-run
timeslice summary (GOOD only — BROKEN never reaches it):

```
timeslice thread 7:  count=376748  mean=4.08ms  max=125ms     <- vCPU, huge RCB-bounded guest execution
timeslice thread 15: count=238795  max=125ms
timeslice thread 17: count=351508
... threads up to dtid 59 ...
```

The `max=125ms` slices are RCB-bounded guest-instruction runs — the vCPU keeps
getting the cond-var wake, runs a big chunk of guest code, and boot advances to
userspace (**max dtid 59** vs 13 broken), ending in a clean
`Exit{group:true, DetPid(3)}`.

---

## DELTA (precise)

| | GOOD (2a7ca98) | BROKEN (ae2565be) |
|---|---|---|
| BQL handshake `0x5555570c8ec0` | ~185k cycles (normal) | tight, unbounded |
| Guest-clock skew (#1190) | n/a | fixed (~33ms, not 8.53s) |
| Controller `qmp.sock` spin | none | none (fix holds) |
| vCPU cond-var `0x5555570f6708` | repeatedly signalled → vCPU runs | **7th wait @turn 171,416 never woken** |
| vCPU (dtid 7) | dominant, 376,748 slices, max 125ms | **blocked 860,946 turns (83% of run)** |
| Max dtid created | 59 (userspace) | 13 (QEMU startup only) |
| step2d time-jump fired | 0 | 0 |
| committed vtime advance | +5792 s → clean exit | +3865 s → 600s wall timeout |

**Bottom line for the design discussion:** the residual wedge is **not** an
"all-runnable-are-LAST_PRIORITY-pollers" state (the handshake threads are
genuinely-woken futex partners at ordinary priority, which is *why* fork #1's
`run_queue_only_immediate_pollers` forward-jump fired 0× and was inert), and it
is **not** committed-vtime racing ahead per se (GOOD does that too). It is a
**QEMU-internal vCPU cond-var starvation**: the deterministic serialized
interleaving parks the vCPU on `0x5555570f6708` and the remaining runnable
threads never re-enter the guest-execution codepath that would re-signal it. A
forward time-jump cannot fix a deadlock with no future event; the lever that
would help is one that forces the parked vCPU's wakeup dependency to be
satisfied — i.e. a scheduler policy that, in a deadline-less all-immediate-turn
steady state, deterministically prioritizes/advances the blocked-then-woken
guest-execution thread over the tight non-productive handshake — without pacing
or freezing continuous virtual time.
