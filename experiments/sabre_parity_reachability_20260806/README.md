# SaBRe strict parity: a measured reachability wall, and why `--verify` cannot see it

**Date:** 2026-08-06 · **Task:** `ratchet-sabre-strict-parity` · **Local only, no egress**
**Hermit:** `0.2.0 (2026-08-04, g0f891e432a75-dirty)`, debug build · **Host:** devbig014
**Status:** committed to the parent, **not pushed** (egress 403)

## Headline

SaBRe intercepts **only the syscalls the main ELF issues itself**. The dynamic loader and
early libc startup run **uninstrumented and undeterminized**. On a guest that makes 4 syscalls
in `main()`, ptrace determinizes **49** syscalls and SaBRe determinizes **4** — and
`hermit run --backend sabre --verify` still reports **"Determinism verified"**.

That last clause is the important one. **Self-consistency is not parity.** A backend that
intercepts almost nothing is trivially reproducible, so the current verify/parity instrument
scores it green. This is exactly the gap the north star names — full detlog parity against
ptrace, not stdout/exit — and it is not visible to any check we run today.

## Environment (two blockers worth recording; both cost real time)

1. **No hermit binary on this box can execute without help.** Every build fails with
   `libunwind-x86_64.so.8: cannot open shared object file`. The library is not in any standard
   location and `ldconfig` does not know it. It lives at
   `/home/newton/.local/hermit-deps/lu/usr/lib64/`; prefix runs with
   `LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64`.
2. **SaBRe exists only in DEBUG builds.** `sabre` is a cargo feature under
   `third-party-backends`, and every release binary on this box was built without it — the
   release `--backend` list is `ptrace|dbi|liteinst`, the debug list adds `sabre|kvm|e9patch`.
   Scanned 12 built binaries: debug has the symbol, release never does. The
   `compat-envelope/scorecard.csv` claim `cell_state=unavailable, reason="backend binary not
   present in this checkout"` is therefore about the *release* build; the backend is runnable
   today via the debug binary.

Also note guest `/tmp` is isolated — a test binary under host `/tmp` fails with
`Program ... is under host /tmp, but Hermit replaces guest /tmp`. Put fixtures elsewhere or
pass `--tmp=/tmp`.

## GAP-1 — the reachability wall (root cause, and it explains most of the rest)

Fixture `t_dyn`: a dynamically linked C program whose `main()` does exactly
`openat` → `read` → `close` → `write`, then returns.

| syscall | issued by | ptrace | sabre |
|---|---|---|---|
| `openat` | ld.so ×11 + **main ×1** | 12 | **1** |
| `read` | ld.so ×1 + **main ×1** | 2 | **1** |
| `close` | ld.so ×2 + **main ×1** | 3 | **1** |
| `write` | **main ×1** | 1 | **1** |
| `exit_group` | libc exit | 1 | 1 |
| `newfstatat` | ld.so | 9 | **0** |
| `mmap` | ld.so | 8 | **0** |
| `pread64` | ld.so | 4 | **0** |
| `mprotect` | ld.so | 3 | **0** |
| `fstat`, `arch_prctl` | startup | 2 each | **0** |
| `set_tid_address`, `set_robust_list`, `rseq`, `prlimit64`, `munmap`, `brk`, `access` | startup | 1 each | **0** |

The signature is unambiguous: **every syscall `main()` issues is caught 1:1; every syscall from
the loader/startup phase is invisible.** SaBRe rewrites the main ELF and nothing else, so
`ld.so` — which runs first and does the majority of the work — is never instrumented.

Consequence for determinism, not just for logging: anything nondeterministic in the
loader/startup path (mmap layout, `brk`, `AT_RANDOM` consumption, `access`/`openat` probe
ordering, `rseq`/`set_robust_list` registration) is **not determinized** under SaBRe. This is
the same class of defect as the e9patch reachability wall, and it caps SaBRe well below strict
parity regardless of how well the intercepted subset behaves.

It also explains a previously separate observation: `DETLOG prlimit64` appears under ptrace and
is absent under sabre. It is a startup syscall — same root cause, not an independent bug.

## GAP-2 — in-guest DETLOG is written to the guest's stderr, not `--log-file`

Under SaBRe the Detcore tool runs **inside the guest** (`libdetcore_sabre.so` plus a coordinator
RPC socket — hermit logs this at startup). Its tracing output goes to the guest's stderr rather
than into hermit's log file:

