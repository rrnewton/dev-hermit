# epoll / ephemeral-port fixture duplication audit

Task `audit_port_epoll_fixture` (successor obligation from hermit PR #1701, closed
without landing). Audited at hermit `origin/main` `4c70658e7`, host devbig014,
2026-08-06.

## Verdict up front

**Nothing should be removed.** The three epoll fixtures on `main` cover three
distinct surfaces; consolidating any pair would lose coverage. The real duplicate
was PR #1701's *combined* fixture, and it is already closed.

But the closure's stated premise is **half wrong**, and the wrong half hides an
uncovered contract.

## The three existing fixtures are not duplicates

| fixture | surface | why it is not redundant |
|---|---|---|
| `tests/backend-parity/fixtures/epoll_readiness.c` | `epoll_wait` over **one** pre-armed eventfd; register → ready → deregister → empty | the cross-backend parity row for `epoll_wait` itself |
| `tests/backend-parity/fixtures/epoll_pwait2_readiness.c` | **syscall 441** `epoll_pwait2`, zero `timespec` | *different syscall number*, and it is a declared **KVM gap** (ElfExecutor returns ENOSYS). Folding it into the above would erase a backend-matrix distinction |
| `tests/c/epoll_determinism.c` | **multi-ready ordering** across eventfd / pipe / pipe2 / signalfd / socketpair / timerfd | the only fixture that tests *ordering among simultaneously-ready fds* |

`epoll_determinism.c` is genuinely the strong oracle the closure claimed:
it asserts `count == expected_count` (not `n > 0`), requires `EPOLLIN` on each
event, rejects an unknown **or duplicate** tag, and **prints the order**
(`order=<tag>:0x<events>,...`) so the ordering itself is raw-compared.

## The closure's premise, checked against source

PR #1701 was closed as duplicating *"existing deterministic ephemeral-port and
stronger multi-ready epoll oracles."*

- **"stronger multi-ready epoll oracle" — CONFIRMED.** `epoll_determinism.c`
  above. #1701's `n > 0` acceptance really was weaker, and closing it on that
  ground was correct.
- **"existing deterministic ephemeral-port oracle" — REFUTED. It does not
  exist.** The nearest candidate, `tests/backend-parity/fixtures/bind_getsockname.c`,
  binds an **AF_UNIX abstract-namespace** socket to a **fixed literal name**
  ("NO data transfer, NO listen/connect" per its own header). There is no
  ephemeral port, no `sin_port = 0`, no kernel port allocation. Nothing in
  `tests/` prints or asserts an ephemeral port: the TCP fixtures
  (`tcp_info_client4.c`, `so_incoming_cpu_tcp4.c`, `tcp_info_accept4.c`) contain
  no `sin_port` / `ntohs` at all.

**And the multi-ready oracle has zero AF_INET coverage.** Its only `SOCK_STREAM`
is a `socketpair(AF_UNIX, …)` at line 252. So TCP readiness ordering is
uncovered too.

## Measurement: hermit DOES determinize the ephemeral port

Recorded because the whole question of whether a port assertion is even viable
turns on it. Probe: `socket(AF_INET, SOCK_STREAM)` → `bind` loopback with
`sin_port = 0` → `getsockname`, printing the port.

| context | observed |
|---|---|
| native, 3 runs | `32995`, `59907`, `54113` — varies |
| `hermit run --strict`, 5 runs | `32768`, `32768`, `32768`, `32768`, `32768` — **stable** |

Binary: `worktrees/audit/hermit/target/release/hermit` (hermit `4c70658e7`).
Runtime `LD_LIBRARY_PATH` = the fbsource libunwind tree.
*Gotcha:* the probe must not live under `/tmp` — hermit replaces guest `/tmp`
with an isolated directory and refuses with a message naming `--tmp=/tmp`.

So an ephemeral-port determinism contract is **viable** (the value is stable and
differs from native, which is exactly what makes it worth asserting) and
**currently unasserted anywhere**.

## Recommendation

Do not delete anything. The outstanding work is the *optional* item the closure
already scoped: a **narrow TCP extension to `tests/c/epoll_determinism.c`** —
register-before-ready, checked returns, exactly three unique tags, five-run raw
comparison, strict JSON verification. It is justified on evidence, not on the
closure's say-so: it adds the AF_INET surface the multi-ready oracle lacks and
the ephemeral-port determinism nothing currently covers.

Two risks to design against, both already load-bearing in this corpus:

1. **Blocking waits livelock the DBI cooperative scheduler** — every epoll
   fixture header says so. Keep any TCP wait at timeout 0, arming readiness
   before the wait.
2. **Printing the port makes the byte stream host-coupled.** Under hermit it is
   stable, but a native/relaxed run would differ. Either scope the assertion to
   hermit runs or assert *stability across runs* rather than the literal `32768`.

## ID hazard worth keeping

The #1701 closure cited TaskGraph id `audit-port-epoll-fixture-duplication`,
which **can never resolve**: `tg` derives `local_id` from the title by
lowercasing, replacing spaces with underscores, and truncating to **four words**.
The resolvable id is `audit_port_epoll_fixture`. A closure comment that points at
an unreachable id is indistinguishable from one that points at nothing — this is
the second occurrence (the first was repaired on PR #1698).
