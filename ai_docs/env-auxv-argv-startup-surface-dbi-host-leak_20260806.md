# Guest startup surface: env / auxv / argv — all backends self-deterministic, DBI leaks host state

**Date:** 2026-08-06 · **Task:** `env-auxv-argv-determinism` · **Local only, no egress**
**Hermit:** debug `g0f891e432a75-dirty` · **Host:** devbig014
**Status:** committed to the parent, **not pushed** (egress 403)

## Summary

| axis | ptrace | sabre | dbi | e9patch |
|---|---|---|---|---|
| double-run, same context | **identical** | **identical** | **identical** | **identical** |
| vs ptrace (cross-backend) | — | 10 lines, **all auxv addresses** | **186 lines** | **0 — exact parity** |
| guest env depends on host path | no | no | **YES** | no |
| guest env depends on host `$HOME` | no | no | **YES** | no |
| guest env depends on hermit's CLI flags | no | no | **YES** | no |

Every backend passes the obvious test. **The defect is only visible when the context is varied**,
and it is DBI's.

## The surface, and what the host varies natively

Fixture dumps `argv` in order, `environ` **in situ** (contents *and* order, not sorted), every
`/proc/self/auxv` type/value pair, and a relative placement offset. Native double-run varies in
exactly five auxv entries — the ones hermit must determinize:

`AT_BASE(7)`, `AT_PLATFORM(15)`, `AT_RANDOM(25)` *pointer*, `AT_EXECFN(31)`,
`AT_SYSINFO_EHDR(33)` — i.e. loader base, stack-resident strings, and the vDSO base. Native
`environ` and `argv` contents/order are already stable, so the env axis is entirely about what
*hermit and its backends* add.

All four backends determinize all five. Double-run under each: **zero differing lines.**

## DBI injects four backend variables into the guest's environment

`environ` entry counts: ptrace 159, sabre 159, e9patch 159, **dbi 163**. The four extra, present
only under DBI:

```
DYNAMORIO_CONFIGDIR=/home/newton
DYNAMORIO_EXE_PATH=/home/newton/work/dev-hermit/scratch/startup/surface
DYNAMORIO_TAKEOVER_IN_INIT=1
HERMIT_DBI_DETCONFIG={"virtualize_time":true,...,"seed":0,...,"detlog_stack":false,...}
```

The env **ordering** also differs from ptrace's. Each of the three leaks below is demonstrated,
not inferred:

**1. The guest can read the host's `$HOME`.** `DYNAMORIO_CONFIGDIR=/home/newton`. A different
user running the identical program gets a different guest environment.

**2. The guest can read its own absolute host path**, so behaviour depends on *where the binary
lives*. Same binary, two directories:

| backend | guest-env differing lines between the two paths |
|---|---|
| ptrace | **0** — path-independent |
| dbi | **2** — `DYNAMORIO_EXE_PATH` carries the host path in |

**3. The guest can read hermit's own configuration, including which flags the operator passed.**
`HERMIT_DBI_DETCONFIG` is the whole Detcore config serialised as JSON — `seed`, `epoch`,
`max_timeslice`, and every debug toggle. Adding `--detlog-stack`:

| backend | guest-env differing lines with vs without `--detlog-stack` |
|---|---|
| ptrace | **0** |
| dbi | **2** (`"detlog_stack":false` → `"detlog_stack":true`) |

That one deserves naming on its own: **a flag that exists to *measure* determinism changes what
the guest observes.** Under DBI, measuring perturbs the measured. Any guest that hashes its
environment produces a different answer depending on whether you were watching.

## The methodological point

**DBI passed the double-run test.** Self-consistency in a fixed context is not enough to
establish that a startup surface is clean — it will hold happily while the surface is stuffed
with host state, because the host state does not change between two runs a second apart. Catching
this needs **varied-context runs**: same program from a different path, under a different `$HOME`,
with different harness flags. That belongs in the sweep contract, not just double-run.

## Sabre's 10 lines are a different axis (not duplicated here)

All ten are auxv **addresses**: `AT_PHDR` `0x400040` → `0x555555554040`, `AT_ENTRY` `0x401080` →
`0x5555555574a0`, plus the `AT_RANDOM`/`AT_EXECFN`/`AT_PLATFORM` stack pointers. SaBRe's client
loads at the standard PIE base while ptrace's sits at `0x400000`. `environ` and `argv` are
byte-identical, and — verified separately — `AT_RANDOM`'s **contents** are identical across both
backends and all runs; only the pointer to them moves. This is the address-layout task's axis;
per this task's instruction I am flagging the overlap rather than re-deriving it.

**e9patch is at exact parity with ptrace on this surface — 0 differing lines.** Worth recording
as the positive control: full startup-surface parity is achievable, so DBI's divergence is not
an inherent cost of using a backend.

## Fix direction — and explicitly NOT the #140 anti-pattern

The task rightly forbids fixing this by zeroing or freezing a value. That is not what is needed
here, and the distinction matters:

- **Freezing** would mean pinning `DYNAMORIO_EXE_PATH` to a constant so the hash stops moving.
  That fakes the measurement and leaves the guest reading a fabricated value.
- **The actual fix** is that these variables have no business being guest-visible *at all*. They
  are backend plumbing consumed at startup. Removing them from the guest's `environ` after the
  backend reads them does not invent a value — it restores the guest's **true** environment,
  which is exactly what ptrace, sabre and e9patch already present.

**The precedent already exists in-tree.** `reverie-sabre`'s `take_private_env`
(`reverie/experimental/reverie-sabre/src/paths.rs:63`) does precisely this: reads the value,
splices the entry out of `environ`, and zeroes the underlying bytes — with an assertion confining
it to a `REVERIE_SABRE_` namespace. That is why SaBRe's guest shows 159 entries and not 160+.
DBI has no equivalent, which is the whole gap.

Two caveats for whoever implements it: DynamoRIO reads `DYNAMORIO_*` in its own initialiser, so
the scrub must happen after DR init, not before; and `HERMIT_DBI_DETCONFIG` is hermit's own
channel, so it could avoid the environment entirely (fd or memfd) rather than being scrubbed
after the fact.

## Reproduction

```sh
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64
H=hermit/target/debug/hermit ; G=scratch/startup/surface
for be in ptrace sabre dbi e9patch; do $H run --backend $be -- $G > $be.1; $H run --backend $be -- $G > $be.2
  diff $be.1 $be.2                     # double-run: all clean
  diff ptrace.1 $be.1                  # cross-backend: e9patch 0, sabre 10, dbi 186
done
$H run --backend dbi -- /other/path/surface        # DYNAMORIO_EXE_PATH follows the path
$H run --backend dbi --detlog-stack -- $G          # HERMIT_DBI_DETCONFIG follows the flag
```

## Limits

- One host, one user, one shell environment. The `$HOME` leak is argued from the variable's
  content plus the demonstrated path leak; I did not run as a second user to confirm it directly.
- `liteinst` could not be included (activation failure, root-caused separately); `kvm` hangs on
  this host.
- Order differences beyond the four inserted variables were not decomposed — the insertion alone
  explains a reordering, and separating "inserted" from "independently reordered" needs a run
  with the four removed, which is the fix itself.