| guest | backend | DETLOG in `--log-file` | DETLOG in guest stderr | stderr bytes |
|---|---|---|---|---|
| `/bin/true` | ptrace | 153 | 0 | 0 |
| `/bin/true` | **sabre** | **1** | **340** | **48,684** |
| `t_dyn` | ptrace | — | 0 | 0 |
| `t_dyn` | **sabre** | — | **92** | **11,509** |

Two distinct harms:
1. **Detlog parity is not measurable through the documented channel.** `--log-file` gets 1 of
   341 records, so any tool that diffs log files sees an empty SaBRe trace rather than a
   divergence.
2. **Guest stderr parity is broken for every corpus test.** 48 KB of `INFO detcore: DETLOG …`
   on a `/bin/true` run. Any comparison that includes stderr diverges for a reason that has
   nothing to do with the program under test.

The formats also differ — in-guest records are `INFO detcore: DETLOG …` with no timestamp or
module path; supervisor records are `<ts>  INFO detcore::tool_local: DETLOG …` — so merging the
streams is not purely a routing change.

## GAP-3 — the backend's own activity appears in the guest's traced syscall stream

SaBRe logs syscalls ptrace never sees: `clock_gettime` ×51 and `madvise` ×61 for `/bin/true`
(×27 and ×13 for `t_dyn`). These are plugin/runtime activity, not guest behaviour. Structural
record counts for `/bin/true`: `[syscall]` 99 (ptrace) vs 225 (sabre); `[memory]` 49 vs 112.
A parity comparison that just counts records will read this as "SaBRe does more", when in fact
it does far less of the guest and a lot of itself.

## GAP-4 — the instrument is blind to all of the above

`hermit run --backend sabre --verify -- t_dyn` → **rc=0, "Determinism verified"**. Same for
ptrace. Both stdouts are `hi`. So on the two axes the scorecard records — stdout hash and exit
code — **SaBRe and ptrace agree perfectly**, while differing by 45 of 49 determinized syscalls.

This is the concrete demonstration of why the north star is phrased as it is. It also matches
the known shape of the scorecard: parity is a stdout SHA, not a bitwise or detlog comparison.
**Until parity is measured on the detlog, a backend can reach 100% on the current scorecard
without determinizing anything.**

## Reproduction

```sh
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64
H=hermit/target/debug/hermit          # release has no sabre feature
G=scratch/sabre_parity/t_dyn          # NOT under /tmp (guest /tmp is isolated)
for be in ptrace sabre; do
  $H --log=info --log-file=$be.log run --backend $be -- $G > $be.out 2> $be.err
  cat $be.log $be.err | grep -o 'inbound syscall: [a-z_0-9]*' | awk '{print $3}' | sort | uniq -c
done
```

## What I did NOT do, and why

**No fix attempted for GAP-1.** Making SaBRe instrument `ld.so` is an architectural change to
the rewriting strategy, it overlaps the in-guest-toolhost unification track, and it is not a
change to make unilaterally at the end of a session. Filed with the mechanism and a
reproduction instead.

**No fix attempted for GAP-2.** Routing the in-guest tracing subscriber into the supervisor's
log file crosses the guest/coordinator RPC boundary in `detcore-sabre`, and the format
difference means it is not a one-line redirect. It is the **cheapest high-value fix of the
four** and the natural next step: it would make the gap measurable, which is the precondition
for ratcheting anything.

**No corpus sweep.** With GAP-2 open, a corpus run cannot measure detlog parity — it would only
re-measure the stdout/exit axes that already say "pass". Running 600 cells to reconfirm a known
blind spot would be the vacuous-test pattern. The corpus becomes worth running the moment
detlog is comparable.

## Recommended order

1. **Fix GAP-2** (route in-guest DETLOG to `--log-file`, normalise the record format). Unblocks
   measurement and removes the stderr-parity breakage in one change.
2. **Add a detlog-parity axis to the scorecard** (per-record-kind counts vs ptrace), so GAP-1
   becomes a number that can be ratcheted rather than a discovery.
3. **Then** decide GAP-1 on the evidence: SaBRe cannot reach strict parity while `ld.so` is
   uninstrumented, so either the rewriting strategy extends to the loader or SaBRe's envelope is
   documented as "main-ELF syscalls only".
