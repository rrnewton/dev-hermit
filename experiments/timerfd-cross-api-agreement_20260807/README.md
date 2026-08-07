# timerfd: do all Linux access APIs agree about the same timer?

## Question

If two Linux APIs disagree about who owns a timerfd, expiry and read semantics
diverge by access path — a determinism bug. Does one armed timerfd report the
same state through every readiness and consumption API under Hermit?

## Result

**5 of 7 agree with native Linux. 2 of 7 diverge: `poll` and `epoll_wait`.**

| API | native | `hermit run --strict` | |
| --- | --- | --- | --- |
| `read` | rc=8 val=1 | rc=8 val=1 | agrees |
| `readv` | rc=8 val=1 | rc=8 val=1 | agrees |
| `ppoll` | rc=1 revents=0x1 | rc=1 revents=0x1 | agrees |
| `select` | rc=1 isset=1 | rc=1 isset=1 | agrees |
| `pselect6` | rc=1 isset=1 | rc=1 isset=1 | agrees |
| **`poll`** | rc=1 revents=0x1 | **rc=0, timed out** | **diverges** |
| **`epoll_wait`** | rc=1 | **rc=0, timed out** | **diverges** |

Native control on the combined guest: **7 of 7** — every readiness API reports
ready, `readv` consumes the single expiration, and the following `read` returns
`EAGAIN`. No double-delivery.

## Method

`tfd_disc.c` takes the API as `argv[1]` and arms a **fresh** 10 ms
`CLOCK_MONOTONIC` one-shot per mode, then uses exactly one API. Per-mode
isolation matters: a readiness call cannot mask for another, and no API can
consume an expiration another was about to observe.

`tfd_apis.c` is the combined guest. It collects all five readiness votes with a
**zero** timeout *before* any consumption, so every vote is cast on identical
unconsumed state, then consumes once via `readv` and checks that a following
`read` finds nothing left.

```
gcc -O0 -o tfd_disc tfd_disc.c
hermit run --strict --base-env minimal -- ./tfd_disc poll      # rc=0  (diverges)
hermit run --strict --base-env minimal -- ./tfd_disc ppoll     # rc=1  (agrees)
```

`--base-env minimal` is load-bearing for reproducibility; golden behaviour
otherwise picks up ambient host filesystem state.

## Interpretation

The divergence is **deterministic and structural, not a timing artifact**. Both
alternatives were tested and refuted:

- **Not flaky** — `poll` rc=0 on 3 of 3 runs, `epoll_wait` rc=0 on 3 of 3.
- **Not a timeout race** — raising `poll`'s timeout from 2 s to 30 s, a 15×
  margin against a 10 ms timer, still yields rc=0. `poll` does not miss the
  timer by a margin; it never observes timerfd readiness at all.
- **Not a strict-mode gate** — identical failure with and without `--strict`.

Root cause is a split authority, by API:

| dispatch | destination |
| --- | --- |
| `io.rs:410` `handle_poll` | `handle_internal_poll` — detcore's virtual model |
| `io.rs:1112` `handle_epoll_wait` | `handle_internal_epoll_wait` — same |
| `io.rs:834` `handle_ppoll` | `prepare_ppoll_probe` + `inject_with_retry` — the **host** |

`select`/`pselect6` also reach the host, and `read`/`readv` are host reads. The
internal readiness model **has no timerfd readiness source** — a timerfd only
ever becomes ready via a timer, and that model has none. `git grep Timerfd`
across `io.rs` returns nothing. Every path that asks the host kernel sees the
timer; the two that ask the internal model cannot.

## Scope, and what this is not

This is **not** the gap recorded by
`detcore/tests/lit/timerfd_virtual_time/hermit-run-strict.lit`, which is `XFAIL`
for timerfd-vs-**virtual-clock** (the host timer fires before the guest's virtual
deadline). The poll/epoll readiness divergence measured here is a **second,
separate defect and is covered by no XFAIL**.

It is also not the defect described by PR #1213's review blockers. That PR is
**closed unmerged** (2026-08-05) and none of its code is on main —
`timerfd_virtualized`, `TimerfdState`, `timerfd_remaining_ns` all return zero
hits. Its blocker text says `select`/`pselect6` lack "the timerfd handoff used by
poll/epoll"; on main the assignment is **inverted** — those five are correct and
`poll`/`epoll_wait` are the two on the wrong side. The requirement it states —
one timer authority governing readiness and consumption across
poll/ppoll/epoll/select/pselect6/read/readv — is the right requirement, and it is
violated on main today.

## Not fixed here

Giving the internal poll/epoll model a timerfd readiness source is the change
PR #1213 attempted and failed at across five review rounds; reviewers converged
on "virtual poll/epoll readiness plus a cross-thread arm/rearm wake subsystem".
This experiment localises the defect and proves it reproducible. It does not
attempt the engine change.

## Reproduction

`metadata.json` carries the exact SHAs, host, command, and repetition counts.
`results.csv` is the table above in machine-readable form.
