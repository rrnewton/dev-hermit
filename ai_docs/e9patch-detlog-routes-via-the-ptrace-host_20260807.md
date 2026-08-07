# e9patch routes DETLOG via the ptrace host — the architecture gate, measured

**Measurement and documentation only.** Per the owner's standing gate, a patching
backend whose syscall path depends on a ptrace round trip is not architecturally
correct yet and is therefore not ready for perf work at all. Nothing here
optimizes anything.

- hermit `e808322` (fresh `origin/main`), host devbig014, 316 cores
- `strace 6.12`, `e9tool` 935,112 bytes at `HERMIT_E9TOOL`
- Guest `/bin/true` throughout, so the result is about the *path*, not the workload

## 1. The finding that reframes the task

The task is titled "e9patch routes DETLOG via the ptrace host". That is true, but
the reason is stronger than routing: **there is no e9patch DETLOG code at all.**

I enumerated every `DETLOG` occurrence in Rust source — 92 hits across 14 files —
and classified each rather than accepting a summary:

| class | count | where |
|---|---:|---|
| emission macro — the only place a DETLOG line is produced | 3 | `detcore/src/detlog.rs:39,51,62` |
| SaBRe **in-guest** forwarding | 4 | `detcore-sabre/src/lib.rs:39,100,107,389` |
| consumer: log comparison, not emission | 43 | `detcore/src/logdiff.rs` |
| comment / doc | 22 | scheduler, detlog, logdiff |
| SaBRe plumbing + tests in the CLI | 16 | `hermit-cli/src/{lib.rs,bin/hermit/run.rs}`, `tests/` |
| generic test fixture strings | 4 | `verify.rs`, `hermit_modes.rs` |
| **e9patch-specific** | **0** | — |

Zero. e9patch has no emission path of its own to route *through*. DETLOG is
produced by `detcore` running inside the **ptrace tracer process**, exactly as it
is for the plain ptrace backend, because e9patch *is* the ptrace runtime plus a
rewriting pre-pass.

The code says so directly. In `hermit-cli/src/bin/hermit/run.rs`, `Backend::E9patch`
shares its match arm with `Ptrace` and `Liteinst` and never branches to a runner of
its own — unlike `Backend::Dbi`, which calls `run_dbi`. And `perf_supported` for
E9patch is `reverie_ptrace::is_perf_supported()`. Hermit's own banner is unambiguous
(`record_start.rs:298,316` and observed live):

```
:: Backend: e9patch preprocessing + ptrace runtime; candidate_sites=0; mapped_sites=0; ...
```

## 2. The strace litmus, measured with a discriminating control

ptrace permits one tracer per process, so putting `strace` on hermit denies hermit
the ability to trace its own guest. A backend that survives this does not depend on
a ptrace round trip. This is unfakeable in the useful direction: you cannot pass it
by claiming to.

| backend | without strace | under `strace -f` |
|---|---|---|
| ptrace | rc=0 | **FAILS** — `a child PTRACE_TRACEME probe was denied` |
| **e9patch** | rc=0 | **FAILS** — *byte-identical* error |
| DBI | — | **rc=0**, 32 syscalls serviced, 25,782 branches, 30,080 strace lines |

The DBI row is the control that makes the other two mean something: the litmus is
not "strace breaks hermit", it is "strace breaks the backends whose guest is
ptraced". DBI's guest is not, so it survives.

That e9patch's failure message is *identical* to ptrace's is itself evidence of the
shared runtime, not merely a coincidence of wording.

One further observation from the same run: on `/bin/true`, e9patch reported
`candidate_sites=0; mapped_sites=0`. It rewrote nothing, so **100% of that guest's
syscalls went through the ptracer.** The rewriting pre-pass contributed no syscall
interception at all on this guest.

## 3. What the in-guest alternative concretely is

It already exists, for a different backend. `detcore-sabre` forwards DETLOG from
inside the guest:

- `detcore-sabre/src/lib.rs:39` — `DETLOG_FORWARD_ENV = "REVERIE_SABRE_HERMIT_FORWARD_DETLOG"`
- `detcore-sabre/src/lib.rs:107` — the in-guest side consumes it via `sabre::take_private_env`
- `detcore-sabre/src/lib.rs:100` — and writes `INFO detcore: DETLOG ` straight to stderr from in-guest
- `hermit-cli/src/lib.rs:1041,1043` — the host sets/clears the env for the guest
- `hermit-cli/src/bin/hermit/run.rs:67,86,87` — the host **normalizes the timestamp** to `1970-01-01T00:00:00.000000Z`, because an in-guest writer has no access to the host's log clock
- `hermit-cli/src/bin/hermit/run.rs:91,2754,2758` — and **counts** `DETLOG [syscall]` records so that "zero records" is a loud failure rather than a silent vacuous pass

That last point is the transferable design lesson: moving DETLOG in-guest
introduces a new way to be silently empty, and SaBRe's path already pays for a
counter to close it.

## 4. What would have to move, and what stays ptrace-dependent afterward

For e9patch the honest answer is that DETLOG is not a separable piece. DETLOG is
emitted by `detcore` wherever `detcore` runs. Under e9patch, `detcore` runs in the
tracer. So:

- **Moving DETLOG in-guest is not a DETLOG change.** It requires an in-guest
  `detcore` host — the thing `detcore-sabre` and `detcore-dbi` are, and which
  e9patch has no equivalent of. There is nothing smaller to move first.
- **The rewriting pre-pass is not the gap.** e9patch already rewrites; on this
  guest it mapped zero sites, and even a fully-mapped binary would still need
  somewhere in-guest for the rewritten sites to *call into*.
- **Remaining ptrace-dependent even with in-guest DETLOG:** process lifecycle and
  `PTRACE_TRACEME` at spawn; the scheduler's preemption via PMU/RCB signals; every
  syscall from an unmapped site (all of them, on this guest); and the CPUID/RDTSC
  interception that gives e9patch its TSC-cleanliness. That last one is worth
  naming precisely — e9patch's TSC-cleanliness is **inherited from the attached
  ptracer, not earned**, which is the same dependency surfacing as a measurement
  artifact rather than as an error.

So the concrete first step is not "route DETLOG differently". It is "give e9patch
an in-guest tool host at all", after which DETLOG follows for free by the same
mechanism SaBRe already uses. Until then, the strace litmus in §2 is the standing
check, and e9patch fails it identically to plain ptrace.

## Reproduction

```bash
B=./target/debug/hermit
export HERMIT_E9TOOL=<path to e9tool>
$B --log=off run --backend ptrace  --strict --base-env=minimal -- /bin/true   # rc=0
strace -f -o /tmp/p.txt  $B --log=off run --backend ptrace  --strict --base-env=minimal -- /bin/true   # FAILS
strace -f -o /tmp/e.txt  $B --log=off run --backend e9patch --strict --base-env=minimal -- /bin/true   # FAILS, same error
strace -f -o /tmp/d.txt  $B --log=off run --backend dbi     --strict --base-env=minimal -- /bin/true   # rc=0
```

## Limitations

One guest (`/bin/true`) and one host. The `mapped_sites=0` figure is specific to
that guest; a guest with mapped sites would shift *which* syscalls take the ptrace
path but not *whether* the path exists, since the litmus in §2 fails before the
guest runs. `e9tool` was taken from a transient `/tmp` build tree, so the exact
binary is not durably pinned.
